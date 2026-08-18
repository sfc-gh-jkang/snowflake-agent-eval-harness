/*=============================================================================
  03_search.sql — Cortex Search services for the Agent Eval Demo.
  
  Creates:
    1. AI.OPS_KNOWLEDGE_CORPUS — table for ops docs (loaded from parquet)
    2. AI.ITEM_CATALOG_SEARCH — Cortex Search over SKU/description/taxonomy
    3. AI.OPS_KNOWLEDGE_SEARCH — Cortex Search over ops knowledge documents
  
  Both services enable REQUEST_LOGGING for observability.
  Two Cortex Search services over the synthetic corpus: one for product/SKU
  lookup, one for the ops-knowledge documents.
  
  Connection: my_snowflake_connection (ACCOUNTADMIN on the primary demo account)
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;
USE DATABASE AGENT_EVAL_DEMO;

-- ============ Stage for corpus data ============
CREATE STAGE IF NOT EXISTS AI.CORPUS_STAGE
  COMMENT = 'Ops knowledge corpus parquet files';

-- ============ Ops Knowledge Corpus Table ============
CREATE OR REPLACE TABLE AI.OPS_KNOWLEDGE_CORPUS (
    DOC_ID VARCHAR(50) NOT NULL,
    TITLE VARCHAR(200) NOT NULL,
    DOC_TYPE VARCHAR(30) NOT NULL,
    CARRIER VARCHAR(10),
    WAREHOUSE_ID VARCHAR(20),
    CONTENT VARCHAR(16777216) NOT NULL
)
COMMENT = 'Synthetic ops documents: carrier tariffs, SOPs, exception playbooks, cutoff policies';

-- Load via: PUT file:///.../ops_knowledge_corpus.parquet @AI.CORPUS_STAGE
-- Then:
COPY INTO AI.OPS_KNOWLEDGE_CORPUS
  FROM @AI.CORPUS_STAGE/ops_knowledge_corpus.parquet
  FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

-- ============ Cortex Search: ITEM_CATALOG_SEARCH ============
-- Fuzzy search over SKU catalog (description + category + subcategory).
-- Fuzzy product/SKU lookup over the synthetic item master.
CREATE OR REPLACE CORTEX SEARCH SERVICE AI.ITEM_CATALOG_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES SKU, CATEGORY, SUBCATEGORY
  WAREHOUSE = AGENT_EVAL_DEMO_WH
  TARGET_LAG = '1 hour'
  COMMENT = 'Fuzzy SKU/description/taxonomy search over the synthetic item master'
AS (
  SELECT
    SKU,
    DESCRIPTION || ' | ' || CATEGORY || ' > ' || SUBCATEGORY AS SEARCH_TEXT,
    CATEGORY,
    SUBCATEGORY
  FROM AGENT_EVAL_DEMO.INVENTORY_INTELLIGENCE.ITEM_MASTER
);

-- ============ Cortex Search: OPS_KNOWLEDGE_SEARCH ============
-- Full-text search over ops knowledge documents (tariffs, SOPs, playbooks, policies).
CREATE OR REPLACE CORTEX SEARCH SERVICE AI.OPS_KNOWLEDGE_SEARCH
  ON CONTENT
  ATTRIBUTES DOC_ID, TITLE, DOC_TYPE, CARRIER, WAREHOUSE_ID
  WAREHOUSE = AGENT_EVAL_DEMO_WH
  TARGET_LAG = '1 hour'
  COMMENT = 'Ops knowledge base: carrier tariffs, pick/pack SOPs, exception playbooks, cutoff policies'
AS (
  SELECT
    DOC_ID,
    TITLE,
    DOC_TYPE,
    CARRIER,
    WAREHOUSE_ID,
    CONTENT
  FROM AGENT_EVAL_DEMO.AI.OPS_KNOWLEDGE_CORPUS
);

-- ============ Enable REQUEST_LOGGING for observability ============
ALTER CORTEX SEARCH SERVICE AI.ITEM_CATALOG_SEARCH SET
  REQUEST_LOGGING = TRUE;

ALTER CORTEX SEARCH SERVICE AI.OPS_KNOWLEDGE_SEARCH SET
  REQUEST_LOGGING = TRUE;

-- ============ Verify ============
-- `rows` is a reserved word; aliasing to it fails with 001003 and exits the
-- script non-zero even though the search services above were created fine.
SELECT 'OPS_KNOWLEDGE_CORPUS' AS object, COUNT(*) AS row_count FROM AI.OPS_KNOWLEDGE_CORPUS
UNION ALL
SELECT 'ITEM_MASTER (source)', COUNT(*) FROM INVENTORY_INTELLIGENCE.ITEM_MASTER;
