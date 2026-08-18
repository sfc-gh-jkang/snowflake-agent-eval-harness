/*=============================================================================
  09_streamlit.sql — Deploy observability Streamlit app to AGENT_EVAL_DEMO.OPS
  Connection: my_snowflake_connection
  
  Deploys the unified AI observability dashboard that shows both native
  Cortex Agent (FULFILLMENT_ANALYST) and external agent (EXTERNAL_SIM)
  telemetry in a single pane.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA OPS;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

-- Stage for Streamlit source files
CREATE STAGE IF NOT EXISTS AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE
  COMMENT = 'Streamlit app source files';

-- Upload the app source. THIS IS NOT OPTIONAL AND IT IS NOT DONE FOR YOU.
--
-- CREATE OR REPLACE STREAMLIT below only points at a stage path. It does NOT
-- upload anything, and it reports success against whatever bytes are already
-- on the stage -- including a stale copy from weeks ago. That is exactly how
-- the deployed app silently drifted 30 lines behind the repo on 2026-08-14 and
-- broke two of its seven tabs while every SQL script still exited 0.
--
-- BOTH files must be uploaded: observability_app.py AND pyproject.toml.
-- pyproject.toml is what pins Streamlit 1.61.1 -- without it on the stage,
-- container runtime installs nothing and the app fails with "Failed to get the
-- version of the Streamlit library".
--
-- Run this every time either file changes:
--
--   snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO;
--     PUT 'file://$PWD/streamlit/observability_app.py'
--     @AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability
--     AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--     PUT 'file://$PWD/streamlit/pyproject.toml'
--     @AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability
--     AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
--
-- tests/test_08_repro.py::TestDeployedStreamlitMatchesRepo asserts the staged
-- copy is byte-identical to the repo, so drift fails the suite instead of
-- failing live in front of a customer.

-- Dedicated Container Runtime pool for the app.
--
-- Separate from AGENT_EVAL_DEMO_NB_POOL on purpose: that pool is MAX_NODES = 1 and a
-- Container Runtime workload occupies a whole node, so sharing it would make the
-- Act 6 notebook and the Act 6 dashboard evict each other.
CREATE COMPUTE POOL IF NOT EXISTS AGENT_EVAL_DEMO_APP_POOL
    MIN_NODES = 1
    MAX_NODES = 1
    INSTANCE_FAMILY = CPU_X64_XS
    AUTO_RESUME = TRUE
    AUTO_SUSPEND_SECS = 300
    COMMENT = 'Dedicated Container Runtime pool for AGENT_EVAL_DEMO.OPS.OBSERVABILITY_APP';

GRANT USAGE, MONITOR, OPERATE ON COMPUTE POOL AGENT_EVAL_DEMO_APP_POOL TO ROLE ACCOUNTADMIN;
GRANT USAGE ON INTEGRATION PYPI_ACCESS_INTEGRATION TO ROLE ACCOUNTADMIN;
-- Container Runtime binds a service endpoint internally; without this the app
-- fails to start with a permissions error.
GRANT BIND SERVICE ENDPOINT ON ACCOUNT TO ROLE ACCOUNTADMIN;

-- Create the Streamlit app on CONTAINER RUNTIME.
--
-- WHY NOT WAREHOUSE RUNTIME: the warehouse runtime ships an old bundled
-- Streamlit that predates `hide_index` (added in 1.23). On 2026-08-14 the app
-- died on its FIRST tab with:
--   TypeError: DataFrameSelectorMixin.dataframe() got an unexpected keyword
--             argument 'hide_index'
-- Container Runtime + a pinned modern Streamlit in pyproject.toml fixes this
-- properly instead of degrading the app to a years-old API surface.
--
-- FOUR THINGS ARE ALL REQUIRED, and omitting any one fails differently:
--   FROM (not ROOT_LOCATION)      -- ROOT_LOCATION is the legacy warehouse syntax
--   RUNTIME_NAME                  -- without it you silently get warehouse runtime
--   COMPUTE_POOL                  -- without it you silently get warehouse runtime
--   EXTERNAL_ACCESS_INTEGRATIONS  -- without it PyPI installs fail to resolve
--
-- VERIFY IT TOOK. Neither SHOW STREAMLITS nor INFORMATION_SCHEMA.STREAMLITS
-- exposes runtime_name or compute_pool -- the same trap as SHOW NOTEBOOKS. Only
-- DESCRIBE STREAMLIT shows them:
--   DESCRIBE STREAMLIT AGENT_EVAL_DEMO.OPS.OBSERVABILITY_APP;
--   -- expect runtime_name = SYSTEM$ST_CONTAINER_RUNTIME_PY3_11
--   --        compute_pool = AGENT_EVAL_DEMO_APP_POOL
--
-- Do NOT add `ALTER STREAMLIT ... ADD LIVE VERSION FROM LAST` after this:
-- CREATE OR REPLACE already establishes a live version and the ALTER then fails
-- with 099106 "There is already a live version."
CREATE OR REPLACE STREAMLIT AGENT_EVAL_DEMO.OPS.OBSERVABILITY_APP
  FROM '@AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability'
  MAIN_FILE = 'observability_app.py'
  RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
  COMPUTE_POOL = AGENT_EVAL_DEMO_APP_POOL
  QUERY_WAREHOUSE = AGENT_EVAL_DEMO_WH
  EXTERNAL_ACCESS_INTEGRATIONS = (PYPI_ACCESS_INTEGRATION)
  TITLE = 'Agent Eval - AI Observability'
  COMMENT = 'Unified native + external agent telemetry dashboard';

-- Grant usage so demo roles can view
GRANT USAGE ON STREAMLIT AGENT_EVAL_DEMO.OPS.OBSERVABILITY_APP TO ROLE PUBLIC;

SELECT 'Streamlit OBSERVABILITY_APP deployed' AS status;
