"""Active probes (asyncio): ICMP ping, TCP connect, DNS resolution, HTTP(S).

Every probe returns a :class:`ProbeResult`. Probes never raise: failures are
reported as ``ok=False`` with an ``error`` string so the collector loop keeps going.

ICMP on Windows: ``icmplib`` works for an unprivileged user on Windows (it uses
the OS ICMP socket support), so it is the default backend. If it is unavailable
or refuses to run (e.g. Linux without ``net.ipv4.ping_group_range``), we fall back
to running the system ``ping`` binary and parsing its output.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

USER_AGENT = "noc-pulse/0.1 (+https://github.com/RutuK2005/noc-pulse; low-rate monitoring)"


@dataclass
class ProbeResult:
    ts: float
    target: str
    probe: str
    ok: bool
    latency_ms: float | None = None
    sent: int | None = None
    received: int | None = None
    rtts: list[float] | None = None
    status: int | None = None
    cert_days: float | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    # --- derived metrics used by the alarm rules and KPI code ---------------
    @property
    def loss_pct(self) -> float | None:
        if not self.sent:
            return None
        return 100.0 * (self.sent - (self.received or 0)) / self.sent

    @property
    def jitter_ms(self) -> float | None:
        from .kpi import mean_abs_delta

        if not self.rtts or len(self.rtts) < 2:
            return None
        return mean_abs_delta(self.rtts)

    def metrics(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "loss_pct": self.loss_pct,
            "jitter_ms": self.jitter_ms,
            "status": self.status,
            "cert_days": self.cert_days,
        }

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["ok"] = int(self.ok)
        row["rtts"] = json.dumps([round(x, 3) for x in self.rtts]) if self.rtts is not None else None
        row["detail"] = json.dumps(self.detail) if self.detail else None
        return row

    @classmethod
    def from_row(cls, row: Any) -> ProbeResult:
        d = dict(row)
        d.pop("id", None)
        d["ok"] = bool(d["ok"])
        d["rtts"] = json.loads(d["rtts"]) if d.get("rtts") else None
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
        return cls(**d)


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 3)


def _err(exc: BaseException) -> str:
    text = str(exc) or ("timed out" if isinstance(exc, TimeoutError) else exc.__class__.__name__)
    return f"{exc.__class__.__name__}: {text}"[:300]


# ---------------------------------------------------------------------------
# ICMP
# ---------------------------------------------------------------------------
_RTT_RE = re.compile(r"time\s*([=<])\s*([0-9]+(?:\.[0-9]+)?)\s*ms", re.IGNORECASE)
_WIN_STATS = re.compile(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", re.IGNORECASE)
_NIX_STATS = re.compile(r"(\d+)\s+packets transmitted,\s*(\d+)\s+(?:packets )?received")


def parse_ping_output(text: str, count: int) -> tuple[int, int, list[float]]:
    """Parse Windows or Linux ``ping`` output -> (sent, received, rtts_ms).

    Windows prints ``time<1ms`` for sub-millisecond replies; we record 0.5 ms.
    Lines such as ``Destination host unreachable`` carry no ``time=`` and are
    therefore counted as lost.
    """
    rtts: list[float] = []
    for m in _RTT_RE.finditer(text):
        value = float(m.group(2))
        rtts.append(0.5 if m.group(1) == "<" else value)
    sent, received = count, len(rtts)
    m = _WIN_STATS.search(text) or _NIX_STATS.search(text)
    if m:
        sent = int(m.group(1))
        received = min(int(m.group(2)), len(rtts)) if rtts else 0
    return sent, received, rtts


async def _system_ping(host: str, count: int, timeout_s: float) -> tuple[int, int, list[float]]:
    if platform.system() == "Windows":
        args = ["ping", "-n", str(count), "-w", str(int(timeout_s * 1000)), host]
    else:
        args = ["ping", "-c", str(count), "-W", str(max(1, int(timeout_s))), "-i", "0.2", host]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=count * (timeout_s + 1) + 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return parse_ping_output(out.decode(errors="replace"), count)


async def ping_probe(
    target: str,
    host: str,
    count: int = 10,
    interval_s: float = 0.2,
    timeout_s: float = 1.0,
    backend: str = "auto",
) -> ProbeResult:
    ts = time.time()
    used = backend
    try:
        rtts: list[float]
        if backend in ("auto", "icmplib"):
            try:
                import icmplib

                h = await icmplib.async_ping(
                    host, count=count, interval=interval_s, timeout=timeout_s, privileged=False
                )
                sent, received, rtts = h.packets_sent, h.packets_received, list(h.rtts)
                used = "icmplib"
            except Exception as exc:
                # fall back only when the ICMP socket itself cannot be opened
                socket_problem = isinstance(exc, ImportError | PermissionError) or (
                    exc.__class__.__name__ in {"SocketPermissionError", "ICMPSocketError"}
                )
                if backend == "icmplib" or not socket_problem:
                    raise
                sent, received, rtts = await _system_ping(host, count, timeout_s)
                used = "system"
        else:
            sent, received, rtts = await _system_ping(host, count, timeout_s)
            used = "system"
        ok = received > 0
        latency = round(sum(rtts) / len(rtts), 3) if rtts else None
        return ProbeResult(
            ts=ts,
            target=target,
            probe="ping",
            ok=ok,
            latency_ms=latency,
            sent=sent,
            received=received,
            rtts=[round(x, 3) for x in rtts],
            error=None if ok else "no echo replies",
            detail={"backend": used},
        )
    except Exception as exc:  # noqa: BLE001 - a probe must never crash the collector
        return ProbeResult(
            ts=ts,
            target=target,
            probe="ping",
            ok=False,
            sent=count,
            received=0,
            rtts=[],
            error=_err(exc),
            detail={"backend": used},
        )


# ---------------------------------------------------------------------------
# TCP connect
# ---------------------------------------------------------------------------
async def tcp_probe(target: str, host: str, port: int, timeout_s: float = 3.0) -> ProbeResult:
    ts = time.time()
    try:
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=timeout_s
        )
        dns_ms = _ms(time.perf_counter() - t0)
        ip = infos[0][4][0]
        t1 = time.perf_counter()
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout_s)
        connect_ms = _ms(time.perf_counter() - t1)
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, TimeoutError):
            pass
        return ProbeResult(
            ts=ts,
            target=target,
            probe="tcp",
            ok=True,
            latency_ms=connect_ms,
            detail={"port": port, "dns_ms": dns_ms},
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            ts=ts, target=target, probe="tcp", ok=False, error=_err(exc), detail={"port": port}
        )


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------
async def dns_probe(
    target: str, server: str, query: str, rdtype: str = "A", timeout_s: float = 3.0
) -> ProbeResult:
    """Resolve ``query`` against ``server`` (an IP) or the OS resolver (``system``)."""
    ts = time.time()
    try:
        import dns.asyncresolver
        import dns.exception
        import dns.resolver

        if server == "system":
            resolver = dns.asyncresolver.Resolver()
        else:
            resolver = dns.asyncresolver.Resolver(configure=False)
            resolver.nameservers = [server]
        resolver.lifetime = timeout_s
        resolver.timeout = timeout_s
        t0 = time.perf_counter()
        answer = await resolver.resolve(query, rdtype, search=False)
        elapsed = _ms(time.perf_counter() - t0)
        return ProbeResult(
            ts=ts,
            target=target,
            probe="dns",
            ok=True,
            latency_ms=elapsed,
            detail={"query": query, "rdtype": rdtype, "answers": len(answer.rrset or [])},
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            ts=ts,
            target=target,
            probe="dns",
            ok=False,
            error=_err(exc),
            detail={"query": query, "rdtype": rdtype},
        )


# ---------------------------------------------------------------------------
# HTTP(S)
# ---------------------------------------------------------------------------
def cert_days_left(not_after: str, now: float | None = None) -> float:
    """Days until a certificate's ``notAfter`` (format used by ``ssl.getpeercert``)."""
    expires = ssl.cert_time_to_seconds(not_after)
    return round((expires - (time.time() if now is None else now)) / 86400.0, 2)


async def _http_once(url: str, timeout_s: float) -> dict[str, Any]:
    parts = urlsplit(url)
    secure = parts.scheme == "https"
    host = parts.hostname or ""
    port = parts.port or (443 if secure else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    loop = asyncio.get_running_loop()
    out: dict[str, Any] = {}
    t0 = time.perf_counter()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    t1 = time.perf_counter()
    out["dns_ms"] = _ms(t1 - t0)
    reader, writer = await asyncio.open_connection(infos[0][4][0], port)
    t2 = time.perf_counter()
    out["connect_ms"] = _ms(t2 - t1)
    try:
        if secure:
            ctx = ssl.create_default_context()
            await writer.start_tls(ctx, server_hostname=host)
            t3 = time.perf_counter()
            out["tls_ms"] = _ms(t3 - t2)
            cert = writer.get_extra_info("ssl_object").getpeercert()
            if cert and cert.get("notAfter"):
                out["cert_days"] = cert_days_left(cert["notAfter"])
        else:
            t3 = t2
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {USER_AGENT}\r\n"
            "Accept: */*\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        status_line = await reader.readline()
        t4 = time.perf_counter()
        out["ttfb_ms"] = _ms(t4 - t3)
        out["total_ms"] = _ms(t4 - t0)
        m = re.match(rb"HTTP/\d(?:\.\d)?\s+(\d{3})", status_line)
        if not m:
            raise ValueError(f"bad status line: {status_line[:60]!r}")
        out["status"] = int(m.group(1))
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except (OSError, TimeoutError, ssl.SSLError):
            pass
    return out


async def http_probe(target: str, url: str, timeout_s: float = 10.0) -> ProbeResult:
    """One GET request; we read only the status line, then close the connection.

    ``latency_ms`` is the total time to first byte including DNS, TCP and TLS.
    A 2xx/3xx answer is "up"; 4xx/5xx is reported as a failure.
    """
    ts = time.time()
    try:
        d = await asyncio.wait_for(_http_once(url, timeout_s), timeout=timeout_s)
        status = d.pop("status")
        ok = 200 <= status < 400
        return ProbeResult(
            ts=ts,
            target=target,
            probe="http",
            ok=ok,
            latency_ms=d["total_ms"],
            status=status,
            cert_days=d.pop("cert_days", None),
            error=None if ok else f"HTTP {status}",
            detail=d,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(ts=ts, target=target, probe="http", ok=False, error=_err(exc))


async def run_probe(target_id: str, host: str, kind: str, params: dict[str, Any]) -> ProbeResult:
    """Dispatch one probe described by an inventory ``ProbeSpec``."""
    if kind == "ping":
        return await ping_probe(
            target_id,
            host,
            count=int(params.get("count", 10)),
            interval_s=float(params.get("echo_interval_s", 0.2)),
            timeout_s=float(params.get("timeout_s", 1.0)),
            backend=params.get("backend", "auto"),
        )
    if kind == "tcp":
        return await tcp_probe(
            target_id, host, int(params.get("port", 443)), float(params.get("timeout_s", 3.0))
        )
    if kind == "dns":
        return await dns_probe(
            target_id,
            host,
            params.get("query", "www.tatacommunications.com"),
            params.get("rdtype", "A"),
            float(params.get("timeout_s", 3.0)),
        )
    if kind == "http":
        return await http_probe(
            target_id, params.get("url", f"https://{host}/"), float(params.get("timeout_s", 10.0))
        )
    raise ValueError(f"unknown probe kind {kind}")
