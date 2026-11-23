import pytest

from noc_pulse.diagnostics import summarize_findings
from noc_pulse.report import coverage, render_markdown, service_availability, target_kpis
from noc_pulse.store import Store

from conftest import sample


def test_service_availability_is_strict_per_minute():
    results = [
        sample("gw", "ping", 0),
        sample("app", "http", 10),
        sample("gw", "ping", 60),
        sample("app", "http", 70, ok=False),  # one failure marks minute 1 down
        sample("gw", "ping", 120),
    ]
    pct, good, total = service_availability(results)
    assert (good, total) == (2, 3)
    assert pct == pytest.approx(200 / 3)


@pytest.mark.parametrize(
    "findings,expected",
    [
        ({"ping": False, "tcp": False}, "host unreachable"),
        ({"ping": True, "tcp": False}, "port refused/filtered"),
        ({"ping": False, "tcp": True}, "ICMP blocked"),
        ({"dns": False}, "name resolution failing"),
        ({"ping": True, "tcp": True, "http": False}, "application layer"),
        ({"ping": True, "tcp": True, "http": True}, "all checks pass"),
    ],
)
def test_l1_triage_summary(findings, expected):
    assert expected in summarize_findings(findings)


def test_report_end_to_end_on_store(inv):
    store = Store(":memory:")
    for i in range(10):
        store.insert_sample(sample("gw", "ping", i * 30, ok=i != 4))
        store.insert_sample(sample("app", "http", i * 30 + 5, latency=200.0))
    cov = coverage(store)
    assert cov["samples"] == 20 and cov["gaps"] == []
    rows = {(r["target"], r["probe"]): r for r in target_kpis(store)}
    assert rows[("gw", "ping")]["availability_pct"] == pytest.approx(90.0)
    md = render_markdown(store, inv)
    assert "| gw | ping | 10 | 90.000 |" in md
    assert "Customer services vs SLA" in md
