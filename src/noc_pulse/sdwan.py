"""SD-WAN path-selection simulation over *measured* time series.

This is a simulation, not a real SD-WAN: each "WAN path" is the ICMP time series
we measured towards one destination (e.g. path-a = 1.1.1.1, path-b = 8.8.8.8).
All paths share the same physical access link, so their failures are correlated
-- in a real branch the paths would be separate circuits (MPLS, broadband, LTE).

Per interval (bucket) and per path we compute latency (mean RTT), jitter
(mean |delta RTT| inside each ping burst) and loss (% echoes lost). Each
application class has an SLA (e.g. voice: latency < 150 ms, jitter < 30 ms,
loss < 1 %). We then compare three policies on the same data:

* static   : always use the primary path; fail over only when it is hard-down
             (no reply at all in the previous interval) -- classic routing.
* dynamic  : SLA-aware steering. Decide using the *previous* interval's
             measurements (no peeking at the future). If the current path broke
             its SLA, move to the preferred path that met it. A hold-down of H
             intervals after every switch prevents flapping, and we return to
             the primary once it has met the SLA for H intervals in a row.
* oracle   : upper bound -- "some path met the SLA in this interval".

Score = % of intervals in which the chosen path actually met the SLA.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .probes import ProbeResult


@dataclass(frozen=True)
class AppClass:
    name: str
    max_latency_ms: float
    max_jitter_ms: float
    max_loss_pct: float


DEFAULT_CLASSES: dict[str, AppClass] = {
    "voice": AppClass("voice", 150.0, 30.0, 1.0),
    "video": AppClass("video", 200.0, 50.0, 2.0),
    "bulk": AppClass("bulk", 400.0, math.inf, 5.0),
}


@dataclass(frozen=True)
class PathSample:
    latency_ms: float | None
    jitter_ms: float | None
    loss_pct: float
    echoes: int

    @property
    def hard_down(self) -> bool:
        return self.loss_pct >= 100.0


DOWN = PathSample(None, None, 100.0, 0)


def meets(s: PathSample | None, cls: AppClass) -> bool:
    if s is None or s.hard_down or s.latency_ms is None:
        return False
    jitter = s.jitter_ms or 0.0
    return s.latency_ms < cls.max_latency_ms and jitter < cls.max_jitter_ms and s.loss_pct < cls.max_loss_pct


def bucketize(
    results_by_path: dict[str, list[ProbeResult]], bucket_s: float = 60.0
) -> tuple[list[float], dict[str, list[PathSample]]]:
    """Align ping results of every path into common time buckets.

    The window is the overlap of all paths' first..last buckets. Inside it,
    buckets where *no* path has data are dropped (collector gap, e.g. the PC was
    asleep), and a bucket where only some paths lack data counts as hard-down for
    them. Note: after a dropped gap, the "previous interval" a policy decides on is
    the last bucket before the gap.
    """
    per_path: dict[str, dict[int, list[ProbeResult]]] = {}
    for path, results in results_by_path.items():
        d: dict[int, list[ProbeResult]] = {}
        for r in results:
            d.setdefault(int(r.ts // bucket_s), []).append(r)
        per_path[path] = d
    if not per_path or not any(per_path.values()):
        return [], {p: [] for p in results_by_path}
    first = max(min(d) for d in per_path.values() if d)
    last = min(max(d) for d in per_path.values() if d)
    buckets = [b for b in range(first, last + 1) if any(b in d for d in per_path.values())]
    out: dict[str, list[PathSample]] = {p: [] for p in per_path}
    for b in buckets:
        for path, d in per_path.items():
            rs = d.get(b)
            if not rs:
                out[path].append(DOWN)
                continue
            rtts = [x for r in rs for x in (r.rtts or [])]
            deltas = [abs(y - x) for r in rs for x, y in zip(r.rtts or [], (r.rtts or [])[1:], strict=False)]
            sent = sum(r.sent or 0 for r in rs)
            recv = sum(r.received or 0 for r in rs)
            out[path].append(
                PathSample(
                    latency_ms=sum(rtts) / len(rtts) if rtts else None,
                    jitter_ms=sum(deltas) / len(deltas) if deltas else None,
                    loss_pct=100.0 * (sent - recv) / sent if sent else 100.0,
                    echoes=sent,
                )
            )
    return [b * bucket_s for b in buckets], out


@dataclass
class PolicyResult:
    policy: str
    app: str
    intervals: int
    met: int
    switches: int
    choices: list[str] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return 100.0 * self.met / self.intervals if self.intervals else 0.0


def _rank(paths: list[str], primary: str) -> list[str]:
    return [primary] + [p for p in paths if p != primary]


def simulate_static(series: dict[str, list[PathSample]], cls: AppClass, primary: str) -> PolicyResult:
    paths = _rank(list(series), primary)
    n = len(series[primary])
    res = PolicyResult("static", cls.name, 0, 0, 0)
    current = primary
    for t in range(1, n):
        prev = {p: series[p][t - 1] for p in paths}
        if not prev[primary].hard_down:
            want = primary
        elif current != primary and not prev[current].hard_down:
            want = current
        else:  # primary dead: first backup that answered at all
            want = next((p for p in paths if not prev[p].hard_down), primary)
        if want != current:
            res.switches += 1
            current = want
        res.choices.append(current)
        res.intervals += 1
        res.met += meets(series[current][t], cls)
    return res


def simulate_dynamic(
    series: dict[str, list[PathSample]], cls: AppClass, primary: str, hold_down: int = 3
) -> PolicyResult:
    paths = _rank(list(series), primary)
    n = len(series[primary])
    res = PolicyResult("dynamic", cls.name, 0, 0, 0)
    current = primary
    since_switch = hold_down  # allow an immediate decision at start
    primary_good_streak = 0
    for t in range(1, n):
        prev = {p: series[p][t - 1] for p in paths}
        primary_good_streak = primary_good_streak + 1 if meets(prev[primary], cls) else 0
        want = current
        current_ok = meets(prev[current], cls)
        if not current_ok and (since_switch >= hold_down or prev[current].hard_down):
            candidates = [p for p in paths if meets(prev[p], cls)]
            if candidates:
                want = candidates[0]  # preference order: primary first
            else:  # nobody meets the SLA: least-bad path (loss, then latency)
                want = min(
                    paths,
                    key=lambda p: (prev[p].loss_pct, prev[p].latency_ms or math.inf),
                )
        elif current != primary and since_switch >= hold_down and primary_good_streak >= max(1, hold_down):
            want = primary  # pre-empt back to the preferred path once it is stable
        if want != current:
            res.switches += 1
            current = want
            since_switch = 0
        else:
            since_switch += 1
        res.choices.append(current)
        res.intervals += 1
        res.met += meets(series[current][t], cls)
    return res


def simulate_oracle(series: dict[str, list[PathSample]], cls: AppClass) -> PolicyResult:
    paths = list(series)
    n = len(series[paths[0]]) if paths else 0
    res = PolicyResult("oracle", cls.name, 0, 0, 0)
    for t in range(1, n):
        res.intervals += 1
        res.met += any(meets(series[p][t], cls) for p in paths)
    return res


def per_path_compliance(series: dict[str, list[PathSample]], cls: AppClass) -> dict[str, float]:
    out = {}
    for p, xs in series.items():
        window = xs[1:]
        out[p] = 100.0 * sum(meets(s, cls) for s in window) / len(window) if window else 0.0
    return out


def run_simulation(
    results_by_path: dict[str, list[ProbeResult]],
    primary: str,
    classes: dict[str, AppClass] | None = None,
    bucket_s: float = 60.0,
    hold_down: int = 3,
) -> dict[str, Any]:
    classes = classes or DEFAULT_CLASSES
    starts, series = bucketize(results_by_path, bucket_s)
    out: dict[str, Any] = {
        "intervals": max(0, len(starts) - 1),
        "bucket_s": bucket_s,
        "hold_down": hold_down,
        "primary": primary,
        "paths": list(series),
        "start": starts[0] if starts else None,
        "end": (starts[-1] + bucket_s) if starts else None,
        "classes": {},
    }
    for name, cls in classes.items():
        st = simulate_static(series, cls, primary) if starts else PolicyResult("static", name, 0, 0, 0)
        dy = (
            simulate_dynamic(series, cls, primary, hold_down)
            if starts
            else PolicyResult("dynamic", name, 0, 0, 0)
        )
        orc = simulate_oracle(series, cls) if starts else PolicyResult("oracle", name, 0, 0, 0)
        out["classes"][name] = {
            "sla": {
                "latency_ms": cls.max_latency_ms,
                "jitter_ms": cls.max_jitter_ms,
                "loss_pct": cls.max_loss_pct,
            },
            "static_pct": st.pct,
            "dynamic_pct": dy.pct,
            "oracle_pct": orc.pct,
            "static_met": st.met,
            "dynamic_met": dy.met,
            "oracle_met": orc.met,
            "intervals": st.intervals,
            "static_switches": st.switches,
            "dynamic_switches": dy.switches,
            "per_path_pct": per_path_compliance(series, cls),
        }
    return out
