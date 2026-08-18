/*
 * 08_external_agent.sql — External Agent (EXTERNAL_SIM) + side-by-side trace query
 *
 * The EXTERNAL AGENT object is auto-created by TruLens when python/external_sim/run.py
 * executes. This file contains:
 *   1. Manual CREATE EXTERNAL AGENT DDL (fallback if TruLens auto-create fails)
 *   2. The KEY DELIVERABLE: one query returning native + external agent traces side by side
 *   3. Comparison views for the observability Streamlit app
 *
 * Addresses the "are we boxed in on architecture?" objection: an external
 * orchestrator stays exactly where it is and still gets identical evals and
 * traces, alongside the native agent, in one pane.
 */

USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

-- =============================================================================
-- 1. External Agent registration (fallback — TruLens normally creates this)
-- =============================================================================
-- NOTE: EXTERNAL AGENT shares namespace with model objects. If EXTERNAL_SIM
-- already exists (from TruLens auto-create), this is a no-op via IF NOT EXISTS.
-- COMMENT is not supported in CREATE EXTERNAL AGENT; add via ALTER after.
CREATE EXTERNAL AGENT IF NOT EXISTS AGENT_EVAL_DEMO.AI.EXTERNAL_SIM;

-- NOTE: Snowflake does NOT concatenate adjacent string literals the way Python
-- and C do. Writing 'part one ' 'part two' is a syntax error (001003), so this
-- comment must be one literal or use explicit || concatenation.
COMMENT ON EXTERNAL AGENT AGENT_EVAL_DEMO.AI.EXTERNAL_SIM IS
    'ADK-shaped external orchestrator (planner->router->responder). Instrumented via TruLens >=2.1.2. Calls the same semantic views + search services as the native FULFILLMENT_ANALYST agent.';

-- Grant USAGE so observability queries work for non-admin roles
GRANT USAGE ON EXTERNAL AGENT AGENT_EVAL_DEMO.AI.EXTERNAL_SIM TO ROLE ACCOUNTADMIN;

-- =============================================================================
-- 2. KEY DELIVERABLE: Native + External agent traces SIDE BY SIDE
-- =============================================================================
-- This single query is the entire "you are not boxed in" argument.
-- It proves that no matter WHERE the orchestrator runs (Snowflake-native or
-- external the external orchestrator on GCP), the traces land in the same observability layer.
--
-- Verified column names from GET_AI_OBSERVABILITY_EVENTS_NORMALIZED (46 cols):
-- TIMESTAMP, START_TIMESTAMP, DURATION_MS, RECORD_TYPE, RECORD_ID, INPUT_ID,
-- THREAD_ID, SPAN_TYPE, SPAN_NAME, SPAN_ID, PARENT_SPAN_ID, RUN_ID, RUN_NAME,
-- REQUEST_ID, LLM_INPUT_TOKENS, LLM_OUTPUT_TOKENS, EVAL_ROOT_ID,
-- TARGET_RECORD_ID, METRIC_NAME, EVAL_AGG_SCORE, METRIC_TYPE, METRIC_CRITERIA,
-- METRIC_EXPLANATION, EVAL_SCORE, METRIC_STATUS, METRIC_METADATA, INPUT, OUTPUT,
-- ERROR, GROUND_TRUTH, GROUND_TRUTH_FROM_SPAN, FIRST_INPUT, MESSAGES_JSON,
-- AGENT_VERSION, USER_NAME, ROLE_NAME, LLM_MODEL, LLM_REASONING, STATUS,
-- FEEDBACK_POSITIVE, FEEDBACK_MESSAGE, RECORD, RECORD_ATTRIBUTES,
-- RESOURCE_ATTRIBUTES, VALUE, TRACE

CREATE OR REPLACE VIEW AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE AS
WITH native_traces AS (
    SELECT
        'CORTEX AGENT' AS AGENT_TYPE,
        'FULFILLMENT_ANALYST' AS AGENT_NAME,
        TIMESTAMP,
        START_TIMESTAMP,
        DURATION_MS,
        SPAN_TYPE,
        SPAN_NAME,
        SPAN_ID,
        PARENT_SPAN_ID,
        THREAD_ID,
        RUN_NAME,
        LLM_INPUT_TOKENS,
        LLM_OUTPUT_TOKENS,
        LLM_MODEL,
        STATUS,
        ERROR,
        INPUT,
        OUTPUT,
        METRIC_NAME,
        EVAL_AGG_SCORE,
        METRIC_TYPE,
        METRIC_CRITERIA,
        METRIC_EXPLANATION,
        USER_NAME,
        ROLE_NAME,
        FEEDBACK_POSITIVE,
        FEEDBACK_MESSAGE
    FROM TABLE(
        SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
            'AGENT_EVAL_DEMO', 'AI', 'FULFILLMENT_ANALYST', 'CORTEX AGENT'
        )
    )
),
external_traces AS (
    SELECT
        'EXTERNAL AGENT' AS AGENT_TYPE,
        'EXTERNAL_SIM' AS AGENT_NAME,
        TIMESTAMP,
        START_TIMESTAMP,
        DURATION_MS,
        SPAN_TYPE,
        SPAN_NAME,
        SPAN_ID,
        PARENT_SPAN_ID,
        THREAD_ID,
        RUN_NAME,
        LLM_INPUT_TOKENS,
        LLM_OUTPUT_TOKENS,
        LLM_MODEL,
        STATUS,
        ERROR,
        INPUT,
        OUTPUT,
        METRIC_NAME,
        EVAL_AGG_SCORE,
        METRIC_TYPE,
        METRIC_CRITERIA,
        METRIC_EXPLANATION,
        USER_NAME,
        ROLE_NAME,
        FEEDBACK_POSITIVE,
        FEEDBACK_MESSAGE
    FROM TABLE(
        SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
            'AGENT_EVAL_DEMO', 'AI', 'EXTERNAL_SIM', 'EXTERNAL AGENT'
        )
    )
)
SELECT * FROM native_traces
UNION ALL
SELECT * FROM external_traces;

COMMENT ON VIEW AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE IS
    'Unified native + external agent trace view. One result set proves "you are not boxed in" — the external orchestrator stays, gets identical observability.';

-- =============================================================================
-- 3. Summary comparison: KPIs side by side
-- =============================================================================
CREATE OR REPLACE VIEW AGENT_EVAL_DEMO.OPS.AGENT_COMPARISON_SUMMARY AS
SELECT
    AGENT_TYPE,
    AGENT_NAME,
    COUNT(*) AS TOTAL_SPANS,
    COUNT(DISTINCT RUN_NAME) AS DISTINCT_RUNS,
    AVG(DURATION_MS) AS AVG_DURATION_MS,
    MEDIAN(DURATION_MS) AS P50_DURATION_MS,
    APPROX_PERCENTILE(DURATION_MS, 0.95) AS P95_DURATION_MS,
    SUM(LLM_INPUT_TOKENS) AS TOTAL_INPUT_TOKENS,
    SUM(LLM_OUTPUT_TOKENS) AS TOTAL_OUTPUT_TOKENS,
    SUM(COALESCE(LLM_INPUT_TOKENS,0) + COALESCE(LLM_OUTPUT_TOKENS,0)) AS TOTAL_TOKENS,
    COUNT_IF(STATUS = 'ERROR') AS ERROR_COUNT,
    COUNT_IF(METRIC_NAME IS NOT NULL) AS EVAL_METRIC_COUNT,
    AVG(CASE WHEN METRIC_NAME IS NOT NULL THEN EVAL_AGG_SCORE END) AS AVG_EVAL_SCORE
FROM AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE
GROUP BY AGENT_TYPE, AGENT_NAME;

COMMENT ON VIEW AGENT_EVAL_DEMO.OPS.AGENT_COMPARISON_SUMMARY IS
    'Aggregate KPIs for native vs external agent: latency, tokens, errors, eval scores.';

-- =============================================================================
-- 4. Tool usage comparison
-- =============================================================================
CREATE OR REPLACE VIEW AGENT_EVAL_DEMO.OPS.AGENT_TOOL_USAGE_COMPARISON AS
SELECT
    AGENT_TYPE,
    AGENT_NAME,
    SPAN_NAME AS TOOL_NAME,
    COUNT(*) AS INVOCATION_COUNT,
    AVG(DURATION_MS) AS AVG_DURATION_MS,
    COUNT_IF(STATUS = 'ERROR') AS ERROR_COUNT,
    ROUND(COUNT_IF(STATUS = 'ERROR') / NULLIF(COUNT(*), 0) * 100, 1) AS ERROR_RATE_PCT
FROM AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE
WHERE SPAN_TYPE = 'TOOL'
GROUP BY AGENT_TYPE, AGENT_NAME, SPAN_NAME
ORDER BY AGENT_TYPE, INVOCATION_COUNT DESC;

-- =============================================================================
-- 5. Ad-hoc query for the demo (standalone, no view dependency)
-- =============================================================================
-- Copy-paste this into Snowsight for the live demo moment:
/*
SELECT
    AGENT_TYPE,
    AGENT_NAME,
    SPAN_TYPE,
    SPAN_NAME,
    DURATION_MS,
    COALESCE(LLM_INPUT_TOKENS, 0) + COALESCE(LLM_OUTPUT_TOKENS, 0) AS TOTAL_TOKENS,
    STATUS,
    SUBSTR(INPUT, 1, 100) AS INPUT_PREVIEW,
    SUBSTR(OUTPUT, 1, 100) AS OUTPUT_PREVIEW,
    TIMESTAMP
FROM AGENT_EVAL_DEMO.OPS.AGENT_TRACES_SIDE_BY_SIDE
ORDER BY TIMESTAMP DESC
LIMIT 50;
*/

-- =============================================================================
-- 6. Eval results table (populated by python/external_sim/run.py)
-- =============================================================================
CREATE TABLE IF NOT EXISTS AGENT_EVAL_DEMO.EVAL.EXTERNAL_V1_RESULTS (
    RUN_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    QUESTION VARCHAR,
    ANSWER VARCHAR,
    ELAPSED_S FLOAT,
    STATUS VARCHAR
);
