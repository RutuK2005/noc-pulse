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


TRACERT = """
Tracing route to 192.0.2.1 over a maximum of 12 hops

  1     2 ms     1 ms     1 ms  192.168.1.10
  2     4 ms     3 ms     3 ms  10.20.0.1
  3     *        *        *     Request timed out.
  4     6 ms     5 ms     5 ms  203.0.113.9
  5     *        *        *     Request timed out.
"""

LINUX_TRACE = """traceroute to 1.1.1.1 (1.1.1.1), 12 hops max, 60 byte packets
 1  192.168.1.1  1.234 ms  1.100 ms  1.050 ms
 2  1.1.1.1  5.120 ms  5.010 ms  4.990 ms
"""


def test_last_responding_hop():
    from noc_pulse.diagnostics import last_responding_hop

    assert last_responding_hop(TRACERT) == (4, "203.0.113.9")
    assert last_responding_hop("Request timed out.") is None


def test_no_icmp_only_check_hypothesis():
    assert "traceroute" in summarize_findings({"ping": False})
