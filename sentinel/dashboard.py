"""
Sentinel AI — Event Collector Dashboard (Section 3 deliverable).

Reads the raw sentinel_events.jsonl log written by the async event
writer and renders it as something actually looks at: KPIs, a
stage-latency breakdown, a recent-traces table, and a per-trace
waterfall. This is the visual proof that tracing (Section 3) is really
capturing what it claims to -- Section 4+ will replace the JSONL file
with Postgres, but the dashboard's job stays the same.
"""

import json
import os

import pandas as pd
import altair as alt
import streamlit as st

from .trace.trace_assembler import sync_events_to_db
from .config import EVENTS_PATH


def load_events() -> pd.DataFrame:
    if not os.path.exists(EVENTS_PATH):
        return pd.DataFrame()

    rows = []
    with open(EVENTS_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp_start"] = pd.to_datetime(df["timestamp_start"])
    return df


def render_dashboard():
    st.subheader("📊 Sentinel AI — Event Collector Dashboard")

    if st.button("🔄 Refresh"):
        st.rerun()

    newly_synced = sync_events_to_db()
    if newly_synced:
        st.caption(f"Synced {newly_synced} new trace(s) to the database (sentinel.db).")

    events_df = load_events()

    if events_df.empty:
        st.info("No events captured yet. Ask a question in the Chat tab first.")
        return

    # ---- Trace-level rollup ----
    traces = (
        events_df.groupby("trace_id")
        .agg(
            num_stages=("stage_name", "count"),
            total_latency_ms=("latency_ms", "sum"),
            has_error=("status", lambda s: (s == "error").any()),
            started_at=("timestamp_start", "min"),
        )
        .reset_index()
        .sort_values("started_at", ascending=False)
    )

    # ---- KPIs ----
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Traces", len(traces))
    col2.metric("Total Events", len(events_df))
    col3.metric("Avg Trace Latency", f"{traces['total_latency_ms'].mean():.1f} ms")
    error_rate = (traces["has_error"].sum() / len(traces)) * 100
    col4.metric("Error Rate", f"{error_rate:.1f}%")

    st.divider()

    # ---- Stage-wise average latency (where is time actually going) ----
    st.markdown("**Avg latency by stage (across all traces)**")
    stage_avg = (
        events_df.groupby("stage_name")["latency_ms"]
        .mean()
        .reset_index()
        .sort_values("latency_ms", ascending=False)
    )
    stage_chart = (
        alt.Chart(stage_avg)
        .mark_bar()
        .encode(
            x=alt.X("latency_ms:Q", title="Avg latency (ms)"),
            y=alt.Y("stage_name:N", sort="-x", title=None),
            tooltip=["stage_name", "latency_ms"],
        )
        .properties(height=250)
    )
    st.altair_chart(stage_chart, use_container_width=True)

    st.divider()

    # ---- Recent traces table ----
    st.markdown("**Recent traces**")
    display_traces = traces.copy()
    display_traces["trace_id"] = display_traces["trace_id"].str[:8] + "..."
    display_traces["status"] = display_traces["has_error"].map({True: "❌ error", False: "✅ success"})
    st.dataframe(
        display_traces[["trace_id", "started_at", "num_stages", "total_latency_ms", "status"]],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ---- Per-trace waterfall ----
    st.markdown("**Inspect a trace**")
    trace_options = traces["trace_id"].tolist()
    selected = st.selectbox(
        "Select a trace_id",
        trace_options,
        format_func=lambda t: f"{t[:8]}... ({traces.loc[traces.trace_id == t, 'num_stages'].values[0]} stages)",
    )

    if selected:
        trace_events = events_df[events_df["trace_id"] == selected].sort_values("timestamp_start")

        waterfall = (
            alt.Chart(trace_events)
            .mark_bar()
            .encode(
                x=alt.X("latency_ms:Q", title="Latency (ms)"),
                y=alt.Y("stage_name:N", sort=None, title=None),
                color=alt.Color(
                    "status:N",
                    scale=alt.Scale(domain=["success", "error"], range=["#2ecc71", "#e74c3c"]),
                ),
                tooltip=["stage_name", "latency_ms", "status", "error"],
            )
            .properties(height=250)
        )
        st.altair_chart(waterfall, use_container_width=True)

        with st.expander("Raw event details for this trace"):
            st.dataframe(
                trace_events[["stage_name", "latency_ms", "status", "error", "output_summary"]],
                use_container_width=True,
                hide_index=True,
            )
