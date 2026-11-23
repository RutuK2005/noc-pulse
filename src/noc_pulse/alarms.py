"""Alarm engine: threshold rules, hysteresis, de-duplication, flap handling, correlation.

How a sample becomes an alarm
-----------------------------
1. Every rule that applies to the probe kind is evaluated on the sample
   (``bad`` = condition true, ``good`` = condition false, ``None`` = metric
   missing, e.g. no latency because the probe failed -> no evidence either way).
2. Hysteresis: a rule keeps a streak counter per (target, probe, rule) key.
   The alarm is *raised* after ``raise_after`` consecutive bad samples and
   *cleared* after ``clear_after`` consecutive good samples. One bad sample
   in a sea of good ones never pages anybody.
3. De-duplication: while an alarm is active, further bad samples only bump
   ``occurrences`` / ``last_seen`` on the same alarm.
4. Flapping: if the same key is raised again within ``flap_window_s`` of
   clearing, the old alarm is re-opened (``flap_count`` + 1) instead of a new
   alarm being created, so a bouncing link produces one alarm, not twenty.
5. Correlation: if an *ancestor* in the dependency tree (e.g. the site gateway
   or the DNS resolver) has an active service-affecting alarm, the new alarm is
   marked ``suppressed`` and linked to that root cause. If the parent alarm is
   raised *after* its children, the children are re-linked ("correlated"
   event). After the root clears, children get ``orphan_grace_s`` to recover
   before they are treated as independent faults again ("unsuppressed").
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Any

from .inventory import Inventory, RuleSpec
from .probes import ProbeResult

OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


@dataclass
class Alarm:
    id: int
    key: str
    target: str
    probe: str
    rule: str
    severity: str
    raised_at: float
    service_affecting: bool = False
    state: str = "active"
    cleared_at: float | None = None
    last_seen: float | None = None
    occurrences: int = 1
    flap_count: int = 0
    suppressed: bool = False
    root_cause_id: int | None = None
    ticket_id: str | None = None
    message: str = ""
    orphaned_at: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "target": self.target,
            "probe": self.probe,
            "rule": self.rule,
            "severity": self.severity,
            "state": self.state,
            "raised_at": self.raised_at,
            "cleared_at": self.cleared_at,
            "last_seen": self.last_seen,
            "occurrences": self.occurrences,
            "flap_count": self.flap_count,
            "suppressed": int(self.suppressed),
            "root_cause_id": self.root_cause_id,
            "ticket_id": self.ticket_id,
            "message": self.message,
        }


@dataclass
class AlarmEvent:
    kind: str  # raised | reraised | cleared | correlated | unsuppressed
    alarm: Alarm
    ts: float
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Streak:
    bad: int = 0
    good: int = 0


def evaluate_rule(rule: RuleSpec, result: ProbeResult, threshold: Any = None) -> bool | None:
    """True = condition met (bad), False = not met (good), None = metric missing."""
    if result.probe not in rule.applies_to:
        return None
    value = result.metrics().get(rule.field)
    limit = rule.threshold_for(result.probe) if threshold is None else threshold
    if value is None or limit is None:
        return None
    return bool(OPS[rule.op](value, limit))


class AlarmEngine:
    def __init__(self, inv: Inventory, orphan_grace_s: float = 300.0) -> None:
        self.inv = inv
        self.orphan_grace_s = orphan_grace_s
        self.streaks: dict[str, _Streak] = {}
        self.active: dict[str, Alarm] = {}
        self.recently_cleared: dict[str, Alarm] = {}
        self.alarms: dict[int, Alarm] = {}
        self._next_id = 1

    # ------------------------------------------------------------------
    def _threshold(self, rule: RuleSpec, result: ProbeResult) -> Any:
        override = (
            self.inv.targets[result.target].thresholds.get(rule.id)
            if (result.target in self.inv.targets)
            else None
        )
        if isinstance(override, dict):
            return override.get(result.probe)
        return override

    def _root_for(self, target: str, exclude: str | None = None) -> Alarm | None:
        """Root cause for a new alarm on ``target``.

        Candidates, nearest first: another active service-affecting alarm on the
        same target (e.g. "unreachable" explains "packet_loss"), then active
        service-affecting alarms on ancestors. Chains are followed to the top.
        """
        if target not in self.inv.targets:
            return None
        for anc in [target, *self.inv.ancestors(target)]:
            for a in self.active.values():
                if a.target == anc and a.service_affecting and a.key != exclude:
                    root = a
                    while root.suppressed and root.root_cause_id in self.alarms:
                        root = self.alarms[root.root_cause_id]
                    return root
        return None

    def _message(self, rule: RuleSpec, result: ProbeResult) -> str:
        value = result.metrics().get(rule.field)
        if rule.field == "ok":
            return f"{result.target}/{result.probe}: {rule.description} ({result.error or 'failed'})"
        shown = f"{value:.1f}" if isinstance(value, float) else value
        limit = self._threshold(rule, result) or rule.threshold_for(result.probe)
        return f"{result.target}/{result.probe}: {rule.field}={shown} {rule.op} {limit}"

    # ------------------------------------------------------------------
    def evaluate(self, result: ProbeResult) -> list[AlarmEvent]:
        events: list[AlarmEvent] = []
        ts = result.ts
        for rule in self.inv.rules:
            verdict = evaluate_rule(rule, result, self._threshold(rule, result))
            if verdict is None:
                continue
            key = f"{result.target}|{result.probe}|{rule.id}"
            st = self.streaks.setdefault(key, _Streak())
            if verdict:
                st.bad += 1
                st.good = 0
            else:
                st.good += 1
                st.bad = 0
            alarm = self.active.get(key)
            if alarm is None and verdict and st.bad >= rule.raise_after:
                events.extend(self._raise(key, rule, result))
            elif alarm is not None and verdict:
                alarm.occurrences += 1
                alarm.last_seen = ts
                events.extend(self._maybe_unsuppress(alarm, ts))
            elif alarm is not None and not verdict and st.good >= rule.clear_after:
                events.extend(self._clear(alarm, ts))
        return events

    def _raise(self, key: str, rule: RuleSpec, result: ProbeResult) -> list[AlarmEvent]:
        ts = result.ts
        events: list[AlarmEvent] = []
        old = self.recently_cleared.get(key)
        if old is not None and old.cleared_at is not None and ts - old.cleared_at <= (self.inv.flap_window_s):
            alarm = old
            alarm.state = "active"
            alarm.cleared_at = None
            alarm.flap_count += 1
            alarm.occurrences += 1
            alarm.last_seen = ts
            del self.recently_cleared[key]
            kind = "reraised"
        else:
            alarm = Alarm(
                id=self._next_id,
                key=key,
                target=result.target,
                probe=result.probe,
                rule=rule.id,
                severity=rule.severity,
                raised_at=ts,
                last_seen=ts,
                occurrences=rule.raise_after,
                service_affecting=rule.service_affecting,
                message=self._message(rule, result),
            )
            self._next_id += 1
            self.alarms[alarm.id] = alarm
            kind = "raised"
        self.active[key] = alarm
        root = self._root_for(alarm.target, exclude=key)
        alarm.suppressed = root is not None
        alarm.root_cause_id = root.id if root else None
        alarm.orphaned_at = None
        events.append(AlarmEvent(kind, alarm, ts))
        # late parent: re-link children that are already active
        if alarm.service_affecting and not alarm.suppressed:
            below = set(self.inv.descendants(alarm.target)) if alarm.target in self.inv.targets else set()
            for child in list(self.active.values()):
                if child is alarm:
                    continue
                same_target = child.target == alarm.target and not child.service_affecting
                if child.target not in below and not same_target:
                    continue
                if child.suppressed and child.root_cause_id == alarm.id:
                    continue
                child.suppressed = True
                child.root_cause_id = alarm.id
                child.orphaned_at = None
                events.append(AlarmEvent("correlated", child, ts, {"root": alarm.id}))
        return events

    def _clear(self, alarm: Alarm, ts: float) -> list[AlarmEvent]:
        alarm.state = "cleared"
        alarm.cleared_at = ts
        del self.active[alarm.key]
        self.recently_cleared[alarm.key] = alarm
        events = [AlarmEvent("cleared", alarm, ts)]
        for child in self.active.values():
            if child.root_cause_id == alarm.id:
                child.orphaned_at = ts
        return events

    def _maybe_unsuppress(self, alarm: Alarm, ts: float) -> list[AlarmEvent]:
        if not alarm.suppressed or alarm.orphaned_at is None:
            return []
        if ts - alarm.orphaned_at < self.orphan_grace_s:
            return []
        root = self._root_for(alarm.target, exclude=alarm.key)
        if root is not None:
            alarm.root_cause_id = root.id
            alarm.orphaned_at = None
            return []
        alarm.suppressed = False
        alarm.root_cause_id = None
        alarm.orphaned_at = None
        return [AlarmEvent("unsuppressed", alarm, ts)]

    # ------------------------------------------------------------------
    def active_alarms(self) -> list[Alarm]:
        return sorted(self.active.values(), key=lambda a: a.raised_at)
