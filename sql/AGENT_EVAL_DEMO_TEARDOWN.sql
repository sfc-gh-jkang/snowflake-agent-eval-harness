/*=============================================================================
  AGENT_EVAL_DEMO_TEARDOWN.sql — Drops ALL demo objects. Irreversible.

  Drops SIX objects:
    1. Database AGENT_EVAL_DEMO (all schemas, tables, views, agents, etc.)
    2. Warehouse AGENT_EVAL_DEMO_WH
    3. Compute pool AGENT_EVAL_DEMO_NB_POOL   (notebook)
    4. Compute pool AGENT_EVAL_DEMO_APP_POOL  (Streamlit, Container Runtime)
    5. Account-level role TENANT_ALDERWOOD
    6. Account-level role TENANT_BELLWEATHER
  Then verifies zero residual objects remain.

  Order matters: the database is dropped FIRST so the Streamlit app and notebook
  are gone before their compute pools are dropped. DROP COMPUTE POOL fails while
  a service still runs on it.
=============================================================================*/

USE ROLE ACCOUNTADMIN;

-- 1. Drop the database (cascades all contained objects)
DROP DATABASE IF EXISTS AGENT_EVAL_DEMO;

-- 2. Drop the warehouse
DROP WAREHOUSE IF EXISTS AGENT_EVAL_DEMO_WH;

-- 3. Drop the notebook's dedicated compute pool. A compute pool is an
--    ACCOUNT-level object, so dropping the database does NOT remove it, and it
--    bills again whenever anything resumes it -- the same leak class as the
--    tenant roles below. The notebook that used it is already gone by now.
DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_NB_POOL;
--    Same for the Act 7 Streamlit pool (Container Runtime).
DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_APP_POOL;

-- 4. Drop account-level tenant roles (these LEAK if you only drop the DB)
DROP ROLE IF EXISTS TENANT_ALDERWOOD;
DROP ROLE IF EXISTS TENANT_BELLWEATHER;

-- 5. Residue assertions — must be REAL-TIME.
--    Do NOT use SNOWFLAKE.ACCOUNT_USAGE here: those views lag by up to a few
--    hours, so immediately after a teardown they still list the dropped objects
--    and the check reports a false failure (or worse, a false pass on rebuild).
--    SHOW is real-time. Each of these must return ZERO rows.
-- ----------------------------------------------------------------------------
-- VERIFY. Each SHOW must print "No data".
--
-- These are bare SHOW statements on purpose. The obvious way to write this is
-- SELECT COUNT(*) FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())), and that is what
-- this script used to do -- but RESULT_SCAN is a query and therefore needs an
-- active warehouse, and by this point the script has already dropped
-- AGENT_EVAL_DEMO_WH. On a connection whose default warehouse WAS
-- AGENT_EVAL_DEMO_WH the verification then dies with:
--
--   000606 (57P03): No active warehouse selected in the current session.
--
-- The drops had all succeeded; only the self-check failed, which is the worst
-- shape for a teardown script -- it looks like the teardown broke. SHOW is
-- metadata-only and needs no compute, so this version verifies correctly even
-- though the warehouse it used to run on no longer exists.
--
-- Measured on both an AWS and an Azure account: 6 objects dropped, all five
-- SHOWs return "No data".
-- ----------------------------------------------------------------------------

SHOW DATABASES LIKE 'AGENT_EVAL_DEMO';
SHOW WAREHOUSES LIKE 'AGENT_EVAL_DEMO_WH';
SHOW COMPUTE POOLS LIKE 'AGENT_EVAL_DEMO_%_POOL';
SHOW ROLES LIKE 'TENANT_ALDERWOOD';
SHOW ROLES LIKE 'TENANT_BELLWEATHER';

-- If any SHOW above returned a row, that object survived -- re-run its DROP.
-- Compute pools are the usual survivor: they are account-level and are NOT
-- removed by DROP DATABASE.
