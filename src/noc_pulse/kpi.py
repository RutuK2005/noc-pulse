"""KPI maths: availability, percentiles, jitter, packet loss, E-model R-factor/MOS, SLA checks.

Everything here is pure Python with no I/O so it can be unit-tested with
hand-computed values (see ``tests/test_kpi.py`` and ``docs/LEARN.md``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .inventory import SlaPolicy
    from .probes import ProbeResult


# ---------------------------------------------------------------------------
# basic statistics
# ---------------------------------------------------------------------------
def percentile(values: Iterable[float], p: float) -> float | None:
    """Percentile with linear interpolation between closest ranks.

    This is the same definition as ``numpy.percentile(..., method="linear")``
    (Hyndman & Fan type 7): rank h = (n - 1) * p / 100, interpolate between
    the values at floor(h) and ceil(h).
    """
    xs = sorted(values)
    if not xs:
        return None
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    h = (len(xs) - 1) * p / 100.0
    lo = math.floor(h)
    hi = math.ceil(h)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def mean(values: Iterable[float]) -> float | None:
    xs = list(values)
    return sum(xs) / len(xs) if xs else None


def mean_abs_delta(series: Sequence[float]) -> float | None:
    """Mean of |x[i] - x[i-1]| over consecutive samples.

    For RTTs this is the *mean deviation* of the packet-to-packet delay
    difference D -- the quantity that the RFC 3550 jitter estimator below
    smooths. It is what we report as "jitter".
    """
    if len(series) < 2:
        return None
    deltas = [abs(b - a) for a, b in zip(series, series[1:], strict=False)]
    return sum(deltas) / len(deltas)


def rfc3550_jitter(series: Sequence[float]) -> float | None:
    """RFC 3550 section 6.4.1 interarrival-jitter estimator.

    J(i) = J(i-1) + (|D(i-1, i)| - J(i-1)) / 16, starting at J = 0, where D is
    the difference in transit time of consecutive packets. With ping we use
    the RTT of consecutive echo replies as the transit time (two-way, so it
    over-states one-way jitter a little). Returns the final J.
    """
    if len(series) < 2:
        return None
    j = 0.0
    for a, b in zip(series, series[1:], strict=False):
        j += (abs(b - a) - j) / 16.0
    return j


def availability_pct(oks: Iterable[bool]) -> float | None:
    flags = list(oks)
    if not flags:
        return None
    return 100.0 * sum(1 for f in flags if f) / len(flags)


def loss_pct(sent: int, received: int) -> float | None:
    if sent <= 0:
        return None
    return 100.0 * (sent - received) / sent


# ---------------------------------------------------------------------------
# ITU-T G.107 E-model (simplified) -> R-factor -> MOS
# ---------------------------------------------------------------------------
R0_MINUS_IS = 93.2  # R0 - Is with all G.107 default parameters (no delay, no loss)
G711_IE = 0.0  # equipment impairment of G.711 (ITU-T G.113 Appendix I)
G711_PLC_BPL = 25.1  # packet-loss robustness of G.711 with PLC (ITU-T G.113 Appendix I)


def one_way_delay_ms(
    rtt_ms: float, jitter_ms: float, codec_delay_ms: float = 20.0, jitter_buffer_factor: float = 2.0
) -> float:
    """Mouth-to-ear delay estimate used by the E-model.

    d = RTT/2 (network one-way) + jitter_buffer_factor * jitter (de-jitter buffer)
        + codec_delay_ms (G.711 20 ms packetisation).
    """
    return rtt_ms / 2.0 + jitter_buffer_factor * jitter_ms + codec_delay_ms


def delay_impairment(d_ms: float) -> float:
    """Id from Cole & Rosenbluth (2001), an approximation of the G.107 Id term.

    Id = 0.024 d + 0.11 (d - 177.3) H(d - 177.3), H = step function.
    """
    return 0.024 * d_ms + (0.11 * (d_ms - 177.3) if d_ms > 177.3 else 0.0)


def loss_impairment(loss_percent: float, ie: float = G711_IE, bpl: float = G711_PLC_BPL) -> float:
    """Effective equipment impairment Ie,eff (ITU-T G.107, random loss, BurstR = 1).

    Ie,eff = Ie + (95 - Ie) * Ppl / (Ppl + Bpl)
    """
    ppl = max(0.0, loss_percent)
    return ie + (95.0 - ie) * ppl / (ppl + bpl)


def r_factor(
    rtt_ms: float,
    jitter_ms: float,
    loss_percent: float,
    codec_delay_ms: float = 20.0,
    ie: float = G711_IE,
    bpl: float = G711_PLC_BPL,
) -> float:
    """R = (R0 - Is) - Id - Ie,eff + A, with A = 0 (no advantage factor)."""
    d = one_way_delay_ms(rtt_ms, jitter_ms, codec_delay_ms)
    return R0_MINUS_IS - delay_impairment(d) - loss_impairment(loss_percent, ie, bpl)


def mos_from_r(r: float) -> float:
    """ITU-T G.107 Annex B: MOS_CQE = 1 + 0.035R + R(R - 60)(100 - R) * 7e-6.

    The cubic dips slightly below 1 for R < ~6.5, so (as in current G.107
    editions) MOS is 1 below R = 6.5 and 4.5 above R = 100.
    """
    if r < 6.5:
        return 1.0
    if r > 100:
        return 4.5
    return max(1.0, 1.0 + 0.035 * r + r * (r - 60.0) * (100.0 - r) * 7e-6)


def mos(rtt_ms: float, jitter_ms: float, loss_percent: float, **kw: float) -> float:
    return mos_from_r(r_factor(rtt_ms, jitter_ms, loss_percent, **kw))


# ---------------------------------------------------------------------------
# summaries over probe results
# ---------------------------------------------------------------------------
@dataclass
class KpiSummary:
    samples: int = 0
    ok: int = 0
    availability_pct: float | None = None
    latency_mean_ms: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    jitter_ms: float | None = None
    loss_pct: float | None = None
    echoes_sent: int = 0
    echoes_received: int = 0
    r_factor: float | None = None
    mos: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "extra"}
        d.update(self.extra)
        return d


def summarize(results: Sequence[ProbeResult]) -> KpiSummary:
    """KPIs for one (target, probe) series.

    * availability = successful samples / samples (sample-based)
    * latency: ping -> every individual echo RTT; other probes -> per-sample latency
    * jitter (ping only): mean |RTT[i] - RTT[i-1]| within each burst, pooled
    * loss (ping only): (echoes sent - received) / sent over the window
    * MOS (ping only): E-model on mean RTT, jitter and loss of the window
    """
    s = KpiSummary(samples=len(results))
    if not results:
        return s
    s.ok = sum(1 for r in results if r.ok)
    s.availability_pct = availability_pct(r.ok for r in results)
    is_ping = results[0].probe == "ping"
    if is_ping:
        lat = [x for r in results for x in (r.rtts or [])]
        deltas = [abs(b - a) for r in results for a, b in zip(r.rtts or [], (r.rtts or [])[1:], strict=False)]
        s.jitter_ms = sum(deltas) / len(deltas) if deltas else None
        s.echoes_sent = sum(r.sent or 0 for r in results)
        s.echoes_received = sum(r.received or 0 for r in results)
        s.loss_pct = loss_pct(s.echoes_sent, s.echoes_received)
    else:
        lat = [r.latency_ms for r in results if r.ok and r.latency_ms is not None]
    s.latency_mean_ms = mean(lat)
    s.latency_p50_ms = percentile(lat, 50)
    s.latency_p95_ms = percentile(lat, 95)
    if is_ping and s.latency_mean_ms is not None and s.loss_pct is not None:
        s.r_factor = r_factor(s.latency_mean_ms, s.jitter_ms or 0.0, s.loss_pct)
        s.mos = mos_from_r(s.r_factor)
    return s


@dataclass
class SlaCheck:
    metric: str
    target: float
    actual: float | None
    breached: bool


def evaluate_sla(k: KpiSummary, policy: SlaPolicy) -> list[SlaCheck]:
    """Compare a KPI summary with an SLA policy. Missing metrics are not breaches."""
    checks = [
        ("availability_pct", policy.availability_pct, k.availability_pct, "min"),
        ("latency_p95_ms", policy.latency_p95_ms, k.latency_p95_ms, "max"),
        ("jitter_ms", policy.jitter_ms, k.jitter_ms, "max"),
        ("loss_pct", policy.loss_pct, k.loss_pct, "max"),
    ]
    if policy.mos_min is not None:
        checks.append(("mos", policy.mos_min, k.mos, "min"))
    out = []
    for metric, target, actual, kind in checks:
        if actual is None:
            out.append(SlaCheck(metric, target, None, False))
            continue
        breached = actual < target if kind == "min" else actual > target
        out.append(SlaCheck(metric, target, actual, breached))
    return out
