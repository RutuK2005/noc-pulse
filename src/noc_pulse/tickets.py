"""ITIL-style incident tickets: priority matrix, lifecycle state machine, SLA timers, MTTA/MTTR.

Lifecycle (anything else raises :class:`InvalidTransition`)::

    New --> Acknowledged --> In Progress --> Resolved --> Closed
     |           |                              |
     +-----------+--> Resolved (auto-clear)     +--> In Progress (re-open)

* Priority = matrix[impact][urgency] -> P1..P4 (impact from the affected
  services, urgency from the alarm severity).
* SLA timers per priority: *response* (time to acknowledge) and *resolve*
  (time to restore service). Breach flags are computed against "now" for
  open tickets and against the actual timestamps for finished ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .kpi import mean


class TicketState(StrEnum):
    NEW = "New"
    ACKNOWLEDGED = "Acknowledged"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


S = TicketState
TRANSITIONS: dict[TicketState, set[TicketState]] = {
    S.NEW: {S.ACKNOWLEDGED, S.RESOLVED},
    S.ACKNOWLEDGED: {S.IN_PROGRESS, S.RESOLVED},
    S.IN_PROGRESS: {S.RESOLVED},
    S.RESOLVED: {S.CLOSED, S.IN_PROGRESS},
    S.CLOSED: set(),
}


class InvalidTransition(ValueError):
    pass


def priority_for(impact: str, urgency: str, matrix: dict[str, dict[str, str]]) -> str:
    try:
        return matrix[impact][urgency]
    except KeyError as exc:
        raise ValueError(f"no priority for impact={impact!r} urgency={urgency!r}") from exc


@dataclass
class Ticket:
    id: str
    title: str
    target: str
    impact: str
    urgency: str
    priority: str
    opened_at: float
    response_due: float
    resolve_due: float
    alarm_id: int | None = None
    state: TicketState = TicketState.NEW
    acknowledged_at: float | None = None
    in_progress_at: float | None = None
    resolved_at: float | None = None
    closed_at: float | None = None
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    diagnostics: str | None = None
    resolution: str | None = None
    worklog: list[tuple[float, str, str]] = field(default_factory=list)

    # ---- lifecycle -------------------------------------------------------
    def transition(self, new: TicketState | str, ts: float, note: str = "") -> None:
        new = TicketState(new)
        if new not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.id}: {self.state.value} -> {new.value} is not allowed")
        old = self.state
        self.state = new
        if new is S.ACKNOWLEDGED:
            self.acknowledged_at = ts
        elif new is S.IN_PROGRESS:
            if old is S.RESOLVED:  # re-open
                self.resolved_at = None
                self.resolution = None
            self.in_progress_at = self.in_progress_at or ts
        elif new is S.RESOLVED:
            self.resolved_at = ts
            if note:
                self.resolution = note
        elif new is S.CLOSED:
            self.closed_at = ts
        self.log(ts, "state", f"{old.value} -> {new.value}" + (f": {note}" if note else ""))

    def log(self, ts: float, kind: str, text: str) -> None:
        self.worklog.append((ts, kind, text))

    # ---- SLA timers ------------------------------------------------------
    @property
    def first_response_at(self) -> float | None:
        """Acknowledgement, or resolution if the alarm cleared before anyone acked."""
        candidates = [t for t in (self.acknowledged_at, self.resolved_at) if t is not None]
        return min(candidates) if candidates else None

    def response_breached(self, now: float) -> bool:
        t = self.first_response_at
        return (t if t is not None else now) > self.response_due

    def resolve_breached(self, now: float) -> bool:
        t = self.resolved_at
        return (t if t is not None else now) > self.resolve_due

    def time_to_ack(self) -> float | None:
        return None if self.acknowledged_at is None else self.acknowledged_at - self.opened_at

    def time_to_resolve(self) -> float | None:
        return None if self.resolved_at is None else self.resolved_at - self.opened_at

    def remaining(self, now: float) -> dict[str, float]:
        return {"response_s": self.response_due - now, "resolve_s": self.resolve_due - now}

    # ---- persistence -----------------------------------------------------
    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "alarm_id": self.alarm_id,
            "target": self.target,
            "title": self.title,
            "impact": self.impact,
            "urgency": self.urgency,
            "priority": self.priority,
            "state": self.state.value,
            "opened_at": self.opened_at,
            "acknowledged_at": self.acknowledged_at,
            "in_progress_at": self.in_progress_at,
            "resolved_at": self.resolved_at,
            "closed_at": self.closed_at,
            "response_due": self.response_due,
            "resolve_due": self.resolve_due,
            "parent_id": self.parent_id,
            "services": json.dumps(self.services),
            "diagnostics": self.diagnostics,
            "resolution": self.resolution,
            "data": json.dumps({"children": self.children}),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any], worklog: list[tuple[float, str, str]] | None = None) -> Ticket:
        data = json.loads(row.get("data") or "{}")
        return cls(
            id=row["id"],
            title=row["title"],
            target=row["target"],
            impact=row["impact"],
            urgency=row["urgency"],
            priority=row["priority"],
            opened_at=row["opened_at"],
            response_due=row["response_due"],
            resolve_due=row["resolve_due"],
            alarm_id=row.get("alarm_id"),
            state=TicketState(row["state"]),
            acknowledged_at=row.get("acknowledged_at"),
            in_progress_at=row.get("in_progress_at"),
            resolved_at=row.get("resolved_at"),
            closed_at=row.get("closed_at"),
            parent_id=row.get("parent_id"),
            children=list(data.get("children", [])),
            services=json.loads(row.get("services") or "[]"),
            diagnostics=row.get("diagnostics"),
            resolution=row.get("resolution"),
            worklog=list(worklog or []),
        )


class TicketManager:
    """Creates tickets with the right priority and SLA due times."""

    def __init__(self, policy: dict[str, Any], start_seq: int = 1) -> None:
        self.policy = policy
        self.tickets: dict[str, Ticket] = {}
        self._seq = start_seq

    def next_id(self) -> str:
        tid = f"INC{self._seq:06d}"
        self._seq += 1
        return tid

    def open(
        self,
        title: str,
        target: str,
        impact: str,
        severity: str,
        ts: float,
        services: list[str] | None = None,
        alarm_id: int | None = None,
    ) -> Ticket:
        urgency = self.policy["urgency_from_severity"].get(severity, "low")
        priority = priority_for(impact, urgency, self.policy["priority_matrix"])
        sla = self.policy["sla_minutes"][priority]
        t = Ticket(
            id=self.next_id(),
            title=title,
            target=target,
            impact=impact,
            urgency=urgency,
            priority=priority,
            opened_at=ts,
            response_due=ts + 60.0 * sla["response"],
            resolve_due=ts + 60.0 * sla["resolve"],
            alarm_id=alarm_id,
            services=list(services or []),
        )
        t.log(ts, "opened", f"{priority} (impact={impact}, urgency={urgency}): {title}")
        self.tickets[t.id] = t
        return t

    def link_child(self, parent: Ticket, child: Ticket, ts: float) -> None:
        child.parent_id = parent.id
        if child.id not in parent.children:
            parent.children.append(child.id)
        parent.log(ts, "correlation", f"child incident {child.id} linked ({child.title})")
        child.log(ts, "correlation", f"linked to parent incident {parent.id}")


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def mtta_s(tickets: list[Ticket]) -> float | None:
    """Mean time to acknowledge over tickets that were acknowledged."""
    return mean(t for t in (x.time_to_ack() for x in tickets) if t is not None)


def mttr_s(tickets: list[Ticket]) -> float | None:
    """Mean time to resolve (open -> Resolved) over resolved tickets."""
    return mean(t for t in (x.time_to_resolve() for x in tickets) if t is not None)


def sla_report(tickets: list[Ticket], now: float) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in tickets:
        row = out.setdefault(
            t.priority,
            {"tickets": 0, "resolved": 0, "response_breaches": 0, "resolve_breaches": 0},
        )
        row["tickets"] += 1
        row["resolved"] += t.resolved_at is not None
        row["response_breaches"] += t.response_breached(now)
        row["resolve_breaches"] += t.resolve_breached(now)
    return dict(sorted(out.items()))
