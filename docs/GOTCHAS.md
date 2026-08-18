# Platform Gotchas

Every item below was hit while building this demo. Error codes are exact.

---

## 1. 392700 — Key column must be logical column

**Error:** `392700: Column referenced in primary key or relationship must also be declared as a logical column.`

**Symptom:** `CREATE SEMANTIC VIEW` succeeds, but Cortex Analyst silently refuses queries involving that view's tables.

**Fix:** Every column named in a `primary key (...)` or `relationship (...)` clause must ALSO appear in the `columns (...)` block of the same table as a logical column with a type and description.

---

## 2. Metrics compute 0 records — session schema

**Error:** Eval run returns 0 results with no error.

**Root cause:** SQL-driven evaluations resolve the tracking dataset against the *session* schema. If the session is in `PUBLIC` but the eval objects live in `AI`, the judge never finds the UDTF.

**Fix:** Always set `USE SCHEMA AI` in the same session *before* calling `EXECUTE_AI_EVALUATION`. The demo guide's pre-flight does this.

---

## 3. TSA needs snake_case tool names

**Error:** `tool_selection_accuracy` silently scores 0.00.

**Root cause:** The ground-truth `tool_name` field in the eval dataset must use the **normalized snake_case** form as it appears in traces (e.g., `fulfillment_data`), NOT the agent spec's display name (`Fulfillment Data`). Display names silently zero TSA because the judge does exact string matching.

**Fix:** Inspect actual trace spans to find the canonical tool name:
```sql
SELECT DISTINCT SPAN_NAME
FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
    'AGENT_EVAL_DEMO','AI','FULFILLMENT_ANALYST','CORTEX AGENT'))
WHERE SPAN_TYPE = 'tool';
```

---

## 4. TEA needs prose tool_input / tool_output

**Error:** `tool_execution_accuracy` scores a flat 0.00.

**Root cause:** Documentation says `tool_input` and `tool_output` in the ground truth are optional. In practice, omitting them yields TEA 0.00. Rigid JSON (e.g., `{"query": "SELECT ..."}`) also scores 0 — the judge interprets these fields semantically as **prose**.

**Fix:** Write ground truth `tool_input` and `tool_output` as natural-language descriptions:
```
tool_input: "Query to find all orders shipped late relative to SHIP_BY_DATE for tenant T001 in fiscal period 3"
tool_output: "Returns a table with ORDER_ID, SHIP_BY_DATE, actual ship date, and days late"
```

---

## 5. Datasets are versioned snapshots — 210007

**Error:** `210007: Dataset version SYSTEM_AI_OBS_CORTEX_AGENT_DATASET_VERSION_DO_NOT_DELETE already exists.`

**Root cause:** A Snowflake Dataset is a **versioned snapshot** of the source table at creation time. Updating the source table does NOT change an existing dataset. Reusing the same `dataset_name` after correcting data fails with 210007.

**Fix:** When correcting ground truth:
1. Update the source table (`EVAL.AGENT_EVAL_DATASET`)
2. Use a NEW `dataset_name` in the YAML (e.g., `AGENT_EVAL_DATASET_V4`)
3. If you want to reuse an already-created dataset without re-snapshotting, omit the `dataset:` block entirely and keep only `source_metadata.dataset_name`.

---

## 6. Snowpark qmark paramstyle — unexpected '%'

**Error:** `ProgrammingError: unexpected '%'` when using `%s` bindings.

**Root cause:** Snowpark connections use `qmark` paramstyle (the `?` placeholder), not `format` (`%s`). Any code using `cursor.execute("SELECT ... WHERE x = %s", (val,))` will fail.

**Fix:** Use `?` for Snowpark session cursors, or open a separate `snowflake.connector.connect()` connection (which also uses qmark by default, but at least you control it). The test harness uses `snowflake.connector` directly.

---

## 7. TruLens 2.12 import and compatibility

**Errors:** Various import failures and KeyErrors.

**Key facts:**
- `TruApp` lives in `trulens.apps.app`, NOT `trulens.core.app`
- `TRULENS_OTEL_TRACING=1` must be set **before** the first trulens import
- `SnowflakeConnector` takes `snowpark_session=` (not `connection=`)
- `@instrument(attributes={...})` raises `KeyError` — attributes are **selectors**, not literal dicts
- Python must be <3.12 (trulens-connectors-snowflake pins this)

**Fix:** The harness venv (`.venv-harness`) is pinned to Python 3.11. Do not mix with the test venv.

---

## 8. Parquet DATE columns — 100071

**Error:** `100071 (22000): Failed to cast variant value "2025-01-01 00:00:00.000" to DATE`

**Root cause:** Columns declared `DATE` in the Snowflake DDL need Parquet files to carry those columns as `date32` dtype. Using `coerce_timestamps='us'` only sets timestamp precision — it does NOT change the dtype.

**Fix:** Call `to_date32(df, table_name)` before `.to_parquet()` for tables listed in `DATE_ONLY_COLUMNS`. This converts `Timestamp` values to Python `date` objects, which PyArrow writes as `date32`.

---

## 9. AI_OBSERVABILITY_EVENTS is immutable

**Consequence:** Bad turns cannot be deleted. The 18 error turns on `EXTERNAL_SIM` (from instrumentation bugs in V2/V3) are permanent.

**Demo strategy:** Own it — say that the errors are captured *because* observability works.

**Do NOT filter on `STATUS`.** All 99 `EXTERNAL_SIM` spans carry
`STATUS = 'STATUS_CODE_UNSET'`, so `WHERE STATUS = 'success'` returns **zero rows** —
verified live on the primary demo account. Use the `ERROR` column instead, which is the only reliable
success/failure signal here:

```sql
-- clean tiles: successful spans
... WHERE ERROR IS NULL
-- the 18 failed turns
... WHERE ERROR IS NOT NULL
```

If you show a filtered tile, say that you filtered it.

---

## 10. CREATE OR REPLACE on semantic view destroys v1

**Error:** Running `CREATE OR REPLACE SEMANTIC VIEW FULFILLMENT_SV` with the v2 definition obliterates the v1 definition entirely. There is no versioning.

**Fix:** Captured v1 as a separate object `FULFILLMENT_SV_V1` (see `04b_semantic_v1_frozen.sql`). The baseline re-run targets `FULFILLMENT_SV_V1`, not `FULFILLMENT_SV`.

---

## 11. 390422 — Network policy blocks the connection

**Error:** `390422: IP x.x.x.x is not allowed to access Snowflake.`

**Root cause:** the account enforces an account-level network policy whose allowed
list does not include your current egress address. On a corporate account these
entries are often all `/32` singletons (one per VPN egress), so any change in your
network path — reconnecting to a different VPN region, working off-VPN, a new
office — breaks every connection this repo makes.

**Fix:** attach a **user-level** network policy to the user running the build.
A user-level policy overrides the account-level one for that user only, leaving
the account policy protecting everyone else — "the most specific network policy
overrides more general network policies"
(https://docs.snowflake.com/en/user-guide/network-policies).

```sql
CREATE NETWORK POLICY MY_OPEN_POLICY ALLOWED_IP_LIST = ('0.0.0.0/0');
ALTER USER <YOUR_USER> SET NETWORK_POLICY = MY_OPEN_POLICY;
```

Verify with `SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN USER <YOUR_USER>` and expect
`level = USER`. **`DESC USER` does not expose it** — that is the trap, and it is
easy to conclude the policy did not attach.

`0.0.0.0/0` is deliberately permissive and appropriate only for a personal demo
account. Scope it to real CIDRs on anything shared.

To revert: `ALTER USER <YOUR_USER> UNSET NETWORK_POLICY;`

**Related:** `run.py` and `score.py` support an optional `SF_EXPECTED_EGRESS_IP`
env var. Set it to fail fast with a clear message instead of dying partway
through a run that has already spent credits. Unset, the check is skipped.

**Do not use `EVALUATE_CANDIDATE_NETWORK_POLICY` as an oracle here.** It reported
`IS_ALLOWED = NO` for an address that was both an explicit `/32` entry in the account
policy *and* the address the session was actively connected from at that moment. A
positive control — asking it about an address that demonstrably worked — disproved the
instrument rather than the configuration. When a diagnostic function contradicts an
observable fact, validate the function before you act on it.

---

## 12. CALL validation and function discovery

**Limitation:** `only_compile=true` cannot validate `CALL` statements. `SNOWFLAKE.INFORMATION_SCHEMA.FUNCTIONS` does not list eval UDTFs.

**Workaround:** Use `SHOW FUNCTIONS IN ACCOUNT` or `SHOW PROCEDURES IN ACCOUNT` to discover available callables. Or just run the CALL against a throwaway run name.

---

## 13. `CREATE OR REPLACE AGENT` destroys all AI observability history

**Symptom:** After replacing the agent, `GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(... 'CORTEX AGENT')` returned `(0, 0)` — 935 events and every prior agent eval run vanished. The native-agent event-count claim silently became false.

**Root cause:** Replacing an AGENT object discards its observability lineage. This is NOT symmetric with semantic views, where `CREATE OR REPLACE` preserves events.

**Consequence:** The external agent (`EXTERNAL_SIM`) kept every event it had (99 at the time; 324 now) because it was untouched, which made the asymmetry obvious.

**Fix / avoidance:** If you only need to change instructions or tools, prefer `ALTER AGENT` over `CREATE OR REPLACE AGENT`. If you must replace it, plan to re-run every agent eval afterwards and re-measure any event-count claim.

---

## 14. `EXECUTE_AI_EVALUATION('DELETE')` frees the run NAME but does NOT purge events

**Symptom:** After deleting `OPTIMIZED_V2_FINAL` and re-running it, the raw event count went 62 → 124 → 186. Old pre-rename spans stayed queryable under the same run name.

**Root cause:** `DELETE` clears the run registration (which is what unblocks `210007 Ingestion already executed`), but observability spans persist in the backing event table.

**Also tested and FALSE:** `DROP SEMANTIC VIEW` appears to purge — the events function returns 0 while the object is absent — but recreating the view under the SAME name brings every old event back. Events are keyed by object name, not object identity.

**Consequence for this demo:** 22 raw spans on `FULFILLMENT_SV` still contain pre-rename tenant names. They are NOT reachable from any demo surface: `GET_ANALYST_AI_EVALUATION_DATA` for the canonical runs returns 0 old-name questions (asserted by `test_07_claims`). Only a deliberate `SELECT` against the normalized events function with a text filter surfaces them.

**If you need a genuinely clean slate:** use a NEW object name, or rebuild the database. Deleting runs is not sufficient.

---

## 15. LLM-judge metrics are not deterministic — quote bands, not point estimates

**Observation:** Five runs of the optimized eval against an IDENTICAL semantic view and an IDENTICAL set of 20 verified queries scored **0.525, 0.625, 0.650, 0.625, 0.525** — a 0.125 spread with zero code change. An earlier pair of runs scored 0.70 and 0.65.

**Consequence:** Exact-equality assertions on judge scores fail on every re-run for reasons unrelated to doc drift. `test_07_claims` checks judge-scored claims within `JUDGE_TOL = 0.06` — note the observed spread (0.125) EXCEEDS that tolerance, which is why the docs lead with a band and the guard also asserts the ordering claim (`min(optimized) > max(baseline)`) rather than any decimal. Deterministic claims (row counts, question counts) stay exact.

**Demo framing:** this is a feature, not an embarrassment. It is the argument for evaluating on a fixed question set and tracking a band over time rather than chasing a single number.

---

## 16. Verified queries resolve against the LOGICAL model, metrics against PHYSICAL columns

**Error:** `Invalid semantic model yaml / SQL compilation error: error line 8 at position 50 invalid identifier 'S.SHIP_BY_DATE'` — raised when Cortex Analyst loads the view, not when you create it.

**Root cause:** An asymmetry that hides the bug. A metric expression may reference any *physical* column of a declared table. A **verified query's SQL** resolves only against the *logical* model — the columns you actually projected in `facts`/`dimensions`. So a verified query can reference `SHIPMENTS.SHIP_BY_DATE`, the physical column can exist, `CREATE SEMANTIC VIEW` can succeed, and the whole model still fails to load.

**Fix:** Every column any verified query touches must be declared as a logical column. Guarded statically by `test_10_sql_static::test_verified_query_columns_are_declared_in_the_model`.

**Trap when writing that guard:** `sql/04_semantic_v1.sql` defines **two** views. A whole-file version of the check merged both logical models and emitted a false `ZONE_RATE_CARDS.CARRIER` failure. Split per view before trusting it — fix the instrument before editing SQL on its say-so.

---

## 17. Eval status — never substring-match `COMPLETED`

**Symptom:** A snapshot taken from a "completed" run returned 0 metric rows.

**Root cause:** `EXECUTE_AI_EVALUATION('STATUS', ...)` moves through `CREATED` → `INVOCATION_IN_PROGRESS` → `INVOCATION_COMPLETED` → `COMPUTATION_IN_PROGRESS` → `COMPLETED`. `INGESTION_COMPLETED` and `INVOCATION_COMPLETED` both **contain** the string `COMPLETED`, so `IF 'COMPLETED' IN status` matches a half-finished run.

**Fix:** Compare the parsed status for **exact** equality with `COMPLETED`. Every eval script also calls `SYSTEM$WAIT` before snapshotting, and asserts the snapshot is non-empty (`THEN 1/0` forces `100051 Division by zero` rather than silently storing nothing). Docs: https://docs.snowflake.com/en/sql-reference/functions/execute_ai_evaluation

---

## 18. Streamlit in a warehouse runtime ships an old Streamlit

**Error:** `TypeError: DataFrameSelectorMixin.dataframe() got an unexpected keyword argument 'hide_index'`

**Root cause:** The warehouse runtime's bundled Streamlit predates `hide_index`, so the app died on the first tab.

**Fix:** Container Runtime with a pinned version — `RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'`, a `COMPUTE_POOL`, `EXTERNAL_ACCESS_INTEGRATIONS = (PYPI_ACCESS_INTEGRATION)`, and `GRANT BIND SERVICE ENDPOINT ON ACCOUNT`. Use `FROM` (not the legacy `ROOT_LOCATION`). With a `pyproject.toml` present **nothing is pre-installed** — you must declare `streamlit[snowflake]` explicitly.

Related traps:
- Neither `SHOW STREAMLITS` nor `INFORMATION_SCHEMA.STREAMLITS` exposes `runtime_name` or `compute_pool`. Only `DESCRIBE STREAMLIT` does — same trap as `SHOW NOTEBOOKS`.
- `ALTER STREAMLIT ... ADD LIVE VERSION FROM LAST` fails with `099106` after `CREATE OR REPLACE`, which already sets one.
- `use_container_width` is superseded by `width` (`width='stretch'`). On 1.61.1 both still work and `width` already defaults to `'stretch'`.
- The app must be re-created after uploading new files to the stage, and the first open installs packages from PyPI — open it once before a demo to warm the container.

---

## 19. `rows` is a reserved word; adjacent string literals do not concatenate

**Errors:** `001003 (42000): SQL compilation error: syntax error line N at position M unexpected 'rows'`, and `001003` again for `'part one' 'part two'`.

**Root cause:** `COUNT(*) AS rows` is invalid. Separately, Snowflake does **not** join adjacent string literals the way C or Python do — `'a' 'b'` is a syntax error, not `'ab'`.

**Consequence worth noting:** `sql/01_load_data.sql` failed with exit 1 *after* every `COPY INTO` had already succeeded, which makes it look like a load failure when it is a cosmetic alias bug.

**Fix:** `AS row_count`; write one literal. Both are statically guarded.

---

## 20. `--` or `;` inside a string literal breaks naive SQL parsers

**Symptom:** A single added comment produced 463 "unbalanced quote" failures; a later fix split one `CREATE` statement into two.

**Root cause:** Putting `-- do NOT use ...` inside a quoted column comment makes a comment-stripper eat the closing quote. Putting `;` inside one makes a statement-splitter cut the statement in half. This suite's own parsers have both behaviours, and so do plenty of migration tools.

**Fix:** Use a period instead. Guarded by `test_no_comment_delimiters_inside_sql_string_literals`, which on introduction immediately found **two pre-existing** instances in `04_semantic_v1.sql` and `04c_shipping_sv.sql`.

---

## 21. `COPY INTO` from an empty stage succeeds with 0 rows and exit 0

**Symptom:** A load script "passes" and every downstream count is zero.

**Root cause:** `COPY INTO` finding no files is not an error.

**Fix:** Assert row counts after loading rather than trusting the exit code. Related: `PUT` needs `AUTO_COMPRESS = FALSE` for `.parquet`, or the files land gzipped and the load silently matches nothing.

---

## 22. An external agent emits no `RETRIEVAL` span unless you ask for one — and two metrics silently become uncomputable

**Symptom:** `run.compute_metrics()` on the `EXTERNAL_SIM` external agent returned scores for `coherence`, `answer_relevance` and `correctness` but nothing for `context_relevance` or `groundedness`. No error anywhere.

**Root cause:** Those two metrics read `RETRIEVAL.QUERY_TEXT` and `RETRIEVAL.RETRIEVED_CONTEXTS`. The orchestrator carried five `@instrument` decorators (`graph_node`, `tool`, `generation`, `agent`, `record_root`) and `tools.py` carried **none**, so no span of type `retrieval` was ever emitted. A metric whose required attributes are absent does not fail — it just does not appear.

**Fix:** Instrument the tool methods as `RETRIEVAL` with an `attributes=` callback (`python/external_sim/tools.py`). Measured before/after on the same app: 5 span types → 6 (`retrieval` = 10 spans over 9 records), and metrics computed went 3 of 5 → **5 of 5**. `query_analyst` is included deliberately even though it is text-to-SQL rather than vector search: the responder grounds on those rows, so omitting it would score groundedness against a partial context set.

Note the `attributes=` callback must not raise — on an exception `ret` is `None`, and a throwing callback loses the whole span, hiding the very failure you want to see. Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/ai-observability/reference

---

## 23. Tool-trajectory metrics are NOT available for external agents — and both failure modes are silent

**Symptom:** Trying to give the external agent the equivalent of native `tool_selection_accuracy` / `tool_execution_accuracy`. Both available routes report success and produce nothing.

**Route 1 — server-side by name.** `run.compute_metrics(metrics=["tool_selection"])` is accepted by the SDK, dispatched as a server-side metric, and returns `"Metrics computation in progress."`. It never computes. `GET_AI_OBSERVABILITY_LOGS` (note: **4 arguments**, not 5 — passing a run name is a compile error) shows the server parsing the metric *name* as a JSON custom-metric definition:

```
com.fasterxml.jackson.core.JsonParseException:
Unrecognized token 'tool_selection': was expecting (JSON String, Number,
Array, Object or token 'null', 'true' or 'false')
  at ComputeAIObservabilityMetricsProcedure.runInternal(...:196)
```

Per-metric `completion_status` in `run.describe()` is `FAILED` / `record_count: 0`, while the five documented metrics are `COMPLETED` / `9`. Same result for `tool_calling`, `plan_quality`, `logical_consistency`. Conclusion: the five documented metrics **are** the server-side set; an unrecognized name is treated as a custom-metric JSON blob.

**Route 2 — client-side `Metric` objects.** `trulens.feedback.llm_provider` really does ship seven trace-input judges (`tool_selection_with_cot_reasons`, `tool_calling_with_cot_reasons`, `plan_quality_with_cot_reasons`, `plan_adherence…`, `execution_efficiency…`, `tool_quality…`, `logical_consistency…`), and wiring them as `Metric(implementation=…)` logs `Successfully computed client-side metric`. The scores then **never persist** — zero rows in `AI_OBSERVABILITY_EVENTS` for those names, so they cannot be read back or compared against the server-side scores.

**Fix:** There isn't one on this path with TruLens 2.12.0. Do **not** claim trajectory parity with native Cortex Agent evals. `--trajectory` in `score.py` exists only to re-test when the capability lands. If trajectory scoring is genuinely required, the route is a custom metric supplied as a proper JSON definition — the mechanism native agent evals use, where the prompt can reference `{{tool_info}}` and `{{span_type}}`.

Related trap: a client-side metric whose events aren't queryable yet logs `No events found for app …` at WARNING and **returns silently**. Nothing configures TruLens logging by default, so that warning is invisible. Enable `logging.basicConfig(level=logging.INFO)` before diagnosing any client-side metric.

---

## 24. `INVOCATION_COMPLETED` contains `COMPLETED` — the same substring trap, now on the TruLens run API

**Symptom:** `score.py` printed `NO SCORES. Metrics did not compute` and exited. Four metrics landed 90 seconds later.

**Root cause:** The same class of bug as #17, on a different API. `Run.get_status()` returns `RunStatus.INVOCATION_COMPLETED` once the app has finished running, long before any judge has scored anything. A poll that accepts any status containing `COMPLETED` returns immediately, so the read-back races the judge.

**Fix:** Two distinct predicates — `wait_for_invocation()` (accepts `INVOCATION_COMPLETED`) and `wait_for_metrics()`, which strips the `INVOCATION_*` variants *before* testing for `COMPLETED`. Unit-tested against all seven documented `RunStatus` values. Also note `run.compute_metrics()` is asynchronous and a metric **cannot be recomputed** for the same run — re-scoring needs a new `run_name`, the same immutability trap as #14.


---

## 25. Three score scales in one result column, and a custom-metric name collision

**Symptom:** the native agent's `correctness` averaged 0.284 while the external
agent averaged 0.444 — the governed agent apparently losing on the metric the
whole demo is about. Every other metric favoured the native agent by a wide
margin, which is what made it worth a second look.

**Root cause: `EVAL_AGG_SCORE` carries whatever range each metric declared —
three different ranges across this repo — and we normalized one of them twice.**

Query the distribution before trusting any aggregate:

```sql
SELECT LOWER(METRIC_NAME) AS metric,
       MIN(EVAL_AGG_SCORE), MAX(EVAL_AGG_SCORE),
       ARRAY_AGG(DISTINCT ROUND(EVAL_AGG_SCORE,2)) AS values_seen
FROM EVAL.PARITY_AGENT_V4_RESULTS
WHERE METRIC_NAME IS NOT NULL GROUP BY 1;
```

Measured on one run, with all five custom metrics declared with **identical**
`score_ranges` on 0–3:

| Metric | Values seen | Actual scale |
|---|---|---|
| coherence, answer_relevance | `[3]` | raw 0–3 |
| context_relevance | `[2,3]` | raw 0–3 |
| groundedness | `[2, 2.8, 3]` | raw 0–3 |
| **correctness** | `[0.33, 0.67, 1]` | **already 0–1** |
| answer_correctness, logical_consistency, TSA, TEA | `[0, 0.33, 0.5, 0.67, 1]` | natively 0–1 (GPA system metrics) |
| sql_correctness (baseline/optimized runs) | `[0, 0.5, 1]` | 0–1 |
| **tenant_isolation** (`TENANT_ISOLATION_V2`) | max `10`, mean `4.0833` | **1–10 as declared** |

**A custom metric named `correctness` does not behave like the other customs.**
It collides with the built-in `answer_correctness` family: it returned values
already normalized to 0–1, and its per-question scores were identical to
`answer_correctness` on all 9 questions (though the judge explanations differed,
so it is not simply an alias). The declared 0–3 `score_ranges` was ignored.

Dividing it by 3 a second time turned **0.852 into 0.284** and inverted the
headline comparison. Nothing was wrong with the agent, the golds, or the judge.

**Fix — detect the ceiling, never assume it.** Note that `> 1 means divide by
3` is *also* wrong: it turns `tenant_isolation`'s 4.0833 into 1.361. Snap to the
smallest declared ceiling at or above the observed max:

```sql
-- per metric, over the rows you are about to average
CASE WHEN MAX(EVAL_AGG_SCORE) <= 1  THEN 1.0
     WHEN MAX(EVAL_AGG_SCORE) <= 3  THEN 3.0
     WHEN MAX(EVAL_AGG_SCORE) <= 5  THEN 5.0
     ELSE 10.0
END AS scale                       -- then: AVG(EVAL_AGG_SCORE) / scale
```

This reproduces every documented figure: sql_correctness 0.45 and 0.70,
groundedness 2.6444/3 = 0.881, context_relevance 2.8889/3 = 0.963,
tenant_isolation 4.0833/10 = 0.408. `sql/08d_parity_eval.sql` divides by a
literal 3.0 because it reads a single run containing only 0–1 and 0–3 metrics;
`streamlit/observability_app.py` uses the ladder because it unions runs that
include `tenant_isolation`. Always surface the divisor next to the result — the
ceiling is inferred from observed scores, not read from the YAML, so a 1–10
metric that never scored above 3 would be normalized against 3.

**Two lessons worth more than the fix:**

1. **Do not name a custom metric after a built-in one.** Pick a name that cannot
   collide — `answer_accuracy_custom`, not `correctness`. You will not get a
   warning; you will get a silently different scale.
2. **An anomaly that contradicts every other signal is usually your own
   arithmetic.** Four metrics said the native agent led by 0.1–0.55; one said it
   trailed by 0.16. The outlier was the bug. We initially wrote a long, plausible
   explanation for the inversion — judge harshness, small-sample
   non-determinism — before checking the scale. In a demo about measurement, the
   measurement code deserves the same scrutiny as the thing measured.
