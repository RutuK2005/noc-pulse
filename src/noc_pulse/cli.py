"""Typer CLI.

noc-pulse collect   run the probe collector (optionally with the live alarm/ticket engine)
noc-pulse probe     run every probe once and print the results
noc-pulse report    data coverage, per-target KPIs, per-service SLA compliance
noc-pulse replay    feed stored samples through the alarm/ticket engine
noc-pulse alarms    list alarms
noc-pulse tickets   list / show / ack / start / resolve / close tickets
noc-pulse sdwan     SD-WAN path-selection simulation over the measured data
noc-pulse diagnose  run L1 auto-diagnostics against one target now
noc-pulse demo      reproducible fault-injection demo on 127.0.0.1
noc-pulse export-sample   write an anonymised CSV sample of the raw data
noc-pulse dashboard launch the Streamlit dashboard
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .inventory import load_inventory
from .store import Store

app = typer.Typer(add_completion=False, help="noc-pulse: a small service-assurance / NOC toolkit")
tickets_app = typer.Typer(help="ITIL-style incident tickets", invoke_without_command=True)
app.add_typer(tickets_app, name="tickets")
console = Console()

DEFAULT_INV = Path("config/inventory.yaml")
DEFAULT_DB = Path("data/noc.db")
InvOpt = typer.Option(DEFAULT_INV, "--inventory", "-i", help="inventory YAML")
DbOpt = typer.Option(DEFAULT_DB, "--db", help="SQLite database")


@app.callback()
def main() -> None:
    """noc-pulse: probes, KPIs, alarm correlation, tickets and SD-WAN simulation."""


def _parse_duration(text: str | None) -> float | None:
    if not text:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


def _since(text: str | None) -> float | None:
    d = _parse_duration(text)
    return None if d is None else time.time() - d


def _f(x: object, nd: int = 2) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _open_store(db: Path) -> Store:
    if not db.exists():
        console.print(f"[red]database {db} not found - run `noc-pulse collect` first[/]")
        raise typer.Exit(1)
    return Store(db)


# ---------------------------------------------------------------------------
@app.command()
def collect(
    inventory: Path = InvOpt,
    db: Path = DbOpt,
    duration: str = typer.Option(None, help="e.g. 90s, 30m, 8h (default: run until Ctrl+C)"),
    keep_awake: bool = typer.Option(False, help="Windows: keep the PC from sleeping while running"),
    engine: bool = typer.Option(False, help="run the alarm/ticket engine live on every sample"),
    log_file: Path = typer.Option(None, help="also write logs to this file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the probe collector and store raw samples in SQLite."""
    from .collector import KeepAwake, run_collector

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    inv = load_inventory(inventory)
    store = Store(db)
    on_result = None
    eng = None
    if engine:
        from .engine import Engine

        eng = Engine(inv, store, diagnostics=True)
        on_result = eng.process_async

    async def go() -> None:
        await run_collector(inv, store, _parse_duration(duration), on_result=on_result)
        if eng is not None:
            await eng.drain()

    with KeepAwake(keep_awake):
        try:
            asyncio.run(go())
        except KeyboardInterrupt:
            console.print("[yellow]stopped[/]")
    store.close()


@app.command()
def probe(inventory: Path = InvOpt) -> None:
    """Run every probe in the inventory once and print the results (nothing is stored)."""
    from .inventory import resolve_host
    from .probes import run_probe

    inv = load_inventory(inventory)

    async def go():  # type: ignore[no-untyped-def]
        jobs = [
            run_probe(t.id, resolve_host(t.host), p.kind, p.params)
            for t in inv.targets.values()
            for p in t.probes
        ]
        return await asyncio.gather(*jobs)

    table = Table(title="one-shot probe results")
    for col in ("target", "probe", "ok", "latency ms", "loss %", "jitter ms", "status", "cert days", "error"):
        table.add_column(col)
    for r in asyncio.run(go()):
        table.add_row(
            r.target,
            r.probe,
            "[green]up[/]" if r.ok else "[red]DOWN[/]",
            _f(r.latency_ms, 1),
            _f(r.loss_pct, 0),
            _f(r.jitter_ms),
            _f(r.status),
            _f(r.cert_days, 1),
            r.error or "",
        )
    console.print(table)


@app.command()
def report(
    inventory: Path = InvOpt,
    db: Path = DbOpt,
    since: str = typer.Option(None, help="only the last N (e.g. 2h); default all data"),
    markdown: Path = typer.Option(None, help="also write a Markdown report to this file"),
) -> None:
    """Data coverage, per-target KPIs and per-service SLA compliance."""
    from .report import coverage, fmt_ts, render_markdown, service_kpis, target_kpis

    inv = load_inventory(inventory)
    store = _open_store(db)
    s = _since(since)
    cov = coverage(store)
    console.print(
        f"[bold]data[/]: {fmt_ts(cov['first'])} -> {fmt_ts(cov['last'])} "
        f"({cov['duration_h']:.2f} h), {cov['samples']} samples, "
        f"{cov['echoes_sent']} ICMP echoes, {len(cov['gaps'])} collector gaps > 120 s"
    )
    t = Table(title="per-target KPIs")
    for col in (
        "target",
        "probe",
        "n",
        "avail %",
        "p50 ms",
        "p95 ms",
        "jitter ms",
        "loss %",
        "MOS",
        "top error",
    ):
        t.add_column(col, justify="right" if col not in ("target", "probe", "top error") else "left")
    for r in target_kpis(store, since=s):
        avail = r["availability_pct"]
        colour = (
            "green" if avail is not None and avail >= 99.9 else "yellow" if avail and avail >= 99 else "red"
        )
        t.add_row(
            r["target"],
            r["probe"],
            str(r["samples"]),
            f"[{colour}]{_f(avail, 3)}[/]",
            _f(r["latency_p50_ms"]),
            _f(r["latency_p95_ms"]),
            _f(r["jitter_ms"]),
            _f(r["loss_pct"], 3),
            _f(r["mos"]),
            r["top_error"],
        )
    console.print(t)
    t = Table(title="customer services vs SLA")
    for col in ("service", "class", "kpi source", "avail %", "p95 ms", "jitter ms", "loss %", "MOS", "SLA"):
        t.add_column(col)
    for r in service_kpis(store, inv, since=s):
        verdict = "[green]met[/]" if not r["breaches"] else "[red]BREACH[/] " + ",".join(r["breaches"])
        t.add_row(
            r["service"],
            r["sla_class"],
            r["kpi_source"],
            _f(r["availability_pct"], 3),
            _f(r["latency_p95_ms"]),
            _f(r["jitter_ms"]),
            _f(r["loss_pct"], 3),
            _f(r["mos"]),
            verdict,
        )
    console.print(t)
    if markdown:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(store, inv), encoding="utf-8")
        console.print(f"wrote {markdown}")


@app.command()
def replay(inventory: Path = InvOpt, db: Path = DbOpt) -> None:
    """Re-run the alarm/correlation/ticket pipeline over all stored samples."""
    from .engine import replay as do_replay
    from .report import alarm_summary, ticket_summary

    inv = load_inventory(inventory)
    store = _open_store(db)
    t0 = time.perf_counter()
    do_replay(inv, store)
    al = alarm_summary(store)
    tk = ticket_summary(store, now=store.time_range()[1])
    console.print(
        f"replayed {store.time_range()[2]} samples in {time.perf_counter() - t0:.1f}s: "
        f"{al['total']} alarms ({al['suppressed']} suppressed by correlation, {al['flaps']} flap "
        f"re-raises), {tk['total']} tickets {tk['by_priority']}, MTTR "
        f"{_f(tk['mttr_s'] and tk['mttr_s'] / 60, 1)} min"
    )


@app.command()
def alarms(db: Path = DbOpt, active: bool = typer.Option(False, help="only active alarms")) -> None:
    """List alarms (from a live engine run or `replay`)."""
    from .report import fmt_ts

    store = _open_store(db)
    sql = "SELECT * FROM alarms" + (" WHERE state='active'" if active else "") + " ORDER BY raised_at"
    t = Table(title="alarms")
    for col in (
        "#",
        "raised",
        "cleared",
        "target/probe",
        "rule",
        "sev",
        "count",
        "flaps",
        "suppressed",
        "ticket",
    ):
        t.add_column(col)
    for a in store.rows(sql):
        t.add_row(
            str(a["id"]),
            fmt_ts(a["raised_at"]),
            fmt_ts(a["cleared_at"]),
            f"{a['target']}/{a['probe']}",
            a["rule"],
            a["severity"],
            str(a["occurrences"]),
            str(a["flap_count"]),
            f"root #{a['root_cause_id']}" if a["suppressed"] else "-",
            a["ticket_id"] or "-",
        )
    console.print(t)


# ---------------------------------------------------------------------------
@tickets_app.callback()
def tickets_list(
    ctx: typer.Context,
    db: Path = DbOpt,
    state: str = typer.Option(None, help="filter by state, e.g. New"),
) -> None:
    """List tickets with SLA timers (default action)."""
    if ctx.invoked_subcommand is not None:
        return
    from .engine import load_tickets
    from .report import fmt_ts
    from .tickets import mtta_s, mttr_s

    store = _open_store(db)
    tks = [t for t in load_tickets(store) if state is None or t.state.value == state]
    now = time.time()
    t = Table(title="incident tickets")
    for col in ("id", "pri", "state", "opened", "target", "title", "resp SLA", "resolve SLA", "TTR"):
        t.add_column(col)
    for k in tks:
        resp = "[red]BREACHED[/]" if k.response_breached(now) else "ok"
        res = "[red]BREACHED[/]" if k.resolve_breached(now) else "ok"
        ttr = k.time_to_resolve()
        t.add_row(
            k.id,
            k.priority,
            k.state.value,
            fmt_ts(k.opened_at),
            k.target,
            k.title[:50],
            resp,
            res,
            "-" if ttr is None else f"{ttr / 60:.1f} min",
        )
    console.print(t)
    mtta, mttr = mtta_s(tks), mttr_s(tks)
    mtta_txt = "-" if mtta is None else f"{mtta:.0f}s"
    mttr_txt = "-" if mttr is None else f"{mttr / 60:.1f} min"
    console.print(f"MTTA: {mtta_txt}   MTTR: {mttr_txt}")


@tickets_app.command("show")
def tickets_show(ticket_id: str, db: Path = DbOpt) -> None:
    """Show one ticket with its worklog and auto-diagnostics."""
    from .engine import load_tickets
    from .report import fmt_ts

    store = _open_store(db)
    t = next((x for x in load_tickets(store) if x.id == ticket_id), None)
    if t is None:
        console.print(f"[red]{ticket_id} not found[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{t.id}[/] {t.priority} [{t.state.value}] {t.title}")
    console.print(f"impact={t.impact} urgency={t.urgency} services={', '.join(t.services)}")
    console.print(f"response due {fmt_ts(t.response_due)}, resolve due {fmt_ts(t.resolve_due)}")
    if t.parent_id or t.children:
        console.print(f"parent={t.parent_id} children={t.children}")
    for ts, kind, text in t.worklog:
        console.print(f"  {fmt_ts(ts)} [{kind}] {text}")
    if t.diagnostics:
        console.rule("auto-diagnostics")
        console.print(t.diagnostics, markup=False)


def _ticket_action(ticket_id: str, action: str, note: str, db: Path) -> None:
    from .engine import load_tickets, save_ticket
    from .tickets import InvalidTransition, TicketState

    mapping = {
        "ack": TicketState.ACKNOWLEDGED,
        "start": TicketState.IN_PROGRESS,
        "resolve": TicketState.RESOLVED,
        "close": TicketState.CLOSED,
    }
    store = _open_store(db)
    t = next((x for x in load_tickets(store) if x.id == ticket_id), None)
    if t is None:
        console.print(f"[red]{ticket_id} not found[/]")
        raise typer.Exit(1)
    try:
        t.transition(mapping[action], time.time(), note)
    except InvalidTransition as exc:
        console.print(f"[red]rejected:[/] {exc}")
        raise typer.Exit(2) from exc
    save_ticket(store, t)
    console.print(f"{t.id} -> {t.state.value}")


@tickets_app.command("ack")
def tickets_ack(ticket_id: str, note: str = "", db: Path = DbOpt) -> None:
    """New -> Acknowledged."""
    _ticket_action(ticket_id, "ack", note, db)


@tickets_app.command("start")
def tickets_start(ticket_id: str, note: str = "", db: Path = DbOpt) -> None:
    """Acknowledged / Resolved -> In Progress."""
    _ticket_action(ticket_id, "start", note, db)


@tickets_app.command("resolve")
def tickets_resolve(
    ticket_id: str, note: str = typer.Option(..., help="resolution note"), db: Path = DbOpt
) -> None:
    """-> Resolved (a resolution note is required)."""
    _ticket_action(ticket_id, "resolve", note, db)


@tickets_app.command("close")
def tickets_close(ticket_id: str, note: str = "", db: Path = DbOpt) -> None:
    """Resolved -> Closed."""
    _ticket_action(ticket_id, "close", note, db)


# ---------------------------------------------------------------------------
@app.command()
def sdwan(
    inventory: Path = InvOpt,
    db: Path = DbOpt,
    json_out: Path = typer.Option(None, "--json", help="write the full result as JSON"),
) -> None:
    """Static primary vs SLA-aware dynamic path selection over the measured ping series."""
    from .report import fmt_ts, sdwan_report

    inv = load_inventory(inventory)
    store = _open_store(db)
    rep = sdwan_report(store, inv)
    console.print(
        f"SIMULATION over measured data: paths {rep['path_targets']}, primary {rep['primary']}, "
        f"{rep['intervals']} intervals x {rep['bucket_s']:.0f}s "
        f"({fmt_ts(rep['start'])} -> {fmt_ts(rep['end'])}), hold-down {rep['hold_down']}"
    )
    t = Table(title="% of intervals in which the chosen path met the application SLA")
    for col in ("app", "static primary", "dynamic (SLA-aware)", "oracle", "switches s/d", "per path"):
        t.add_column(col)
    for name, c in rep["classes"].items():
        t.add_row(
            name,
            f"{c['static_pct']:.2f}% ({c['static_met']}/{c['intervals']})",
            f"{c['dynamic_pct']:.2f}% ({c['dynamic_met']}/{c['intervals']})",
            f"{c['oracle_pct']:.2f}%",
            f"{c['static_switches']}/{c['dynamic_switches']}",
            ", ".join(f"{p} {v:.2f}%" for p, v in c["per_path_pct"].items()),
        )
    console.print(t)
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
        console.print(f"wrote {json_out}")


@app.command()
def diagnose(
    target: str,
    inventory: Path = InvOpt,
    trace: bool = typer.Option(True, help="include traceroute (slower)"),
) -> None:
    """Run the L1 auto-diagnostics for one inventory target right now."""
    from .diagnostics import run_diagnostics
    from .inventory import resolve_host

    inv = load_inventory(inventory)
    if target not in inv.targets:
        console.print(f"[red]unknown target {target}; known: {', '.join(inv.targets)}[/]")
        raise typer.Exit(1)
    t = inv.targets[target]
    text = asyncio.run(run_diagnostics(t, resolve_host(t.host), t.probes[0].kind, trace))
    console.print(text, markup=False)


@app.command()
def demo(
    inventory: Path = typer.Option(Path("config/demo_inventory.yaml"), "--inventory", "-i"),
    db: Path = typer.Option(Path("data/demo.db"), "--db"),
    out: Path = typer.Option(Path("results/fault_demo"), "--out"),
) -> None:
    """Fault-injection demo: alarm -> correlation -> ticket -> diagnostics -> resolve -> MTTR."""
    from .faultdemo import run_demo

    logging.basicConfig(level=logging.WARNING)
    summary = asyncio.run(run_demo(inventory, db, out, echo=lambda s: console.print(s, markup=False)))
    console.rule("summary")
    console.print_json(json.dumps(summary))
    console.print(f"outputs written to {out}/")


@app.command("export-sample")
def export_sample(
    db: Path = DbOpt,
    out: Path = typer.Option(Path("results/sample_raw.csv"), "--out"),
    hours: float = typer.Option(1.0, help="export the last N hours"),
) -> None:
    """Write an anonymised CSV of the raw samples (target ids only, no IPs, errors trimmed)."""
    import re

    store = _open_store(db)
    _, last, _ = store.time_range()
    if last is None:
        raise typer.Exit(1)
    ip_re = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ts_utc",
                "target",
                "probe",
                "ok",
                "latency_ms",
                "sent",
                "received",
                "rtts_ms",
                "http_status",
                "cert_days",
                "error",
            ]
        )
        for r in store.iter_samples(since=last - hours * 3600):
            w.writerow(
                [
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r.ts)),
                    r.target,
                    r.probe,
                    int(r.ok),
                    r.latency_ms,
                    r.sent,
                    r.received,
                    " ".join(f"{x:.3f}" for x in (r.rtts or [])),
                    r.status,
                    r.cert_days,
                    ip_re.sub("x.x.x.x", r.error or ""),
                ]
            )
            n += 1
    console.print(f"wrote {n} rows to {out}")


@app.command()
def dashboard(db: Path = DbOpt, inventory: Path = InvOpt, port: int = 8501) -> None:
    """Launch the Streamlit dashboard (needs `pip install .[dashboard]`)."""
    script = Path(__file__).with_name("dashboard.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.port",
        str(port),
        "--",
        "--db",
        str(db),
        "--inventory",
        str(inventory),
    ]
    raise typer.Exit(subprocess.call(cmd))


if __name__ == "__main__":  # pragma: no cover
    app()
