/*=============================================================================
  06b_eval_optimized.sql — Re-evaluate the optimized v2 semantic view.

  SPLIT OUT OF 06_semantic_v2.sql ON PURPOSE. That file must contain the v2
  CREATE SEMANTIC VIEW statement and NOTHING else: tests/test_08_repro.py
  extracts it and diffs it against the live object to detect drift, so any
  trailing statements break drift detection.

  Run this immediately after 06_semantic_v2.sql.

  PREREQUISITES:
    - 06_semantic_v2.sql executed (FULFILLMENT_SV now holds the v2 definition)
    - 05_eval_baseline.sql executed (BASELINE_V1_FINAL_RESULTS populated)
    - @EVAL.CONFIGS/analyst_evaluation_config.yaml uploaded

  MEASURED: 0.650 on the primary demo account; 0.625 on the a second account rebuild 2026-08-14.
=============================================================================*/

------------------------------------------------------------
-- RE-EVALUATE v2 (run name OPTIMIZED_V2_FINAL)
------------------------------------------------------------
-- This step used to exist only in someone's SQL worksheet. Without it, a
-- rebuilt account has a baseline and no optimized run, so the whole Act 3
-- before/after comparison -- and the Act 6 notebook gate -- has nothing to
-- read. Verified live on a second account 2026-08-14.
--
-- Session context is mandatory and must match where the semantic view lives,
-- or metric computation resolves against AGENT_EVAL_DEMO.PUBLIC and silently
-- produces zero metric records.
USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

CALL EXECUTE_AI_EVALUATION(
    'START',
    OBJECT_CONSTRUCT('run_name', 'OPTIMIZED_V2_FINAL'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/analyst_evaluation_config.yaml'
);

-- Asynchronous: START returns immediately. Wait, then confirm STATUS is
-- exactly COMPLETED (not INGESTION_COMPLETED, not COMPUTATION_IN_PROGRESS)
-- before snapshotting, or the table below lands empty and the script still
-- exits 0.
CALL SYSTEM$WAIT(300);

CALL EXECUTE_AI_EVALUATION(
    'STATUS',
    OBJECT_CONSTRUCT('run_name', 'OPTIMIZED_V2_FINAL'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/analyst_evaluation_config.yaml'
);

-- Canonical name: the Streamlit app and the Act 6 notebook both read
-- EVAL.OPTIMIZED_V2_FINAL_RESULTS.
CREATE OR REPLACE TABLE EVAL.OPTIMIZED_V2_FINAL_RESULTS AS
SELECT * FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
    'AGENT_EVAL_DEMO', 'AI', 'FULFILLMENT_SV', 'SEMANTIC VIEW', 'OPTIMIZED_V2_FINAL'
));

SELECT CASE WHEN COUNT(*) = 0
            THEN 1/0  -- forces an error: run did not finish before snapshot
            ELSE COUNT(*) END AS scored_rows
FROM EVAL.OPTIMIZED_V2_FINAL_RESULTS;

------------------------------------------------------------
-- THE ACT 3 PAYOFF: before/after with regression count
------------------------------------------------------------
-- Deduplicate on INPUT_ID. A single question can receive more than one metric
-- row (observed live: 21 rows for 20 verified queries), which both skews the
-- average and fans out this join.
WITH b AS (
    SELECT INPUT_ID, MAX(EVAL_AGG_SCORE) AS s
    FROM EVAL.BASELINE_V1_FINAL_RESULTS
    WHERE METRIC_NAME = 'sql_correctness' GROUP BY INPUT_ID
), o AS (
    SELECT INPUT_ID, MAX(EVAL_AGG_SCORE) AS s
    FROM EVAL.OPTIMIZED_V2_FINAL_RESULTS
    WHERE METRIC_NAME = 'sql_correctness' GROUP BY INPUT_ID
)
SELECT
    COUNT(*) AS questions,
    COUNT(CASE WHEN o.s > b.s THEN 1 END) AS improved,
    COUNT(CASE WHEN o.s < b.s THEN 1 END) AS regressed,
    COUNT(CASE WHEN o.s = b.s THEN 1 END) AS unchanged,
    ROUND(AVG(b.s), 4) AS baseline_avg,
    ROUND(AVG(o.s), 4) AS optimized_avg,
    ROUND((AVG(o.s) - AVG(b.s)) / NULLIF(AVG(b.s), 0) * 100, 1) AS pct_improvement
FROM b JOIN o USING (INPUT_ID);
