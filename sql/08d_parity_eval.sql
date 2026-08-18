/*=============================================================================
  08d_parity_eval.sql — the apples-to-apples native-vs-external comparison

  This is the script behind the headline result in README.md: the native Cortex
  Agent and the external orchestrator scored on the SAME five metrics, over the
  SAME nine questions, with the SAME orchestration model and the SAME judge.

  Run it AFTER 08c_shared_eval_dataset.sql, which builds
  EVAL.SHARED_EVAL_DATASET. This script evaluates the NATIVE side; the external
  side comes from python/external_sim/score.py. Neither half means anything
  without the other -- the whole point is the comparison.

  WHAT MAKES IT APPLES-TO-APPLES (all three must hold, or delete the claim):
    1. MODEL     both sides pinned to claude-opus-4-8. Native: the
                 models.orchestration field in 07_agent.sql. External:
                 ORCHESTRATION_MODEL in python/external_sim/orchestrator.py.
                 Leaving orchestration as "" lets Snowflake choose, which
                 silently invalidates the comparison.
    2. JUDGE     claude-4-sonnet on both sides. NOT settable here --
                 llm_judge_name is a TruLens RunConfig field and
                 EXECUTE_AI_EVALUATION rejects it with
                 'Unrecognized field "llm_judge_name"'. So parity is achieved
                 from the other direction: score.py is pinned to match whatever
                 the agent eval actually used. Measure it, never assume it.
    3. QUESTIONS the same nine, from EVAL.SHARED_EVAL_DATASET.

  The five custom metrics here return the RAW 0-3 rubric scale, not 0-1:
  METRIC_CALLS[0]:full_metadata:normalized_score is NULL for custom metrics.
  Divide by 3 before comparing against the external agent's TruLens metrics,
  which are natively 0-1. The final SELECT does this.

  A Snowflake Dataset is a VERSIONED SNAPSHOT. If you change ANYTHING in
  EVAL.SHARED_EVAL_DATASET -- the questions OR the ground truth -- you MUST bump
  dataset_name in agent_evaluation_config_v5_parity.yaml AND the run_name here,
  or the run fails with 210007, or worse, silently scores against the old
  snapshot. This bit for real: correcting a stale numeric gold changes the table
  but not an existing dataset, so the corrected run scored the stale golds again
  until the name was bumped. Current: SHARED_EVAL_DATASET_V4 / PARITY_AGENT_V4.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

-- Native side. Asynchronous: START returns immediately.
CALL EXECUTE_AI_EVALUATION(
    'START',
    OBJECT_CONSTRUCT('run_name', 'PARITY_AGENT_V4'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/agent_evaluation_config_v5_parity.yaml'
);

-- Nine questions x nine metrics takes longer than the 4-metric AGENT_V4 run.
CALL SYSTEM$WAIT(600);

CALL EXECUTE_AI_EVALUATION(
    'STATUS',
    OBJECT_CONSTRUCT('run_name', 'PARITY_AGENT_V4'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/agent_evaluation_config_v5_parity.yaml'
);

-- Snapshot: CREATE OR REPLACE AGENT destroys all AI observability history
-- (GOTCHAS #13), so the live GET_AI_EVALUATION_DATA call is not a durable
-- record. The snapshot table is.
CREATE OR REPLACE TABLE EVAL.PARITY_AGENT_V4_RESULTS AS
SELECT * FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
    'AGENT_EVAL_DEMO', 'AI', 'FULFILLMENT_ANALYST', 'CORTEX AGENT', 'PARITY_AGENT_V4'
));

SELECT CASE WHEN COUNT(*) = 0
            THEN 1/0  -- forces an error: the run had not finished
            ELSE COUNT(*) END AS scored_rows
FROM EVAL.PARITY_AGENT_V4_RESULTS;

-- The comparison, on the 0-1 scale the external agent's TruLens metrics use.
--
-- CRITICAL SCALE TRAP -- two scales coexist in this one result table, and
-- normalizing the wrong one inverts the headline result:
--
--   * Custom metrics declared with score_ranges on 0-3 return the RAW 0-3
--     value in EVAL_AGG_SCORE. Divide by 3. (coherence, answer_relevance,
--     context_relevance, groundedness -- observed values 2 and 3.)
--   * A custom metric named `correctness` does NOT. Despite being declared
--     with identical score_ranges, it comes back ALREADY normalized to 0-1
--     (observed values 0.33 / 0.67 / 1.00, and per-question scores identical
--     to the built-in answer_correctness). The name collides with the built-in
--     answer_correctness metric family. Dividing it by 3 a second time
--     understates it threefold.
--   * The four GPA system metrics are natively 0-1. Never divide those.
--
-- This bit hard: dividing `correctness` by 3 turned 0.852 into 0.284 and made
-- the external agent look better than the native one. Detect the scale, do not
-- assume it. In THIS run the only ranges present are 0-1 and 0-3, so MAX > 1
-- implies 0-3. That shortcut is NOT general: tenant_isolation declares 1-10,
-- so dividing its 4.0833 by 3 yields 1.361. See docs/GOTCHAS.md #25 for the
-- ceiling ladder to use when a query spans runs with different declared ranges.
SELECT
    LOWER(METRIC_NAME)                        AS metric,
    COUNT(*)                                  AS n,
    ROUND(AVG(EVAL_AGG_SCORE), 4)             AS raw_score,
    ROUND(MAX(EVAL_AGG_SCORE), 2)             AS max_seen,
    CASE WHEN MAX(EVAL_AGG_SCORE) > 1
         THEN ROUND(AVG(EVAL_AGG_SCORE) / 3.0, 4)
         ELSE ROUND(AVG(EVAL_AGG_SCORE), 4)
    END                                       AS normalized_0_1
FROM EVAL.PARITY_AGENT_V4_RESULTS
WHERE METRIC_NAME IS NOT NULL
  AND LOWER(METRIC_NAME) IN (
      'coherence', 'answer_relevance', 'correctness',
      'context_relevance', 'groundedness')
GROUP BY LOWER(METRIC_NAME)
ORDER BY metric;
