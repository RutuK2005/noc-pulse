"""The pipeline that turns samples into alarms and tickets.

``Engine.process(sample)`` is used in two ways with exactly the same code:

* live: the collector calls it for every new sample (``collect --engine``),
  and auto-diagnostics run in the background when a ticket opens;
* replay: ``noc-pulse replay`` feeds stored samples in time order (the clock
  is the sample timestamp), which is how the overnight data was processed.
  Diagnostics cannot be run for the past, so replay skips them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from .alarms import Alarm, AlarmEngine, AlarmEvent
from .inventory import Inventory, resolve_host
from .probes import ProbeResult
from .store import Store
from .tickets import Ticket, TicketManager, TicketState

log = logging.getLogger("noc_pulse.engine")


class Engine:
    def __init__(
        self,
        inv: Inventory,
        store: Store | None = None,
        diagnostics: bool = False,
        trace: bool = True,
        auto_close_s: float = 3600.0,
        on_event: Callable[[float, str], None] | None = None,
    ) -> None:
        self.inv = inv
        self.store = store
        self.alarms = AlarmEngine(inv)
        start_seq = 1
        if store is not None:  # continue numbering after anything already in the database
            max_alarm = store.conn.execute("SELECT MAX(id) FROM alarms").fetchone()[0]
            self.alarms._next_id = int(max_alarm or 0) + 1
            ids = [r[0] for r in store.conn.execute("SELECT id FROM tickets")]
            nums = [int(x[3:]) for x in ids if x.startswith("INC") and x[3:].isdigit()]
            start_seq = max(nums, default=0) + 1
        self.tickets = TicketManager(inv.ticketing, start_seq=start_seq)
        self.diagnostics = diagnostics
        self.trace = trace
        self.auto_close_s = auto_close_s
        self.on_event = on_event
        self.timeline: list[tuple[float, str]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._hosts: dict[str, str] = {}
        # alarms waiting out the correlation window before a ticket is opened
        self.correlation_window_s = inv.correlation_window_s
        self.pending: dict[int, float] = {}

    # ------------------------------------------------------------------
    def _note(self, ts: float, text: str) -> None:
        self.timeline.append((ts, text))
        log.info(text)
        if self.on_event:
            self.on_event(ts, text)

    def _host(self, target: str) -> str:
        if target not in self._hosts:
            self._hosts[target] = resolve_host(self.inv.targets[target].host)
        return self._hosts[target]

    def _save_alarm(self, a: Alarm) -> None:
        if self.store is not None:
            self.store.upsert("alarms", a.to_row())

    def _save_ticket(self, t: Ticket) -> None:
        if self.store is not None:
            save_ticket(self.store, t)

    def _sync(self, t: Ticket | None) -> Ticket | None:
        """Reload a ticket from the database before changing it.

        An operator may have acknowledged / resolved / closed it with the CLI while
        the engine was running; without this the engine would overwrite that change.
        """
        if t is None or self.store is None:
            return t
        rows = self.store.rows("SELECT * FROM tickets WHERE id = ?", (t.id,))
        if rows:
            events = [
                (r["ts"], r["kind"], r["text"])
                for r in self.store.rows(
                    "SELECT ts, kind, text FROM ticket_events WHERE ticket_id = ? ORDER BY id", (t.id,)
                )
            ]
            t.__dict__.update(Ticket.from_row(rows[0], events).__dict__)
        return t

    # ------------------------------------------------------------------
    def process(self, result: ProbeResult) -> list[AlarmEvent]:
        events = self.alarms.evaluate(result)
        for ev in events:
            self._handle(ev)
        self._flush_pending(result.ts)
        self._auto_close(result.ts)
        return events

    def _flush_pending(self, now: float) -> None:
        """Open tickets for alarms that survived the correlation window un-suppressed."""
        for alarm_id, due in list(self.pending.items()):
            a = self.alarms.alarms[alarm_id]
            if a.state != "active":
                del self.pending[alarm_id]
                self._note(now, f"alarm #{a.id} cleared inside the correlation window: no ticket")
            elif a.suppressed:
                del self.pending[alarm_id]
            elif now >= due:
                del self.pending[alarm_id]
                self._ticket_for(a, now, "raised")
                self._save_alarm(a)

    async def process_async(self, result: ProbeResult) -> None:
        self.process(result)

    async def drain(self) -> None:
        """Wait for background diagnostics to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------
    def _handle(self, ev: AlarmEvent) -> None:
        a, ts = ev.alarm, ev.ts
        tag = f"alarm #{a.id} {a.target}/{a.probe}/{a.rule} [{a.severity}]"
        if ev.kind in ("raised", "reraised", "unsuppressed"):
            if a.suppressed:
                root = self.alarms.alarms[a.root_cause_id] if a.root_cause_id else None
                self._note(
                    ts,
                    f"{ev.kind.upper()} {tag} SUPPRESSED -> root cause alarm #{root.id}"
                    f" ({root.target}/{root.rule})"
                    if root
                    else f"{ev.kind} {tag} suppressed",
                )
                if root and root.ticket_id in self.tickets.tickets:
                    rt = self._sync(self.tickets.tickets[root.ticket_id])
                    rt.log(ts, "correlation", f"child alarm #{a.id} suppressed: {a.message}")
                    self._save_ticket(rt)
            else:
                self._note(ts, f"{ev.kind.upper()} {tag}: {a.message}")
                has_ticket = a.ticket_id in self.tickets.tickets
                if self.correlation_window_s > 0 and not has_ticket:
                    self.pending[a.id] = ts + self.correlation_window_s
                else:
                    self._ticket_for(a, ts, ev.kind)
        elif ev.kind == "correlated":
            root = self.alarms.alarms[ev.info["root"]]
            self._note(ts, f"CORRELATED {tag} under root cause alarm #{root.id} ({root.target})")
            parent = self._sync(self.tickets.tickets.get(root.ticket_id or ""))
            child = self._sync(self.tickets.tickets.get(a.ticket_id or ""))
            if parent and child and child.id != parent.id:
                self.tickets.link_child(parent, child, ts)
                self._save_ticket(parent)
                self._save_ticket(child)
        elif ev.kind == "cleared":
            self._note(ts, f"CLEARED {tag} after {ts - a.raised_at:.0f}s")
            t = self._sync(self.tickets.tickets.get(a.ticket_id or ""))
            if t and t.state not in (TicketState.RESOLVED, TicketState.CLOSED):
                self._resolve(t, ts, "auto-resolved: alarm cleared, service restored")
            elif a.suppressed and a.root_cause_id:
                root = self.alarms.alarms.get(a.root_cause_id)
                rt = self._sync(self.tickets.tickets.get(root.ticket_id or "")) if root else None
                if rt:
                    rt.log(ts, "correlation", f"child alarm #{a.id} cleared")
                    self._save_ticket(rt)
        self._save_alarm(a)

    def _ticket_for(self, a: Alarm, ts: float, kind: str) -> None:
        existing = self._sync(self.tickets.tickets.get(a.ticket_id or ""))
        if existing and existing.state is not TicketState.CLOSED:
            if existing.state is TicketState.RESOLVED:
                existing.transition(TicketState.IN_PROGRESS, ts, "re-opened: alarm re-raised (flap)")
            else:
                existing.log(ts, "alarm", f"alarm #{a.id} {kind} again (flap_count={a.flap_count})")
            self._save_ticket(existing)
            return
        if a.severity not in self.inv.ticketing.get("open_for_severities", ["critical", "major"]):
            return
        name = self.inv.targets[a.target].name if a.target in self.inv.targets else a.target
        what = a.rule.replace("_", " ")
        title = f"[{a.severity.upper()}] {name}: {what} ({a.probe})"
        services = (
            [s.id for s in self.inv.impacted_services(a.target)] if (a.target in self.inv.targets) else []
        )
        impact = self.inv.impact_of(a.target) if a.target in self.inv.targets else "low"
        t = self.tickets.open(title, a.target, impact, a.severity, ts, services, alarm_id=a.id)
        t.log(ts, "alarm", a.message)
        a.ticket_id = t.id
        self._note(
            ts,
            f"TICKET {t.id} opened {t.priority} for alarm #{a.id}; "
            f"impacted services: {', '.join(services) or '-'}",
        )
        # late parent inside the correlation window: link child incidents opened earlier
        for child_alarm in list(self.alarms.active.values()):
            if child_alarm.root_cause_id == a.id and child_alarm.ticket_id in self.tickets.tickets:
                child = self._sync(self.tickets.tickets[child_alarm.ticket_id])
                if child is not None and child.id != t.id and child.parent_id is None:
                    self.tickets.link_child(t, child, ts)
                    self._save_ticket(child)
        self._save_ticket(t)
        if self.diagnostics and a.target in self.inv.targets:
            self._schedule_diagnostics(t, a)
        else:
            t.diagnostics = "(auto-diagnostics not run: replay of historical samples)"
            self._save_ticket(t)

    def _schedule_diagnostics(self, t: Ticket, a: Alarm) -> None:
        target = self.inv.targets[a.target]

        async def job() -> None:
            from .diagnostics import run_diagnostics

            try:
                text = await run_diagnostics(target, self._host(a.target), a.probe, self.trace)
            except Exception as exc:  # noqa: BLE001
                text = f"(diagnostics failed: {exc!r})"
            self._sync(t)
            t.diagnostics = text
            t.log(time.time(), "diagnostics", text.splitlines()[-1])
            self._note(time.time(), f"DIAGNOSTICS attached to {t.id}: {text.splitlines()[-1]}")
            self._save_ticket(t)

        try:
            task = asyncio.get_running_loop().create_task(job())
        except RuntimeError:  # no loop (sync caller): run inline
            asyncio.run(job())
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _resolve(self, t: Ticket, ts: float, note: str) -> None:
        self._sync(t)
        if t.state in (TicketState.RESOLVED, TicketState.CLOSED):
            return
        t.transition(TicketState.RESOLVED, ts, note)
        self._note(ts, f"TICKET {t.id} resolved after {ts - t.opened_at:.0f}s ({note})")
        self._save_ticket(t)
        for cid in t.children:
            child = self.tickets.tickets.get(cid)
            if child:
                self._resolve(child, ts, f"resolved with parent incident {t.id}")

    def _auto_close(self, now: float) -> None:
        for t in self.tickets.tickets.values():
            if (
                t.state is TicketState.RESOLVED
                and t.resolved_at is not None
                and (now - t.resolved_at >= self.auto_close_s)
            ):
                self._sync(t)
                if t.state is TicketState.RESOLVED:
                    t.transition(TicketState.CLOSED, now, "auto-closed: no recurrence")
                    self._save_ticket(t)

    # ------------------------------------------------------------------
    def operator_action(self, ticket_id: str, action: str, ts: float, note: str = "") -> Ticket:
        mapping = {
            "ack": TicketState.ACKNOWLEDGED,
            "start": TicketState.IN_PROGRESS,
            "resolve": TicketState.RESOLVED,
            "close": TicketState.CLOSED,
        }
        t = self._sync(self.tickets.tickets[ticket_id])
        t.transition(mapping[action], ts, note)
        self._note(ts, f"OPERATOR {action} {t.id}" + (f": {note}" if note else ""))
        self._save_ticket(t)
        return t


# ---------------------------------------------------------------------------
# helpers for the CLI / dashboard (work on persisted rows)
# ---------------------------------------------------------------------------
def load_tickets(store: Store) -> list[Ticket]:
    events: dict[str, list[tuple[float, str, str]]] = {}
    for r in store.rows("SELECT ticket_id, ts, kind, text FROM ticket_events ORDER BY id"):
        events.setdefault(r["ticket_id"], []).append((r["ts"], r["kind"], r["text"]))
    return [
        Ticket.from_row(r, events.get(r["id"]))
        for r in store.rows("SELECT * FROM tickets ORDER BY opened_at")
    ]


def save_ticket(store: Store, t: Ticket) -> None:
    store.upsert("tickets", t.to_row())
    store.conn.execute("DELETE FROM ticket_events WHERE ticket_id = ?", (t.id,))
    store.conn.executemany(
        "INSERT INTO ticket_events (ticket_id, ts, kind, text) VALUES (?, ?, ?, ?)",
        [(t.id, ts, kind, text) for ts, kind, text in t.worklog],
    )
    store.conn.commit()


def replay(inv: Inventory, store: Store, since: float | None = None, until: float | None = None) -> Engine:
    """Re-run the alarm/ticket pipeline over stored samples (clears old alarms/tickets)."""
    store.clear_engine_state()
    eng = Engine(inv, store, diagnostics=False)
    for r in store.samples(since=since, until=until):
        if r.target in inv.targets:
            eng.process(r)
    return eng
