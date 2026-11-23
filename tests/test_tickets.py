import pytest

from noc_pulse.engine import Engine, load_tickets
from noc_pulse.inventory import DEFAULT_TICKETING
from noc_pulse.store import Store
from noc_pulse.tickets import (
    InvalidTransition,
    TicketManager,
    TicketState,
    mtta_s,
    mttr_s,
    priority_for,
    sla_report,
)

from conftest import sample

S = TicketState
MATRIX = DEFAULT_TICKETING["priority_matrix"]


@pytest.mark.parametrize(
    "impact,urgency,expected",
    [
        ("high", "high", "P1"),
        ("high", "medium", "P2"),
        ("medium", "high", "P2"),
        ("medium", "medium", "P3"),
        ("low", "high", "P3"),
        ("low", "low", "P4"),
        ("medium", "low", "P4"),
    ],
)
def test_priority_matrix(impact, urgency, expected):
    assert priority_for(impact, urgency, MATRIX) == expected


def test_priority_matrix_rejects_unknown():
    with pytest.raises(ValueError):
        priority_for("huge", "high", MATRIX)


def new_ticket(ts=0.0, impact="high", severity="critical"):
    tm = TicketManager(DEFAULT_TICKETING)
    return tm, tm.open("gw down", "gw", impact, severity, ts)


def test_open_sets_priority_and_sla_due_times():
    _, t = new_ticket(ts=1000)
    assert t.id == "INC000001"
    assert (t.priority, t.urgency) == ("P1", "high")
    assert t.response_due == 1000 + 15 * 60  # P1: respond in 15 min
    assert t.resolve_due == 1000 + 240 * 60  # P1: resolve in 4 h
    assert t.state is S.NEW


def test_happy_path_lifecycle_and_timestamps():
    _, t = new_ticket()
    t.transition(S.ACKNOWLEDGED, 60)
    t.transition(S.IN_PROGRESS, 120)
    t.transition(S.RESOLVED, 600, "replaced CPE power supply")
    t.transition(S.CLOSED, 900)
    assert (t.acknowledged_at, t.in_progress_at, t.resolved_at, t.closed_at) == (60, 120, 600, 900)
    assert t.resolution == "replaced CPE power supply"
    assert [w[2].split(":")[0] for w in t.worklog[1:]] == [
        "New -> Acknowledged",
        "Acknowledged -> In Progress",
        "In Progress -> Resolved",
        "Resolved -> Closed",
    ]


@pytest.mark.parametrize(
    "path,bad",
    [
        ([], S.IN_PROGRESS),  # New -> In Progress (must acknowledge first)
        ([], S.CLOSED),  # New -> Closed
        ([S.ACKNOWLEDGED], S.NEW),  # backwards
        ([S.ACKNOWLEDGED, S.IN_PROGRESS], S.CLOSED),  # must resolve before closing
        ([S.RESOLVED, S.CLOSED], S.IN_PROGRESS),  # closed is terminal
        ([S.RESOLVED, S.CLOSED], S.RESOLVED),
    ],
)
def test_invalid_transitions_rejected(path, bad):
    _, t = new_ticket()
    for i, st in enumerate(path):
        t.transition(st, 10 * (i + 1))
    before = t.state
    with pytest.raises(InvalidTransition):
        t.transition(bad, 999)
    assert t.state is before


def test_reopen_clears_resolution():
    _, t = new_ticket()
    t.transition(S.RESOLVED, 100, "auto")
    t.transition(S.IN_PROGRESS, 200, "re-opened")
    assert t.resolved_at is None and t.resolution is None and t.state is S.IN_PROGRESS


def test_sla_timers_and_breaches():
    _, t = new_ticket(ts=0)  # P1: response 900 s, resolve 14400 s
    assert not t.response_breached(now=899)
    assert t.response_breached(now=901)  # nobody acknowledged in 15 min
    assert t.remaining(now=600) == {"response_s": 300, "resolve_s": 13800}
    t.transition(S.ACKNOWLEDGED, 1200)  # late ack -> stays breached forever
    assert t.response_breached(now=10_000)
    t.transition(S.IN_PROGRESS, 1300)
    t.transition(S.RESOLVED, 14_000)
    assert not t.resolve_breached(now=99_999)  # resolved within 4 h, judged on resolved_at


def test_auto_resolved_ticket_counts_resolution_as_first_response():
    _, t = new_ticket(ts=0)
    t.transition(S.RESOLVED, 300, "alarm cleared")
    assert t.first_response_at == 300
    assert not t.response_breached(now=5000)


def test_mtta_mttr_and_sla_report():
    tm = TicketManager(DEFAULT_TICKETING)
    a = tm.open("a", "gw", "high", "critical", 0)
    b = tm.open("b", "gw", "low", "major", 100)
    c = tm.open("c", "gw", "medium", "critical", 200)
    a.transition(S.ACKNOWLEDGED, 60)
    a.transition(S.IN_PROGRESS, 120)
    a.transition(S.RESOLVED, 600)
    b.transition(S.ACKNOWLEDGED, 280)
    b.transition(S.RESOLVED, 1000)
    tickets = [a, b, c]
    assert mtta_s(tickets) == pytest.approx((60 + 180) / 2)
    assert mttr_s(tickets) == pytest.approx((600 + 900) / 2)
    rep = sla_report(tickets, now=10_000)
    assert rep["P1"] == {"tickets": 1, "resolved": 1, "response_breaches": 0, "resolve_breaches": 0}
    # c is P2 (medium x high): never acknowledged, 30 min response target -> breached
    assert rep["P2"]["response_breaches"] == 1


# ---------------------------------------------------------------------------
# engine = alarms + correlation + tickets end to end (no network)
# ---------------------------------------------------------------------------
def test_engine_root_cause_ticket_and_child_suppression(inv):
    store = Store(":memory:")
    eng = Engine(inv, store, diagnostics=False)
    ts = 0.0
    for _ in range(3):  # gateway dies
        eng.process(sample("gw", "ping", ts, ok=False))
        eng.process(sample("app", "http", ts + 1, ok=False))
        ts += 30
    for _ in range(3):
        eng.process(sample("app", "http", ts + 1, ok=False))
        ts += 30
    tickets = load_tickets(store)
    assert len(tickets) == 1, "child alarms must not open their own tickets"
    t = tickets[0]
    assert t.target == "gw" and t.priority == "P1" and t.state is S.NEW
    assert set(t.services) == {"voice", "web"}  # gw failure impacts everything below it
    assert any("suppressed" in w[2] for w in t.worklog)
    # operator acknowledges and starts work
    eng.operator_action(t.id, "ack", ts)
    eng.operator_action(t.id, "start", ts + 10)
    # gateway comes back -> alarm clears -> ticket auto-resolves
    for _ in range(2):
        ts += 30
        eng.process(sample("gw", "ping", ts))
    t = load_tickets(store)[0]
    assert t.state is S.RESOLVED
    assert t.time_to_resolve() == pytest.approx(ts - t.opened_at)
    alarms = store.rows("SELECT target, rule, suppressed, root_cause_id FROM alarms ORDER BY id")
    assert {(a["target"], a["suppressed"]) for a in alarms} >= {("gw", 0), ("app", 1)}


def test_engine_child_ticket_linked_and_resolved_with_parent(inv):
    eng = Engine(inv, None, diagnostics=False)
    for i in range(3):  # app fails first and gets its own ticket ...
        eng.process(sample("app", "http", i * 10, ok=False))
    for i in range(3):  # ... then its DNS dependency is found down
        eng.process(sample("dns", "dns", 40 + i * 10, ok=False))
    tks = sorted(eng.tickets.tickets.values(), key=lambda t: t.opened_at)
    app_t, dns_t = tks
    assert app_t.parent_id == dns_t.id and app_t.id in dns_t.children
    for i in range(2):
        eng.process(sample("dns", "dns", 100 + i * 10))
    assert dns_t.state is S.RESOLVED
    assert app_t.state is S.RESOLVED and "parent" in (app_t.resolution or "")


def test_engine_minor_alarm_does_not_open_ticket(inv):
    eng = Engine(inv, None)
    for i in range(3):
        eng.process(sample("app", "http", i * 60, latency=5000))
    assert len(eng.alarms.active) == 1 and eng.tickets.tickets == {}


def test_engine_auto_close_after_quiet_period(inv):
    eng = Engine(inv, None, auto_close_s=3600)
    for i in range(3):
        eng.process(sample("app", "http", i * 60, ok=False))
    for i in range(3, 5):
        eng.process(sample("app", "http", i * 60))
    (t,) = eng.tickets.tickets.values()
    assert t.state is S.RESOLVED
    eng.process(sample("app", "http", 240 + 3600))
    assert t.state is S.CLOSED


def test_correlation_window_waits_for_parent(inv):
    inv.correlation_window_s = 60
    eng = Engine(inv, None)
    # the child (app) crosses its threshold first ...
    for i in range(3):
        eng.process(sample("app", "http", i * 10, ok=False))
    assert eng.tickets.tickets == {} and len(eng.pending) == 1
    # ... the parent (gw) follows 20 s later, inside the 60 s window
    for i in range(3):
        eng.process(sample("gw", "ping", 25 + i * 5, ok=False))
    for i in range(10):
        eng.process(sample("gw", "ping", 40 + i * 10, ok=False))
    targets = [t.target for t in eng.tickets.tickets.values()]
    assert targets == ["gw"], "only the root cause gets a ticket"


def test_alarm_clearing_inside_window_opens_no_ticket(inv):
    inv.correlation_window_s = 300
    eng = Engine(inv, None)
    for i in range(3):
        eng.process(sample("app", "http", i * 60, ok=False))
    for i in range(3, 6):
        eng.process(sample("app", "http", i * 60))
    assert eng.tickets.tickets == {} and eng.pending == {}


def test_replay_over_store_is_deterministic(inv):
    from noc_pulse.engine import replay

    store = Store(":memory:")
    for i in range(6):
        store.insert_sample(sample("gw", "ping", i * 30, ok=i not in (2, 3, 4)))
    first = replay(inv, store)
    rows1 = store.rows("SELECT rule, state, raised_at, cleared_at FROM alarms ORDER BY id")
    second = replay(inv, store)
    rows2 = store.rows("SELECT rule, state, raised_at, cleared_at FROM alarms ORDER BY id")
    assert rows1 == rows2
    assert len(first.tickets.tickets) == len(second.tickets.tickets) == 1
    assert rows1[0]["raised_at"] == 120 and rows1[0]["cleared_at"] is None
