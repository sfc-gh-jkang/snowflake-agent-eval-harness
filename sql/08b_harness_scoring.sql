-- ============================================================================
-- 08b_harness_scoring.sql — SCORE the external agent, not just trace it
-- ============================================================================
--
-- WHY THIS FILE EXISTS
-- --------------------
-- Act 4 originally proved only that an external orchestrator can be TRACED
-- into Snowflake. That left two honest gaps when comparing the external path
-- against native Cortex Agent evaluations:
--
--   Gap 1  EXTERNAL_SIM emitted no RETRIEVAL span, so context_relevance and
--          groundedness could not be computed — 3 of 5 documented server-side
--          metrics were reachable, not 5.
--          FIXED in python/external_sim/tools.py (3 @instrument decorators).
--
--   Gap 2  Nothing in the repo ever called run.start() / compute_metrics(),
--          so the external agent had traces but NO SCORES. The question is
--          "how do I know it got better", and latency spans alone cannot
--          answer that.
--          FIXED by this dataset + python/external_sim/score.py.
--
-- The invocation path is genuinely split and this file does not pretend
-- otherwise. EXECUTE_AI_EVALUATION is documented for native Cortex Agents;
-- custom applications are scored through the TruLens SDK:
--   https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-evaluations
--   https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability/evaluate-applications-trulens
-- The READ path, by contrast, is identical for both agent types — same event
-- table, and agent_type simply becomes 'EXTERNAL AGENT':
--   https://docs.snowflake.com/en/sql-reference/functions/get_ai_evaluation_data-snowflake-local
--
-- ============================================================================
-- GROUND TRUTH PROVENANCE — every value below was COMPUTED, not written
-- ============================================================================
-- The six numeric golds were produced by executing the semantic view's own
-- VERIFIED QUERY SQL with each harness question's exact filters, on the primary demo account
-- on 2026-08-18. They are not estimates and not copied from another doc.
-- The VQ each one derives from is named per row so the definition is auditable
-- (the on-time / fill-rate / units traps each have several defensible
-- definitions — that ambiguity is the whole point of the demo, so the gold
-- MUST state which definition it used).
--
--   Q1 0.6160    VQ_ON_TIME_ALL definition (CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE)
--                + tenant filter via SHIPMENTS->ORDERS. Independently
--                cross-checks the AGENT_EVAL_DATASET note "On-time ~61-62%".
--   Q2 0.8805    VQ_LINE_FILL_RATE definition (SUM(LINES_FILLED)/SUM(TOTAL_LINES))
--   Q3 1044102   VQ_TOTAL_UNITS_SHIPPED — same date range, so this is the
--                verified query verbatim.
--   Q4 6 carriers VQ_AVG_COST_BY_CARRIER verbatim. Requires the
--                SHIPMENTS->ZONE_RATE_CARDS join that the eval caught as
--                missing; this row would have been unanswerable before that fix.
--   Q5 2094      VQ_ORDERS_IN_FISCAL_PERIOD verbatim (question matches exactly).
--   Q6 3686      VQ_ACTIVE_SKU_COUNT definition (DISTINCT SKU in MOVEMENTS),
--                re-windowed to the 30 days ending 2026-03-31.
--
-- The three non-numeric golds quote AGENT_EVAL_DEMO.AI.OPS_KNOWLEDGE_CORPUS
-- directly rather than paraphrasing from memory.
-- ============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

-- ----------------------------------------------------------------------------
-- Dataset consumed by RunConfig(source_type='TABLE') in score.py.
--
-- Column names are referenced by dataset_spec, so renaming a column here
-- silently breaks metric computation rather than erroring — keep the two in
-- sync. INPUT_QUERY must match the questions in run.py EXACTLY, character for
-- character: the run invokes the app with the dataset's input, so a drifted
-- string scores a different question than the one the trace shows.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE TABLE EVAL.EXTERNAL_EVAL_DATASET (
    INPUT_QUERY    VARCHAR NOT NULL,
    GROUND_TRUTH   VARCHAR NOT NULL,
    TRAP           VARCHAR,           -- which ambiguity this question probes
    GOLD_SOURCE    VARCHAR            -- the VQ or corpus doc the gold came from
)
COMMENT = 'Ground truth for scoring the EXTERNAL_SIM external agent via TruLens run.compute_metrics(). Numeric golds computed from verified-query SQL on 2026-08-18.';

INSERT INTO EVAL.EXTERNAL_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH, TRAP, GOLD_SOURCE) VALUES
(
  'What was the on-time shipping rate for tenant T001 between 2025-03-01 and 2025-09-30?',
  'The on-time shipping rate for tenant T001 over that window is 0.6160, i.e. about 61.6%. On-time is defined as CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE. An answer that reports roughly 61-62% is correct. An answer using delivery date rather than first carrier scan is not.',
  'on-time definition (first scan vs delivery)',
  'VQ_ON_TIME_ALL + SHIPMENTS->ORDERS tenant filter'
),
(
  'What is the line fill rate for tenant T002 between 2025-06-01 and 2025-12-31?',
  'The line fill rate for tenant T002 over that window is 0.8805, i.e. about 88.1%. Line fill rate is SUM(LINES_FILLED)/SUM(TOTAL_LINES). Roughly 88% is correct. Order fill rate (fully-filled orders / orders) and unit fill rate (eaches shipped / eaches ordered) are DIFFERENT metrics and are incorrect answers here.',
  'fill rate has three valid definitions',
  'VQ_LINE_FILL_RATE'
),
(
  'How many eaches were shipped between 2025-03-01 and 2025-09-30?',
  'A total of 1044102 eaches were shipped over that window, summing QTY_SHIPPED_EACHES on order lines joined to orders by order date. Approximately 1.04 million is correct. Counting cartons or order lines instead of eaches is incorrect.',
  'units: eaches vs cartons vs lines',
  'VQ_TOTAL_UNITS_SHIPPED (verbatim)'
),
(
  'What is the average cost per shipment by carrier between 2025-06-01 and 2025-12-31?',
  'Average cost per shipment by carrier, highest to lowest: XPO 39.04, DHL 34.84, FEDEX 26.89, UPS 25.95, USPS 18.55, ONTRAC 17.60. Cost requires joining SHIPMENTS to ZONE_RATE_CARDS on carrier, zone and weight break with the ship date inside the rate card effective window, then RATE_PER_PACKAGE * (1 + FUEL_SURCHARGE_PCT/100) * PACKAGE_COUNT. An answer that omits the fuel surcharge or the rate card join is incorrect.',
  'cost requires the zone_rate_cards join',
  'VQ_AVG_COST_BY_CARRIER (verbatim)'
),
(
  'How many orders were placed in fiscal period 7 of fiscal year 2025?',
  '2094 orders were placed in fiscal period 7 of fiscal year 2025. This requires joining orders to FISCAL_CALENDAR_445 on calendar date and filtering FISCAL_YEAR = 2025 AND FISCAL_PERIOD = 7. Treating fiscal period 7 as calendar July is incorrect — this is a 4-4-5 calendar.',
  '4-4-5 fiscal calendar vs calendar month',
  'VQ_ORDERS_IN_FISCAL_PERIOD (verbatim)'
),
(
  'How many active SKUs were there in the 30 days ending 2026-03-31?',
  'There were 3686 active SKUs in the 30 days ending 2026-03-31. Active means the SKU had at least one inventory movement in the window, counted as COUNT(DISTINCT SKU) from MOVEMENTS. Counting all SKUs in the item master instead of only those with movement is incorrect.',
  'active SKU definition (movement vs catalog)',
  'VQ_ACTIVE_SKU_COUNT re-windowed'
),
(
  'Why did the Tuesday wave miss cutoff at ATL-DC1?',
  'A correct answer combines wave/exception data for ATL-DC1 with the operations playbook "Exception Playbook: Wave Missed Carrier Cutoff", which defines the condition as a wave whose pick/pack completion time exceeds the carrier pickup cutoff, delaying delivery by one business day, and lists root causes in frequency order: labor shortage on shift, late wave release, and high exception rate in the wave from short picks or QA failures. An answer citing only data with no playbook, or only the playbook with no data, is incomplete.',
  'multi-tool: structured data + unstructured knowledge',
  'OPS_KNOWLEDGE_CORPUS: Exception Playbook: Wave Missed Carrier Cutoff'
),
(
  'What counts as a short pick in our SOP?',
  'Per the Discrete Order Picking SOP, a short pick occurs when the picker cannot fulfil the requested quantity from the assigned location. It covers three cases: the location is empty, the location has insufficient quantity, or the item is damaged and cannot be shipped. A short pick does NOT mean the order is cancelled. This is a knowledge-base question and should be answered from the SOP without writing SQL.',
  'search-only: should NOT write SQL',
  'OPS_KNOWLEDGE_CORPUS: Standard Pick Process — Discrete Order Picking'
),
(
  'Find SKUs similar to ''blue widget 12oz''',
  'A correct answer returns candidate SKUs from the product catalogue matched fuzzily on description text, using catalogue search rather than an exact literal equality filter. The answer should list specific SKUs with their descriptions and should not claim zero matches purely because no description equals the phrase exactly.',
  'fuzzy literal matching via catalog search',
  'ITEM_CATALOG_SEARCH behaviour'
);

-- Sanity gate: the dataset MUST have exactly the 9 questions run.py invokes.
-- A mismatch here means score.py will score a different question set than the
-- traces show, which is worse than not scoring at all.
SELECT COUNT(*) AS questions, COUNT(DISTINCT INPUT_QUERY) AS distinct_questions
FROM EVAL.EXTERNAL_EVAL_DATASET;

-- ----------------------------------------------------------------------------
-- Snapshot table for scored results.
--
-- score.py also creates this with CREATE TABLE IF NOT EXISTS, so the script is
-- runnable standalone. It is declared HERE as well because the Streamlit app
-- reads it, and tests/test_10_sql_static.py asserts that every EVAL table the
-- app reads is created by some SQL script. A table that only ever comes into
-- existence as a side effect of a Python run is invisible to that guard and to
-- anyone reading the SQL to understand the schema.
--
-- Snapshotted rather than read live because runs are immutable but the metric
-- set is not: re-scoring under a new run_name would otherwise leave no record
-- of what the earlier run measured, and the judge is non-deterministic.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS EVAL.EXTERNAL_SCORED_RESULTS (
    RUN_NAME     VARCHAR,
    MEASURED_ON  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    LLM_JUDGE    VARCHAR,        -- part of run identity: changing it changes scores
    INPUT_ID     VARCHAR,
    METRIC_NAME  VARCHAR,
    SCORE        FLOAT,
    INPUT        VARCHAR,
    OUTPUT       VARCHAR,
    ERROR        VARCHAR
)
COMMENT = 'Per-record LLM-judge scores for the EXTERNAL_SIM external agent, snapshotted from GET_AI_EVALUATION_DATA by python/external_sim/score.py.';

-- ----------------------------------------------------------------------------
-- READ BACK the scores. Note agent_type = 'EXTERNAL AGENT' — the ONLY
-- difference from how AGENT_V4 results are read for the native agent.
-- Replace <RUN_NAME> with the run score.py reports. The canonical scored run
-- named at score.py invocation time (judge claude-4-sonnet, matched to the
-- agent eval's judge for parity; 9 records per metric).
-- ----------------------------------------------------------------------------
-- SELECT METRIC_NAME, COUNT(*) AS records, ROUND(AVG(EVAL_AGG_SCORE), 4) AS score
-- FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
--   'AGENT_EVAL_DEMO', 'AI', 'EXTERNAL_SIM', 'EXTERNAL AGENT', '<RUN_NAME>'))
-- WHERE METRIC_NAME IS NOT NULL
-- GROUP BY METRIC_NAME ORDER BY METRIC_NAME;

-- Confirm the RETRIEVAL span fix actually landed. Before the tools.py change
-- this returned five rows and no 'retrieval'; context_relevance and
-- groundedness were uncomputable as a direct result.
-- SELECT COALESCE(SPAN_TYPE,'(none)') AS span_type, COUNT(*) AS spans
-- FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
--   'AGENT_EVAL_DEMO','AI','EXTERNAL_SIM','EXTERNAL AGENT'))
-- GROUP BY 1 ORDER BY spans DESC;
