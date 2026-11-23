from noc_pulse.alarms import AlarmEngine, evaluate_rule

from conftest import LAB, sample


def kinds(events):
    return [(e.kind, e.alarm.target, e.alarm.rule) for e in events]


def test_rule_evaluation_and_missing_metric(inv):
    unreachable = inv.rules[0]
    latency = inv.rules[2]
    assert evaluate_rule(unreachable, sample("gw", "ping", 0, ok=False)) is True
    assert evaluate_rule(unreachable, sample("gw", "ping", 0)) is False
    # failed sample has no latency -> no evidence for the latency rule
    assert evaluate_rule(latency, sample("app", "http", 0, ok=False)) is None
    # per-probe threshold: http limit is 3000 ms
    assert evaluate_rule(latency, sample("app", "http", 0, latency=2500)) is False
    assert evaluate_rule(latency, sample("app", "http", 0, latency=3500)) is True


def test_hysteresis_raise_after_n_clear_after_m(inv):
    eng = AlarmEngine(inv)
    assert eng.evaluate(sample("app", "http", 0, ok=False)) == []
    assert eng.evaluate(sample("app", "http", 60, ok=False)) == []
    ev = eng.evaluate(sample("app", "http", 120, ok=False))  # 3rd bad -> raise
    assert kinds(ev) == [("raised", "app", "unreachable")]
    alarm = ev[0].alarm
    assert alarm.occurrences == 3
    # one good sample is not enough to clear (clear_after = 2)
    assert eng.evaluate(sample("app", "http", 180)) == []
    ev = eng.evaluate(sample("app", "http", 240))
    assert kinds(ev) == [("cleared", "app", "unreachable")]
    assert alarm.state == "cleared" and alarm.cleared_at == 240


def test_isolated_bad_samples_never_raise(inv):
    eng = AlarmEngine(inv)
    ts = 0
    for _ in range(10):  # bad, bad, good, ... -> streak never reaches 3
        for ok in (False, False, True):
            assert eng.evaluate(sample("app", "http", ts, ok=ok)) == []
            ts += 60
    assert eng.active == {}


def test_deduplication_counts_occurrences(inv):
    eng = AlarmEngine(inv)
    for i in range(10):
        eng.evaluate(sample("app", "http", i * 60, ok=False))
    assert len(eng.alarms) == 1
    (alarm,) = eng.alarms.values()
    assert alarm.occurrences == 10
    assert alarm.last_seen == 540


def test_flap_reopens_same_alarm(inv):
    eng = AlarmEngine(inv)
    ts = 0

    def feed(ok, n):
        nonlocal ts
        out = []
        for _ in range(n):
            out += eng.evaluate(sample("app", "http", ts, ok=ok))
            ts += 60
        return out

    feed(False, 3)
    feed(True, 2)  # cleared at ts=240
    ev = feed(False, 3)  # re-raised at ts=420, within the 600 s flap window
    assert kinds(ev) == [("reraised", "app", "unreachable")]
    assert len(eng.alarms) == 1
    assert ev[0].alarm.flap_count == 1


def test_reraise_after_flap_window_is_a_new_alarm(inv):
    eng = AlarmEngine(inv)
    for i in range(3):
        eng.evaluate(sample("app", "http", i * 60, ok=False))
    for i in range(3, 5):
        eng.evaluate(sample("app", "http", i * 60, ok=True))
    ev = []
    for i in range(3):
        ev += eng.evaluate(sample("app", "http", 2000 + i * 60, ok=False))
    assert kinds(ev) == [("raised", "app", "unreachable")]
    assert len(eng.alarms) == 2


def test_child_alarm_suppressed_by_down_parent(inv):
    eng = AlarmEngine(inv)
    for i in range(3):
        eng.evaluate(sample("gw", "ping", i * 30, ok=False))
    root = next(iter(eng.active.values()))
    assert root.target == "gw" and not root.suppressed
    ev = []
    for i in range(3):
        ev += eng.evaluate(sample("app", "http", 100 + i * 60, ok=False))
    child = ev[-1].alarm
    assert child.target == "app"
    assert child.suppressed and child.root_cause_id == root.id  # app -> dns -> gw


def test_late_parent_correlates_existing_children(inv):
    eng = AlarmEngine(inv)
    for i in range(3):
        eng.evaluate(sample("app", "http", i * 10, ok=False))
        eng.evaluate(sample("api", "tcp", i * 10 + 1, ok=False))
    assert all(not a.suppressed for a in eng.active.values())
    ev = []
    for i in range(3):
        ev += eng.evaluate(sample("dns", "dns", 40 + i * 10, ok=False))
    assert kinds(ev)[0] == ("raised", "dns", "unreachable")
    correlated = {e.alarm.target for e in ev if e.kind == "correlated"}
    assert correlated == {"app", "api"}
    dns_alarm = ev[0].alarm
    assert all(eng.active[k].root_cause_id == dns_alarm.id for k in eng.active if "dns|" not in k)


def test_same_target_symptoms_are_correlated(inv):
    eng = AlarmEngine(inv)
    ev = []
    for i in range(3):  # total loss raises unreachable AND packet_loss on the same target
        ev += eng.evaluate(sample("gw", "ping", i * 30, ok=False))
    by_rule = {e.alarm.rule: e.alarm for e in ev}
    assert not by_rule["unreachable"].suppressed
    assert by_rule["packet_loss"].suppressed
    assert by_rule["packet_loss"].root_cause_id == by_rule["unreachable"].id


def test_unrelated_branch_not_suppressed(inv):
    eng = AlarmEngine(inv)
    for i in range(3):
        eng.evaluate(sample("app", "http", i * 60, ok=False))
    ev = []
    for i in range(3):
        ev += eng.evaluate(sample("api", "tcp", i * 60, ok=False))
    assert not ev[0].alarm.suppressed  # app and api are siblings, not ancestors


def test_orphaned_child_becomes_independent_after_grace(inv):
    eng = AlarmEngine(inv, orphan_grace_s=300)
    for i in range(3):
        eng.evaluate(sample("gw", "ping", i * 30, ok=False))
    for i in range(3):
        eng.evaluate(sample("app", "http", 100 + i * 60, ok=False))
    # gateway recovers at t=400/430 -> root clears
    eng.evaluate(sample("gw", "ping", 400))
    eng.evaluate(sample("gw", "ping", 430))
    # app is still failing on its own; within grace -> still suppressed
    assert eng.evaluate(sample("app", "http", 500, ok=False)) == []
    ev = eng.evaluate(sample("app", "http", 800, ok=False))
    assert kinds(ev) == [("unsuppressed", "app", "unreachable")]
    assert not ev[0].alarm.suppressed


def test_flapping_root_does_not_suppress_itself(inv):
    """Regression: http alarm #1 is root, tcp #2 is its same-target symptom; #1 clears and
    re-raises inside the flap window while #2 is still active. #1 must stay the root
    (not suppressed under itself) and evaluation must terminate."""
    import copy

    from noc_pulse.inventory import parse_inventory

    data = copy.deepcopy(LAB)
    data["targets"][2]["probes"].append({"kind": "tcp", "interval_s": 60, "port": 443})
    lab = parse_inventory(data)
    eng = AlarmEngine(lab)
    ts = 0
    for _ in range(3):
        eng.evaluate(sample("app", "http", ts, ok=False))
        eng.evaluate(sample("app", "tcp", ts + 1, ok=False))
        ts += 60
    http = next(a for a in eng.active.values() if a.probe == "http")
    tcp = next(a for a in eng.active.values() if a.probe == "tcp")
    assert not http.suppressed and tcp.suppressed and tcp.root_cause_id == http.id
    for _ in range(2):  # http recovers, tcp keeps failing
        eng.evaluate(sample("app", "http", ts))
        eng.evaluate(sample("app", "tcp", ts + 1, ok=False))
        ts += 60
    assert http.state == "cleared"
    ev = []
    for _ in range(3):  # http fails again within the flap window
        ev += eng.evaluate(sample("app", "http", ts, ok=False))
        ev += eng.evaluate(sample("app", "tcp", ts + 1, ok=False))
        ts += 60
    assert ("reraised", "app", "unreachable") in kinds(ev)
    assert http.state == "active" and not http.suppressed and http.root_cause_id is None
    assert tcp.root_cause_id == http.id and tcp.orphaned_at is None
    for _ in range(20):  # long after the orphan grace: still terminates, still linked
        eng.evaluate(sample("app", "tcp", ts + 1, ok=False))
        ts += 60
    assert tcp.suppressed and tcp.root_cause_id == http.id


def test_chain_stops_at_cleared_root_and_child_later_becomes_independent(inv):
    eng = AlarmEngine(inv, orphan_grace_s=300)
    t = 0
    for _ in range(3):  # gateway and dns down together
        eng.evaluate(sample("gw", "ping", t, ok=False))
        eng.evaluate(sample("dns", "dns", t + 1, ok=False))
        t += 30
    gw = next(a for a in eng.active.values() if a.target == "gw" and a.rule == "unreachable")
    dns = next(a for a in eng.active.values() if a.target == "dns")
    assert dns.root_cause_id == gw.id
    for _ in range(2):  # gateway recovers, dns still down (inside its grace)
        eng.evaluate(sample("gw", "ping", t))
        eng.evaluate(sample("dns", "dns", t + 1, ok=False))
        t += 30
    for _ in range(3):  # app fails now: its root must be the *active* dns alarm
        eng.evaluate(sample("app", "http", t + 2, ok=False))
        eng.evaluate(sample("dns", "dns", t + 1, ok=False))
        t += 30
    app = next(a for a in eng.active.values() if a.target == "app")
    assert app.suppressed and app.root_cause_id == dns.id
    for _ in range(2):  # dns recovers; app keeps failing on its own
        eng.evaluate(sample("dns", "dns", t + 1))
        eng.evaluate(sample("app", "http", t + 2, ok=False))
        t += 30
    ev = []
    for _ in range(15):
        ev += eng.evaluate(sample("app", "http", t + 2, ok=False))
        t += 30
    assert ("unsuppressed", "app", "unreachable") in kinds(ev)
    assert not app.suppressed
