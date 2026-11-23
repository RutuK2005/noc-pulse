"""Streamlit NOC dashboard (pure Python): service health, KPI charts, alarms, tickets, SD-WAN.

Run:  noc-pulse dashboard            (or)
      streamlit run src/noc_pulse/dashboard.py -- --db data/noc.db --inventory config/inventory.yaml
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from noc_pulse.engine import load_tickets
from noc_pulse.inventory import load_inventory
from noc_pulse.report import (
    alarm_summary,
    coverage,
    fmt_ts,
    sdwan_report,
    service_kpis,
    target_kpis,
)
from noc_pulse.store import Store


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/noc.db")
    p.add_argument("--inventory", default="config/inventory.yaml")
    a, _ = p.parse_known_args()
    return a


ARGS = _args()
st.set_page_config(page_title="noc-pulse", layout="wide")
st.title("noc-pulse - service assurance console")

db_path = st.sidebar.text_input("SQLite database", ARGS.db)
inv_path = st.sidebar.text_input("Inventory", ARGS.inventory)
window = st.sidebar.selectbox("Time window", ["all data", "last 1 h", "last 6 h", "last 24 h"])
if st.sidebar.button("Refresh"):
    st.rerun()

if not Path(db_path).exists():
    st.error(f"{db_path} not found. Run `noc-pulse collect` first.")
    st.stop()

store = Store(db_path)
inv = load_inventory(inv_path)
cov = coverage(store)
hours = {"last 1 h": 1, "last 6 h": 6, "last 24 h": 24}.get(window)
since = (cov["last"] - hours * 3600) if (hours and cov["last"]) else None

tickets = load_tickets(store)
alarms = alarm_summary(store)
open_tickets = [t for t in tickets if t.state.value not in ("Resolved", "Closed")]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Data span", f"{cov['duration_h']:.2f} h")
c2.metric("Samples", f"{cov['samples']:,}")
c3.metric("ICMP echoes", f"{cov['echoes_sent']:,}")
c4.metric("Active alarms", len(alarms["active"]))
c5.metric("Open tickets", len(open_tickets))
st.caption(f"{fmt_ts(cov['first'])} -> {fmt_ts(cov['last'])}; collector gaps > 120 s: {len(cov['gaps'])}")

# ---- service health -------------------------------------------------------
st.header("Customer services vs SLA")
svc = pd.DataFrame(service_kpis(store, inv, since=since))
if not svc.empty:
    svc["SLA"] = svc["breaches"].apply(lambda b: "met" if not b else "BREACH: " + ", ".join(b))
    st.dataframe(
        svc[
            [
                "service",
                "customer",
                "sla_class",
                "kpi_source",
                "availability_pct",
                "latency_p95_ms",
                "jitter_ms",
                "loss_pct",
                "mos",
                "SLA",
            ]
        ].round(3),
        width="stretch",
        hide_index=True,
    )

# ---- KPI charts -----------------------------------------------------------
st.header("KPI trends")
keys = store.series_keys()
labels = [f"{t} / {p}" for t, p in keys]
default = labels.index("wan-cloudflare / ping") if "wan-cloudflare / ping" in labels else 0
choice = st.selectbox("Series", labels, index=default) if labels else None
if choice:
    target, probe = keys[labels.index(choice)]
    rs = store.samples(target=target, probe=probe, since=since)
    df = pd.DataFrame(
        {
            "time": pd.to_datetime([r.ts for r in rs], unit="s"),
            "latency_ms": [r.latency_ms for r in rs],
            "ok": [r.ok for r in rs],
            "loss_pct": [r.loss_pct for r in rs],
            "jitter_ms": [r.jitter_ms for r in rs],
        }
    ).set_index("time")
    left, right = st.columns(2)
    left.subheader("Latency (ms)")
    left.line_chart(df[["latency_ms"]])
    if probe == "ping":
        right.subheader("Jitter (ms) and loss (%) per burst")
        right.line_chart(df[["jitter_ms", "loss_pct"]])
    else:
        right.subheader("Availability per 5 min (%)")
        right.line_chart(df["ok"].astype(float).resample("5min").mean().mul(100))

st.subheader("Per-target KPIs")
st.dataframe(pd.DataFrame(target_kpis(store, since=since)).round(3), width="stretch", hide_index=True)

# ---- alarms and tickets ---------------------------------------------------
st.header("Alarms")
if alarms["rows"]:
    adf = pd.DataFrame(alarms["rows"])
    for col in ("raised_at", "cleared_at", "last_seen"):
        adf[col] = adf[col].apply(fmt_ts)
    st.dataframe(
        adf[
            [
                "id",
                "raised_at",
                "cleared_at",
                "target",
                "probe",
                "rule",
                "severity",
                "state",
                "occurrences",
                "flap_count",
                "suppressed",
                "root_cause_id",
                "ticket_id",
            ]
        ],
        width="stretch",
        hide_index=True,
    )
else:
    st.info("No alarms. Run `noc-pulse replay` or `noc-pulse collect --engine`.")

st.header("Incident tickets")
now = time.time()
if tickets:
    tdf = pd.DataFrame(
        [
            {
                "id": t.id,
                "priority": t.priority,
                "state": t.state.value,
                "opened": fmt_ts(t.opened_at),
                "target": t.target,
                "title": t.title,
                "response SLA": "BREACHED" if t.response_breached(now) else "ok",
                "resolve SLA": "BREACHED" if t.resolve_breached(now) else "ok",
                "time to resolve (min)": None
                if t.time_to_resolve() is None
                else round(t.time_to_resolve() / 60, 1),
                "parent": t.parent_id,
            }
            for t in tickets
        ]
    )
    st.dataframe(tdf, width="stretch", hide_index=True)
    pick = st.selectbox("Ticket details", [t.id for t in tickets])
    t = next(x for x in tickets if x.id == pick)
    st.markdown(f"**{t.id} {t.priority} [{t.state.value}]** {t.title}  \nservices: {', '.join(t.services)}")
    st.code("\n".join(f"{fmt_ts(ts)} [{k}] {x}" for ts, k, x in t.worklog) or "-")
    if t.diagnostics:
        with st.expander("Auto-diagnostics"):
            st.code(t.diagnostics)
else:
    st.info("No tickets yet.")

# ---- SD-WAN ---------------------------------------------------------------
st.header("SD-WAN path selection (simulation over measured data)")
try:
    sd = sdwan_report(store, inv, since=since)
    rows = [
        {
            "app": name,
            "static primary %": round(c["static_pct"], 2),
            "SLA-aware dynamic %": round(c["dynamic_pct"], 2),
            "oracle %": round(c["oracle_pct"], 2),
            "switches (static/dynamic)": f"{c['static_switches']}/{c['dynamic_switches']}",
            "intervals": c["intervals"],
        }
        for name, c in sd["classes"].items()
    ]
    st.caption(f"paths: {sd['path_targets']}; primary {sd['primary']}; hold-down {sd['hold_down']}")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
except ValueError as exc:
    st.info(str(exc))

store.close()
