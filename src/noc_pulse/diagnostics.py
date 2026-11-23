"""Auto-diagnostics attached to a ticket when it opens (what an L1 engineer does first).

For the failing target we run, with hard time-outs:

1. ``ping`` (system binary, 4 echoes)            -> is the host reachable at L3?
2. ``tracert`` / ``traceroute`` (numeric, 12 hops) -> where does the path stop?
3. DNS lookups via the OS resolver and via 1.1.1.1 -> is it a DNS problem?
4. TCP connect to the service port                -> is the port open / refused / filtered?
5. HTTP(S) GET (status line only) for web targets  -> is the application answering?

The output is plain text so it can be pasted into a ticket worklog, followed by a
one-line automated triage summary.
"""

from __future__ import annotations

import asyncio
import ipaddress
import platform
import re
import time
from urllib.parse import urlsplit

from .inventory import Target
from .probes import dns_probe, http_probe, tcp_probe

WINDOWS = platform.system() == "Windows"


async def _run(cmd: list[str], timeout_s: float) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except OSError as exc:
        return f"(could not run {cmd[0]}: {exc})"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"(timed out after {timeout_s:.0f}s)"
    text = out.decode(errors="replace").strip()
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def ping_cmd(host: str) -> list[str]:
    return ["ping", "-n", "4", "-w", "1000", host] if WINDOWS else ["ping", "-c", "4", "-W", "1", host]


def trace_cmd(host: str) -> list[str]:
    if WINDOWS:
        return ["tracert", "-d", "-h", "12", "-w", "500", host]
    return ["traceroute", "-n", "-m", "12", "-w", "1", host]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def run_diagnostics(target: Target, host: str, probe: str, trace: bool = True) -> str:
    """Return a text block with the diagnostic outputs and an automated summary."""
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    sections: list[str] = [f"== auto-diagnostics for {target.id} ({target.name}) at {started} =="]
    findings: dict[str, bool | None] = {}
    tcp_port = next((int(p.params.get("port", 443)) for p in target.probes if p.kind == "tcp"), None)
    http_url = next((p.params.get("url") for p in target.probes if p.kind == "http"), None)
    if http_url and tcp_port is None:
        parts = urlsplit(http_url)
        tcp_port = parts.port or (443 if parts.scheme == "https" else 80)

    jobs: dict[str, asyncio.Task[str]] = {}
    if host != "system":
        jobs["ping"] = asyncio.create_task(_run(ping_cmd(host), 15))
        if trace:
            jobs["traceroute"] = asyncio.create_task(_run(trace_cmd(host), 45))

    # DNS: resolve the name (or, for a resolver target, use it to resolve its query)
    dns_lines: list[str] = []
    if target.role == "dns" or probe == "dns":
        query = next((p.params.get("query") for p in target.probes if p.kind == "dns"), None)
        query = query or "www.tatacommunications.com"
        for server in ([host] if host != "system" else ["system"]) + ["1.1.1.1"]:
            r = await dns_probe(target.id, server, query)
            dns_lines.append(
                f"resolve {query} via {server}: "
                + (
                    f"OK in {r.latency_ms:.1f} ms ({r.detail.get('answers')} answers)"
                    if r.ok
                    else f"FAILED ({r.error})"
                )
            )
            findings.setdefault("dns", r.ok)
    elif not _is_ip(host) and host != "system":
        for server in ("system", "1.1.1.1"):
            r = await dns_probe(target.id, server, host)
            dns_lines.append(
                f"resolve {host} via {server}: "
                + (f"OK in {r.latency_ms:.1f} ms" if r.ok else f"FAILED ({r.error})")
            )
            findings.setdefault("dns", r.ok)

    tcp_line = None
    if tcp_port and host != "system":
        r = await tcp_probe(target.id, host, tcp_port)
        findings["tcp"] = r.ok
        tcp_line = f"TCP connect {host}:{tcp_port}: " + (
            f"open, {r.latency_ms:.1f} ms" if r.ok else f"FAILED ({r.error})"
        )
    http_line = None
    if http_url:
        r = await http_probe(target.id, http_url)
        findings["http"] = r.ok
        http_line = f"HTTP GET {http_url}: " + (
            f"status {r.status}, {r.latency_ms:.0f} ms" if r.status else f"FAILED ({r.error})"
        )

    results = {name: await task for name, task in jobs.items()}
    if "ping" in results:
        out = results["ping"]
        findings["ping"] = ("TTL=" in out.upper()) or ("ttl=" in out)
        sections.append("$ " + " ".join(ping_cmd(host)) + "\n" + out)
    if "traceroute" in results:
        sections.append("$ " + " ".join(trace_cmd(host)) + "\n" + results["traceroute"])
        hop = last_responding_hop(results["traceroute"])
        if hop is not None:
            reached = hop[1] == host
            sections.append(
                f"traceroute: last reply from hop {hop[0]} ({hop[1]})"
                + (" = destination reached" if reached else " -> path stops after this hop")
            )
    if dns_lines:
        sections.append("DNS checks:\n" + "\n".join("  " + x for x in dns_lines))
    if tcp_line:
        sections.append(tcp_line)
    if http_line:
        sections.append(http_line)
    sections.append("L1 triage summary: " + summarize_findings(findings))
    return "\n\n".join(sections)


_HOP_RE = re.compile(r"^\s*(\d+)\s+.*?(\d{1,3}(?:\.\d{1,3}){3})\b")


def last_responding_hop(trace_output: str) -> tuple[int, str] | None:
    """(hop number, address) of the last hop that answered in tracert/traceroute output."""
    last = None
    for line in trace_output.splitlines():
        if line.lstrip().lower().startswith(("tracing", "traceroute", "over a")):
            continue
        m = _HOP_RE.match(line)
        if m:
            last = (int(m.group(1)), m.group(2))
    return last


def summarize_findings(f: dict[str, bool | None]) -> str:
    """Turn the individual checks into a first-line hypothesis (layer by layer)."""
    if f.get("dns") is False and not f.get("tcp") and not f.get("ping"):
        return "name resolution failing -> check resolver / DNS service first"
    if f.get("ping") is False and f.get("tcp") is False:
        return "host unreachable at L3 and service port closed -> suspect link/host down"
    if f.get("ping") is False and f.get("tcp") is None and f.get("http") is None:
        return "no ICMP reply (host/path down or ICMP filtered) -> check where traceroute stops"
    if f.get("ping") is False and f.get("tcp"):
        return "ICMP blocked or rate-limited but TCP works -> service likely fine, check ICMP policy"
    if f.get("ping") and f.get("tcp") is False:
        return "host answers ping but port refused/filtered -> application or firewall issue"
    if f.get("tcp") and f.get("http") is False:
        return "TCP open but HTTP failing -> application layer issue (web server / TLS)"
    if all(v for v in f.values() if v is not None):
        return "all checks pass now -> transient fault or already recovered; monitor"
    return "mixed results -> escalate to L2 with this output"
