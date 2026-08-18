"""
Agent Evaluation -- AI Observability Dashboard
Unified view of native Cortex Agent + external (the external orchestrator/TruLens) agent telemetry.
Uses GET_AI_OBSERVABILITY_EVENTS_NORMALIZED for flat 47-column access.
"""

import streamlit as st
import pandas as pd
import altair as alt
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Agent Eval -- AI Observability", layout="wide")

session = get_active_session()

# --- Privilege warning -----------------------------------------------------------
PRIV_NOTE = (
    "**Note:** Full tool inputs/outputs and conversation text require the "
    "account-level privilege `READ UNREDACTED AI OBSERVABILITY EVENTS TABLE`. "
    "Without it, metadata (tool names, tokens, latency, model, errors) is still visible."
)

# --- Data loading ----------------------------------------------------------------
@st.cache_data(ttl=300)
def load_observability_data():
    """UNION native + external agent events into one DataFrame."""
    sql = """
    SELECT *, 'CORTEX AGENT' AS AGENT_TYPE, 'FULFILLMENT_ANALYST' AS AGENT_NAME
    FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
        'AGENT_EVAL_DEMO', 'AI', 'FULFILLMENT_ANALYST', 'CORTEX AGENT'))
    UNION ALL
    SELECT *, 'EXTERNAL AGENT' AS AGENT_TYPE, 'EXTERNAL_SIM' AS AGENT_NAME
    FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
        'AGENT_EVAL_DEMO', 'AI', 'EXTERNAL_SIM', 'EXTERNAL AGENT'))
    """
    return session.sql(sql).to_pandas()


@st.cache_data(ttl=300)
def load_external_scores():
    """Per-metric means for scored EXTERNAL AGENT runs.

    Deliberately NOT folded into load_eval_data(): that function UNIONs the
    analyst and agent snapshot tables on a fixed column list, and this table
    has a different shape (RUN_NAME/LLM_JUDGE/SCORE rather than
    METRIC_NAME/EVAL_AGG_SCORE plus token columns).

    Returns an empty frame if the table does not exist yet, so a repo checked
    out before 08b_harness_scoring.sql was run still renders the tab. The
    empty case is surfaced as an st.info with the command to fix it -- NOT
    swallowed silently, which is the bug load_eval_data() carried for a week.
    """
    try:
        return session.sql(
            """
            SELECT RUN_NAME, METRIC_NAME, LLM_JUDGE,
                   COUNT(*) AS RECORDS,
                   ROUND(AVG(SCORE), 4) AS SCORE
            FROM AGENT_EVAL_DEMO.EVAL.EXTERNAL_SCORED_RESULTS
            GROUP BY RUN_NAME, METRIC_NAME, LLM_JUDGE
            ORDER BY RUN_NAME, METRIC_NAME
            """
        ).to_pandas()
    except Exception as e:
        st.caption(f"External scores unavailable: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_eval_data():
    """Load evaluation results from persisted snapshot tables.

    Table names MUST match what 05/06/07/08 actually persist. This previously
    read BASELINE_V1_RESULTS / OPTIMIZED_V2_RESULTS / AGENT_V1_RESULTS /
    AGENT_V2_RESULTS -- none of which exist; the canonical runs were superseded
    by the _FINAL / V4 names. Because the whole query sat inside a bare
    `except: return pd.DataFrame()`, the Evaluations tab rendered EMPTY with no
    error, which reads as "we have no eval data" in front of an audience.
    Verified against SHOW TABLES IN SCHEMA AGENT_EVAL_DEMO.EVAL on 2026-08-14.

    Columns are listed EXPLICITLY rather than SELECT *: the agent tables carry
    three extra token columns (TOTAL_INPUT_TOKENS, TOTAL_OUTPUT_TOKENS,
    LLM_CALL_COUNT) that the analyst tables do not, so SELECT * UNION ALL fails
    on column-count mismatch. EXTERNAL_V1_RESULTS is deliberately excluded --
    it is a run log (RUN_TS/QUESTION/ANSWER/ELAPSED_S/STATUS), not eval metrics.
    """
    cols = (
        "RECORD_ID, INPUT_ID, TIMESTAMP, DURATION_MS, INPUT, OUTPUT, ERROR, "
        "GROUND_TRUTH, METRIC_NAME, EVAL_AGG_SCORE, METRIC_TYPE, METRIC_STATUS"
    )
    sql = f"""
    SELECT 'BASELINE_V1_FINAL'  AS RUN_NAME, {cols} FROM AGENT_EVAL_DEMO.EVAL.BASELINE_V1_FINAL_RESULTS
    UNION ALL
    SELECT 'OPTIMIZED_V2_FINAL' AS RUN_NAME, {cols} FROM AGENT_EVAL_DEMO.EVAL.OPTIMIZED_V2_FINAL_RESULTS
    UNION ALL
    SELECT 'AGENT_V4'           AS RUN_NAME, {cols} FROM AGENT_EVAL_DEMO.EVAL.AGENT_V4_RESULTS
    UNION ALL
    SELECT 'TENANT_ISOLATION_V2' AS RUN_NAME, {cols} FROM AGENT_EVAL_DEMO.EVAL.TENANT_ISOLATION_V2_RESULTS
    """
    try:
        return session.sql(sql).to_pandas()
    except Exception as e:
        # Surface the failure. A silent empty frame is indistinguishable from
        # "this account genuinely has no eval runs" and hid this bug entirely.
        st.error(f"Could not load eval results: {e}")
        return pd.DataFrame()


# --- Header ----------------------------------------------------------------------
st.title("🔍 Agent Evaluation -- AI Observability")
st.caption(
    "Unified telemetry for native Cortex Agent (FULFILLMENT_ANALYST) "
    "and external orchestrator (EXTERNAL_SIM via TruLens). "
    "This is what Felix's ops team would embed for production monitoring."
)
st.info(PRIV_NOTE, icon="🔐")

# Load data
with st.spinner("Loading observability events..."):
    df = load_observability_data()

if df.empty:
    st.warning("No observability events found. Run the agent or the external orchestrator sim first.")
    st.stop()

# Normalize column names to uppercase for consistency
df.columns = [c.upper() for c in df.columns]

# Parse timestamps if present
for col in ["START_TIMESTAMP", "END_TIMESTAMP", "TIMESTAMP"]:
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")

# Derive a date column for time-series charts
ts_col = next((c for c in ["START_TIMESTAMP", "TIMESTAMP"] if c in df.columns), None)
if ts_col:
    df["EVENT_DATE"] = df[ts_col].dt.date
    df["EVENT_HOUR"] = df[ts_col].dt.floor("h")

# --- Sidebar filters -------------------------------------------------------------
st.sidebar.header("Filters")
agent_filter = st.sidebar.multiselect(
    "Agent", options=df["AGENT_NAME"].unique().tolist(),
    default=df["AGENT_NAME"].unique().tolist()
)
span_types_raw = df["SPAN_TYPE"].fillna("(none)").unique().tolist()
span_types = sorted(span_types_raw)
span_filter = st.sidebar.multiselect(
    "Span Type", options=span_types, default=span_types
)

df["_SPAN_TYPE_DISPLAY"] = df["SPAN_TYPE"].fillna("(none)")
mask = df["AGENT_NAME"].isin(agent_filter) & df["_SPAN_TYPE_DISPLAY"].isin(span_filter)
filtered = df[mask].copy()

# --- Tabs ------------------------------------------------------------------------
tabs = st.tabs([
    "📈 Adoption", "⏱️ Latency", "💰 Token Economics",
    "🔧 Tool Stats", "🎯 Eval History", "💬 Feedback",
    "🛡️ Tenant Isolation", "🔌 External Agent (TruLens)"
])

# === TAB 1: Adoption =============================================================
with tabs[0]:
    st.subheader("Adoption — Threads, Turns & Users Over Time")

    col1, col2, col3, col4 = st.columns(4)
    threads = filtered["THREAD_ID"].nunique() if "THREAD_ID" in filtered.columns else 0
    # One turn = one record (RECORD_ID groups spans in a single interaction)
    turns = filtered["RECORD_ID"].nunique() if "RECORD_ID" in filtered.columns else 0
    users = filtered["USER_NAME"].nunique() if "USER_NAME" in filtered.columns else 0
    total_events = len(filtered)

    col1.metric("Threads", f"{threads:,}")
    col2.metric("Turns (Traces)", f"{turns:,}")
    col3.metric("Distinct Users", f"{users:,}")
    col4.metric("Total Events", f"{total_events:,}")

    if "EVENT_DATE" in filtered.columns and not filtered["EVENT_DATE"].isna().all():
        daily = filtered.groupby(["EVENT_DATE", "AGENT_NAME"]).agg(
            events=("SPAN_ID", "count"),
            threads=("THREAD_ID", "nunique") if "THREAD_ID" in filtered.columns else ("SPAN_ID", "count"),
            users=("USER_NAME", "nunique") if "USER_NAME" in filtered.columns else ("SPAN_ID", "count"),
        ).reset_index()

        chart = alt.Chart(daily).mark_bar().encode(
            x=alt.X("EVENT_DATE:T", title="Date"),
            y=alt.Y("events:Q", title="Events"),
            color="AGENT_NAME:N",
            tooltip=["EVENT_DATE:T", "AGENT_NAME:N", "events:Q", "threads:Q", "users:Q"]
        ).properties(height=300)
        st.altair_chart(chart, width='stretch')
    else:
        # Fallback: show by agent
        agent_counts = filtered.groupby("AGENT_NAME").size().reset_index(name="events")
        st.bar_chart(agent_counts.set_index("AGENT_NAME"))

# === TAB 2: Latency ==============================================================
with tabs[1]:
    st.subheader("Latency Percentiles by Span Type")

    if "DURATION_MS" in filtered.columns:
        latency_df = filtered[filtered["DURATION_MS"].notna()].copy()
        if not latency_df.empty:
            percentiles = latency_df.groupby(["SPAN_TYPE", "AGENT_NAME"])["DURATION_MS"].describe(
                percentiles=[0.5, 0.75, 0.90, 0.95, 0.99]
            ).reset_index()
            percentiles.columns = [c.replace("%", "p") for c in percentiles.columns]

            st.dataframe(
                percentiles[["SPAN_TYPE", "AGENT_NAME", "count", "mean", "50p", "75p", "90p", "95p", "99p", "max"]].round(1),
                width='stretch', hide_index=True
            )

            # Box plot
            box = alt.Chart(latency_df).mark_boxplot(extent="min-max").encode(
                x=alt.X("SPAN_TYPE:N", title="Span Type"),
                y=alt.Y("DURATION_MS:Q", title="Duration (ms)", scale=alt.Scale(zero=False)),
                color="AGENT_NAME:N",
                tooltip=["SPAN_TYPE:N", "AGENT_NAME:N"]
            ).properties(height=350)
            st.altair_chart(box, width='stretch')

            # Highlight: the external orchestrator planner bottleneck
            harness_planner = latency_df[
                (latency_df["AGENT_NAME"] == "EXTERNAL_SIM") &
                (latency_df["SPAN_TYPE"].str.contains("graph_node|planner", case=False, na=False))
            ]
            if not harness_planner.empty:
                avg_plan = harness_planner["DURATION_MS"].mean()
                st.warning(
                    f"**Key Insight:** the external orchestrator planner stage averages {avg_plan/1000:.1f}s — "
                    f"the dominant latency contributor in the external orchestrator. "
                    f"Snowflake surfaced this about *your* code, not ours."
                )
        else:
            st.info("No duration data available.")
    else:
        st.info("DURATION_MS column not present in events.")

# === TAB 3: Token Economics ======================================================
with tabs[2]:
    st.subheader("Token Economics — Cost Per Turn by Model")

    has_tokens = "LLM_INPUT_TOKENS" in filtered.columns and "LLM_OUTPUT_TOKENS" in filtered.columns
    if has_tokens:
        token_df = filtered[
            filtered["LLM_INPUT_TOKENS"].notna() | filtered["LLM_OUTPUT_TOKENS"].notna()
        ].copy()
        token_df["LLM_INPUT_TOKENS"] = pd.to_numeric(token_df["LLM_INPUT_TOKENS"], errors="coerce").fillna(0)
        token_df["LLM_OUTPUT_TOKENS"] = pd.to_numeric(token_df["LLM_OUTPUT_TOKENS"], errors="coerce").fillna(0)
        token_df["TOTAL_TOKENS"] = token_df["LLM_INPUT_TOKENS"] + token_df["LLM_OUTPUT_TOKENS"]

        if not token_df.empty:
            # Summary by model
            model_col = "LLM_MODEL" if "LLM_MODEL" in token_df.columns else "AGENT_NAME"
            model_summary = token_df.groupby([model_col, "AGENT_NAME"]).agg(
                total_input=("LLM_INPUT_TOKENS", "sum"),
                total_output=("LLM_OUTPUT_TOKENS", "sum"),
                total_tokens=("TOTAL_TOKENS", "sum"),
                spans=("SPAN_ID", "count"),
            ).reset_index()
            model_summary["avg_tokens_per_span"] = (model_summary["total_tokens"] / model_summary["spans"]).round(0)

            st.dataframe(model_summary, width='stretch', hide_index=True)

            # Stacked bar: input vs output tokens by agent
            melt_df = token_df.groupby("AGENT_NAME").agg(
                input_tokens=("LLM_INPUT_TOKENS", "sum"),
                output_tokens=("LLM_OUTPUT_TOKENS", "sum"),
            ).reset_index().melt(id_vars="AGENT_NAME", var_name="token_type", value_name="tokens")

            bar = alt.Chart(melt_df).mark_bar().encode(
                x="AGENT_NAME:N",
                y=alt.Y("tokens:Q", title="Total Tokens"),
                color="token_type:N",
                tooltip=["AGENT_NAME:N", "token_type:N", "tokens:Q"]
            ).properties(height=300)
            st.altair_chart(bar, width='stretch')

            # Cost estimate (approximate Cortex pricing)
            st.caption(
                "💡 Cost estimate uses approximate Cortex AI credit rates. "
                "Actual billing depends on model and region."
            )
        else:
            st.info("No token data in filtered events.")
    else:
        st.info("Token columns not present.")

# === TAB 4: Tool Stats ===========================================================
with tabs[3]:
    st.subheader("Tool Invocation Stats")

    tool_df = filtered[filtered["SPAN_TYPE"].str.lower().isin(["tool", "tool_call"]) if "SPAN_TYPE" in filtered.columns else pd.Series([False]*len(filtered))].copy()

    if tool_df.empty:
        # Try matching on span_name patterns for tool invocations
        tool_df = filtered[filtered["SPAN_NAME"].notna()].copy()
        tool_df = tool_df[tool_df["SPAN_TYPE"].str.contains("tool", case=False, na=False)]

    if not tool_df.empty:
        tool_name_col = "SPAN_NAME" if "SPAN_NAME" in tool_df.columns else "TOOL_NAME"

        # Invocation counts
        tool_counts = tool_df.groupby([tool_name_col, "AGENT_NAME"]).agg(
            invocations=("SPAN_ID", "count"),
            avg_duration_ms=("DURATION_MS", "mean") if "DURATION_MS" in tool_df.columns else ("SPAN_ID", "count"),
            errors=("ERROR", lambda x: x.notna().sum()) if "ERROR" in tool_df.columns else ("SPAN_ID", lambda x: 0),
        ).reset_index()

        if "errors" in tool_counts.columns and "invocations" in tool_counts.columns:
            tool_counts["failure_rate"] = (tool_counts["errors"] / tool_counts["invocations"] * 100).round(1)

        st.dataframe(tool_counts.round(1), width='stretch', hide_index=True)

        # Bar chart of tool invocations
        chart = alt.Chart(tool_counts).mark_bar().encode(
            x=alt.X(f"{tool_name_col}:N", title="Tool"),
            y=alt.Y("invocations:Q", title="Invocations"),
            color="AGENT_NAME:N",
            tooltip=[f"{tool_name_col}:N", "AGENT_NAME:N", "invocations:Q"]
        ).properties(height=300)
        st.altair_chart(chart, width='stretch')
    else:
        st.info("No tool invocation spans found in the selected data.")

# === TAB 5: Eval History =========================================================
with tabs[4]:
    st.subheader("Evaluation Accuracy Trend")

    eval_df = load_eval_data()
    if not eval_df.empty:
        eval_df.columns = [c.upper() for c in eval_df.columns]

        if "METRIC_NAME" in eval_df.columns and "EVAL_AGG_SCORE" in eval_df.columns:
            # Aggregate scores by run and metric
            eval_df["EVAL_AGG_SCORE"] = pd.to_numeric(eval_df["EVAL_AGG_SCORE"], errors="coerce")
            agg_scores = eval_df.groupby(["RUN_NAME", "METRIC_NAME"])["EVAL_AGG_SCORE"].mean().reset_index()

            # SCALE DETECTION. EVAL_AGG_SCORE carries whatever range each metric
            # declared, and three different ranges land in this one column:
            #   * GPA system metrics (answer_correctness, logical_consistency,
            #     tool_selection_accuracy, tool_execution_accuracy) are natively 0-1
            #   * sql_correctness also returns 0-1 (baseline 0.45, optimized 0.70)
            #   * the parity custom metrics declare score_ranges on 0-3
            #     (groundedness raw mean 2.6444 -> 0.881)
            #   * tenant_isolation declares 1-10 (raw mean 4.0833)
            # So "max > 1 means divide by 3" is WRONG -- it would turn
            # tenant_isolation's 4.0833 into 1.361. Snap to the smallest declared
            # ceiling at or above the observed max instead. See docs/GOTCHAS.md #25,
            # where mis-normalizing one metric inverted a headline table.
            #
            # LIMITATION: the ceiling is inferred from observed scores, not read
            # from the YAML, so a 1-10 metric that never scored above 3 would be
            # normalized against 3. SCALE is surfaced in the table and tooltip so
            # the divisor is always auditable. It cannot clip the chart, because
            # SCORE_0_1 = mean / ceiling and ceiling >= max >= mean by construction.
            SCALE_LADDER = (1.0, 3.0, 5.0, 10.0)

            def _declared_ceiling(metric: str) -> float:
                mx = metric_max.get(metric)
                if mx is None or pd.isna(mx):
                    return 1.0
                return next((c for c in SCALE_LADDER if mx <= c), float(mx))

            metric_max = eval_df.groupby("METRIC_NAME")["EVAL_AGG_SCORE"].max()
            agg_scores["SCALE"] = agg_scores["METRIC_NAME"].map(_declared_ceiling)
            agg_scores["SCORE_0_1"] = agg_scores["EVAL_AGG_SCORE"] / agg_scores["SCALE"]

            st.caption(
                "EVAL_AGG_SCORE is the raw judge score on each metric's own declared "
                "range (0-1 for the GPA metrics and sql_correctness, 0-3 for the "
                "parity custom metrics, 1-10 for tenant_isolation). SCORE_0_1 divides "
                "by SCALE so metrics are comparable and match README.md."
            )
            st.dataframe(agg_scores.round(4), width='stretch', hide_index=True)

            # Chart the NORMALIZED score so the 0-1 domain is actually correct.
            chart = alt.Chart(agg_scores).mark_bar().encode(
                x="RUN_NAME:N",
                y=alt.Y("SCORE_0_1:Q", title="Score (normalized 0-1)",
                        scale=alt.Scale(domain=[0, 1])),
                color="METRIC_NAME:N",
                column="METRIC_NAME:N",
                tooltip=["RUN_NAME:N", "METRIC_NAME:N",
                         "EVAL_AGG_SCORE:Q", "SCALE:Q", "SCORE_0_1:Q"]
            ).properties(width=200, height=300)
            st.altair_chart(chart)

            # Regression detection
            st.subheader("Regression Detection")
            st.caption(
                "Compares consecutive runs on shared questions. "
                "A regression = question that scored 1.0 in the prior run but <1.0 in the new run."
            )
            if "OUTPUT" in eval_df.columns and "GROUND_TRUTH" in eval_df.columns:
                # Per-question comparison between paired runs
                # Only the analyst pair is a valid before/after on a SHARED question set.
                # AGENT_V1/V2 never existed, and AGENT_V4 vs TENANT_ISOLATION_V2 are
                # different question sets + different metrics, so pairing them would
                # report a meaningless "regression" count.
                for pair in [("BASELINE_V1_FINAL", "OPTIMIZED_V2_FINAL")]:
                    r1 = eval_df[eval_df["RUN_NAME"] == pair[0]]
                    r2 = eval_df[eval_df["RUN_NAME"] == pair[1]]
                    if not r1.empty and not r2.empty and "INPUT" in eval_df.columns:
                        merged = r1[["INPUT", "EVAL_AGG_SCORE"]].merge(
                            r2[["INPUT", "EVAL_AGG_SCORE"]],
                            on="INPUT", suffixes=("_before", "_after"), how="inner"
                        )
                        regressions = merged[
                            (merged["EVAL_AGG_SCORE_before"] > merged["EVAL_AGG_SCORE_after"])
                        ]
                        improved = merged[
                            (merged["EVAL_AGG_SCORE_after"] > merged["EVAL_AGG_SCORE_before"])
                        ]
                        st.markdown(
                            f"**{pair[0]} → {pair[1]}** ({len(merged)} shared questions): "
                            f"✅ {len(improved)} improved, ⚠️ {len(regressions)} regressed, "
                            f"{len(merged) - len(improved) - len(regressions)} unchanged"
                        )
                        if not regressions.empty:
                            st.dataframe(regressions, width='stretch', hide_index=True)
    else:
        # Fallback: try to read eval data from observability events directly
        eval_spans = filtered[filtered["METRIC_NAME"].notna()] if "METRIC_NAME" in filtered.columns else pd.DataFrame()
        if not eval_spans.empty:
            eval_spans["EVAL_AGG_SCORE"] = pd.to_numeric(eval_spans["EVAL_AGG_SCORE"], errors="coerce")
            by_run = eval_spans.groupby(["RUN_NAME", "METRIC_NAME"])["EVAL_AGG_SCORE"].mean().reset_index()
            st.dataframe(by_run.round(4), width='stretch', hide_index=True)
        else:
            st.info("No evaluation data found. Run EXECUTE_AI_EVALUATION first.")

# === TAB 6: Feedback =============================================================
with tabs[5]:
    st.subheader("User Feedback")

    if "FEEDBACK_POSITIVE" in filtered.columns:
        feedback_df = filtered[filtered["FEEDBACK_POSITIVE"].notna()].copy()
        if not feedback_df.empty:
            col1, col2, col3 = st.columns(3)
            pos = (feedback_df["FEEDBACK_POSITIVE"] == True).sum()
            neg = (feedback_df["FEEDBACK_POSITIVE"] == False).sum()
            total_fb = len(feedback_df)
            col1.metric("👍 Positive", pos)
            col2.metric("👎 Negative", neg)
            col3.metric("Satisfaction", f"{pos/total_fb*100:.0f}%" if total_fb > 0 else "N/A")

            # Feedback messages
            if "FEEDBACK_MESSAGE" in feedback_df.columns:
                msgs = feedback_df[feedback_df["FEEDBACK_MESSAGE"].notna()][
                    ["AGENT_NAME", "FEEDBACK_POSITIVE", "FEEDBACK_MESSAGE", "USER_NAME"]
                ]
                if not msgs.empty:
                    st.subheader("Feedback Messages")
                    st.dataframe(msgs, width='stretch', hide_index=True)
        else:
            st.info(
                "No feedback events yet. Feedback events appear when users "
                "click 👍/👎 in the agent chat interface."
            )
    else:
        st.info("FEEDBACK_POSITIVE column not present in events.")

# === TAB 7: Tenant Isolation Audit ===============================================
with tabs[6]:
    st.subheader("Tenant Isolation Audit")
    st.caption(
        "Verifies that row access policies prevent cross-tenant data leakage in agent responses. "
        "Requires Enterprise Edition; on Standard this tab will be empty."
    )

    if "ROLE_NAME" in filtered.columns:
        role_summary = filtered.groupby(["ROLE_NAME", "AGENT_NAME"]).agg(
            events=("SPAN_ID", "count"),
            distinct_threads=("THREAD_ID", "nunique") if "THREAD_ID" in filtered.columns else ("SPAN_ID", "count"),
        ).reset_index()
        st.dataframe(role_summary, width='stretch', hide_index=True)

    # Check for tenant isolation eval results
    st.subheader("Tenant Isolation Eval Results")
    try:
        # Canonical run is TENANT_ISOLATION_V2 -- it matches the dataset name in
        # tenant_isolation_eval_config.yaml and is what 08_tenant_isolation_eval.sql
        # snapshots. This read V1 and so showed "table not found" on any rebuilt
        # account; it only worked on the original account because a stale V1 table lingered.
        iso_sql = """
        SELECT * FROM AGENT_EVAL_DEMO.EVAL.TENANT_ISOLATION_V2_RESULTS
        ORDER BY EVAL_AGG_SCORE ASC
        """
        iso_df = session.sql(iso_sql).to_pandas()
        if not iso_df.empty:
            iso_df.columns = [c.upper() for c in iso_df.columns]
            score_col = "EVAL_AGG_SCORE" if "EVAL_AGG_SCORE" in iso_df.columns else None
            if score_col:
                iso_df[score_col] = pd.to_numeric(iso_df[score_col], errors="coerce")
                avg_score = iso_df[score_col].mean()
                failures = iso_df[iso_df[score_col] < 1.0]

                col1, col2 = st.columns(2)
                col1.metric("Isolation Score", f"{avg_score:.2f}")
                col2.metric("Leakage Failures", len(failures))

                if not failures.empty:
                    st.error("⚠️ Potential tenant data leakage detected:")
                    display_cols = [c for c in ["INPUT", "OUTPUT", score_col, "METRIC_EXPLANATION"] if c in failures.columns]
                    st.dataframe(failures[display_cols] if display_cols else failures, width='stretch', hide_index=True)
                else:
                    st.success("✅ All adversarial prompts correctly isolated — no cross-tenant leakage.")
        else:
            st.info("No tenant isolation eval results. Run TENANT_ISOLATION_V2 eval under a tenant-scoped role.")
    except Exception:
        st.info("Tenant isolation eval table not found. Run the TENANT_ISOLATION_V2 evaluation first.")

# --- Footer ----------------------------------------------------------------------
st.divider()
st.caption(
    "Powered by `GET_AI_OBSERVABILITY_EVENTS_NORMALIZED` — "
    "47 pre-parsed columns, no VARIANT spelunking needed. "
    "Both native Cortex Agent and external (TruLens-instrumented) orchestrators "
    "share the same observability surface."
)


# === TAB 8: External Agent (TruLens) =============================================
# Why this tab exists: Act 4 claims "instrumenting your own orchestrator is cheap
# and pays off immediately". Percentile tables do not make that case -- a per-turn
# waterfall does, because it shows the planner eating most of the turn inside
# code owned by the external orchestrator, which Snowflake never touched.
#
# CRITICAL DATA SEMANTICS: in GET_AI_OBSERVABILITY_EVENTS_NORMALIZED, TIMESTAMP is
# the span END time, not the start. Verified on the author's demo account: for one turn,
# record_root ended 19:48:02.883 with DURATION_MS=76877, and plan/route/respond
# reconstruct to a contiguous sequence only when you compute
# start = TIMESTAMP - DURATION_MS. Assuming TIMESTAMP is the start produces a
# waterfall where every bar begins at the wrong place and the stages overlap.
with tabs[7]:
    st.subheader("External Agent — ADK-shaped orchestrator, instrumented with TruLens")

    ext = df[df["AGENT_NAME"] == "EXTERNAL_SIM"].copy()
    ext = ext[ext["SPAN_TYPE"].notna()]

    if ext.empty:
        st.warning(
            "No EXTERNAL_SIM spans found. Run the harness to generate traces:\n\n"
            "`SF_CONNECTION=my_snowflake_connection "
            ".venv-harness/bin/python -m python.external_sim.run`"
        )
    else:
        st.markdown(
            "**Everything on this tab came from six decorator groups and one "
            "registration call in your own orchestrator.** Snowflake did not "
            "run this agent — it ran on the external orchestrator and shipped OTEL spans in."
        )

        # --- The instrumentation story, tied to live span types -------------------
        DECORATORS = [
            ("@instrument(GRAPH_NODE)", "plan()", "graph_node", "planner"),
            ("@instrument(TOOL)", "route()", "tool", "tool dispatch"),
            ("@instrument(GENERATION)", "respond()", "generation", "responder"),
            ("@instrument(AGENT)", "run()", "agent", "full pipeline"),
            ("@instrument(RECORD_ROOT)", "__call__()", "record_root", "one turn"),
            # 6th decorator group, added to make SCORING possible rather than
            # to improve tracing. context_relevance and groundedness read
            # RETRIEVAL.* attributes, so without a retrieval span those two
            # metrics never compute and never error -- see GOTCHAS #22.
            ("@instrument(RETRIEVAL) x3", "tools.py", "retrieval", "tool retrievals"),
        ]
        live_counts = ext.groupby("SPAN_TYPE")["RECORD_ID"].count().to_dict()
        st.markdown("**Decorator → span type → live spans**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Decorator": d,
                        "Method": m,
                        "SPAN_TYPE": s,
                        "Stage": label,
                        "Spans captured": int(live_counts.get(s, 0)),
                    }
                    for d, m, s, label in DECORATORS
                ]
            ),
            width="stretch",
            hide_index=True,
        )

        # --- Per-turn waterfall --------------------------------------------------
        st.markdown("---")
        st.markdown("### Turn waterfall — where the time actually goes")

        ext["_TS"] = pd.to_datetime(ext["TIMESTAMP"], utc=True, errors="coerce")
        ext["_DUR"] = pd.to_numeric(ext["DURATION_MS"], errors="coerce")
        ext = ext.dropna(subset=["_TS", "_DUR"])
        # TIMESTAMP is the span END -> derive the start.
        ext["_END"] = ext["_TS"]
        ext["_START"] = ext["_TS"] - pd.to_timedelta(ext["_DUR"], unit="ms")

        STAGES = ["graph_node", "tool", "generation"]
        ENVELOPE = ["record_root", "agent"]

        turn_meta = (
            ext.groupby("RECORD_ID")
            .agg(
                turn_ms=("_DUR", "max"),
                started=("_START", "min"),
                errored=("ERROR", lambda s: s.notna().any()),
                stages=("SPAN_TYPE", lambda s: s.isin(STAGES).sum()),
            )
            .reset_index()
        )
        # Only turns that actually carry stage spans can be drawn as a waterfall.
        drawable = turn_meta[turn_meta["stages"] > 0].sort_values(
            "turn_ms", ascending=False
        )

        if drawable.empty:
            st.info(
                "No turn in this dataset has plan/route/respond spans — only "
                "envelope spans were captured, so there is nothing to lay out. "
                "This is what a partially-instrumented run looks like."
            )
        else:
            labels = {
                f"{r.RECORD_ID[:8]}…  {r.turn_ms / 1000:,.1f}s"
                f"{'  ⚠ error' if r.errored else ''}": r.RECORD_ID
                for r in drawable.itertuples()
            }
            pick = st.selectbox(
                "Turn (slowest first)", list(labels.keys()),
                help="Each turn is one RECORD_ID. Stages are sequential: "
                     "plan → route → respond.",
            )
            rid = labels[pick]
            turn = ext[ext["RECORD_ID"] == rid].copy()
            t0 = turn["_START"].min()
            turn["start_s"] = (turn["_START"] - t0).dt.total_seconds()
            turn["end_s"] = (turn["_END"] - t0).dt.total_seconds()
            turn["Stage"] = turn["SPAN_TYPE"]

            stage_rows = turn[turn["SPAN_TYPE"].isin(STAGES)].copy()
            env_rows = turn[turn["SPAN_TYPE"].isin(ENVELOPE)].copy()
            turn_total = float(env_rows["_DUR"].max()) if not env_rows.empty else float(
                stage_rows["_DUR"].sum()
            )

            planner_ms = float(
                stage_rows.loc[stage_rows["SPAN_TYPE"] == "graph_node", "_DUR"].sum()
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Turn duration", f"{turn_total / 1000:,.1f} s")
            c2.metric("Planner (graph_node)", f"{planner_ms / 1000:,.1f} s")
            c3.metric(
                "Planner share of turn",
                f"{(planner_ms / turn_total * 100):.0f}%" if turn_total else "n/a",
            )

            order = [s for s in STAGES if s in set(stage_rows["SPAN_TYPE"])]
            chart = (
                alt.Chart(stage_rows)
                .mark_bar(size=26, cornerRadius=3)
                .encode(
                    x=alt.X("start_s:Q", title="Seconds from start of turn"),
                    x2="end_s:Q",
                    y=alt.Y("Stage:N", sort=order, title=None),
                    color=alt.Color(
                        "Stage:N", sort=order, legend=None,
                        scale=alt.Scale(
                            domain=["graph_node", "tool", "generation"],
                            range=["#d94f4f", "#29b5e8", "#7d5ba6"],
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("Stage:N"),
                        alt.Tooltip("SPAN_NAME:N", title="Instrumented method"),
                        alt.Tooltip("_DUR:Q", title="Duration (ms)", format=",.0f"),
                        alt.Tooltip("start_s:Q", title="Start (s)", format=".2f"),
                    ],
                )
                .properties(height=150)
            )
            st.altair_chart(chart, width='stretch')
            st.caption(
                "Red is the external planner. It runs inside the external orchestrator on your own "
                "infrastructure — Snowflake only received the span."
            )

        # --- Stage share across every turn ---------------------------------------
        st.markdown("---")
        st.markdown("### Stage share across all captured turns")
        share = (
            ext[ext["SPAN_TYPE"].isin(STAGES)]
            .groupby("SPAN_TYPE")["_DUR"]
            .agg(total_ms="sum", avg_ms="mean", spans="count")
            .reset_index()
        )
        if not share.empty:
            share["% of stage time"] = (
                share["total_ms"] / share["total_ms"].sum() * 100
            ).round(1)
            share = share.sort_values("total_ms", ascending=False)
            bar = (
                alt.Chart(share)
                .mark_bar()
                .encode(
                    x=alt.X("total_ms:Q", title="Total time in stage (ms)"),
                    y=alt.Y("SPAN_TYPE:N", sort="-x", title=None),
                    color=alt.Color(
                        "SPAN_TYPE:N", legend=None,
                        scale=alt.Scale(
                            domain=["graph_node", "tool", "generation"],
                            range=["#d94f4f", "#29b5e8", "#7d5ba6"],
                        ),
                    ),
                    tooltip=["SPAN_TYPE:N", "total_ms:Q", "avg_ms:Q", "spans:Q"],
                )
                .properties(height=140)
            )
            st.altair_chart(bar, width='stretch')
            st.dataframe(
                share.rename(
                    columns={
                        "SPAN_TYPE": "Stage",
                        "total_ms": "Total ms",
                        "avg_ms": "Avg ms",
                        "spans": "Spans",
                    }
                ).round(0),
                width="stretch",
                hide_index=True,
            )

        # --- Failed turns --------------------------------------------------------
        st.markdown("---")
        n_turns = ext["RECORD_ID"].nunique()
        err_turns = ext.loc[ext["ERROR"].notna(), "RECORD_ID"].nunique()
        st.markdown(
            f"### Failed turns — {err_turns} of {n_turns} "
            f"({err_turns / n_turns * 100:.0f}%)" if n_turns else "### Failed turns"
        )
        st.caption(
            "Use the ERROR column, never STATUS: every span here is "
            "STATUS_CODE_UNSET, so filtering on STATUS shows zero failures. "
            "AI_OBSERVABILITY_EVENTS is immutable, so these cannot be deleted — "
            "they are real failures from two earlier instrumentation bugs."
        )
        errs = ext[ext["ERROR"].notna()].copy()
        if errs.empty:
            st.success("No errored spans in this dataset.")
        else:
            errs["Error"] = errs["ERROR"].astype(str).str.slice(0, 160)
            st.dataframe(
                errs[["RECORD_ID", "SPAN_TYPE", "SPAN_NAME", "Error"]]
                .drop_duplicates()
                .head(50),
                width="stretch",
                hide_index=True,
            )

        # --- Scores --------------------------------------------------------------
        # Why this section exists: traces answer "where did the time go" and
        # "which stage failed". They cannot answer "is it any good". This is the
        # part that makes the external agent comparable to the native one, and it
        # is read with the SAME function as the native agent's results -- only
        # agent_type changes to 'EXTERNAL AGENT'.
        st.markdown("---")
        st.markdown("### Scored — LLM-as-a-judge metrics on the external agent")

        scored = load_external_scores()

        if scored.empty:
            st.info(
                "No scored runs yet. Traces alone cannot show quality — run:\n\n"
                "`TRULENS_OTEL_TRACING=1 .venv-harness/bin/python "
                "-m python.external_sim.score`"
            )
        else:
            st.dataframe(scored, width="stretch", hide_index=True)
            st.caption(
                "Read back with GET_AI_EVALUATION_DATA(..., 'EXTERNAL AGENT', run). "
                "context_relevance and groundedness are only computable because "
                "tools.py emits RETRIEVAL spans — before that the external agent "
                "could reach only 3 of these 5 metrics (GOTCHAS #22)."
            )
            st.warning(
                "**Not shown, because it does not work:** tool-trajectory metrics "
                "(the native agent's tool_selection_accuracy / "
                "tool_execution_accuracy). Server-side metric names are rejected "
                "with a JSON parse error and client-side Metric objects compute but "
                "never persist. Trajectory grading remains native-agent-only — see "
                "GOTCHAS #23. Do not claim parity."
            )
