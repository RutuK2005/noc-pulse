"""Reports: data coverage, per-target KPIs, per-service SLA compliance, alarms, tickets, SD-WAN.

All functions read from the SQLite store and return plain dicts/lists so the CLI
(Rich tables), the Markdown writer and the Streamlit dashboard can share them.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any

from .inventory import Inventory, Service
from .kpi import KpiSummary, evaluate_sla, summarize
from .probes import ProbeResult
from .sdwan import AppClass, run_simulation
from .store import Store
from .tickets import mtta_s, mttr_s, sla_report


def fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def coverage(store: Store, gap_s: float = 120.0) -> dict[str, Any]:
    first, last, n = store.time_range()
    gaps: list[tuple[float, float]] = []
    prev = None
    for (ts,) in store.conn.execute("SELECT ts FROM samples ORDER BY ts"):
        if prev is not None and ts - prev > gap_s:
            gaps.append((prev, ts))
        prev = ts
    echoes = store.conn.execute(
        "SELECT COALESCE(SUM(sent), 0), COALESCE(SUM(received), 0) FROM samples WHERE probe='ping'"
    ).fetchone()
    by_probe = dict(store.conn.execute("SELECT probe, COUNT(*) FROM samples GROUP BY probe").fetchall())
    return {
        "first": first,
        "last": last,
        "duration_h": ((last - first) / 3600.0) if first is not None else 0.0,
        "samples": n,
        "by_probe": by_probe,
        "echoes_sent": echoes[0],
        "echoes_received": echoes[1],
        "gaps": gaps,
        "gap_total_s": sum(b - a for a, b in gaps),
        "runs": store.runs(),
    }


def target_kpis(store: Store, since: float | None = None, until: float | None = None) -> list[dict[str, Any]]:
    rows = []
    for target, probe in store.series_keys():
        rs = store.samples(target=target, probe=probe, since=since, until=until)
        if not rs:
            continue
        k = summarize(rs)
        row = {"target": target, "probe": probe, **k.as_dict()}
        certs = [r.cert_days for r in rs if r.cert_days is not None]
        if certs:
            row["cert_days_min"] = min(certs)
        errors = Counter((r.error or "").split(":")[0] for r in rs if not r.ok)
        row["top_error"] = errors.most_common(1)[0][0] if errors else ""
        rows.append(row)
    return rows


def _kpi_probe(service: Service, inv: Inventory, keys: set[tuple[str, str]]) -> tuple[str, list[str]]:
    if service.kpi_probe:
        return service.kpi_probe, [t for t in service.targets if (t, service.kpi_probe) in keys]
    for kind in ("ping", "http", "dns", "tcp"):
        members = [t for t in service.targets if (t, kind) in keys]
        if kind == "ping":
            non_gw = [t for t in members if inv.targets[t].role != "gateway"]
            members = non_gw or members
        if members:
            return kind, members
    return "none", []


def service_availability(results: list[ProbeResult], bucket_s: float = 60.0) -> tuple[float | None, int, int]:
    """A bucket (minute) is available only if every sample of every member succeeded in it."""
    buckets: dict[int, bool] = {}
    for r in results:
        b = int(r.ts // bucket_s)
        buckets[b] = buckets.get(b, True) and r.ok
    if not buckets:
        return None, 0, 0
    good = sum(buckets.values())
    return 100.0 * good / len(buckets), good, len(buckets)


def service_kpis(
    store: Store, inv: Inventory, since: float | None = None, until: float | None = None
) -> list[dict[str, Any]]:
    keys = set(store.series_keys())
    out = []
    for svc in inv.services.values():
        members = [r for t in svc.targets for r in store.samples(target=t, since=since, until=until)]
        avail, good, total = service_availability(members)
        kind, kpi_targets = _kpi_probe(svc, inv, keys)
        kpi_results = [
            r for t in kpi_targets for r in store.samples(target=t, probe=kind, since=since, until=until)
        ]
        kpi_results.sort(key=lambda r: r.ts)
        k = summarize(kpi_results) if kpi_results else KpiSummary()
        k.availability_pct = avail
        policy = inv.sla_policies[svc.sla_class]
        checks = evaluate_sla(k, policy)
        out.append(
            {
                "service": svc.id,
                "name": svc.name,
                "customer": svc.customer,
                "sla_class": svc.sla_class,
                "kpi_source": f"{kind}: {', '.join(kpi_targets) or '-'}",
                "minutes": total,
                "minutes_up": good,
                "availability_pct": avail,
                "latency_p50_ms": k.latency_p50_ms,
                "latency_p95_ms": k.latency_p95_ms,
                "jitter_ms": k.jitter_ms,
                "loss_pct": k.loss_pct,
                "mos": k.mos,
                "r_factor": k.r_factor,
                "breaches": [c.metric for c in checks if c.breached],
                "checks": [c.__dict__ for c in checks],
            }
        )
    return out


def alarm_summary(store: Store) -> dict[str, Any]:
    rows = store.rows("SELECT * FROM alarms ORDER BY raised_at")
    return {
        "total": len(rows),
        "suppressed": sum(1 for r in rows if r["suppressed"]),
        "flaps": sum(r["flap_count"] for r in rows),
        "by_rule": dict(Counter(r["rule"] for r in rows)),
        "by_severity": dict(Counter(r["severity"] for r in rows)),
        "active": [r for r in rows if r["state"] == "active"],
        "rows": rows,
    }


def ticket_summary(store: Store, now: float | None = None) -> dict[str, Any]:
    from .engine import load_tickets

    tickets = load_tickets(store)
    now = now if now is not None else time.time()
    return {
        "total": len(tickets),
        "by_priority": dict(Counter(t.priority for t in tickets)),
        "by_state": dict(Counter(t.state.value for t in tickets)),
        "mtta_s": mtta_s(tickets),
        "mttr_s": mttr_s(tickets),
        "sla": sla_report(tickets, now),
        "tickets": tickets,
    }


def sdwan_report(
    store: Store, inv: Inventory, since: float | None = None, until: float | None = None
) -> dict[str, Any]:
    cfg = inv.sdwan
    if not cfg.get("paths"):
        raise ValueError("inventory has no sdwan.paths section")
    by_path = {
        name: store.samples(target=target, probe="ping", since=since, until=until)
        for name, target in cfg["paths"].items()
    }
    classes = {}
    for name, c in (cfg.get("classes") or {}).items():
        classes[name] = AppClass(
            name,
            float(c["latency_ms"]),
            math.inf if c.get("jitter_ms") is None else float(c["jitter_ms"]),
            float(c["loss_pct"]),
        )
    rep = run_simulation(
        by_path,
        primary=cfg.get("primary", next(iter(cfg["paths"]))),
        classes=classes or None,
        bucket_s=float(cfg.get("bucket_s", 60)),
        hold_down=int(cfg.get("hold_down", 3)),
    )
    rep["path_targets"] = dict(cfg["paths"])
    return rep


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _f(x: Any, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        if math.isinf(x):
            return "n/a"
        return f"{x:.{nd}f}"
    return str(x)


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


def render_markdown(store: Store, inv: Inventory, title: str = "noc-pulse report") -> str:
    cov = coverage(store)
    lines = [f"# {title}", ""]
    lines += [
        f"Generated {fmt_ts(time.time())} (local time) by `noc-pulse report --markdown`.",
        "",
        "## Data coverage",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| First sample | {fmt_ts(cov['first'])} |",
        f"| Last sample | {fmt_ts(cov['last'])} |",
        f"| Duration | {cov['duration_h']:.2f} h |",
        f"| Samples (all probes) | {cov['samples']} |",
        "| Samples by probe | " + ", ".join(f"{k}: {v}" for k, v in sorted(cov["by_probe"].items())) + " |",
        f"| ICMP echoes sent / received | {cov['echoes_sent']} / {cov['echoes_received']} |",
        f"| Collector gaps > 120 s | {len(cov['gaps'])} (total {_dur(cov['gap_total_s'])}) |",
        "",
        "## Per-target KPIs",
        "",
        "Latency for ping = every echo RTT; for tcp = connect time; dns = query time; "
        "http = time to first byte incl. DNS+TCP+TLS. Jitter = mean |dRTT| inside a ping burst.",
        "",
        "| Target | Probe | Samples | Avail % | p50 ms | p95 ms | Jitter ms | Loss % | MOS | Min cert days |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in target_kpis(store):
        lines.append(
            f"| {r['target']} | {r['probe']} | {r['samples']} | {_f(r['availability_pct'], 3)} | "
            f"{_f(r['latency_p50_ms'])} | {_f(r['latency_p95_ms'])} | {_f(r['jitter_ms'])} | "
            f"{_f(r['loss_pct'], 3)} | {_f(r['mos'])} | {_f(r.get('cert_days_min'), 1)} |"
        )
    lines += [
        "",
        "## Customer services vs SLA",
        "",
        "Availability is per minute: a minute counts as up only if every probe of every "
        "member target succeeded in it.",
        "",
        "| Service | Class | KPI source | Minutes | Avail % | p95 ms | Jitter ms | Loss % | MOS | SLA |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in service_kpis(store, inv):
        verdict = "met" if not s["breaches"] else "BREACH: " + ", ".join(s["breaches"])
        lines.append(
            f"| {s['service']} | {s['sla_class']} | {s['kpi_source']} | {s['minutes']} | "
            f"{_f(s['availability_pct'], 3)} | {_f(s['latency_p95_ms'])} | {_f(s['jitter_ms'])} | "
            f"{_f(s['loss_pct'], 3)} | {_f(s['mos'])} | {verdict} |"
        )
    al = alarm_summary(store)
    tk = ticket_summary(store, now=cov["last"])
    lines += [
        "",
        "## Alarms and tickets (from `noc-pulse replay` over the samples above)",
        "",
        f"- Alarms raised: {al['total']} (suppressed by correlation: {al['suppressed']}, "
        f"flap re-raises: {al['flaps']})",
        f"- By rule: {', '.join(f'{k}={v}' for k, v in sorted(al['by_rule'].items())) or 'none'}",
        f"- Tickets opened: {tk['total']} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(tk['by_priority'].items())) or 'none'})",
        f"- Ticket states: {', '.join(f'{k}={v}' for k, v in sorted(tk['by_state'].items())) or '-'}",
        f"- MTTR (open -> Resolved): {_dur(tk['mttr_s'])}; MTTA: {_dur(tk['mtta_s'])} "
        "(replay has no human acknowledgements, so MTTA is normally '-')",
        "",
    ]
    if al["rows"]:
        lines += [
            "| # | Raised | Cleared | Target/probe | Rule | Severity | Suppressed (root #) | Ticket |",
            "|---:|---|---|---|---|---|---|---|",
        ]
        for a in al["rows"][:60]:
            sup = f"yes (#{a['root_cause_id']})" if a["suppressed"] else "no"
            lines.append(
                f"| {a['id']} | {fmt_ts(a['raised_at'])} | {fmt_ts(a['cleared_at'])} | "
                f"{a['target']}/{a['probe']} | {a['rule']} | {a['severity']} | {sup} | "
                f"{a['ticket_id'] or '-'} |"
            )
        if len(al["rows"]) > 60:
            lines.append(f"\n({len(al['rows']) - 60} more alarms not shown)")
    try:
        sd = sdwan_report(store, inv)
    except ValueError:
        sd = None
    if sd and sd["intervals"]:
        lines += ["", "## SD-WAN path selection (simulation over the measured ping series)", ""]
        lines.append(
            f"Paths: {', '.join(f'{k} = {v}' for k, v in sd['path_targets'].items())}; "
            f"primary = {sd['primary']}; {sd['intervals']} decision intervals of "
            f"{sd['bucket_s']:.0f} s; hold-down = {sd['hold_down']} intervals."
        )
        lines += [
            "",
            "| App class | SLA (RTT / jitter / loss) | Static primary | SLA-aware dynamic | "
            "Oracle (upper bound) | Switches static / dynamic | Per-path compliance |",
            "|---|---|---:|---:|---:|---|---|",
        ]
        for name, c in sd["classes"].items():
            sla = c["sla"]
            per = ", ".join(f"{p} {v:.2f}%" for p, v in c["per_path_pct"].items())
            lines.append(
                f"| {name} | <{sla['latency_ms']:.0f} ms / <{_f(sla['jitter_ms'], 0)} ms / "
                f"<{sla['loss_pct']:g} % | {c['static_pct']:.2f}% ({c['static_met']}/{c['intervals']}) | "
                f"{c['dynamic_pct']:.2f}% ({c['dynamic_met']}/{c['intervals']}) | "
                f"{c['oracle_pct']:.2f}% | {c['static_switches']} / {c['dynamic_switches']} | {per} |"
            )
    lines.append("")
    return "\n".join(lines)
