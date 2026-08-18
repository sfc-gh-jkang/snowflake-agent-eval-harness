"""Score the EXTERNAL_SIM external agent — not just trace it.

WHAT THIS ADDS OVER run.py
--------------------------
run.py proves an external orchestrator can be TRACED into Snowflake: it
registers a TruApp, invokes the app, and spans land in
AI_OBSERVABILITY_EVENTS. That answers "where did the time go" and "which
stage failed", but it produces NO SCORES. The actual question is "how do
I know it got better", and latency spans cannot answer that.

This script closes that gap: it attaches a ground-truth dataset to a RUN and
computes LLM-as-a-judge metrics, so the external agent gets numbers directly
comparable to the native Cortex Agent's GPA metrics.

THE INVOCATION PATH IS SPLIT — AND THAT IS THE HONEST FRAMING
--------------------------------------------------------------
EXECUTE_AI_EVALUATION is documented for NATIVE Cortex Agents. Custom
applications are scored through this SDK path instead. Both write to the same
event table and both are read back with the SAME functions, so the split is
in how you START a run, not in where results live or how you query them.
  https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-evaluations
  https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability/evaluate-applications-trulens

METRICS
-------
Server-side (by name) — all five documented metrics, all confirmed computing
on the author's demo account with 9 records each:
    coherence, answer_relevance, groundedness, context_relevance, correctness
Two of those five — context_relevance and groundedness — are only reachable
because tools.py now emits RETRIEVAL spans. They read
RETRIEVAL.QUERY_TEXT / RETRIEVAL.RETRIEVED_CONTEXTS and were uncomputable
before that change, so the external agent could previously reach only 3 of 5.

Tool-trajectory metrics (the native agent's tool_selection_accuracy /
tool_execution_accuracy) are NOT available on this path. Both routes were
tried and both fail — see build_trajectory_metrics() for the exact server
exception and the client-side non-persistence. --trajectory exists only to
re-test that. Do not claim parity with native agent evals.

Usage:
    TRULENS_OTEL_TRACING=1 .venv-harness/bin/python -m python.external_sim.score
    ... --run-name MY_RUN                 # optional; defaults to a timestamped name
    ... --metrics-only                    # skip invocation, score an existing run
    ... --trajectory                      # EXPERIMENTAL, known broken

Requires Python 3.9-3.11 (trulens-connectors-snowflake pins <3.12) and VPN.
"""

import argparse
import os

# MUST precede any trulens import or OTEL tracing never initializes and the
# run produces zero spans, which then silently yields zero computed metrics.
os.environ.setdefault("TRULENS_OTEL_TRACING", "1")

import time
from datetime import datetime

import snowflake.connector
from snowflake.snowpark import Session
from trulens.apps.app import TruApp
from trulens.connectors.snowflake import SnowflakeConnector
from trulens.core import TruSession
from trulens.core.run import Run, RunConfig

from .orchestrator import ExternalOrchestrator

CONNECTION_NAME = os.environ.get("SF_CONNECTION", "my_snowflake_connection")
APP_NAME = "EXTERNAL_SIM"
APP_VERSION = "v2-scored"
# SHARED with the native agent eval (eval_configs/agent_evaluation_config_v5_parity.yaml)
# so both agents answer the SAME 9 questions. Pointing at EXTERNAL_EVAL_DATASET
# instead re-introduces the question-set confound.
# GROUND_TRUTH_TEXT is the VARCHAR column; the agent reads the VARIANT one.
DATASET_TABLE = "AGENT_EVAL_DEMO.EVAL.SHARED_EVAL_DATASET"
RESULTS_TABLE = "AGENT_EVAL_DEMO.EVAL.EXTERNAL_SCORED_RESULTS"

# Pinned rather than left to default (llama3.1-70b) so re-runs are comparable.
# Changing the judge changes the scores; treat it as part of the run identity.
#
# MUST match llm_judge_name in eval_configs/agent_evaluation_config_v5.yaml.
# A comparison where the two sides are graded by DIFFERENT judges is not a
# comparison. This was mistral-large2 here while the agent eval left the judge
# unspecified -- and unspecified did NOT mean the documented llama3.1-70b
# default: measured on the author's demo account, ai.observability.eval.llm_judge_name was
# claude-4-sonnet for both AGENT_V4 and TENANT_ISOLATION_V2. So the two sides
# were graded by two different models, neither of which was the one assumed.
# Pin it on both sides and read it back from the event table to confirm.
# claude-4-sonnet, NOT the latest Opus, and deliberately so: the agent eval
# judge is not configurable (llm_judge_name is rejected by
# EXECUTE_AI_EVALUATION) and measures as claude-4-sonnet. Matching it here is
# the only way to get both sides graded by the same judge. If Snowflake ever
# exposes the agent judge, move BOTH to the latest Opus together.
LLM_JUDGE = "claude-4-sonnet"

SERVER_SIDE_METRICS = [
    "coherence",
    "answer_relevance",
    "groundedness",
    "context_relevance",
    "correctness",
]

# Optional egress-IP guard, same as run.py. Set SF_EXPECTED_EGRESS_IP if your
# account enforces a network policy admitting only one address; leave it unset
# to skip the check. Failing fast beats dying halfway through a paid run.
EXPECTED_EGRESS_IP = os.environ.get("SF_EXPECTED_EGRESS_IP")


def build_snowpark_session() -> Session:
    """Snowpark session from ~/.snowflake/connections.toml (no hardcoded creds)."""
    session = Session.builder.config("connection_name", CONNECTION_NAME).create()
    for stmt in ("USE DATABASE AGENT_EVAL_DEMO", "USE SCHEMA AI",
                 "USE WAREHOUSE AGENT_EVAL_DEMO_WH"):
        session.sql(stmt).collect()
    return session


def build_trajectory_metrics(session):
    """EXPERIMENTAL and OFF BY DEFAULT — trajectory scoring does NOT work yet.

    Kept in the tree because the finding is worth preserving and re-testing
    when the capability lands, not because it works. Enable with --trajectory
    only if you are re-testing it. Measured on the author's demo account, 2026-08-18, against
    TruLens 2.12.0 — BOTH available paths fail:

    PATH 1 — server-side, by name: compute_metrics(["tool_selection"]).
    The SDK accepts the name and dispatches it, and the run reports
    "Metrics computation in progress", so it LOOKS like it worked. It does
    not. GET_AI_OBSERVABILITY_LOGS shows the server procedure trying to parse
    the metric NAME as a JSON custom-metric definition and throwing:
        com.fasterxml.jackson.core.JsonParseException:
        Unrecognized token 'tool_selection': was expecting (JSON String,
        Number, Array, Object or token 'null', 'true' or 'false')
        at ComputeAIObservabilityMetricsProcedure.runInternal(...:196)
    Per-metric completion_status is FAILED with record_count 0, while the five
    documented metrics are COMPLETED with 9 each. Same failure for
    tool_calling, plan_quality and logical_consistency. Conclusion: the
    documented five ARE the server-side set; an unknown name is treated as a
    custom-metric JSON blob, not as a built-in.

    PATH 2 — client-side Metric objects wrapping the provider's trace-input
    feedback functions (tool_selection_with_cot_reasons and friends, which do
    exist in trulens.feedback.llm_provider and do take the whole trace).
    These run IN-PROCESS and log "Successfully computed client-side metric",
    but the scores never persist: zero rows in AI_OBSERVABILITY_EVENTS for
    those metric names, so they cannot be read back or compared beside the
    server-side scores. A locally-computed number that never lands is not a
    result we can put in front of a customer.

    NET: tool-trajectory grading remains native-Cortex-Agent-only
    (tool_selection_accuracy / tool_execution_accuracy). Do not claim parity.
    The honest workaround, if trajectory scoring is required, is a CUSTOM
    metric supplied as a proper JSON definition — the same mechanism native
    agent evals use, where the prompt can reference {{tool_info}} and
    {{span_type}}. That is not implemented here.
    """
    try:
        from trulens.core import Metric
        from trulens.providers.cortex import Cortex
    except ImportError as e:
        print(f"  ! trajectory metrics unavailable ({e})")
        return []

    provider = Cortex(snowpark_session=session, model_engine=LLM_JUDGE)
    wanted = [
        ("tool_selection", "tool_selection_with_cot_reasons"),
        ("tool_calling", "tool_calling_with_cot_reasons"),
        ("plan_quality", "plan_quality_with_cot_reasons"),
    ]

    metrics = []
    for name, fn_name in wanted:
        fn = getattr(provider, fn_name, None)
        if fn is None:
            print(f"  ! {name}: provider has no {fn_name}; skipping")
            continue
        metrics.append(
            Metric(name=name, implementation=fn, enable_trace_compression=True)
        )
        print(f"    + {name} (EXPERIMENTAL — expected not to persist)")
    return metrics


def _poll(run: Run, accept, label: str, timeout_s: int, poll_s: int = 20) -> str:
    """Poll run status until `accept(status)` is true, or fail loudly.

    Args:
        accept: predicate over the status string.
        label: what we are waiting for, used in messages.
    """
    terminal_bad = ("FAILED", "CANCELLED")
    deadline = time.time() + timeout_s
    last = None

    while time.time() < deadline:
        status = str(run.get_status())
        if status != last:
            print(f"    [{label}] status={status}", flush=True)
            last = status
        if accept(status):
            return status
        if any(s in status for s in terminal_bad):
            raise SystemExit(f"ABORT: run reached terminal status {status}")
        time.sleep(poll_s)

    raise SystemExit(f"ABORT: {label} did not finish within {timeout_s}s "
                     f"(last status {last})")


def wait_for_invocation(run: Run, timeout_s: int = 1800) -> str:
    """Block until the run has invoked the app and ingested records.

    Polling is mandatory, not politeness. The docs are explicit that record
    ingestion after start() is ASYNCHRONOUS and that calling compute_metrics()
    too early "may result in 0 events being found and no metrics being
    computed" — a silent no-op that looks like a scoring failure.
    """
    return _poll(
        run,
        lambda s: "INVOCATION_COMPLETED" in s or "INVOCATION_PARTIALLY_COMPLETED" in s
                  or _metrics_done(s),
        "invocation", timeout_s,
    )


def _metrics_done(status: str) -> bool:
    """True once METRIC COMPUTATION has finished, not merely invocation.

    The substring trap that cost the first scored run: testing for "COMPLETED"
    matches "INVOCATION_COMPLETED" too, so a naive check returns the instant
    the app finishes running and long before any judge has scored anything.
    That is exactly how EXTERNAL_SCORED_V2 reported "NO SCORES" while four
    metrics were still computing server-side and landed minutes later. Strip
    the INVOCATION_ prefix cases first, then look for the completion states.
    """
    s = status.replace("INVOCATION_PARTIALLY_COMPLETED", "").replace(
        "INVOCATION_COMPLETED", "")
    return "COMPLETED" in s


def wait_for_metrics(run: Run, timeout_s: int = 2400) -> str:
    """Block until metric computation reaches a completed state."""
    return _poll(run, _metrics_done, "metrics", timeout_s)


def persist_scores(conn, run_name: str) -> int:
    """Snapshot the scored results so a later run cannot overwrite the record.

    Reads through GET_AI_EVALUATION_DATA with agent_type='EXTERNAL AGENT' —
    the only difference from how the native agent's AGENT_V4 results are read.
    """
    cur = conn.cursor()
    try:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {RESULTS_TABLE} (
                RUN_NAME     VARCHAR,
                MEASURED_ON  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                LLM_JUDGE    VARCHAR,
                INPUT_ID     VARCHAR,
                METRIC_NAME  VARCHAR,
                SCORE        FLOAT,
                INPUT        VARCHAR,
                OUTPUT       VARCHAR,
                ERROR        VARCHAR
            )
        """)
        # Dedup defensively. GET_ANALYST_AI_EVALUATION_DATA has been observed
        # returning duplicate metric rows for a single input in this same repo
        # (21 rows for 20 questions), which skewed a mean from 0.700 to 0.6667.
        # Assume the agent-side function can do the same rather than finding
        # out from a wrong number on a slide.
        cur.execute(f"""
            INSERT INTO {RESULTS_TABLE}
              (RUN_NAME, LLM_JUDGE, INPUT_ID, METRIC_NAME, SCORE, INPUT, OUTPUT, ERROR)
            SELECT %s, %s, INPUT_ID, METRIC_NAME, EVAL_AGG_SCORE,
                   LEFT(INPUT, 2000), LEFT(OUTPUT, 2000), LEFT(ERROR, 2000)
            FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                'AGENT_EVAL_DEMO', 'AI', %s, 'EXTERNAL AGENT', %s))
            WHERE METRIC_NAME IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY INPUT_ID, METRIC_NAME
                ORDER BY EVAL_AGG_SCORE DESC NULLS LAST) = 1
        """, (run_name, LLM_JUDGE, APP_NAME, run_name))
        inserted = cur.rowcount or 0
        print(f"  persisted {inserted} scored rows to {RESULTS_TABLE}")
        return inserted
    finally:
        cur.close()


def report(conn, run_name: str) -> None:
    """Print per-metric means, and name any metric that failed to compute.

    A metric that computes nothing is reported as absent rather than quietly
    omitted — an uncomputed metric is a finding, not a blank cell.
    """
    cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT METRIC_NAME, COUNT(*) AS records,
                   ROUND(AVG(SCORE), 4) AS score
            FROM {RESULTS_TABLE}
            WHERE RUN_NAME = %s
            GROUP BY METRIC_NAME ORDER BY METRIC_NAME
        """, (run_name,))
        rows = cur.fetchall()
        print(f"\n  === {run_name} (judge={LLM_JUDGE}) ===")
        if not rows:
            print("  NO SCORES. Metrics did not compute — check that the run "
                  "reached INVOCATION_COMPLETED and that spans carry the "
                  "attributes each metric requires.")
            return
        got = {r[0] for r in rows}
        for name, records, score in rows:
            print(f"  {name:24} n={records:<3} score={score}")
        for expected in SERVER_SIDE_METRICS:
            if expected not in got:
                print(f"  {expected:24} NOT COMPUTED — reported as unavailable, "
                      "not substituted")
    finally:
        cur.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    # Default to a TIMESTAMPED name, never a fixed one. Runs are immutable and a
    # metric cannot be recomputed for an existing run, so any hardcoded default
    # is wrong on the second invocation. The previous default was
    # "EXTERNAL_SCORED_V1" — which is the name of the run that DIED with a
    # TypeError, making the no-argument invocation the one guaranteed to fail.
    ap.add_argument("--run-name",
                    default=f"EXTERNAL_SCORED_{datetime.now():%Y%m%d_%H%M%S}",
                    help="Runs are immutable; defaults to a fresh timestamped name. "
                         "Pass EXTERNAL_SCORED_V2 with --metrics-only to inspect the "
                         "canonical scored run.")
    ap.add_argument("--trajectory", action="store_true",
                    help="EXPERIMENTAL: attempt tool-trajectory metrics. "
                         "Measured NOT to work (see build_trajectory_metrics).")
    ap.add_argument("--metrics-only", action="store_true",
                    help="Skip invocation; compute metrics on an existing run.")
    ap.add_argument("--skip-ip-check", action="store_true",
                    help="Bypass the VPN egress guard (off-VPN is unproven).")
    args = ap.parse_args()

    print(f"[{datetime.now().isoformat()}] scoring {APP_NAME} run={args.run_name}")

    session = build_snowpark_session()
    ip = session.sql("SELECT CURRENT_IP_ADDRESS()").collect()[0][0]
    acct = session.sql("SELECT CURRENT_ACCOUNT()").collect()[0][0]
    print(f"  account={acct} ip={ip}")
    if EXPECTED_EGRESS_IP and ip != EXPECTED_EGRESS_IP and not args.skip_ip_check:
        raise SystemExit(
            f"ABORT: egress IP {ip} does not match SF_EXPECTED_EGRESS_IP "
            f"({EXPECTED_EGRESS_IP}). Check your network path, "
            "or pass --skip-ip-check."
        )

    connector = SnowflakeConnector(
        snowpark_session=session, database="AGENT_EVAL_DEMO", schema="AI",
    )
    TruSession(connector=connector)

    # Own raw connection, NOT session.connection: Snowpark sets
    # paramstyle='qmark' on its underlying connection, which leaves the
    # orchestrator's and tools' %s (pyformat) bindings unsubstituted and
    # fails with "001003 (42000): syntax error ... unexpected '%'". Nine
    # EXTERNAL_SIM turns died exactly this way before it was diagnosed.
    raw_conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
    cur = raw_conn.cursor()
    for stmt in ("USE DATABASE AGENT_EVAL_DEMO", "USE SCHEMA AI",
                 "USE WAREHOUSE AGENT_EVAL_DEMO_WH"):
        cur.execute(stmt)
    cur.close()

    orchestrator = ExternalOrchestrator(raw_conn)
    tru_app = TruApp(
        orchestrator,
        app_name=APP_NAME,
        app_version=APP_VERSION,
        main_method=orchestrator.__call__,
        connector=connector,
    )

    # dataset_spec keys are span attributes; values are COLUMN NAMES in
    # DATASET_TABLE.
    #
    # DO NOT add "RETRIEVAL.QUERY_TEXT" here. TruLens passes one positional
    # argument to main_method per non-ground-truth entry in this mapping, so
    # listing both RECORD_ROOT.INPUT and RETRIEVAL.QUERY_TEXT invokes
    # __call__(question, question) and the run dies immediately with
    #   TypeError: ExternalOrchestrator.__call__() takes 2 positional
    #   arguments but 3 were given
    # (measured — this is exactly how the first EXTERNAL_SCORED_V1 attempt
    # failed). It is also unnecessary: tools.py emits RETRIEVAL.QUERY_TEXT on
    # the retrieval spans themselves, which is where context_relevance reads
    # it from. The docs example maps both keys only because that app's
    # retrieval query is a separate dataset column from the record input.
    run_config = RunConfig(
        run_name=args.run_name,
        description="External agent scored on the same 9 ambiguity traps the "
                    "native Cortex Agent faces.",
        label="external-agent-scored",
        source_type="TABLE",
        dataset_name=DATASET_TABLE,
        dataset_spec={
            "RECORD_ROOT.INPUT": "INPUT_QUERY",
            "RECORD_ROOT.GROUND_TRUTH_OUTPUT": "GROUND_TRUTH_TEXT",
        },
        llm_judge_name=LLM_JUDGE,
    )

    if args.metrics_only:
        run = tru_app.get_run(run_name=args.run_name)
        print("  reusing existing run (--metrics-only)")
    else:
        run = tru_app.add_run(run_config=run_config)
        print(f"  invoking app over {DATASET_TABLE}")
        run.start()
        print("  waiting for ingestion")
        wait_for_invocation(run)

    metrics = list(SERVER_SIDE_METRICS)
    if args.trajectory:
        metrics += build_trajectory_metrics(session)
    print(f"  computing {len(metrics)} metrics")
    print(f"    {run.compute_metrics(metrics=metrics)}")

    # compute_metrics is asynchronous. Wait for METRIC completion specifically
    # — not merely INVOCATION_COMPLETED, which is already true by this point.
    wait_for_metrics(run)

    persist_scores(raw_conn, args.run_name)
    report(raw_conn, args.run_name)

    raw_conn.close()
    session.close()
    print(f"[{datetime.now().isoformat()}] done")


if __name__ == "__main__":
    main()
