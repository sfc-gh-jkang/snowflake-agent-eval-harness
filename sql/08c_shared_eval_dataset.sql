-- ============================================================================
-- 08c_shared_eval_dataset.sql — ONE question set for BOTH agents
-- ============================================================================
-- Removes the biggest confound in the native-vs-external comparison. Measured
-- before this file: the agent was scored on 10 questions, the external agent on
-- 9, and only ONE question was shared verbatim. Two agents graded on different
-- questions cannot be compared, no matter how the metrics line up.
--
-- One table, two consumers, different columns:
--   GROUND_TRUTH_TEXT  VARCHAR  -> external agent (RECORD_ROOT.GROUND_TRUTH_OUTPUT)
--   GROUND_TRUTH       VARIANT  -> native agent  (ground_truth_output +
--                                  ground_truth_invocations for TSA/TEA)
--
-- The 9 questions are the harness set, because they are the six ambiguity traps
-- plus the three routing cases, and their numeric golds were computed from the
-- semantic view's own VERIFIED QUERY SQL (see 08b_harness_scoring.sql for the
-- per-value provenance). Nothing here is estimated.
--
-- ground_truth_invocations tool names MUST match the TRACE tool names, which are
-- lowercase snake_case: fulfillment_data, shipping_data, product_search,
-- knowledge_base.
--
-- MEASURED, and counter-intuitive: sql/07_agent.sql declares the tools with
-- DISPLAY names ("Fulfillment Data"), but the trace records the normalized
-- snake_case form. Using the display names scored tool_selection_accuracy 0.00
-- on every question, with the judge reporting:
--   Missing: [`fulfillment data`]   Extra: [`fulfillment_data` (+1)]
-- Do not "correct" these to the display names -- that is a regression from
-- 0.633/0.57 to 0.00/0.00.
-- ============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

CREATE OR REPLACE TABLE EVAL.SHARED_EVAL_DATASET (
    INPUT_QUERY        VARCHAR NOT NULL,
    GROUND_TRUTH_TEXT  VARCHAR NOT NULL,   -- external agent
    GROUND_TRUTH       VARIANT NOT NULL,   -- native agent
    TRAP               VARCHAR,
    GOLD_SOURCE        VARCHAR
)
COMMENT = 'Single shared question set for BOTH the native Cortex Agent and the external TruLens-instrumented agent, so the two are comparable. Numeric golds computed from verified-query SQL 2026-08-18.';

INSERT INTO EVAL.SHARED_EVAL_DATASET
  (INPUT_QUERY, GROUND_TRUTH_TEXT, GROUND_TRUTH, TRAP, GOLD_SOURCE)
SELECT
  d.INPUT_QUERY,
  d.GROUND_TRUTH,
  OBJECT_CONSTRUCT(
      'ground_truth_output', d.GROUND_TRUTH,
      'ground_truth_invocations', m.INVOCATIONS
  )::VARIANT,
  d.TRAP,
  d.GOLD_SOURCE
FROM EVAL.EXTERNAL_EVAL_DATASET d
JOIN (
    SELECT 'What was the on-time shipping rate for tenant T001 between 2025-03-01 and 2025-09-30?' AS q,
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','shipping_data')) AS INVOCATIONS
    UNION ALL SELECT 'What is the line fill rate for tenant T002 between 2025-06-01 and 2025-12-31?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','fulfillment_data'))
    UNION ALL SELECT 'How many eaches were shipped between 2025-03-01 and 2025-09-30?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','fulfillment_data'))
    UNION ALL SELECT 'What is the average cost per shipment by carrier between 2025-06-01 and 2025-12-31?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','shipping_data'))
    UNION ALL SELECT 'How many orders were placed in fiscal period 7 of fiscal year 2025?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','fulfillment_data'))
    UNION ALL SELECT 'How many active SKUs were there in the 30 days ending 2026-03-31?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','fulfillment_data'))
    -- Multi-tool: needs BOTH the wave data and the exception playbook.
    UNION ALL SELECT 'Why did the Tuesday wave miss cutoff at ATL-DC1?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','knowledge_base'),
                           OBJECT_CONSTRUCT('tool_name','fulfillment_data'))
    -- Knowledge-only: writing SQL here is the failure mode being tested.
    UNION ALL SELECT 'What counts as a short pick in our SOP?',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','knowledge_base'))
    UNION ALL SELECT 'Find SKUs similar to ''blue widget 12oz''',
           ARRAY_CONSTRUCT(OBJECT_CONSTRUCT('tool_name','product_search'))
) m ON m.q = d.INPUT_QUERY;

-- Gate: every harness question must have received an invocation mapping.
-- A silent join miss here would score the agent on fewer questions than the
-- external agent, quietly re-introducing the confound this file exists to remove.
SELECT
    (SELECT COUNT(*) FROM EVAL.EXTERNAL_EVAL_DATASET) AS harness_questions,
    (SELECT COUNT(*) FROM EVAL.SHARED_EVAL_DATASET)  AS shared_questions,
    (SELECT COUNT(*) FROM EVAL.SHARED_EVAL_DATASET
      WHERE ARRAY_SIZE(GROUND_TRUTH:ground_truth_invocations) = 0) AS missing_invocations;
