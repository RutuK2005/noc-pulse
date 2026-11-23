"""Asyncio collector: one polling loop per (target, probe), results go to SQLite.

Scheduling is drift-free (next run = start + k * interval) and every loop starts
at a random offset inside its interval so probes are spread out instead of
bursting together. An optional ``on_result`` callback (the alarm/ticket engine)
is called for every sample, which is how live alarming works.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from . import __version__
from .inventory import Inventory, ProbeSpec, Target, resolve_host
from .probes import ProbeResult, run_probe
from .store import Store

log = logging.getLogger("noc_pulse.collector")

ResultCallback = Callable[[ProbeResult], Awaitable[None] | None]


class KeepAwake:
    """Ask Windows not to sleep while the collector runs (process-scoped, auto-reverts)."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled and platform.system() == "Windows"

    def __enter__(self) -> KeepAwake:
        if self.enabled:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
            log.info("keep-awake enabled (SetThreadExecutionState)")
        return self

    def __exit__(self, *exc: object) -> None:
        if self.enabled:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)


async def _probe_loop(
    target: Target,
    host: str,
    spec: ProbeSpec,
    store: Store,
    deadline: float | None,
    on_result: ResultCallback | None,
    stop: asyncio.Event,
    jitter_start: bool,
) -> None:
    offset = random.uniform(0, spec.interval_s) if jitter_start else 0.0
    start = time.monotonic() + offset
    k = 0
    while not stop.is_set():
        next_at = start + k * spec.interval_s
        delay = next_at - time.monotonic()
        if delay > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
        if deadline is not None and time.time() >= deadline:
            return
        k += 1
        try:
            result = await run_probe(target.id, host, spec.kind, spec.params)
        except Exception as exc:  # noqa: BLE001 - defensive; probes should not raise
            result = ProbeResult(ts=time.time(), target=target.id, probe=spec.kind, ok=False, error=repr(exc))
        try:
            store.insert_sample(result)
        except Exception:  # noqa: BLE001
            log.exception("could not store sample for %s/%s", target.id, spec.kind)
        if on_result is not None:
            try:
                maybe = on_result(result)
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception:  # noqa: BLE001
                log.exception("engine callback failed")
        log.debug(
            "%s/%s ok=%s latency=%s err=%s",
            target.id,
            spec.kind,
            result.ok,
            result.latency_ms,
            result.error,
        )
        # skip ticks we missed (e.g. laptop was suspended) instead of bursting
        while start + k * spec.interval_s < time.monotonic():
            k += 1


async def run_collector(
    inv: Inventory,
    store: Store,
    duration_s: float | None = None,
    on_result: ResultCallback | None = None,
    stop: asyncio.Event | None = None,
    jitter_start: bool = True,
    meta: dict[str, Any] | None = None,
) -> int:
    """Run all probe loops until ``duration_s`` elapses or ``stop`` is set. Returns run id."""
    stop = stop or asyncio.Event()
    deadline = time.time() + duration_s if duration_s else None
    hosts = {t.id: resolve_host(t.host) for t in inv.targets.values()}
    run_meta = {
        "version": __version__,
        "targets": sorted(inv.targets),
        "probes": sum(len(t.probes) for t in inv.targets.values()),
        "platform": platform.system(),
        **(meta or {}),
    }
    run_id = store.start_run(run_meta)
    log.info("collector run %s started: %s probe loops", run_id, run_meta["probes"])

    async def heartbeat() -> None:
        while not stop.is_set():
            store.heartbeat(run_id)
            if deadline is not None and time.time() >= deadline:
                stop.set()
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=15)
            except TimeoutError:
                pass

    tasks = [
        asyncio.create_task(_probe_loop(t, hosts[t.id], spec, store, deadline, on_result, stop, jitter_start))
        for t in inv.targets.values()
        for spec in t.probes
    ]
    hb = asyncio.create_task(heartbeat())
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        await hb
        store.stop_run(run_id)
        log.info("collector run %s stopped", run_id)
    return run_id
