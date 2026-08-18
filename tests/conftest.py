"""Shared pytest fixtures and constants for the agent-eval demo suite.

TIERS (select with -m):
    preflight   fast go/no-go before a customer call (<60s, no eval runs)
    data        row counts, timestamp sanity, the six ambiguity traps
    governance  row access policy + tenant role isolation
    search      Cortex Search services serving
    semantic    semantic view structure, v1-vs-v2, verified query parity
    evals       persisted evaluation runs and their scores
    agents      native + external agent traces, GPA metrics
    claims      CLAIM AUDIT: every number in the docs backed by live evidence
    repro       committed .sql reproduces the live objects
    offline     pure python, NO Snowflake needed (safe for CI)

Everything except `offline` needs a reachable Snowflake account. Those tests are
skipped (not failed) when Snowflake is unreachable, so a clone with no connection
configured still gets a clean offline run -- which is what CI executes.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CONNECTION_NAME = os.environ.get("SF_CONNECTION", "my_snowflake_connection")
# Pin the account the live tests expect alongside the connection so both move
# together. Unset by default: there is no account this repo can assume.
EXPECTED_ACCOUNT = os.environ.get("SF_ACCOUNT", "")

DATABASE = "AGENT_EVAL_DEMO"
WAREHOUSE = "AGENT_EVAL_DEMO_WH"
AI_SCHEMA = f"{DATABASE}.AI"

# ---------------------------------------------------------------------------
# Canonical facts. These are the ONLY numbers the demo is allowed to claim.
# Ranges (not exact values) per the score-assertion policy: LLM judges are
# non-deterministic, so a re-run will not reproduce a stored score exactly.
# Doc-vs-live EXACT drift detection lives in test_07_claims.py instead, which
# compares the demo guide's written numbers against what the account reports.
# ---------------------------------------------------------------------------

EXPECTED_ROWS = {
    "FULFILLMENT_INTELLIGENCE.ORDERS": 40_000,
    "FULFILLMENT_INTELLIGENCE.ORDER_LINES": 211_501,
    "FULFILLMENT_INTELLIGENCE.WAVES": 4_204,
    "FULFILLMENT_INTELLIGENCE.EXCEPTIONS": 2_000,
    "FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445": 514,
    "INVENTORY_INTELLIGENCE.ITEM_MASTER": 8_000,
    "INVENTORY_INTELLIGENCE.ON_HAND": 96_000,
    "INVENTORY_INTELLIGENCE.MOVEMENTS": 139_683,
    "LABOR_INTELLIGENCE.PICK_TASKS": 55_737,
    "LABOR_INTELLIGENCE.LABOR_STANDARDS": 20,
    "SHIPPING_INTELLIGENCE.SHIPMENTS": 37_821,
    "SHIPPING_INTELLIGENCE.CARRIER_SCANS": 162_574,
    "SHIPPING_INTELLIGENCE.ZONE_RATE_CARDS": 240,
}

# generate_data.py is deterministic within one environment, but the data loaded
# on the original account came from an earlier, partially re-run version of the generator.
# Rebuilding from scratch on a second account reproduced the fixed-cardinality tables
# EXACTLY and moved the derived ones by at most 0.48%:
#   ORDER_LINES 211,583 vs 211,501   WAVES 4,184 vs 4,204
#   MOVEMENTS  139,270 vs 139,683    PICK_TASKS 55,669 vs 55,737
#   SHIPMENTS   37,785 vs  37,821    CARRIER_SCANS 162,458 vs 162,574
# The two totals you will see quoted therefore differ by 571 rows, and both are
# real: EXPECTED_ROWS below sums to 758,294 (the original account), while a fresh
# build from the committed generator sums to 757,723 -- the figure README.md and
# docs/SETUP.md quote, and the one reproduced byte-identically on two clouds.
# These tables are sized by random draws (lines per order, scans per shipment),
# so pin them to a tolerance instead of a literal. 2% still catches every real
# load failure -- a missing or truncated parquet file is off by orders of
# magnitude, not fractions of a percent.
VARIABLE_CARDINALITY_TABLES = {
    "FULFILLMENT_INTELLIGENCE.ORDER_LINES",
    "FULFILLMENT_INTELLIGENCE.WAVES",
    "INVENTORY_INTELLIGENCE.MOVEMENTS",
    "LABOR_INTELLIGENCE.PICK_TASKS",
    "SHIPPING_INTELLIGENCE.SHIPMENTS",
    "SHIPPING_INTELLIGENCE.CARRIER_SCANS",
}
ROW_COUNT_TOLERANCE_PCT = 2.0

# Canonical eval runs -> (metric, low, high). Bands are deliberately wide
# enough to survive judge non-determinism but tight enough to catch a real
# regression or a mis-pointed run (e.g. a "baseline" accidentally scoring v2).
EXPECTED_RUNS = {
    # Measured n=5 on the primary demo account 2026-08-17 AFTER fixing the undeclared
    # ZONE_RATE_CARDS in FULFILLMENT_SV_V1: 0.450, 0.450, 0.400, 0.450, 0.450.
    # The pre-fix 0.325 came from a view that failed to load, which zeroed the 3
    # cost questions for a structural reason. Floor kept below 0.40 for judge
    # slack; ceiling below the optimized floor so a mis-pointed run still fails.
    "BASELINE_V1_FINAL": {"sql_correctness": (0.34, 0.50)},
    # Measured n=5 on the UNCHANGED v2 view: 0.525, 0.625, 0.650, 0.625, 0.525.
    # That spread (0.125) is over 2x the old JUDGE_TOL of 0.06, which is why the
    # docs now lead with a band instead of a decimal. Floor must stay above the
    # baseline ceiling so "optimized" can never pass while scoring like v1.
    "OPTIMIZED_V2_FINAL": {"sql_correctness": (0.51, 0.75)},
    "AGENT_V4": {
        "answer_correctness": (0.65, 0.95),
        "logical_consistency": (0.80, 1.00),
        "tool_selection_accuracy": (0.45, 0.80),
        # 0.00 is the CORRECT value here, not a failure: the ground truth
        # supplies only tool_name, and TEA grades tool input/output quality.
        # README.md and docs/DEMO_GUIDE.md both state this. The band starts at
        # 0.0 so a legitimate 0.00 passes while a missing metric still fails.
        "tool_execution_accuracy": (0.00, 0.80),
    },
    "TENANT_ISOLATION_V2": {"tenant_isolation": (2.5, 6.0)},
}

# Metrics whose value depends on which TOOL the agent chose to call, not on
# whether the build is correct. Verified on an independent second-account rebuild:
# with a byte-identical agent spec, tool_execution_accuracy went 0.57 -> 0.00
# purely because the agent reached for fulfillment_data/knowledge_base instead
# of shipping_data. Bands for these are only enforced on CLAIMS_ACCOUNT; other
# accounts still require the metric to be present and in range.
ROUTER_DEPENDENT_METRICS = {"tool_execution_accuracy"}
# The account where the canonical score bands in this file were measured.
# Score-band assertions run ONLY when SF_ACCOUNT == SF_CLAIMS_ACCOUNT, so a
# fresh clone on someone else's account skips them instead of failing on
# numbers it never produced. Set both to your own account once you have
# measured your own bands.
CLAIMS_ACCOUNT = os.environ.get("SF_CLAIMS_ACCOUNT", "")

# Which semantic view each analyst run is stored under. SINGLE SOURCE OF TRUTH.
#
# The baseline lives on the FROZEN view, not FULFILLMENT_SV. Step G replaced
# FULFILLMENT_SV in place with the optimized v2 definition, so a baseline run
# targeting FULFILLMENT_SV is not reproducible -- re-running it scores v2 and
# reports ~0.70 instead of ~0.325. Pointing the baseline at FULFILLMENT_SV_V1
# makes the before/after comparison independent of mutation order.
#
# Both views carry the SAME 20 verified queries, which is what makes the
# comparison honest; test_04_semantic asserts that question sets are identical.
ANALYST_RUN_VIEWS = {
    "BASELINE_V1_FINAL": "FULFILLMENT_SV_V1",
    "OPTIMIZED_V2_FINAL": "FULFILLMENT_SV",
}

# Runs that exist but MUST NOT be quoted: scored against a different question
# set, or instrumentation experiments. Guards against demo-guide drift.
SUPERSEDED_RUNS = {"BASELINE_V1", "BASELINE_V1_R2", "BASELINE_V1_R3",
                   "BASELINE_V1_R4", "BASELINE_V1_REBALANCED",
                   "BASELINE_V1_TEST", "BASELINE_V1_TEST2",
                   "OPTIMIZED_V2", "OPTIMIZED_V2_R2",
                   "AGENT_V1", "AGENT_V2", "AGENT_V3",
                   "TENANT_ISOLATION_V1"}

SEMANTIC_VIEWS = {"FULFILLMENT_SV", "FULFILLMENT_SV_V1", "SHIPPING_SV"}
SEARCH_SERVICES = {"ITEM_CATALOG_SEARCH", "OPS_KNOWLEDGE_SEARCH"}
NATIVE_AGENT = "FULFILLMENT_ANALYST"
EXTERNAL_AGENT = "EXTERNAL_SIM"
TENANT_ROLES = {"TENANT_ALDERWOOD": "T001", "TENANT_BELLWEATHER": "T002"}
RAP_NAME = f"{DATABASE}.OPS.TENANT_ISOLATION_POLICY"
RAP_TABLE_COUNT = 8

# Measured ambiguity-trap spreads. If these collapse, the demo's premise is
# gone: the failures would no longer come from genuine business ambiguity.
TRAP_TOLERANCE = 1.5  # percentage points


# ---------------------------------------------------------------------------
# Snowflake connection
# ---------------------------------------------------------------------------

_conn_error: str | None = None


def _connect():
    """Open a session with context set. Returns (conn, None) or (None, reason)."""
    global _conn_error
    try:
        import snowflake.connector
    except ImportError as e:  # pragma: no cover
        return None, f"snowflake-connector-python not installed: {e}"
    try:
        conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
        cur = conn.cursor()
        for stmt in (
            "USE ROLE ACCOUNTADMIN",
            f"USE DATABASE {DATABASE}",
            "USE SCHEMA AI",
            f"USE WAREHOUSE {WAREHOUSE}",
        ):
            cur.execute(stmt)
        cur.close()
        return conn, None
    except Exception as e:
        msg = str(e)
        if "390422" in msg:
            msg = (
                "Snowflake rejected the connection with 390422 (network policy). "
                "The account enforces a network policy that does not admit this "
                "egress address. See docs/GOTCHAS.md #11. Original: "
                + msg[:200]
            )
        _conn_error = msg
        return None, msg


@pytest.fixture(scope="session")
def sf():
    """Session-scoped Snowflake connection. Skips the test if unreachable."""
    conn, reason = _connect()
    if conn is None:
        pytest.skip(f"Snowflake unavailable: {reason}")
    yield conn
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_session_context(request):
    """Make every live test order-independent.

    Tests that exercise tenant isolation must `USE ROLE TENANT_*` on a real
    session. Snowflake drops the current DATABASE/SCHEMA/WAREHOUSE when the role
    changes, and restoring only the ROLE does NOT restore them -- so without
    this, every test that ran *after* a governance test failed with
    "Cannot perform SELECT. This session does not have a current database."

    Symptom if you remove this: test_01_data passes 27/27 in isolation but fails
    in the full suite. Order-dependent test suites are worse than no tests, so
    the context is re-established before each test rather than trusting cleanup.
    """
    if "sf" not in request.fixturenames:
        yield  # offline test, nothing to reset
        return
    conn = request.getfixturevalue("sf")
    cur = conn.cursor()
    try:
        for stmt in (
            "USE ROLE ACCOUNTADMIN",
            f"USE DATABASE {DATABASE}",
            "USE SCHEMA AI",
            f"USE WAREHOUSE {WAREHOUSE}",
        ):
            cur.execute(stmt)
    finally:
        cur.close()
    yield


@pytest.fixture(scope="session")
def q(sf):
    """Query helper: q('select 1') -> list[tuple]."""

    def _q(sql: str, params: tuple | None = None):
        cur = sf.cursor()
        try:
            cur.execute(sql, params) if params else cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.close()

    return _q


@pytest.fixture(scope="session")
def scalar(q):
    """First column of the first row, or None."""

    def _scalar(sql: str, params: tuple | None = None):
        rows = q(sql, params)
        return rows[0][0] if rows else None

    return _scalar


@pytest.fixture(scope="session")
def analyst_eval(q):
    """Rows from GET_ANALYST_AI_EVALUATION_DATA for a semantic-view run."""

    def _f(run_name: str, view: str | None = None):
        # Resolve the view from ANALYST_RUN_VIEWS so callers cannot silently
        # point a baseline run at the optimized view (or vice versa).
        if view is None:
            view = ANALYST_RUN_VIEWS.get(run_name, "FULFILLMENT_SV")
        return q(
            f"""SELECT INPUT, METRIC_NAME, EVAL_AGG_SCORE
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','{view}','SEMANTIC VIEW','{run_name}'))
                WHERE METRIC_NAME IS NOT NULL"""
        )

    return _f


@pytest.fixture(scope="session")
def agent_events(q):
    """Rows from GET_AI_OBSERVABILITY_EVENTS_NORMALIZED for an agent."""

    def _f(name: str, agent_type: str = "CORTEX AGENT", cols: str = "*"):
        return q(
            f"""SELECT {cols}
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                    '{DATABASE}','AI','{name}','{agent_type}'))"""
        )

    return _f


@pytest.fixture(scope="session")
def demo_guide_text():
    return (REPO / "docs" / "DEMO_GUIDE.md").read_text()


@pytest.fixture(scope="session")
def readme_text():
    return (REPO / "README.md").read_text()


def ddl_normalize(ddl: str) -> str:
    """Collapse whitespace/case so committed DDL can be compared to GET_DDL.

    GET_DDL emits tabs and its own line breaks, so a byte comparison against a
    hand-edited file always fails. We compare semantic content only.
    """
    s = ddl.strip().rstrip(";")
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)   # block comments
    s = re.sub(r"--[^\n]*", " ", s)                 # line comments
    s = re.sub(r"\s+", " ", s)
    return s.lower()
