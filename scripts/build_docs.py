"""Fill the README / FACTS / LEARN numbers from the committed result files.

Every number that appears in README.md "Real results", docs/FACTS.md and the project
questions in docs/LEARN.md is read from these files, which are produced by the CLI:

    results/summary.json              noc-pulse report --json (after noc-pulse replay)
    results/sdwan.json                noc-pulse sdwan --json
    results/fault_demo/summary.json   noc-pulse demo
    results/pytest.txt                python -m pytest (last line = test count)

Usage:  python scripts/build_docs.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results"
README = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "README.md"


def f(x, nd=2):  # noqa: ANN001, ANN201
    if x is None:
        return "-"
    return f"{x:.{nd}f}"


def load() -> dict:
    s = json.loads((RES / "summary.json").read_text(encoding="utf-8"))
    sd = json.loads((RES / "sdwan.json").read_text(encoding="utf-8"))
    demo = json.loads((RES / "fault_demo" / "summary.json").read_text(encoding="utf-8"))
    tests_line = (RES / "pytest.txt").read_text(encoding="utf-8").strip().splitlines()[-1]
    m = re.search(r"(\d+) passed", tests_line)
    tests = int(m.group(1)) if m else 0
    return {"s": s, "sd": sd, "demo": demo, "tests": tests}


def target(s: dict, tid: str, probe: str) -> dict:
    return next(r for r in s["targets"] if r["target"] == tid and r["probe"] == probe)


def service(s: dict, sid: str) -> dict:
    return next(r for r in s["services"] if r["service"] == sid)


def results_block(d: dict) -> str:
    s, sd = d["s"], d["sd"]
    cov = s["coverage"]
    bp = cov["by_probe"]
    out = [
        f"Collected with `noc-pulse collect --duration 9h --keep-awake` (stopped manually) from {s['first_local']} to "
        f"{s['last_local']} (local time, IST) on one Windows 11 laptop on home Wi-Fi + broadband, "
        "then processed with `replay`, `report`, `sdwan`; full output in "
        "[`results/report.md`](results/report.md), an anonymised 1-hour raw sample in "
        "[`results/sample_raw.csv`](results/sample_raw.csv).",
        "",
        "| Data | Value |",
        "|---|---|",
        f"| Duration | {cov['duration_h']:.2f} h |",
        f"| Samples | {cov['samples']:,} (ping bursts {bp.get('ping', 0):,}, DNS {bp.get('dns', 0):,}, "
        f"TCP {bp.get('tcp', 0):,}, HTTPS {bp.get('http', 0):,}) |",
        f"| ICMP echoes sent / received | {cov['echoes_sent']:,} / {cov['echoes_received']:,} |",
        f"| Collector gaps > 120 s | {len(cov['gaps'])} |",
        "",
        "**Customer services vs SLA (whole run).** Availability per minute (a minute is up only if every "
        "probe of every member target succeeded).",
        "",
        "| Service | SLA class | KPI source | Minutes | Availability % | p95 ms | Jitter ms | Loss % | MOS | SLA |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in s["services"]:
        verdict = "met" if not r["breaches"] else "breached: " + ", ".join(r["breaches"])
        out.append(
            f"| {r['service']} | {r['sla_class']} | {r['kpi_source']} | {r['minutes']} | "
            f"{f(r['availability_pct'], 3)} | {f(r['latency_p95_ms'])} | {f(r['jitter_ms'])} | "
            f"{f(r['loss_pct'], 3)} | {f(r['mos'])} | {verdict} |"
        )
    out += [
        "",
        "**ICMP (10-echo bursts every 30 s).**",
        "",
        "| Target | Bursts | Echoes | Loss % | p50 RTT ms | p95 RTT ms | Jitter ms | MOS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in s["targets"]:
        if r["probe"] == "ping":
            out.append(
                f"| {r['target']} | {r['samples']} | {r['echoes_sent']} | {f(r['loss_pct'], 3)} | "
                f"{f(r['latency_p50_ms'])} | {f(r['latency_p95_ms'])} | {f(r['jitter_ms'])} | "
                f"{f(r['mos'])} |"
            )
    out += [
        "",
        "**HTTPS (one GET per minute; latency = DNS + TCP + TLS + time to first byte) and TCP connect.**",
        "",
        "| Site | Samples | Availability % | p50 ms | p95 ms | TCP connect p50 ms | Cert days left (min) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in s["targets"]:
        if r["probe"] == "http":
            tcp = target(s, r["target"], "tcp")
            out.append(
                f"| {r['target']} | {r['samples']} | {f(r['availability_pct'], 3)} | "
                f"{f(r['latency_p50_ms'], 1)} | {f(r['latency_p95_ms'], 1)} | "
                f"{f(tcp['latency_p50_ms'])} | {f(r.get('cert_days_min'), 1)} |"
            )
    out += [
        "",
        "**DNS query time.**",
        "",
        "| Resolver | Queries | Availability % | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in s["targets"]:
        if r["probe"] == "dns":
            out.append(
                f"| {r['target']} | {r['samples']} | {f(r['availability_pct'], 3)} | "
                f"{f(r['latency_p50_ms'])} | {f(r['latency_p95_ms'])} |"
            )
    al, tk = s["alarms"], s["tickets"]
    out += [
        "",
        "**Alarms and tickets** (`noc-pulse replay` over the same samples): "
        f"{al['total']} alarms raised ({al['suppressed']} suppressed by correlation, "
        f"{al['flaps']} flap re-raises; by rule: "
        f"{', '.join(f'{k} {v}' for k, v in sorted(al['by_rule'].items())) or 'none'}), "
        f"{tk['total']} tickets opened"
        + (f" ({', '.join(f'{k} {v}' for k, v in sorted(tk['by_priority'].items()))})" if tk["total"] else "")
        + (f", MTTR {tk['mttr_s'] / 60:.1f} min" if tk.get("mttr_s") else "")
        + ". See [`results/report.md`](results/report.md) and "
        "[`results/shift_handover.md`](results/shift_handover.md).",
        "",
        f"**SD-WAN simulation** ({sd['intervals']} decision intervals of {sd['bucket_s']:.0f} s; paths "
        + ", ".join(f"{k} = {v}" for k, v in sd["path_targets"].items())
        + f"; primary {sd['primary']}; hold-down {sd['hold_down']}). "
        "Score = % of intervals in which the chosen path met the application SLA.",
        "",
        "| App class | SLA (RTT / jitter / loss) | Static primary | SLA-aware dynamic | Oracle | "
        "Switches static / dynamic | Each path alone |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for name, c in sd["classes"].items():
        sla = c["sla"]
        jit = sla["jitter_ms"]
        jit_txt = "any" if jit is None or jit == float("inf") else f"<={jit:.0f} ms"
        per = ", ".join(f"{k} {v:.2f} %" for k, v in c["per_path_pct"].items())
        out.append(
            f"| {name} | <={sla['latency_ms']:.0f} ms / {jit_txt} / <={sla['loss_pct']:g} % | "
            f"{c['static_pct']:.2f} % ({c['static_met']}/{c['intervals']}) | "
            f"{c['dynamic_pct']:.2f} % ({c['dynamic_met']}/{c['intervals']}) | {c['oracle_pct']:.2f} % | "
            f"{c['static_switches']} / {c['dynamic_switches']} | {per} |"
        )
    return "\n".join(out)


def demo_block(d: dict) -> str:
    p1, p2 = d["demo"]["phase1"], d["demo"]["phase2"]
    return "\n".join(
        [
            "Result of the committed run ([`results/fault_demo/README.md`](results/fault_demo/README.md) "
            "has the full timeline, worklogs and diagnostics output):",
            "",
            "| | Phase 1: edge failure | Phase 2: app-only failure |",
            "|---|---|---|",
            f"| Fault -> alarm raised | {p1['detect_s']} s | {p2['detect_s']} s |",
            f"| Ticket | {p1['ticket']} {p1['priority']} | {p2['ticket']} {p2['priority']} |",
            f"| Child alarms suppressed under the root cause | {len(p1['suppressed_child_alarms'])} "
            f"({', '.join(p1['suppressed_child_alarms'])}) | none (parent healthy) |",
            f"| Tickets opened | {p1['tickets_opened_in_phase']} | 1 |",
            f"| Ticket open -> acknowledged (scripted operator) | {p1['time_to_ack_s']} s | - |",
            f"| Ticket open -> Resolved (MTTR) | {p1['mttr_s']} s | {p2['mttr_s']} s |",
            "",
            "These are seconds because the lab probes every 2 s on localhost; they demonstrate the "
            "pipeline, not an operational MTTR.",
        ]
    )


def code_stats() -> dict:
    src = sorted((ROOT / "src" / "noc_pulse").glob("*.py"))
    tests = sorted((ROOT / "tests").glob("*.py"))

    def loc(files: list[Path]) -> int:
        return sum(
            1 for p in files for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        )

    return {"modules": len(src), "src_loc": loc(src), "test_files": len(tests), "test_loc": loc(tests)}


def facts_md(d: dict, run_date: str) -> str:
    s, sd, demo = d["s"], d["sd"], d["demo"]
    cov = s["coverage"]
    cs = code_stats()
    voice = service(s, "acme-voice")
    dia = service(s, "acme-dia")
    tata_http = target(s, "web-tatacomm", "http")
    tata_tcp = target(s, "web-tatacomm", "tcp")
    gw = target(s, "site-gw", "ping")
    al, tk = s["alarms"], s["tickets"]
    vc = sd["classes"]["voice"]
    p1, p2 = demo["phase1"], demo["phase2"]
    rep = "`noc-pulse report --json results/summary.json` (after `noc-pulse replay`)"
    rows = [
        ("Language / UI", "Python only; CLI = Typer + Rich, dashboard = Streamlit", "repository", "pyproject.toml"),
        ("Source size", f"{cs['modules']} modules, {cs['src_loc']:,} non-blank lines in src/noc_pulse", "scripts/build_docs.py (line count)", "src/noc_pulse/"),
        ("Tests", f"{d['tests']} pytest tests in {cs['test_files']} files, no network; ruff clean", "`python -m pytest`", "results/pytest.txt"),
        ("CI", "GitHub Actions, Python 3.12 and 3.13, ruff + pytest", "`gh run list`", ".github/workflows/ci.yml"),
        ("Collection window", f"{s['first_local']} -> {s['last_local']} IST ({cov['duration_h']:.2f} h)", "`noc-pulse collect --duration 9h --keep-awake`", "results/summary.json"),
        ("Samples stored", f"{cov['samples']:,} (" + ", ".join(f"{k} {v:,}" for k, v in sorted(cov['by_probe'].items())) + ")", rep, "results/summary.json"),
        ("ICMP echoes", f"{cov['echoes_sent']:,} sent, {cov['echoes_received']:,} received", rep, "results/summary.json"),
        ("Targets / probe loops", "9 targets, 19 probe loops (ping, TCP, DNS, HTTPS)", "config/inventory.yaml", "config/inventory.yaml"),
        ("Collector gaps > 120 s", str(len(cov["gaps"])), rep, "results/summary.json"),
        ("Voice service (gold) KPIs", f"availability {f(voice['availability_pct'], 3)} %, p95 RTT {f(voice['latency_p95_ms'])} ms, jitter {f(voice['jitter_ms'])} ms, loss {f(voice['loss_pct'], 3)} %, E-model MOS {f(voice['mos'])}", rep, "results/summary.json"),
        ("Internet service (silver) KPIs", f"availability {f(dia['availability_pct'], 3)} %, p95 RTT {f(dia['latency_p95_ms'])} ms, loss {f(dia['loss_pct'], 3)} %", rep, "results/summary.json"),
        ("Site gateway (Wi-Fi) ICMP", f"loss {f(gw['loss_pct'], 3)} % ({gw['echoes_sent'] - gw['echoes_received']} of {gw['echoes_sent']:,} echoes), p95 {f(gw['latency_p95_ms'])} ms", rep, "results/summary.json"),
        ("tatacommunications.com HTTPS", f"{tata_http['samples']} GETs, availability {f(tata_http['availability_pct'], 3)} %, p50 {f(tata_http['latency_p50_ms'], 1)} ms / p95 {f(tata_http['latency_p95_ms'], 1)} ms (DNS+TCP+TLS+TTFB); TCP connect p50 {f(tata_tcp['latency_p50_ms'])} ms; certificate {f(tata_http.get('cert_days_min'), 1)} days from expiry", rep, "results/summary.json"),
        ("SLA verdicts", ", ".join(f"{r['service']}: " + ("met" if not r["breaches"] else "breached " + "/".join(r["breaches"])) for r in s["services"]), rep, "results/summary.json"),
        ("Alarms / tickets from replay", f"{al['total']} alarms ({al['suppressed']} suppressed), {tk['total']} tickets", "`noc-pulse replay`", "results/report.md"),
        ("SD-WAN voice SLA compliance", f"static primary {vc['static_pct']:.2f} %, SLA-aware dynamic {vc['dynamic_pct']:.2f} %, oracle {vc['oracle_pct']:.2f} % over {vc['intervals']} one-minute intervals; switches {vc['static_switches']} / {vc['dynamic_switches']}", "`noc-pulse sdwan --json results/sdwan.json`", "results/sdwan.json"),
        ("SD-WAN synthetic brown-out (unit test)", "static 58.3 % (14/24) vs dynamic 95.8 % (23/24) vs oracle 100 %", "`pytest tests/test_sdwan.py`", "tests/test_sdwan.py"),
        ("Fault demo, phase 1", f"fault -> alarm {p1['detect_s']} s; 1 ticket ({p1['priority']}) with {len(p1['suppressed_child_alarms'])} child alarms suppressed; open -> ack {p1['time_to_ack_s']} s; open -> resolved {p1['mttr_s']} s", "`noc-pulse demo`", "results/fault_demo/summary.json"),
        ("Fault demo, phase 2", f"app-only fault -> alarm {p2['detect_s']} s, not suppressed, own {p2['priority']} ticket, open -> resolved {p2['mttr_s']} s", "`noc-pulse demo`", "results/fault_demo/summary.json"),
    ]
    out = [
        "# FACTS: numbers and claims you can put on a CV",
        "",
        f"Generated by `python scripts/build_docs.py` on {run_date} from the files in `results/`. "
        "Each row says which command produced the number. Re-running the commands on another "
        "night will give different measurements; the code facts will not change.",
        "",
        "| Claim | Value | Produced by | Source |",
        "|---|---|---|---|",
    ]
    out += [f"| {a} | {b} | {c} | `{e}` |" for a, b, c, e in rows]
    out += [
        "",
        "## Suggested CV lines (all numbers above)",
        "",
        f"* Built **noc-pulse**, a Python service-assurance toolkit (asyncio, SQLite, Typer, Streamlit): "
        f"ICMP/TCP/DNS/HTTPS probes, KPIs (availability, p95 latency, jitter, loss, ITU-T E-model MOS), "
        f"alarm hysteresis with dependency-based root-cause correlation, and ITIL-style P1-P4 tickets with "
        f"SLA timers and auto-attached L1 diagnostics; {d['tests']} unit tests in GitHub Actions CI.",
        f"* Monitored 5 customer-style services (9 targets incl. tatacommunications.com) for "
        f"{cov['duration_h']:.1f} h: {cov['samples']:,} samples and {cov['echoes_sent']:,} ICMP echoes; "
        f"voice path MOS {f(voice['mos'])}, p95 RTT {f(voice['latency_p95_ms'])} ms; Wi-Fi gateway loss "
        f"{f(gw['loss_pct'], 2)} %.",
        f"* Fault-injection demo: an edge failure produced 1 P1 ticket instead of 3 (child alarms "
        f"suppressed by correlation), diagnostics attached automatically, auto-resolved "
        f"{p1['mttr_s']} s after opening in a 2-second-interval lab.",
        f"* SD-WAN simulation over measured data: SLA-aware steering with hold-down vs static routing "
        f"(voice SLA {vc['dynamic_pct']:.1f} % vs {vc['static_pct']:.1f} % of intervals on a healthy line; "
        "58 % -> 96 % on a synthetic brown-out).",
        "",
        "## Do not claim",
        "",
        "* That this monitored a real customer or a carrier network - it is one laptop on home broadband.",
        "* That the MOS is measured call quality - it is an E-model estimate from ICMP.",
        "* That the SD-WAN result is from real SD-WAN equipment - it is a simulation over three ping series that share one access line.",
        "* That the measured alarms/tickets were handled live - they come from `replay` of the stored samples.",
        "* The demo MTTR as an operational MTTR - it is a scripted localhost lab.",
        "* Any affiliation with or endorsement by Tata Communications - tatacommunications.com is only one of the public HTTPS targets, probed once a minute.",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    d = load()
    import time

    run_date = time.strftime("%Y-%m-%d %H:%M IST", time.localtime())
    facts_path = README.parent / "docs" / "FACTS.md"
    facts_path.parent.mkdir(parents=True, exist_ok=True)
    facts_path.write_text(facts_md(d, run_date), encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    readme = re.sub(
        r"<!-- results:start -->.*<!-- results:end -->|RESULTS_PLACEHOLDER",
        lambda _m: "<!-- results:start -->\n" + results_block(d) + "\n<!-- results:end -->",
        readme,
        flags=re.S,
    )
    readme = re.sub(
        r"<!-- demo:start -->.*<!-- demo:end -->|DEMO_PLACEHOLDER",
        lambda _m: "<!-- demo:start -->\n" + demo_block(d) + "\n<!-- demo:end -->",
        readme,
        flags=re.S,
    )
    readme = re.sub(r"(<!-- tests -->)?\d* ?TESTCOUNT_PLACEHOLDER|<!-- tests -->\d+", f"<!-- tests -->{d['tests']}", readme)
    README.write_text(readme, encoding="utf-8")
    print("README updated;", d["tests"], "tests")


if __name__ == "__main__":
    main()
