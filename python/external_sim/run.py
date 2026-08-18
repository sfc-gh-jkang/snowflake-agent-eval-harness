"""Run the external orchestrator simulator with TruLens instrumentation.

This is the "you are not boxed in" proof. An external orchestrator
(ADK-shaped: planner -> router -> responder) stays exactly where it is; TruLens exports
OpenTelemetry spans from it into Snowflake, where they land in the SAME
AI_OBSERVABILITY_EVENTS table as a native Cortex Agent. One query then returns
traces for both — see AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE.

Registering the app with TruApp() auto-creates the EXTERNAL AGENT object
'EXTERNAL_SIM' in AGENT_EVAL_DEMO.AI. That object stores metadata only (app name,
version, run name) — never code, prompts, traces, or scores.

Usage:
    TRULENS_OTEL_TRACING=1 .venv-harness/bin/python -m python.external_sim.run

Requirements (Python 3.9-3.11 ONLY — trulens-connectors-snowflake pins <3.12):
    pip install trulens-core trulens-connectors-snowflake trulens-providers-cortex \
                snowflake-snowpark-python snowflake-connector-python

API NOTES (TruLens 2.12.0, verified live — the 1.x docs are misleading):
  * TruApp lives in trulens.apps.app, NOT trulens.core.app. TruCustomApp is the
    deprecated 1.x name.
  * SnowflakeConnector takes a SNOWPARK SESSION (snowpark_session=), not a raw
    snowflake.connector connection.
  * Cortex(provider) also takes snowpark_session, not a raw connection.
  * TRULENS_OTEL_TRACING=1 must be set BEFORE importing trulens, or no OTEL
    spans are emitted and the event table stays empty.
  * Do NOT call session.reset_database() — it is destructive and wipes prior runs.
"""

import os

# MUST precede any trulens import or OTEL tracing never initializes.
os.environ.setdefault("TRULENS_OTEL_TRACING", "1")

import time
from datetime import datetime

import snowflake.connector
from snowflake.snowpark import Session
from trulens.apps.app import TruApp
from trulens.connectors.snowflake import SnowflakeConnector
from trulens.core import TruSession

from .orchestrator import ExternalOrchestrator

CONNECTION_NAME = os.environ.get("SF_CONNECTION", "my_snowflake_connection")
APP_NAME = "EXTERNAL_SIM"
APP_VERSION = "v1"
RUN_NAME = "EXTERNAL_V1"

# Deliberately spans the same six ambiguity traps the native agent faces, plus
# the multi-tool routing cases, so the two agents are compared on like work.
EVAL_QUESTIONS = [
    # on-time definition (trap 1)
    "What was the on-time shipping rate for tenant T001 between 2025-03-01 and 2025-09-30?",
    # fill rate: three valid definitions (trap 2)
    "What is the line fill rate for tenant T002 between 2025-06-01 and 2025-12-31?",
    # units: eaches vs cartons vs lines (trap 3)
    "How many eaches were shipped between 2025-03-01 and 2025-09-30?",
    # cost requires the zone_rate_cards join (trap 4)
    "What is the average cost per shipment by carrier between 2025-06-01 and 2025-12-31?",
    # 4-4-5 fiscal calendar (trap 5)
    "How many orders were placed in fiscal period 7 of fiscal year 2025?",
    # active SKU definition (trap 6)
    "How many active SKUs were there in the 30 days ending 2026-03-31?",
    # multi-tool: structured + unstructured
    "Why did the Tuesday wave miss cutoff at ATL-DC1?",
    # search-only: should NOT write SQL
    "What counts as a short pick in our SOP?",
    # fuzzy literal matching via ITEM_CATALOG_SEARCH
    "Find SKUs similar to 'blue widget 12oz'",
]


def build_snowpark_session() -> Session:
    """Snowpark session from ~/.snowflake/connections.toml (no hardcoded creds)."""
    session = Session.builder.config("connection_name", CONNECTION_NAME).create()
    session.sql("USE DATABASE AGENT_EVAL_DEMO").collect()
    session.sql("USE SCHEMA AI").collect()
    session.sql("USE WAREHOUSE AGENT_EVAL_DEMO_WH").collect()
    return session


def run_harness_eval():
    print(f"[{datetime.now().isoformat()}] EXTERNAL_SIM run starting")
    print(f"  TRULENS_OTEL_TRACING={os.environ.get('TRULENS_OTEL_TRACING')}")

    session = build_snowpark_session()
    ip = session.sql("SELECT CURRENT_IP_ADDRESS()").collect()[0][0]
    acct = session.sql("SELECT CURRENT_ACCOUNT()").collect()[0][0]
    print(f"  account={acct} ip={ip}")
    # Optional egress-IP guard. If your account enforces a network policy that
    # only admits a specific address (a VPN egress, a bastion), set
    # SF_EXPECTED_EGRESS_IP and this fails fast instead of dying mid-run --
    # a partially-completed run still costs credits and leaves half a trace.
    # Unset by default, because no policy is assumed.
    expected_ip = os.environ.get("SF_EXPECTED_EGRESS_IP")
    if expected_ip and ip != expected_ip:
        raise SystemExit(
            f"ABORT: egress IP {ip} does not match SF_EXPECTED_EGRESS_IP "
            f"({expected_ip}). Check your network path before running."
        )

    connector = SnowflakeConnector(
        snowpark_session=session,
        database="AGENT_EVAL_DEMO",
        schema="AI",
    )
    tru_session = TruSession(connector=connector)

    # IMPORTANT: give the orchestrator its OWN raw connector connection rather
    # than session.connection. Snowpark sets paramstyle='qmark' on its
    # underlying connection, so the orchestrator's and tools' `%s` (pyformat)
    # bindings are never substituted and Snowflake fails with
    # "001003 (42000): syntax error ... unexpected '%'".
    raw_conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
    raw_cur = raw_conn.cursor()
    for stmt in ("USE DATABASE AGENT_EVAL_DEMO", "USE SCHEMA AI",
                 "USE WAREHOUSE AGENT_EVAL_DEMO_WH"):
        raw_cur.execute(stmt)
    raw_cur.close()
    print(f"  orchestrator paramstyle={snowflake.connector.paramstyle}")

    orchestrator = ExternalOrchestrator(raw_conn)

    # TruApp registration auto-creates the EXTERNAL AGENT object.
    tru_app = TruApp(
        orchestrator,
        app_name=APP_NAME,
        app_version=APP_VERSION,
        main_method=orchestrator.__call__,
        connector=connector,
    )

    results = []
    print(f"  running {len(EVAL_QUESTIONS)} questions")
    for i, question in enumerate(EVAL_QUESTIONS, 1):
        print(f"  [{i}/{len(EVAL_QUESTIONS)}] {question[:70]}")
        start = time.time()
        try:
            with tru_app as _:
                answer = orchestrator(question)
            elapsed = time.time() - start
            results.append((question, str(answer)[:500], round(elapsed, 2), "success"))
            print(f"      ok  {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - start
            results.append((question, None, round(elapsed, 2), f"error: {e}"))
            print(f"      ERR {type(e).__name__}: {e}")

    print("  flushing spans to AI_OBSERVABILITY_EVENTS")
    try:
        tru_session.force_flush()
    except Exception as e:
        print(f"    force_flush warning: {e}")
    time.sleep(10)

    ok = sum(1 for r in results if r[3] == "success")
    print(f"\n  {RUN_NAME}: {ok}/{len(results)} succeeded, "
          f"{sum(r[2] for r in results):.1f}s total")

    # Use raw_conn (pyformat), NOT session.connection (qmark) — otherwise the
    # %s placeholders below fail with "syntax error ... unexpected '%'".
    cur = raw_conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS AGENT_EVAL_DEMO.EVAL.EXTERNAL_V1_RESULTS (
                RUN_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
                QUESTION VARCHAR, ANSWER VARCHAR, ELAPSED_S FLOAT, STATUS VARCHAR
            )
        """)
        cur.executemany(
            "INSERT INTO AGENT_EVAL_DEMO.EVAL.EXTERNAL_V1_RESULTS "
            "(QUESTION, ANSWER, ELAPSED_S, STATUS) VALUES (%s, %s, %s, %s)",
            results,
        )
        print("  persisted to AGENT_EVAL_DEMO.EVAL.EXTERNAL_V1_RESULTS")
    finally:
        cur.close()

    # Prove the whole point of this script.
    n = session.sql(
        "SELECT COUNT(*) FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS("
        "'AGENT_EVAL_DEMO','AI','EXTERNAL_SIM','EXTERNAL AGENT'))"
    ).collect()[0][0]
    print(f"  EXTERNAL AGENT observability events now visible: {n}")
    if n == 0:
        print("  WARNING: 0 events — traces did NOT reach the event table.")

    raw_conn.close()
    session.close()
    print(f"[{datetime.now().isoformat()}] done")
    return results


if __name__ == "__main__":
    run_harness_eval()
