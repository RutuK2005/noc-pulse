import pytest

from noc_pulse.probes import ProbeResult
from noc_pulse.sdwan import (
    DEFAULT_CLASSES,
    PathSample,
    bucketize,
    meets,
    run_simulation,
    simulate_dynamic,
    simulate_oracle,
    simulate_static,
)

VOICE = DEFAULT_CLASSES["voice"]
GOOD = PathSample(20.0, 2.0, 0.0, 20)
BAD = PathSample(20.0, 2.0, 10.0, 20)  # 10 % loss breaks the voice SLA
DEAD = PathSample(None, None, 100.0, 20)


def test_meets_voice_sla_thresholds():
    assert meets(GOOD, VOICE)
    assert not meets(BAD, VOICE)
    assert not meets(DEAD, VOICE)
    assert not meets(PathSample(160.0, 2.0, 0.0, 20), VOICE)  # latency >= 150
    assert not meets(PathSample(20.0, 35.0, 0.0, 20), VOICE)  # jitter >= 30
    assert meets(PathSample(20.0, 35.0, 0.0, 20), DEFAULT_CLASSES["bulk"])


def test_brownout_static_stays_dynamic_moves():
    # primary "a" has a 10-interval brown-out (loss, but not dead); "b" is clean
    a = [GOOD] * 5 + [BAD] * 10 + [GOOD] * 10
    b = [GOOD] * 25
    series = {"a": a, "b": b}
    st = simulate_static(series, VOICE, "a")
    dy = simulate_dynamic(series, VOICE, "a", hold_down=3)
    orc = simulate_oracle(series, VOICE)
    assert st.intervals == dy.intervals == 24
    assert st.met == 14  # static never leaves a brown-out (the path is not "down")
    # dynamic decides on t-1 data, so it loses exactly the first brown-out interval
    assert dy.met == 23
    assert orc.met == 24
    assert dy.switches == 2  # a -> b, then back to a once a is stable for 3 intervals
    assert dy.choices[5] == "b"


def test_static_fails_over_only_on_hard_down():
    a = [GOOD] * 3 + [DEAD] * 5 + [GOOD] * 3
    b = [GOOD] * 11
    st = simulate_static({"a": a, "b": b}, VOICE, "a")
    assert st.switches == 2
    assert st.met == 9  # 10 intervals, one lost while the outage is detected


def test_hold_down_prevents_flapping():
    # primary alternates good/bad every interval; b is always good
    a = [GOOD, BAD] * 15
    b = [GOOD] * 30
    no_hold = simulate_dynamic({"a": a, "b": b}, VOICE, "a", hold_down=0)
    hold = simulate_dynamic({"a": a, "b": b}, VOICE, "a", hold_down=3)
    assert hold.switches < no_hold.switches
    assert hold.met >= no_hold.met


def test_no_path_meets_sla_picks_least_bad():
    worse = PathSample(30.0, 2.0, 40.0, 20)
    a = [GOOD] + [worse] * 5
    b = [GOOD] + [BAD] * 5
    dy = simulate_dynamic({"a": a, "b": b}, VOICE, "a", hold_down=1)
    assert dy.choices[-1] == "b"  # 10 % loss beats 40 % loss


def _ping(target, ts, rtts, sent=10):
    return ProbeResult(
        ts=ts, target=target, probe="ping", ok=bool(rtts), sent=sent, received=len(rtts), rtts=rtts
    )


def test_bucketize_aligns_paths_and_drops_collector_gaps():
    a = [_ping("a", 0, [10, 12]), _ping("a", 30, [11]), _ping("a", 600, [10])]
    b = [_ping("b", 5, [20]), _ping("b", 65, [21]), _ping("b", 605, [22])]
    starts, series = bucketize({"a": a, "b": b}, bucket_s=60)
    # buckets 0 and 1 have data, 2..9 are a gap for both (dropped), 10 has data
    assert starts == [0, 60, 600]
    assert series["a"][1].hard_down  # a had no sample in bucket 1 -> counted as down
    assert series["a"][0].latency_ms == pytest.approx(11.0)
    assert series["a"][0].loss_pct == pytest.approx(100 * (20 - 3) / 20)


def test_run_simulation_report_shape():
    a = [_ping("a", i * 60, [10] * 10) for i in range(10)]
    b = [_ping("b", i * 60 + 1, [12] * 10) for i in range(10)]
    rep = run_simulation({"a": a, "b": b}, primary="a")
    assert rep["intervals"] == 9
    voice = rep["classes"]["voice"]
    assert voice["static_pct"] == voice["dynamic_pct"] == voice["oracle_pct"] == 100.0


def test_zero_hold_down_does_not_bounce_back_to_a_failing_primary():
    a = [GOOD] + [BAD] * 20
    b = [GOOD] * 21
    dy = simulate_dynamic({"a": a, "b": b}, VOICE, "a", hold_down=0)
    assert dy.met == 19 and dy.switches == 1  # leaves a once, never returns
