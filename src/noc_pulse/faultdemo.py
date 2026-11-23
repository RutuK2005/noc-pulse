"""Reproducible fault-injection demo (``noc-pulse demo``), entirely on 127.0.0.1.

Scenario
--------
Phase 0  start three local listeners: edge router (TCP 18081), web app (HTTP 18080),
         API (TCP 18082); app and API depend on the edge in the inventory.
Phase 1  "edge router failure": stop all three listeners.
         Expect: ONE root-cause alarm on the edge -> P1 ticket with auto-diagnostics;
         app/API alarms are suppressed and linked to the root cause (no extra tickets).
         A scripted L1 operator acknowledges and starts work; the listeners are
         restored; the edge alarm clears -> ticket auto-resolves -> operator closes it.
Phase 2  "application-only failure": stop only the web app while the edge is fine.
         Expect: the app alarm is NOT suppressed (its parent is healthy) and gets its own
         ticket; restore -> auto-resolve.

Output: a timeline and the tickets (with worklogs and diagnostics) written to
``results/fault_demo/``. Times depend on the probe interval (2 s) and the
hysteresis (raise after 3 bad, clear after 2 good samples).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .collector import run_collector
from .engine import Engine
from .inventory import load_inventory
from .store import Store
from .tickets import Ticket, TicketState

EDGE_PORT, APP_PORT, API_PORT = 18081, 18080, 18082


class LabServers:
    def __init__(self) -> None:
        self.servers: dict[str, asyncio.base_events.Server] = {}

    @staticmethod
    async def _tcp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    @staticmethod
    async def _http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            body = b'{"status":"ok"}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def start(self, name: str) -> None:
        port = {"edge": EDGE_PORT, "app": APP_PORT, "api": API_PORT}[name]
        handler = self._http_handler if name == "app" else self._tcp_handler
        self.servers[name] = await asyncio.start_server(handler, "127.0.0.1", port)

    async def stop(self, name: str) -> None:
        srv = self.servers.pop(name, None)
        if srv is not None:
            srv.close()
            await srv.wait_closed()

    async def stop_all(self) -> None:
        for name in list(self.servers):
            await self.stop(name)


def _ticket_dict(t: Ticket, t0: float) -> dict[str, Any]:
    def rel(x: float | None) -> float | None:
        return None if x is None else round(x - t0, 1)

    return {
        "id": t.id,
        "title": t.title,
        "priority": t.priority,
        "impact": t.impact,
        "urgency": t.urgency,
        "state": t.state.value,
        "services": t.services,
        "parent_id": t.parent_id,
        "children": t.children,
        "opened_at_s": rel(t.opened_at),
        "acknowledged_at_s": rel(t.acknowledged_at),
        "in_progress_at_s": rel(t.in_progress_at),
        "resolved_at_s": rel(t.resolved_at),
        "closed_at_s": rel(t.closed_at),
        "time_to_ack_s": None if t.time_to_ack() is None else round(t.time_to_ack(), 1),
        "time_to_resolve_s": None if t.time_to_resolve() is None else round(t.time_to_resolve(), 1),
        "response_breached": t.response_breached(t.closed_at or time.time()),
        "resolve_breached": t.resolve_breached(t.closed_at or time.time()),
        "resolution": t.resolution,
        "worklog": [{"t_s": rel(ts), "kind": k, "text": x} for ts, k, x in t.worklog],
        "diagnostics": t.diagnostics,
    }


async def _wait_for(pred, timeout: float, step: float = 0.25) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


async def run_demo(inventory_path: Path, db_path: Path, out_dir: Path, echo=print) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    inv = load_inventory(inventory_path)
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    store = Store(db_path)
    t0 = time.time()
    timeline: list[tuple[float, str]] = []  # (seconds since start, text)

    def note(text: str, ts: float | None = None) -> None:
        rel = (time.time() if ts is None else ts) - t0
        timeline.append((rel, text))
        echo(f"[t+{rel:6.1f}s] {text}")

    eng = Engine(inv, store, diagnostics=True, trace=True, on_event=lambda ts, text: note(text, ts))
    lab = LabServers()
    stop = asyncio.Event()

    async def scenario() -> dict[str, Any]:
        marks: dict[str, float] = {}
        for name in ("edge", "app", "api"):
            await lab.start(name)
        note("lab started: edge TCP 18081, app HTTP 18080, api TCP 18082 (all healthy)")
        await asyncio.sleep(10)

        # ---- phase 1: edge failure takes down everything behind it ----------
        marks["fault1"] = time.time()
        note("FAULT INJECTED #1: edge router down (all three listeners stopped)")
        await lab.stop_all()

        def edge_ticket() -> Ticket | None:
            return next((t for t in eng.tickets.tickets.values() if t.target == "lab-edge"), None)

        await _wait_for(lambda: edge_ticket() is not None, 30)
        t1 = edge_ticket()
        assert t1 is not None, "edge ticket was not opened"
        await _wait_for(lambda: sum(a.suppressed for a in eng.alarms.alarms.values()) >= 2, 20)
        await asyncio.sleep(3)
        eng.operator_action(t1.id, "ack", time.time(), "L1: acknowledged, edge unreachable, checking")
        await asyncio.sleep(3)
        eng.operator_action(t1.id, "start", time.time(), "L1: field/carrier check requested")
        await _wait_for(lambda: t1.diagnostics is not None and "triage" in (t1.diagnostics or ""), 60)
        await asyncio.sleep(4)
        marks["fix1"] = time.time()
        note("FIX APPLIED #1: edge router restored (listeners started again)")
        for name in ("edge", "app", "api"):
            await lab.start(name)
        await _wait_for(lambda: t1.state is TicketState.RESOLVED, 30)
        marks["resolved1"] = time.time()
        await asyncio.sleep(4)
        eng.operator_action(t1.id, "close", time.time(), "customer confirmed service restored")

        # ---- phase 2: application-only failure --------------------------------
        await asyncio.sleep(6)
        marks["fault2"] = time.time()
        note("FAULT INJECTED #2: web app down only (edge healthy)")
        await lab.stop("app")

        def app_ticket() -> Ticket | None:
            return next((t for t in eng.tickets.tickets.values() if t.target == "lab-app"), None)

        await _wait_for(lambda: app_ticket() is not None, 30)
        t2 = app_ticket()
        assert t2 is not None, "app ticket was not opened"
        await _wait_for(lambda: t2.diagnostics is not None and "triage" in (t2.diagnostics or ""), 60)
        await asyncio.sleep(3)
        marks["fix2"] = time.time()
        note("FIX APPLIED #2: web app restarted")
        await lab.start("app")
        await _wait_for(lambda: t2.state is TicketState.RESOLVED, 30)
        marks["resolved2"] = time.time()
        await asyncio.sleep(2)
        stop.set()
        return marks

    collector = asyncio.create_task(
        run_collector(inv, store, on_result=eng.process_async, stop=stop, jitter_start=False)
    )
    try:
        marks = await scenario()
    finally:
        stop.set()
        await collector
        await eng.drain()
        await lab.stop_all()

    tickets = sorted(eng.tickets.tickets.values(), key=lambda t: t.opened_at)
    alarms = sorted(eng.alarms.alarms.values(), key=lambda a: a.id)
    edge_alarm = next(a for a in alarms if a.target == "lab-edge")
    app2 = [a for a in alarms if a.target == "lab-app" and not a.suppressed]
    t_edge = next(t for t in tickets if t.target == "lab-edge")
    t_app = next(t for t in tickets if t.target == "lab-app")
    summary = {
        "phase1": {
            "detect_s": round(edge_alarm.raised_at - marks["fault1"], 1),
            "ticket": t_edge.id,
            "priority": t_edge.priority,
            "suppressed_child_alarms": [
                f"#{a.id} {a.target}" for a in alarms if a.root_cause_id == edge_alarm.id
            ],
            "tickets_opened_in_phase": sum(1 for t in tickets if t.opened_at < marks["fault2"]),
            "time_to_ack_s": round(t_edge.time_to_ack() or 0, 1),
            "mttr_s": round(t_edge.time_to_resolve() or 0, 1),
            "restore_after_fix_s": round((t_edge.resolved_at or 0) - marks["fix1"], 1),
        },
        "phase2": {
            "detect_s": round(app2[0].raised_at - marks["fault2"], 1) if app2 else None,
            "ticket": t_app.id,
            "priority": t_app.priority,
            "suppressed": bool(app2 and app2[0].suppressed),
            "mttr_s": round(t_app.time_to_resolve() or 0, 1),
        },
        "tickets_total": len(tickets),
        "alarms_total": len(alarms),
        "alarms_suppressed": sum(a.suppressed for a in alarms),
        "samples": store.time_range()[2],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tickets.json").write_text(
        json.dumps([_ticket_dict(t, t0) for t in tickets], indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = [
        "# Fault-injection demo output",
        "",
        f"Run on {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t0))} (local time) with "
        "`noc-pulse demo`. Probe interval 2 s, raise after 3 bad / clear after 2 good samples. "
        "All targets are local listeners on 127.0.0.1.",
        "",
        "## Timeline (seconds since start)",
        "",
        "```",
        *[f"t+{rel:6.1f}s  {text}" for rel, text in timeline],  # processing order
        "```",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(summary, indent=2),
        "```",
        "",
        "## Tickets",
        "",
    ]
    for t in tickets:
        md += [f"### {t.id} {t.priority} {t.state.value}: {t.title}", ""]
        md += [f"- services: {', '.join(t.services)}", f"- resolution: {t.resolution}", ""]
        md += ["Worklog:", "", "```"]
        md += [f"t+{ts - t0:6.1f}s [{k}] {x}" for ts, k, x in t.worklog]
        md += ["```", "", "Auto-diagnostics attached on open:", "", "```", t.diagnostics or "-", "```", ""]
    (out_dir / "README.md").write_text("\n".join(md), encoding="utf-8")
    store.close()
    return summary
