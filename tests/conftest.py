import pytest

from noc_pulse.inventory import parse_inventory
from noc_pulse.probes import ProbeResult

LAB = {
    "sla_policies": {
        "gold": {
            "availability_pct": 99.9,
            "latency_p95_ms": 100,
            "jitter_ms": 20,
            "loss_pct": 0.5,
            "mos_min": 4.0,
        },
        "bronze": {
            "availability_pct": 99.0,
            "latency_p95_ms": 1500,
            "jitter_ms": 50,
            "loss_pct": 2.0,
            "mos_min": None,
        },
    },
    "targets": [
        {
            "id": "gw",
            "name": "Gateway",
            "role": "gateway",
            "host": "192.0.2.1",
            "probes": [{"kind": "ping", "interval_s": 30}],
        },
        {
            "id": "dns",
            "name": "DNS",
            "role": "dns",
            "host": "192.0.2.53",
            "depends_on": ["gw"],
            "probes": [{"kind": "dns", "interval_s": 30}],
        },
        {
            "id": "app",
            "name": "App",
            "role": "app",
            "host": "app.example",
            "depends_on": ["dns"],
            "probes": [{"kind": "http", "interval_s": 60, "url": "https://app.example/"}],
        },
        {
            "id": "api",
            "name": "API",
            "role": "app",
            "host": "api.example",
            "depends_on": ["dns"],
            "probes": [{"kind": "tcp", "interval_s": 60, "port": 443}],
        },
    ],
    "services": [
        {"id": "voice", "sla_class": "gold", "impact": "high", "targets": ["gw"]},
        {"id": "web", "sla_class": "bronze", "impact": "low", "targets": ["app"]},
    ],
    "alarm_rules": [
        {
            "id": "unreachable",
            "applies_to": ["ping", "tcp", "dns", "http"],
            "field": "ok",
            "op": "==",
            "value": False,
            "severity": "critical",
            "raise_after": 3,
            "clear_after": 2,
            "service_affecting": True,
            "description": "Probe failed",
        },
        {
            "id": "packet_loss",
            "applies_to": ["ping"],
            "field": "loss_pct",
            "op": ">=",
            "value": 20,
            "severity": "major",
            "raise_after": 3,
            "clear_after": 3,
        },
        {
            "id": "high_latency",
            "applies_to": ["ping", "http"],
            "field": "latency_ms",
            "op": ">",
            "value": {"ping": 100, "http": 3000},
            "severity": "minor",
            "raise_after": 2,
            "clear_after": 2,
        },
    ],
    "alarm_settings": {"flap_window_s": 600},
}


@pytest.fixture
def inv():
    import copy

    return parse_inventory(copy.deepcopy(LAB))


def sample(target, probe, ts, ok=True, latency=10.0, sent=None, received=None, rtts=None):
    if probe == "ping" and rtts is None:
        rtts = [latency] * 5 if ok else []
        sent, received = 5, len(rtts)
    return ProbeResult(
        ts=ts,
        target=target,
        probe=probe,
        ok=ok,
        latency_ms=latency if ok else None,
        sent=sent,
        received=received,
        rtts=rtts,
        error=None if ok else "timeout",
    )
