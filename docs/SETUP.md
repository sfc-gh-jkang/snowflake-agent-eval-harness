# Setup Guide — Agent Eval Demo

## Prerequisites

- Snowflake account with **ACCOUNTADMIN** access
- Account must have: Cortex Agent, Cortex Search, Semantic Views enabled
- **Enterprise edition or higher** — Act 5 uses row access policies, which
  Standard edition does not support
- Privilege to `CREATE COMPUTE POOL`. **Two** pools get created, both `CPU_X64_XS`:
  `AGENT_EVAL_DEMO_NB_POOL` for the Act 6 Container Runtime notebook (which occupies a
  whole node, so it does not borrow an existing pool) and `AGENT_EVAL_DEMO_APP_POOL` for
  the Act 7 Streamlit app. Also `GRANT BIND SERVICE ENDPOINT ON ACCOUNT` for the app.
- An external access integration named `PYPI_ACCESS_INTEGRATION` — needed by **both**
  the notebook (pip installs) and the Streamlit Container Runtime app (which installs
  `streamlit[snowflake]` from `pyproject.toml` on first start)
- `snow` CLI installed, with a connection name exported as `$CONN` below
- **Two Python environments**, deliberately separate:
  - `.venv-test` — Python 3.10+ with `snowflake-connector-python`, `pandas`, `pyarrow`
    (data generation, doctor, pytest)
  - `.venv-harness` — Python **3.11** for the TruLens harness in step 15b.
    `trulens-connectors-snowflake` pins Python <3.12, so this cannot share the test
    venv. See GOTCHAS #7.

## Quick Start (from scratch)

This order is the one that was actually executed end to end on a brand-new
account (a second account, 2026-08-14). Earlier versions of this guide listed 10 of the
15 SQL files and none of the file uploads, which silently produced empty tables
and a broken Act 6/Act 7.

**The upload steps are not optional.** `COPY INTO` from an empty stage succeeds
with 0 rows and exit code 0, so skipping them yields 13 empty tables and no
error anywhere.

```bash
# 0. Pick the target account once
export CONN=my_snowflake_connection   # or: my_second_account

# 1. Clone and enter
cd ~/Code/snowflake-agent-eval-harness

# 2. Database, schemas, warehouse, config stage
snow sql -c $CONN -f sql/00_setup.sql

# 3. Generate data locally (deterministic: numpy seed 42)
python python/generate_data.py         # 13 parquet files -> data/
python python/generate_ops_corpus.py   # 63-doc ops corpus -> data/

# 4. Create the load stages, THEN upload, THEN load.
#    01_load_data.sql creates the stages it copies from, so a single run of it
#    on a fresh account finds an empty stage and loads nothing.
snow sql -c $CONN -q "
  USE DATABASE AGENT_EVAL_DEMO;
  CREATE STAGE IF NOT EXISTS FULFILLMENT_INTELLIGENCE.DATA_STAGE;
  CREATE STAGE IF NOT EXISTS INVENTORY_INTELLIGENCE.DATA_STAGE;
  CREATE STAGE IF NOT EXISTS LABOR_INTELLIGENCE.DATA_STAGE;
  CREATE STAGE IF NOT EXISTS SHIPPING_INTELLIGENCE.DATA_STAGE;
  CREATE STAGE IF NOT EXISTS AI.CORPUS_STAGE;"

# AUTO_COMPRESS=FALSE matters: the COPY INTO statements reference
# <name>.parquet, and the default PUT would gzip them to <name>.parquet.gz.
D=$PWD/data
for f in orders order_lines waves exceptions fiscal_calendar_445; do
  snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$D/$f.parquet' @FULFILLMENT_INTELLIGENCE.DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
done
for f in item_master on_hand movements; do
  snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$D/$f.parquet' @INVENTORY_INTELLIGENCE.DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
done
for f in pick_tasks labor_standards; do
  snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$D/$f.parquet' @LABOR_INTELLIGENCE.DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
done
for f in shipments carrier_scans zone_rate_cards; do
  snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$D/$f.parquet' @SHIPPING_INTELLIGENCE.DATA_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
done
snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$D/ops_knowledge_corpus.parquet' @AI.CORPUS_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"

snow sql -c $CONN -f sql/01_load_data.sql
# Expect ~758K rows across 13 tables: 757,723 on the a second account rebuild,
# 758,294 on the primary demo account (both verified live). The 571-row gap is expected --
# see "Data reproducibility" below. Tests allow 2% on the six
# variable-cardinality tables and assert the fixed ones exactly.

# 5. Upload the eval configs and the notebook BEFORE the scripts that need them
for y in eval_configs/*.yaml; do
  snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$PWD/$y' @EVAL.CONFIGS AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
done
snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO; PUT 'file://$PWD/notebook/eval_cicd_gating.ipynb' @EVAL.CONFIGS/notebooks/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"

# 6. Governance (row access policies + tenant roles)
snow sql -c $CONN -f sql/02_governance.sql

# 7. Cortex Search services
snow sql -c $CONN -f sql/03_search.sql

# 8. Semantic views. All THREE are required:
snow sql -c $CONN -f sql/04_semantic_v1.sql        # FULFILLMENT_SV (weak v1)
snow sql -c $CONN -f sql/04b_semantic_v1_frozen.sql # FULFILLMENT_SV_V1 (frozen)
snow sql -c $CONN -f sql/04c_shipping_sv.sql        # SHIPPING_SV

# 9. Baseline eval -> BASELINE_V1_FINAL_RESULTS (~5 min)
#    Targets the FROZEN FULFILLMENT_SV_V1, so it stays reproducible after
#    06 mutates FULFILLMENT_SV in place.
snow sql -c $CONN -f sql/05_eval_baseline.sql

# 10. Optimized v2 DDL, then its re-eval -> OPTIMIZED_V2_FINAL_RESULTS (~5 min)
#     06 must stay pure DDL (test_08_repro diffs it against the live object),
#     so the eval lives in 06b.
snow sql -c $CONN -f sql/06_semantic_v2.sql
snow sql -c $CONN -f sql/06b_eval_optimized.sql

# 11. Agent + AGENT_V4 eval -> AGENT_V4_RESULTS (~4 min)
snow sql -c $CONN -f sql/07_agent.sql

# 12. External agent (EXTERNAL_SIM) + side-by-side trace views
snow sql -c $CONN -f sql/08_external_agent.sql

# 13. Tenant isolation eval -> TENANT_ISOLATION_V2_RESULTS (~3 min)
snow sql -c $CONN -f sql/08_tenant_isolation_eval.sql

# 14. Act 6 notebook (creates AGENT_EVAL_DEMO_NB_POOL)
snow sql -c $CONN -f sql/08_notebook.sql

# 15. Act 7 Streamlit observability app.
#     The PUT is REQUIRED. CREATE OR REPLACE STREAMLIT only points at a stage
#     path -- it uploads nothing and succeeds against stale bytes, which is how
#     the deployed app drifted 30 lines behind the repo and broke two tabs.
#     BOTH files are required -- pyproject.toml pins Streamlit 1.61.1, and the
#     warehouse runtime's bundled Streamlit is too old for hide_index.
#     Create the stage FIRST. 09_streamlit.sql creates it too, but it also
#     reads from it in the same script, so a fresh account fails the PUT with
#     "does not exist" -- the same trap as the data stages in step 4.
snow sql -c $CONN -q "CREATE STAGE IF NOT EXISTS AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE;"
snow sql -c $CONN -q "USE DATABASE AGENT_EVAL_DEMO;
  PUT 'file://$PWD/streamlit/observability_app.py'
  @AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability
  AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
  PUT 'file://$PWD/streamlit/pyproject.toml'
  @AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability
  AUTO_COMPRESS=FALSE OVERWRITE=TRUE;"
snow sql -c $CONN -f sql/09_streamlit.sql
# Open the app ONCE after deploying: the first container start installs packages
# from PyPI and is much slower than later starts.

# 15b. External agent traces. REQUIRED for Act 4 -- 08_external_agent.sql only
#      registers the EXTERNAL_SIM object; the traces come from running the
#      TruLens harness, which emits ~45 spans (9 questions, ~8.5 min).
#      Without this, the side-by-side trace view is empty.
SF_CONNECTION=$CONN .venv-harness/bin/python -m python.external_sim.run

# 15c. External agent SCORES. Required for the Act 4 comparison -- this is the
#      only place the external agent gets judge metrics rather than just
#      latency traces.
#      Two commands: the ground-truth dataset, then the scoring run.
#      ~6 min (9 invocations plus 5 server-side metrics).
snow sql -c $CONN -f sql/08b_harness_scoring.sql
SF_CONNECTION=$CONN TRULENS_OTEL_TRACING=1 \
  .venv-harness/bin/python -m python.external_sim.score --run-name PARITY_EXTERNAL_V11
#      Expect 5 metrics x 9 records. If context_relevance or groundedness are
#      MISSING, the @instrument(RETRIEVAL) decorators in python/external_sim/tools.py
#      are gone -- those two metrics fail SILENTLY without a retrieval span
#      (GOTCHAS #22). `python scripts/doctor.py` now checks for this directly.
#      Runs are immutable: to re-score, use a new --run-name (it defaults to a
#      timestamped one), then update tests/test_06_agents.py::TestExternalAgentScored.

# 15d. Shared eval dataset for the apples-to-apples comparison. MUST run AFTER
#      08b: it reads EVAL.EXTERNAL_EVAL_DATASET, which 08b creates. Running it
#      first fails with 002003 (42S02) "object does not exist".
#      This builds EVAL.SHARED_EVAL_DATASET -- the same 9 questions in both the
#      VARCHAR shape the external scorer needs and the VARIANT
#      ground_truth_invocations shape the agent eval needs. Without it the
#      native-vs-external comparison cannot run at all.
snow sql -c $CONN -f sql/08c_shared_eval_dataset.sql

# 15e. The apples-to-apples comparison -- the headline result in README.md.
#      This evaluates the NATIVE agent on the same 9 questions and the same 5
#      metrics the external scorer used in 15c. Without BOTH halves there is no
#      comparison, only two unrelated numbers. ~8 min.
#      Custom metrics return the RAW 0-3 scale; the script's final SELECT
#      divides by 3 to reach the 0-1 scale score.py reports on.
snow sql -c $CONN -f sql/08d_parity_eval.sql

# 16. Verify
SF_CONNECTION=$CONN SF_ACCOUNT=<ACCT> python scripts/doctor.py
snow sql -c $CONN -q "USE ROLE SYSADMIN; USE WAREHOUSE AGENT_EVAL_DEMO_WH;
  EXECUTE NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING();"   # ~2.5 min, must exit 0
```

Each eval script contains a `CALL SYSTEM$WAIT(...)` before it snapshots,
because `EXECUTE_AI_EVALUATION('START')` returns immediately. If a snapshot
table lands empty, the run had not finished: re-run the `STATUS` call until it
returns exactly `COMPLETED` and then re-run the `CREATE OR REPLACE TABLE`.
Intermediate states include `INVOCATION_IN_PROGRESS`, `INGESTION_COMPLETED`,
and `COMPUTATION_IN_PROGRESS` — never match on a substring.

## Data reproducibility

`generate_data.py` is deterministic **within** one environment (verified by
regenerating twice and diffing row counts), but the parquet files currently
loaded on the primary demo account were produced by an earlier, partially re-run version of the
script. A fresh rebuild therefore differs on the six variable-cardinality
tables (ORDER_LINES, WAVES, MOVEMENTS, PICK_TASKS, SHIPMENTS, CARRIER_SCANS) by
a few hundred rows. Fixed-cardinality tables — ORDERS (40,000), ITEM_MASTER
(8,000), ON_HAND (96,000), EXCEPTIONS (2,000), FISCAL_CALENDAR_445 (514),
LABOR_STANDARDS (20), ZONE_RATE_CARDS (240) — match exactly, and so does the
Act 5 figure of 6,667 ALDERWOOD-visible orders.

## Eval score reproducibility

Rebuilt on a second account on 2026-08-14:

| Metric | the primary demo account | Rebuild | Reproduces? |
|---|---|---|---|
| `BASELINE_V1_FINAL` sql_correctness | 0.450 | 0.400 | within judge noise |
| `OPTIMIZED_V2_FINAL` sql_correctness | 0.700 | not re-sampled | — |
| baseline band (n=5) | 0.40–0.45 | not re-sampled | development-time; per-run evidence not shipped |
| optimized band (n=4) | 0.700 (all 4) | not re-sampled | development-time; per-run evidence not shipped |
| optimized > baseline | direction | yes | **yes** — the assertion the suite enforces |
| `TENANT_ISOLATION` score | 4.0833/10 | 4.9167/10 | close; breach count 8 of 12 vs 7 of 12 |
| agent `answer_correctness` | 0.70 (band 0.65–0.95) | 0.833 | in band |
| agent `tool_execution_accuracy` | 0.00 | 0.00 | exact — 0.00 is correct, see below |

The LLM judge is not deterministic — five runs of the UNCHANGED optimized view
scored 0.525, 0.625, 0.650, 0.625, 0.525. Quote the DIRECTION (optimized beat
baseline in every one of 25 pairings) and the band. Never promise a specific
decimal, and treat the agent tool metrics as indicative only, since tool routing
varied most.

Note the pre-2026-08-17 pair (0.325 -> 0.650, "+100%, zero regressions") is
superseded: it was measured against a FULFILLMENT_SV_V1 whose verified queries
joined an undeclared ZONE_RATE_CARDS, so the model failed to load and the 3 cost
questions scored 0 for a structural reason. Fixing the view raised the baseline.

## Important Session Context

**CRITICAL:** Before running `EXECUTE_AI_EVALUATION`, the session MUST be set to the schema where the semantic view/agent lives:

```sql
USE DATABASE AGENT_EVAL_DEMO;
USE SCHEMA AI;          -- Not PUBLIC, not EVAL
USE WAREHOUSE AGENT_EVAL_DEMO_WH;
```

Without this, the eval framework resolves objects in the wrong schema and metrics return 0 records.

## Network Policy

If your account enforces an account-level network policy, connections from an
address outside its allowed list fail with `390422`. A **user-level** policy on the
user running the build overrides the account policy for that user only. Check
whether one is bound before blaming the network:

```sql
SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN USER <YOUR_USER>;   -- expect level = USER
SELECT CURRENT_IP_ADDRESS();                           -- do not compare to a hardcoded IP
```

`DESC USER` does **not** expose `NETWORK_POLICY`. See GOTCHAS #11 — including why
`EVALUATE_CANDIDATE_NETWORK_POLICY` is not a trustworthy oracle here.

## Eval Run Names

The canonical runs are:
- `BASELINE_V1_FINAL` — baseline sql_correctness 0.450, band 0.40–0.45 (on `FULFILLMENT_SV_V1`, via
  `analyst_evaluation_config_v1.yaml`)
- `OPTIMIZED_V2_FINAL` — optimized sql_correctness 0.700 (on `FULFILLMENT_SV`, via
  `analyst_evaluation_config.yaml`)
- `AGENT_V4` — agent GPA eval, 4 metrics (via `agent_evaluation_config_v4.yaml`)
- `TENANT_ISOLATION_V2` — adversarial isolation eval

Re-running the same name behaves **differently** by eval type, which is easy to trip on:

- **Analyst evals version per run name** and re-run cleanly — no delete needed.
- **Agent evals do not.** The dataset version name is fixed
  (`SYSTEM_AI_OBS_CORTEX_AGENT_DATASET_VERSION_DO_NOT_DELETE`), so a second `START`
  on the same `dataset_name` always fails with `210007` regardless of the run name.
  Use a new `dataset_name` (that is why the v4/v5 configs exist) or drop the dataset.

To free a run name: `CALL EXECUTE_AI_EVALUATION('DELETE', OBJECT_CONSTRUCT('run_name','<name>'), '<yaml_path>')`.
Note this frees the name but does **not** purge observability events — see GOTCHAS #14.

## YAML Configs on Stage

```
@EVAL.CONFIGS/analyst_evaluation_config.yaml      — Analyst eval on FULFILLMENT_SV (v2)
@EVAL.CONFIGS/analyst_evaluation_config_v1.yaml   — Analyst eval on FULFILLMENT_SV_V1 (baseline)
@EVAL.CONFIGS/agent_evaluation_config.yaml        — Agent GPA eval (superseded)
@EVAL.CONFIGS/agent_evaluation_config_v4.yaml     — Agent GPA eval, AGENT_V4 (canonical)
@EVAL.CONFIGS/agent_evaluation_config_v5.yaml     — Agent GPA eval, spare dataset name
@EVAL.CONFIGS/tenant_isolation_eval_config.yaml   — Tenant isolation
```

Note the stage path has no `configs/` prefix — `LIST @EVAL.CONFIGS` prints names like
`configs/analyst_evaluation_config.yaml`, but that leading `configs/` is the **stage
name**, not a folder. `GET @EVAL.CONFIGS/configs/...` fails.

All uploaded with `AUTO_COMPRESS=FALSE`.

## Teardown

```sql
-- NOT sufficient on its own: compute pools and tenant roles are ACCOUNT-level
-- objects and SURVIVE a DROP DATABASE. Left behind, the two pools keep billing.
DROP DATABASE AGENT_EVAL_DEMO;
DROP WAREHOUSE AGENT_EVAL_DEMO_WH;
DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_NB_POOL;
DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_APP_POOL;
-- plus the tenant roles created by 02_governance.sql
```

Prefer the teardown script, which handles all of the above and then re-checks for
residue with `SHOW COMPUTE POOLS LIKE 'AGENT_EVAL_DEMO_%_POOL'`:
```bash
snow sql -c my_snowflake_connection -f sql/AGENT_EVAL_DEMO_TEARDOWN.sql
```

## Object Inventory

| Schema | Objects |
|--------|---------|
| FULFILLMENT_INTELLIGENCE | ORDERS, ORDER_LINES, WAVES, EXCEPTIONS, FISCAL_CALENDAR_445 |
| INVENTORY_INTELLIGENCE | ITEM_MASTER, ON_HAND, MOVEMENTS |
| LABOR_INTELLIGENCE | PICK_TASKS, LABOR_STANDARDS |
| SHIPPING_INTELLIGENCE | SHIPMENTS, CARRIER_SCANS, ZONE_RATE_CARDS |
| AI | FULFILLMENT_SV, FULFILLMENT_SV_V1, SHIPPING_SV, ITEM_CATALOG_SEARCH, OPS_KNOWLEDGE_SEARCH, FULFILLMENT_ANALYST, EXTERNAL_SIM, EVAL_CICD_GATING (notebook, SYSADMIN-owned) |
| EVAL | CONFIGS stage, *_RESULTS tables, *_DATASET tables, ANALYST_GROUND_TRUTH |
| OPS | TENANT_ROLE_MAPPING, TENANT_ISOLATION_POLICY (RAP, on 8 tables), STREAMLIT_STAGE, OBSERVABILITY_APP, helper views |
| *(account-level — survives `DROP DATABASE`)* | `AGENT_EVAL_DEMO_NB_POOL`, `AGENT_EVAL_DEMO_APP_POOL`, tenant roles |
