"""KPI maths checked against values worked out by hand (see docs/LEARN.md, "Formulas")."""

import pytest

from noc_pulse.inventory import SlaPolicy
from noc_pulse.kpi import (
    availability_pct,
    delay_impairment,
    evaluate_sla,
    loss_impairment,
    loss_pct,
    mean_abs_delta,
    mos,
    mos_from_r,
    one_way_delay_ms,
    percentile,
    r_factor,
    rfc3550_jitter,
    summarize,
)
from noc_pulse.probes import ProbeResult


def test_percentile_linear_interpolation():
    xs = [5, 1, 4, 2, 3]  # unsorted on purpose
    assert percentile(xs, 50) == 3
    # rank h = (5 - 1) * 0.95 = 3.8 -> 4 + 0.8 * (5 - 4) = 4.8
    assert percentile(xs, 95) == pytest.approx(4.8)
    assert percentile([10, 20], 95) == pytest.approx(19.5)
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None
    with pytest.raises(ValueError):
        percentile([1, 2], 101)


def test_mean_abs_delta_is_mean_deviation_of_D():
    # |12-10| + |11-12| + |15-11| = 2 + 1 + 4 = 7 over 3 pairs
    assert mean_abs_delta([10, 12, 11, 15]) == pytest.approx(7 / 3)
    assert mean_abs_delta([10]) is None
    assert mean_abs_delta([5, 5, 5]) == 0


def test_rfc3550_jitter_estimator_by_hand():
    # J1 = 0 + (2 - 0)/16 = 0.125
    # J2 = 0.125 + (1 - 0.125)/16 = 0.1796875
    # J3 = 0.1796875 + (4 - 0.1796875)/16 = 0.41845703125
    assert rfc3550_jitter([10, 12, 11, 15]) == pytest.approx(0.41845703125)


def test_rfc3550_converges_to_mean_abs_delta_for_steady_D():
    series = [10 + (i % 2) * 4 for i in range(400)]  # |D| = 4 every packet
    assert rfc3550_jitter(series) == pytest.approx(4.0, rel=1e-6)


def test_availability_and_loss():
    assert availability_pct([True, True, False, True]) == 75.0
    assert availability_pct([]) is None
    assert loss_pct(10, 9) == 10.0
    assert loss_pct(0, 0) is None


def test_emodel_good_call():
    # RTT 40, jitter 5, loss 0:
    # d = 40/2 + 2*5 + 20 = 50 ms ; Id = 0.024*50 = 1.2 ; Ie,eff = 0
    # R = 93.2 - 1.2 - 0 = 92.0
    # MOS = 1 + 0.035*92 + 92*(92-60)*(100-92)*7e-6 = 1 + 3.22 + 0.164864 = 4.384864
    assert one_way_delay_ms(40, 5) == 50
    assert r_factor(40, 5, 0) == pytest.approx(92.0)
    assert mos(40, 5, 0) == pytest.approx(4.384864)


def test_emodel_degraded_call():
    # RTT 300, jitter 20, loss 2 %:
    # d = 150 + 40 + 20 = 210 ; Id = 0.024*210 + 0.11*(210-177.3) = 5.04 + 3.597 = 8.637
    # Ie,eff = 95 * 2 / (2 + 25.1) = 7.0111 ; R = 93.2 - 8.637 - 7.0111 = 77.5519
    assert delay_impairment(210) == pytest.approx(8.637)
    assert loss_impairment(2) == pytest.approx(190 / 27.1)
    assert r_factor(300, 20, 2) == pytest.approx(77.5519, abs=1e-4)
    assert mos(300, 20, 2) == pytest.approx(3.9282, abs=1e-4)


def test_mos_bounds():
    assert mos_from_r(-5) == 1.0
    assert mos_from_r(0) == 1.0
    assert mos_from_r(100) == 4.5
    assert mos_from_r(120) == 4.5
    # best case with default parameters: R = 93.2 -> MOS ~ 4.41
    assert mos_from_r(93.2) == pytest.approx(4.4093, abs=1e-4)
    # MOS is monotonic in R
    values = [mos_from_r(r) for r in range(0, 101, 5)]
    assert values == sorted(values)


def _ping(ts, rtts, sent=10):
    return ProbeResult(
        ts=ts,
        target="t",
        probe="ping",
        ok=bool(rtts),
        latency_ms=None,
        sent=sent,
        received=len(rtts),
        rtts=rtts,
    )


def test_summarize_ping_pools_echoes_and_bursts():
    results = [_ping(0, [10, 12, 11], sent=4), _ping(30, [20, 20], sent=2), _ping(60, [], sent=2)]
    k = summarize(results)
    assert k.samples == 3 and k.ok == 2
    assert k.availability_pct == pytest.approx(200 / 3)
    # jitter pairs only *inside* bursts: |12-10|, |11-12|, |20-20| -> (2+1+0)/3 = 1
    assert k.jitter_ms == pytest.approx(1.0)
    # loss: sent 8, received 5 -> 37.5 %
    assert k.loss_pct == pytest.approx(37.5)
    assert k.latency_p50_ms == 12
    assert k.mos is not None


def test_summarize_non_ping_uses_successful_latencies_only():
    rs = [
        ProbeResult(ts=0, target="w", probe="http", ok=True, latency_ms=100),
        ProbeResult(ts=60, target="w", probe="http", ok=False, latency_ms=None, error="x"),
        ProbeResult(ts=120, target="w", probe="http", ok=True, latency_ms=300),
    ]
    k = summarize(rs)
    assert k.availability_pct == pytest.approx(200 / 3)
    assert k.latency_p50_ms == 200
    assert k.jitter_ms is None and k.loss_pct is None and k.mos is None


def test_sla_evaluation_flags_only_breached_metrics():
    policy = SlaPolicy("gold", 99.9, 100, 20, 0.5, 4.0)
    k = summarize([_ping(0, [50, 52, 51, 53]), _ping(30, [50, 52, 51, 53])])
    checks = {c.metric: c for c in evaluate_sla(k, policy)}
    assert not checks["latency_p95_ms"].breached
    assert checks["loss_pct"].breached  # received 8 of 20 echoes -> 60 % loss
    assert checks["availability_pct"].actual == 100.0
    assert not checks["availability_pct"].breached
