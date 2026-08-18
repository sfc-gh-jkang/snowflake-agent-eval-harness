/*=============================================================================
  07_agent.sql — Cortex Agent FULFILLMENT_ANALYST + GPA evaluation
  
  Agent has 4 tools:
    - Fulfillment Data (FULFILLMENT_SV) — orders, lines, fill rates, inventory
    - Shipping Data (SHIPPING_SV) — shipments, costs, on-time
    - Product Search (ITEM_CATALOG_SEARCH) — fuzzy SKU lookup
    - Knowledge Base (OPS_KNOWLEDGE_SEARCH) — SOPs, policies
  
  v1: Weak tool descriptions → routing traps fire (TSA ≈ 0.64)
  v2: Clear tool descriptions + orchestration instructions → TSA ≈ 0.65
  
  MEASURED RESULTS:
    AGENT_V1: answer_correctness=0.60, TSA=0.642, TEA=0.0, logical=0.933
    AGENT_V2: answer_correctness=0.60, TSA=0.654, TEA=0.0, logical=0.967
  
  NOTE: TEA=0.0 because ground truth omits tool_input/tool_output fields.
  The framework confirms invocation but cannot score execution quality without
  reference inputs/outputs. This is acceptable for the demo which focuses on
  TSA (routing) as the "which hop broke" metric.
  
  CRITICAL SYNTAX NOTE:
    - tool_name in ground_truth_invocations must match the TRACE tool name
      (lowercase + underscores): "fulfillment_data", "shipping_data",
      "product_search", "knowledge_base"
    - Agent requires execution_environment.warehouse for analyst tools
    - Models: use "" (empty string) for auto model selection
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

------------------------------------------------------------
-- 1. AGENT v2 (current live version — optimized routing)
--    To demo v1, recreate with weak descriptions below
------------------------------------------------------------
CREATE OR REPLACE AGENT AI.FULFILLMENT_ANALYST
COMMENT = 'Fulfilment operations assistant - v2 optimized routing'
FROM SPECIFICATION
$$
models:
  # PINNED, not auto-selected. Leaving this as "" lets Snowflake choose, which
  # (a) makes the model invisible unless you go read LLM_MODEL in the event table
  # and (b) can change under you, silently invalidating the comparison against
  # the external agent. claude-opus-4-8 is what auto-select actually resolved to
  # on the primary demo account (measured: 72 spans), so pinning it aligns the two agents WITHOUT
  # changing the real behaviour of this agent.
  # Must stay in lockstep with ORCHESTRATION_MODEL in python/external_sim/orchestrator.py.
  orchestration: "claude-opus-4-8"
instructions:
  response: "You are a supply-chain fulfillment operations assistant for a 3PL warehouse company. Answer questions about orders, shipping costs, inventory, and operational policies with specific data."
  orchestration: "Route questions to the correct tool based on content:
    - Fulfillment Data: orders, order lines, fill rates, waves, exceptions, inventory movements, fiscal calendar, item catalog. Use for any question about order volumes, fill rates, SKU activity, or fulfillment metrics.
    - Shipping Data: shipments, carrier performance, on-time rates, shipping costs, zone rate cards, delivery tracking. Use for any question involving carriers, shipping costs, delivery timing, or on-time SLA.
    - Product Search: fuzzy/semantic product lookup by name, description, or partial SKU. Use when the user provides approximate or misspelled product names.
    - Knowledge Base: SOPs, carrier tariff policies, exception playbooks, cutoff rules, operational procedures. Use for policy/process questions like 'what is our policy on X' or 'what does the SOP say about Y'.
    When a question needs both data AND policy context, call both the relevant data tool AND Knowledge Base."
  sample_questions:
    - question: "What is the order fill rate for Alderwood Logistics?"
    - question: "What is the average shipping cost per package by carrier?"
    - question: "What counts as a short pick in our SOP?"
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Fulfillment Data"
      description: "Query fulfillment operations data: orders, order lines, fill rates, waves, exceptions, inventory movements, fiscal periods, and item catalog. Covers order volumes, fill rate metrics (order/line/unit), SKU activity, and warehouse performance."
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Shipping Data"
      description: "Query shipping and carrier data: shipments, on-time delivery rates, shipping costs by carrier/zone/weight, zone rate cards, carrier scan tracking, and delivery performance. On-time SLA = CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE."
  - tool_spec:
      type: "cortex_search"
      name: "Product Search"
      description: "Semantic product search for fuzzy/approximate SKU or product name lookup. Use when the user provides a misspelled, partial, or approximate product name and needs to find matching SKUs."
  - tool_spec:
      type: "cortex_search"
      name: "Knowledge Base"
      description: "Search operational knowledge documents: SOPs, carrier tariff sheets, exception playbooks, wave cutoff policies, pick/pack procedures, and compliance guidelines. Use for any question about policies, procedures, or operational rules."
tool_resources:
  Fulfillment Data:
    semantic_view: "AGENT_EVAL_DEMO.AI.FULFILLMENT_SV"
    execution_environment:
      type: "warehouse"
      warehouse: "AGENT_EVAL_DEMO_WH"
  Shipping Data:
    semantic_view: "AGENT_EVAL_DEMO.AI.SHIPPING_SV"
    execution_environment:
      type: "warehouse"
      warehouse: "AGENT_EVAL_DEMO_WH"
  Product Search:
    name: "AGENT_EVAL_DEMO.AI.ITEM_CATALOG_SEARCH"
    id_column: "SKU"
    max_results: 10
  Knowledge Base:
    name: "AGENT_EVAL_DEMO.AI.OPS_KNOWLEDGE_SEARCH"
    id_column: "DOC_ID"
    max_results: 5
$$;

------------------------------------------------------------
-- 2. AGENT EVALUATION DATASET
------------------------------------------------------------
CREATE OR REPLACE TABLE EVAL.AGENT_EVAL_DATASET (
    INPUT_QUERY VARCHAR(2000),
    GROUND_TRUTH VARIANT
);

-- 10 eval queries covering routing traps
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'How many orders were placed between 2025-06-01 and 2025-09-30?',
       PARSE_JSON('{"ground_truth_output":"Count of orders filtered to ORDER_DATE between 2025-06-01 and 2025-09-30. Expected ~13000-14000.","ground_truth_invocations":[{"tool_name":"fulfillment_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What is the average shipping cost per package by carrier for shipments in 2025?',
       PARSE_JSON('{"ground_truth_output":"Average cost by carrier using ZONE_RATE_CARDS joined to SHIPMENTS.","ground_truth_invocations":[{"tool_name":"shipping_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What is the on-time shipping rate for tenant T001 in the second half of 2025?',
       PARSE_JSON('{"ground_truth_output":"On-time ~59-61% based on CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE.","ground_truth_invocations":[{"tool_name":"shipping_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What counts as a short pick in our SOP?',
       PARSE_JSON('{"ground_truth_output":"Definition from ops SOP documents. Knowledge question, not data.","ground_truth_invocations":[{"tool_name":"knowledge_base"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'Find SKUs similar to ''blu widget 12oz''',
       PARSE_JSON('{"ground_truth_output":"Fuzzy SKU matches from product catalog search.","ground_truth_invocations":[{"tool_name":"product_search"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'Why did the Tuesday wave miss the carrier cutoff, and what does our SOP say about late waves?',
       PARSE_JSON('{"ground_truth_output":"Combines wave data AND SOP policy. Both tools needed.","ground_truth_invocations":[{"tool_name":"knowledge_base"},{"tool_name":"fulfillment_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What is the order fill rate for each tenant in 2025?',
       PARSE_JSON('{"ground_truth_output":"Order fill = LINES_FILLED=TOTAL_LINES / total, grouped by tenant. ~53% overall.","ground_truth_invocations":[{"tool_name":"fulfillment_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What is the current on-hand inventory for hazmat items?',
       PARSE_JSON('{"ground_truth_output":"On-hand for HAZMAT_FLAG=Y items from inventory.","ground_truth_invocations":[{"tool_name":"fulfillment_data"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'What is the carrier cutoff time for UPS ground shipments?',
       PARSE_JSON('{"ground_truth_output":"UPS ground cutoff from ops knowledge documents.","ground_truth_invocations":[{"tool_name":"knowledge_base"}]}');
INSERT INTO EVAL.AGENT_EVAL_DATASET (INPUT_QUERY, GROUND_TRUTH)
SELECT 'Which carrier has the highest fuel surcharge for zone 5?',
       PARSE_JSON('{"ground_truth_output":"Carrier with max FUEL_SURCHARGE_PCT for ZONE=5 from rate cards.","ground_truth_invocations":[{"tool_name":"shipping_data"}]}');

------------------------------------------------------------
-- 3. AGENT EVAL CONFIG (on stage)
-- Content of @EVAL.CONFIGS/agent_evaluation_config.yaml:
--
-- evaluation:
--   agent_params:
--     agent_name: "AGENT_EVAL_DEMO.AI.FULFILLMENT_ANALYST"
--     agent_type: "CORTEX AGENT"
--   source_metadata:
--     type: "dataset"
--     dataset_name: "AGENT_EVAL_DATASET"
-- dataset:
--   dataset_type: "CORTEX AGENT"
--   dataset_name: "AGENT_EVAL_DATASET"
--   table_name: "AGENT_EVAL_DEMO.EVAL.AGENT_EVAL_DATASET"
--   column_mapping:
--     query_text: "INPUT_QUERY"
--     ground_truth: "GROUND_TRUTH"
-- metrics:
--   - "answer_correctness"
--   - "logical_consistency"
--   - "tool_selection_accuracy"
--   - "tool_execution_accuracy"
------------------------------------------------------------

-- Run eval (session must be USE SCHEMA AI).
--
-- CANONICAL RUN IS AGENT_V4, using agent_evaluation_config_v4.yaml. Two traps:
--
-- 1. The agent dataset version name is FIXED, not per-run:
--    SYSTEM_AI_OBS_CORTEX_AGENT_DATASET_VERSION_DO_NOT_DELETE. A second START
--    against the same dataset_name therefore ALWAYS fails with 210007
--    "Dataset version ... already exists", no matter what run_name you pass.
--    That is why the v4/v5 configs declare a NEW dataset_name
--    (AGENT_EVAL_DATASET_V4 / _V5) over the same underlying table. To re-run,
--    bump to a fresh dataset_name or DROP DATASET AI.AGENT_EVAL_DATASET_V4.
--    (Analyst evals differ: they version per run_name and re-run cleanly.)
--
-- 2. Corrected ground truth in EVAL.AGENT_EVAL_DATASET is only picked up under
--    a NEW dataset_name -- editing the table alone changes nothing.
CALL EXECUTE_AI_EVALUATION(
    'START',
    OBJECT_CONSTRUCT('run_name', 'AGENT_V4'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/agent_evaluation_config_v4.yaml'
);

-- Asynchronous. Wait, then require STATUS to be exactly COMPLETED -- both
-- INGESTION_COMPLETED and COMPUTATION_IN_PROGRESS contain substrings that
-- naive matching mistakes for a terminal state.
CALL SYSTEM$WAIT(300);

CALL EXECUTE_AI_EVALUATION(
    'STATUS',
    OBJECT_CONSTRUCT('run_name', 'AGENT_V4'),
    '@AGENT_EVAL_DEMO.EVAL.CONFIGS/agent_evaluation_config_v4.yaml'
);

-- Snapshot under the canonical name the Streamlit app reads.
CREATE OR REPLACE TABLE EVAL.AGENT_V4_RESULTS AS
SELECT * FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
    'AGENT_EVAL_DEMO', 'AI', 'FULFILLMENT_ANALYST', 'CORTEX AGENT', 'AGENT_V4'
));

SELECT CASE WHEN COUNT(*) = 0
            THEN 1/0  -- forces an error: run did not finish before snapshot
            ELSE COUNT(*) END AS scored_rows
FROM EVAL.AGENT_V4_RESULTS;

-- Per-metric scores.
--
-- MEASURED on the AWS demo account (re-measured after AGENT_V4 was recreated):
--   answer_correctness 0.70, logical_consistency 1.0, tool_selection 0.617,
--   tool_execution 0.00
-- MEASURED on a second account rebuild 2026-08-14: answer_correctness 0.833,
--   logical_consistency 1.0, tool_selection 0.62, tool_execution 0.00
--
-- Do NOT treat any of these as fixed. answer_correctness is judge-scored and is
-- asserted as a BAND (0.65-0.95) in tests/conftest.py, which is the single source
-- of truth. tool_execution_accuracy is 0.00 on BOTH accounts and that is correct:
-- the ground truth supplies only tool_name, and TEA grades tool input/output.
--
-- The agent spec is byte-identical between the two accounts (verified via
-- DESCRIBE AGENT), so the tool_execution collapse is LLM ROUTING VARIANCE, not
-- drift: the agent answered correctly but reached for fulfillment_data /
-- knowledge_base where the ground truth expects shipping_data. Treat agent
-- tool metrics as indicative, never as a promised number.
SELECT METRIC_NAME, COUNT(*) AS records, ROUND(AVG(EVAL_AGG_SCORE), 4) AS score
FROM EVAL.AGENT_V4_RESULTS
GROUP BY METRIC_NAME
ORDER BY METRIC_NAME;
