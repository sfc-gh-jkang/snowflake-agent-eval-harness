/*=============================================================================
  00_setup.sql — Agent Eval Demo: database, schemas, warehouse, stages
  Idempotent: safe to re-run. Connection: my_snowflake_connection
=============================================================================*/

USE ROLE ACCOUNTADMIN;

-- Database
CREATE DATABASE IF NOT EXISTS AGENT_EVAL_DEMO
  COMMENT = 'agent eval demo — drop this DB for full teardown';

-- Warehouse
CREATE WAREHOUSE IF NOT EXISTS AGENT_EVAL_DEMO_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  COMMENT = 'agent eval demo compute';

USE WAREHOUSE AGENT_EVAL_DEMO_WH;
USE DATABASE AGENT_EVAL_DEMO;

-- Schemas: one per functional domain of a 3PL fulfilment operation
CREATE SCHEMA IF NOT EXISTS FULFILLMENT_INTELLIGENCE;
CREATE SCHEMA IF NOT EXISTS INVENTORY_INTELLIGENCE;
CREATE SCHEMA IF NOT EXISTS LABOR_INTELLIGENCE;
CREATE SCHEMA IF NOT EXISTS SHIPPING_INTELLIGENCE;
CREATE SCHEMA IF NOT EXISTS AI;
CREATE SCHEMA IF NOT EXISTS EVAL;
CREATE SCHEMA IF NOT EXISTS OPS;

-- Internal stage for evaluation config YAML files
CREATE STAGE IF NOT EXISTS EVAL.CONFIGS
  COMMENT = 'Evaluation YAML configs (uncompressed)';

-- Confirm
SELECT 'AGENT_EVAL_DEMO setup complete' AS status;
