"""Inventory: monitored targets, customer services, SLA policies, alarm rules, ticket policy.

The inventory is a YAML file (see ``config/inventory.yaml``). Loading it validates:

* ids are unique and every ``depends_on`` / service target exists,
* the dependency graph has no cycles,
* external (public) targets are not probed more often than every 30 s (politeness).
"""

from __future__ import annotations

import ipaddress
import platform
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MIN_EXTERNAL_INTERVAL_S = 30.0
PROBE_KINDS = {"ping", "tcp", "dns", "http"}
IMPACT_LEVELS = ("high", "medium", "low")


class InventoryError(ValueError):
    """Raised when the inventory file is invalid."""


@dataclass(frozen=True)
class ProbeSpec:
    kind: str
    interval_s: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Target:
    id: str
    name: str
    role: str
    host: str
    probes: list[ProbeSpec]
    depends_on: list[str] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SlaPolicy:
    name: str
    availability_pct: float
    latency_p95_ms: float
    jitter_ms: float
    loss_pct: float
    mos_min: float | None = None


@dataclass
class Service:
    id: str
    name: str
    customer: str
    site: str
    sla_class: str
    impact: str
    targets: list[str]
    voice: bool = False
    kpi_probe: str | None = None  # which probe kind feeds latency/jitter/loss KPIs


@dataclass(frozen=True)
class RuleSpec:
    id: str
    description: str
    applies_to: tuple[str, ...]
    field: str
    op: str
    value: Any
    severity: str
    raise_after: int = 3
    clear_after: int = 2
    service_affecting: bool = False

    def threshold_for(self, probe: str) -> Any:
        """Threshold value, possibly per probe kind (``value: {ping: 100, http: 3000}``)."""
        if isinstance(self.value, dict):
            return self.value.get(probe)
        return self.value


DEFAULT_TICKETING: dict[str, Any] = {
    "priority_matrix": {
        "high": {"high": "P1", "medium": "P2", "low": "P3"},
        "medium": {"high": "P2", "medium": "P3", "low": "P4"},
        "low": {"high": "P3", "medium": "P4", "low": "P4"},
    },
    "urgency_from_severity": {
        "critical": "high",
        "major": "medium",
        "minor": "low",
        "warning": "low",
    },
    "sla_minutes": {
        "P1": {"response": 15, "resolve": 240},
        "P2": {"response": 30, "resolve": 480},
        "P3": {"response": 120, "resolve": 1440},
        "P4": {"response": 480, "resolve": 4320},
    },
    "open_for_severities": ["critical", "major"],
}


@dataclass
class Inventory:
    targets: dict[str, Target]
    services: dict[str, Service]
    sla_policies: dict[str, SlaPolicy]
    rules: list[RuleSpec]
    ticketing: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TICKETING))
    flap_window_s: float = 600.0
    correlation_window_s: float = 0.0
    sdwan: dict[str, Any] = field(default_factory=dict)

    # ---- dependency graph -------------------------------------------------
    def parents(self, target_id: str) -> list[str]:
        return list(self.targets[target_id].depends_on)

    def ancestors(self, target_id: str) -> list[str]:
        """All upstream targets, nearest first (breadth-first)."""
        seen: list[str] = []
        queue = list(self.targets[target_id].depends_on)
        while queue:
            t = queue.pop(0)
            if t not in seen:
                seen.append(t)
                queue.extend(self.targets[t].depends_on)
        return seen

    def descendants(self, target_id: str) -> list[str]:
        out: list[str] = []
        queue = [t.id for t in self.targets.values() if target_id in t.depends_on]
        while queue:
            t = queue.pop(0)
            if t not in out:
                out.append(t)
                queue.extend(x.id for x in self.targets.values() if t in x.depends_on)
        return out

    def impacted_services(self, target_id: str) -> list[Service]:
        """Services that use the target itself or anything downstream of it."""
        affected = {target_id, *self.descendants(target_id)}
        return [s for s in self.services.values() if affected & set(s.targets)]

    def impact_of(self, target_id: str) -> str:
        services = self.impacted_services(target_id)
        if not services:
            return "low"
        if len(services) >= 3:  # widespread outage is always high impact
            return "high"
        return min((s.impact for s in services), key=IMPACT_LEVELS.index)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def is_external(host: str) -> bool:
    """True for public IPs and DNS names (anything that is not private/loopback)."""
    if host in {"auto-gateway", "system", "localhost"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


def detect_default_gateway() -> str | None:
    """Best-effort default gateway lookup (Windows ``ipconfig`` / Linux ``ip route``)."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=10, errors="replace"
            ).stdout
            for m in re.finditer(r"Default Gateway[ .]*:\s*([0-9]+(?:\.[0-9]+){3})", out):
                return m.group(1)
            # the gateway can sit on the following line when IPv6 is listed first
            m = re.search(r"Default Gateway[^\n]*\n\s+([0-9]+(?:\.[0-9]+){3})", out)
            return m.group(1) if m else None
        out = subprocess.run(
            ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=10
        ).stdout
        m = re.search(r"default via ([0-9.]+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _detect_cycles(targets: dict[str, Target]) -> None:
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(tid: str, path: list[str]) -> None:
        if state.get(tid) == 2:
            return
        if state.get(tid) == 1:
            raise InventoryError("dependency cycle: " + " -> ".join([*path, tid]))
        state[tid] = 1
        for p in targets[tid].depends_on:
            visit(p, [*path, tid])
        state[tid] = 2

    for tid in targets:
        visit(tid, [])


def parse_inventory(data: dict[str, Any], *, enforce_politeness: bool = True) -> Inventory:
    if not isinstance(data, dict):
        raise InventoryError("inventory must be a mapping")

    policies: dict[str, SlaPolicy] = {}
    for name, p in (data.get("sla_policies") or {}).items():
        policies[name] = SlaPolicy(
            name=name,
            availability_pct=float(p["availability_pct"]),
            latency_p95_ms=float(p["latency_p95_ms"]),
            jitter_ms=float(p["jitter_ms"]),
            loss_pct=float(p["loss_pct"]),
            mos_min=None if p.get("mos_min") is None else float(p["mos_min"]),
        )

    targets: dict[str, Target] = {}
    for raw in data.get("targets") or []:
        tid = raw["id"]
        if tid in targets:
            raise InventoryError(f"duplicate target id: {tid}")
        probes = []
        for pr in raw.get("probes") or []:
            pr = dict(pr)
            kind = pr.pop("kind")
            if kind not in PROBE_KINDS:
                raise InventoryError(f"{tid}: unknown probe kind {kind!r}")
            interval = float(pr.pop("interval_s", 60))
            if enforce_politeness and is_external(raw["host"]) and interval < MIN_EXTERNAL_INTERVAL_S:
                raise InventoryError(
                    f"{tid}: interval {interval}s is below the {MIN_EXTERNAL_INTERVAL_S:.0f}s "
                    "politeness floor for external targets"
                )
            probes.append(ProbeSpec(kind=kind, interval_s=interval, params=pr))
        targets[tid] = Target(
            id=tid,
            name=raw.get("name", tid),
            role=raw.get("role", "app"),
            host=str(raw["host"]),
            probes=probes,
            depends_on=list(raw.get("depends_on") or []),
            thresholds=dict(raw.get("thresholds") or {}),
        )

    for t in targets.values():
        for p in t.depends_on:
            if p not in targets:
                raise InventoryError(f"{t.id}: depends_on unknown target {p!r}")
    _detect_cycles(targets)

    services: dict[str, Service] = {}
    for raw in data.get("services") or []:
        sid = raw["id"]
        if sid in services:
            raise InventoryError(f"duplicate service id: {sid}")
        if raw["sla_class"] not in policies:
            raise InventoryError(f"{sid}: unknown sla_class {raw['sla_class']!r}")
        impact = raw.get("impact", "medium")
        if impact not in IMPACT_LEVELS:
            raise InventoryError(f"{sid}: impact must be one of {IMPACT_LEVELS}")
        for t in raw["targets"]:
            if t not in targets:
                raise InventoryError(f"{sid}: unknown target {t!r}")
        services[sid] = Service(
            id=sid,
            name=raw.get("name", sid),
            customer=raw.get("customer", ""),
            site=raw.get("site", ""),
            sla_class=raw["sla_class"],
            impact=impact,
            targets=list(raw["targets"]),
            voice=bool(raw.get("voice", False)),
            kpi_probe=raw.get("kpi_probe"),
        )

    rules = []
    for r in data.get("alarm_rules") or []:
        rules.append(
            RuleSpec(
                id=r["id"],
                description=r.get("description", ""),
                applies_to=tuple(r.get("applies_to") or sorted(PROBE_KINDS)),
                field=r["field"],
                op=r["op"],
                value=r["value"],
                severity=r.get("severity", "major"),
                raise_after=int(r.get("raise_after", 3)),
                clear_after=int(r.get("clear_after", 2)),
                service_affecting=bool(r.get("service_affecting", False)),
            )
        )

    ticketing = dict(DEFAULT_TICKETING)
    ticketing.update(data.get("ticketing") or {})
    settings = data.get("alarm_settings") or {}
    return Inventory(
        targets=targets,
        services=services,
        sla_policies=policies,
        rules=rules,
        ticketing=ticketing,
        flap_window_s=float(settings.get("flap_window_s", 600)),
        correlation_window_s=float(settings.get("correlation_window_s", 0)),
        sdwan=dict(data.get("sdwan") or {}),
    )


def load_inventory(path: str | Path, *, enforce_politeness: bool = True) -> Inventory:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return parse_inventory(data, enforce_politeness=enforce_politeness)


def resolve_host(host: str) -> str:
    """Turn the ``auto-gateway`` placeholder into a real address."""
    if host == "auto-gateway":
        gw = detect_default_gateway()
        if not gw:
            raise InventoryError("could not detect the default gateway; set host explicitly")
        return gw
    return host
