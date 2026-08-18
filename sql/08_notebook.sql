/*=============================================================================
  08_notebook.sql — Deploy eval CI/CD gating notebook (Container Runtime)
  
  REQUIREMENTS:
    - SYSADMIN role (Container Runtime cannot run as ACCOUNTADMIN/ORGADMIN/SECURITYADMIN)
    - PYPI_ACCESS_INTEGRATION already exists
    - eval_cicd_gating.ipynb uploaded to @EVAL.CONFIGS stage
  
  Container Runtime is mandatory for ML/AI notebooks that import packages.
  The notebook imports: snowflake-snowpark-python, pandas (both pre-installed
  in Container Runtime), plus standard library (time, json).
  
  NOTE: After CREATE NOTEBOOK, you must ADD LIVE VERSION to make it runnable.
=============================================================================*/

USE ROLE ACCOUNTADMIN;
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;
USE WAREHOUSE AGENT_EVAL_DEMO_WH;

------------------------------------------------------------
-- 0. Dedicated compute pool
--    The demo owns its own pool rather than borrowing an unrelated one. A
--    Container Runtime notebook occupies a WHOLE node, so on a shared pool
--    this demo's teardown can evict another demo's running service -- and
--    theirs can evict ours mid-demo.
--
--    COST: a compute node carries a 5-minute MINIMUM charge every time it
--    starts or resumes (stated in the Snowflake Service Consumption Table,
--    not on the docs site). CPU_X64_XS is the cheapest family at 0.060 cr/hr,
--    so one notebook run costs ~0.005 cr. AUTO_SUSPEND_SECS below 300
--    therefore saves nothing -- it just pays the 5-minute floor more often.
------------------------------------------------------------
CREATE COMPUTE POOL IF NOT EXISTS AGENT_EVAL_DEMO_NB_POOL
    MIN_NODES = 1
    MAX_NODES = 1
    INSTANCE_FAMILY = CPU_X64_XS
    AUTO_RESUME = TRUE
    AUTO_SUSPEND_SECS = 300
    COMMENT = 'Dedicated Container Runtime pool for AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING notebook';

------------------------------------------------------------
-- 1. Grant SYSADMIN the required privileges
--    The notebook is SYSADMIN-owned (Container Runtime cannot run as
--    ACCOUNTADMIN), but AGENT_EVAL_DEMO and its schemas are ACCOUNTADMIN-owned.
--    So SYSADMIN inherits NOTHING here and every privilege the notebook needs
--    must be granted explicitly. Verified 2026-08-13 by running each notebook
--    operation as SYSADMIN -- USAGE alone is NOT enough:
--      * CREATE TABLE on EVAL  -> cells 12/23 write NB_*_SNAPSHOT tables.
--        Without it the notebook fails mid-run with "Insufficient privileges
--        to operate on schema 'EVAL'" AFTER both ~4-min evals have already
--        been paid for.
--      * SELECT on both semantic views -> the two eval runs score them.
--      * SNOWFLAKE.CORTEX_USER -> the LLM judge that produces the scores.
------------------------------------------------------------
GRANT USAGE, MONITOR, OPERATE ON COMPUTE POOL AGENT_EVAL_DEMO_NB_POOL TO ROLE SYSADMIN;
GRANT CREATE NOTEBOOK ON SCHEMA AGENT_EVAL_DEMO.AI TO ROLE SYSADMIN;
GRANT USAGE ON INTEGRATION PYPI_ACCESS_INTEGRATION TO ROLE SYSADMIN;
GRANT READ ON STAGE AGENT_EVAL_DEMO.EVAL.CONFIGS TO ROLE SYSADMIN;
GRANT USAGE ON DATABASE AGENT_EVAL_DEMO TO ROLE SYSADMIN;
GRANT USAGE ON SCHEMA AGENT_EVAL_DEMO.AI TO ROLE SYSADMIN;
GRANT USAGE ON SCHEMA AGENT_EVAL_DEMO.EVAL TO ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE AGENT_EVAL_DEMO_WH TO ROLE SYSADMIN;
GRANT CREATE TABLE, CREATE VIEW ON SCHEMA AGENT_EVAL_DEMO.EVAL TO ROLE SYSADMIN;
GRANT SELECT ON SEMANTIC VIEW AGENT_EVAL_DEMO.AI.FULFILLMENT_SV TO ROLE SYSADMIN;
GRANT SELECT ON SEMANTIC VIEW AGENT_EVAL_DEMO.AI.FULFILLMENT_SV_V1 TO ROLE SYSADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE SYSADMIN;

------------------------------------------------------------
-- 2. Upload notebook file to stage
------------------------------------------------------------
-- PUT file:///path/to/notebook/eval_cicd_gating.ipynb
--     @AGENT_EVAL_DEMO.EVAL.CONFIGS/notebooks/
--     AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

------------------------------------------------------------
-- 3. Create notebook (SYSADMIN-owned, Container Runtime)
--    RUNTIME_NAME is REQUIRED. Passing COMPUTE_POOL alone is silently
--    accepted and recorded by DESCRIBE, but the notebook still runs on
--    WAREHOUSE runtime -- verified 2026-08-13: with COMPUTE_POOL set and no
--    RUNTIME_NAME, DESCRIBE reported runtime_name = NULL,
--    runtime_environment_version = 'WH-RUNTIME-2.0' and
--    code_warehouse = 'SYSTEM$STREAMLIT_NOTEBOOK_WH'. Adding RUNTIME_NAME
--    flipped it: runtime_name set, code_warehouse NULL.
--
--    NOTE: SHOW NOTEBOOKS has NO compute_pool / runtime_name columns at all,
--    so you CANNOT diagnose runtime from SHOW -- always use DESCRIBE NOTEBOOK.
------------------------------------------------------------
USE ROLE SYSADMIN;

CREATE OR REPLACE NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING
    FROM '@AGENT_EVAL_DEMO.EVAL.CONFIGS/notebooks/'
    MAIN_FILE = 'eval_cicd_gating.ipynb'
    QUERY_WAREHOUSE = AGENT_EVAL_DEMO_WH
    COMPUTE_POOL = AGENT_EVAL_DEMO_NB_POOL
    RUNTIME_NAME = 'SYSTEM$BASIC_RUNTIME'
    EXTERNAL_ACCESS_INTEGRATIONS = ('PYPI_ACCESS_INTEGRATION');

------------------------------------------------------------
-- 4. Add live version (makes it executable)
------------------------------------------------------------
ALTER NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING ADD LIVE VERSION FROM LAST;

------------------------------------------------------------
-- 5. Verify deployment
------------------------------------------------------------
DESCRIBE NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING;

-- Optional: execute the notebook programmatically
-- EXECUTE NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING;
