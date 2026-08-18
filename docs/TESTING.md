# Testing Strategy

## Tiers

| Tier | Marker | Needs VPN | Purpose | Command |
|---|---|---|---|---|
| Offline | `offline` | No | Pure Python checks: data-gen logic, SQL syntax, YAML validity, doc hygiene | `make test-offline` |
| Preflight | `preflight` | Yes | Fast go/no-go before a customer call (<60s) | `make doctor` |
| Data | `data` | Yes | Row counts, timestamp sanity, the six ambiguity traps | `make data` |
| Governance | `governance` | Yes | Row access policy + tenant role isolation | `make governance` |
| Search | `search` | Yes | Cortex Search services serving | `make search` |
| Semantic | `semantic` | Yes | Semantic view structure, v1-vs-v2, verified query parity | `make semantic` |
| Evals | `evals` | Yes | Persisted evaluation runs and their scores | `make evals` |
| Agents | `agents` | Yes | Native + external agent traces, GPA metrics | `make agents` |
| Claims | `claims` | Yes | CLAIM AUDIT: every documented number backed by live evidence | `make claims` |
| Repro | `repro` | Yes | Committed .sql reproduces the live objects | `make repro` |

## Running Tests

```bash
# Full suite (needs VPN)
make test

# Offline only (CI, airplane, no VPN)
make test-offline

# Single tier
make data
make claims

# Or directly:
.venv-test/bin/pytest -m offline -v
.venv-test/bin/pytest -m "data or governance" -v
```

## What Each Guards

### Offline (`test_09_offline.py`)
- `generate_data.py` DATE_ONLY_COLUMNS matches the 4 DATE-typed tables
- `to_date32()` actually produces `date` objects
- Short-pick logic can produce partial quantities (ambiguity trap 2 non-degenerate)
- Every `.sql` file has balanced quotes and a trailing semicolon
- Every `eval_configs/*.yaml` is valid YAML with a `metrics` list
- `DEMO_GUIDE.md` contains all mandatory sections and no superseded numbers

### Preflight (`test_00_preflight.py`)
- Connection works (VPN up, account reachable)
- Warehouse responsive
- Correct object counts (tables, semantic views, search services, agents)

### Claims (`test_07_claims.py`)
- EXACT match between numbers in docs (DEMO_GUIDE.md, README.md) and the live account
- This is the **only** tier with exact assertions — score tiers use ranges

### SQL static guards (`test_10_sql_static.py`, offline)

Guards the **committed SQL**, not the live objects — these catch *rebuild*
regressions that every live test misses.

Why this exists: during the build, several defects were fixed on the live object
and never written back to the file. `test_04_semantic.py` has a 392700 guard, but
it inspects the live view, so it stayed green while `sql/04_semantic_v1.sql`
would have recreated `SHIPPING_SV` **without** `CARRIER_SCANS.SCAN_ID` — silently
reintroducing error 392700 on the next rebuild.

**23 guards.** Semantic-view structure:

- every PRIMARY KEY column is also projected as a logical column
- every column used in a RELATIONSHIP is declared, with a **matching alias**
  (relationships resolve by logical name; aliasing `SHIPMENT_ID` to
  `CS_SHIPMENT_ID` fails with `000904 invalid identifier`)
- every column referenced by a **verified query** is declared in the logical model.
  This asymmetry is subtle and cost real time: metric expressions resolve against
  *physical* columns, but verified-query SQL resolves against the *logical* model, so
  an undeclared column invalidates the whole view even though the physical column
  exists (`invalid identifier 'S.SHIP_BY_DATE'`). The guard splits multi-view files
  per view — an earlier whole-file version merged two logical models and produced a
  false failure.
- pinned regression test for the SCAN_ID omission specifically

SQL hygiene (each of these was a real, shipped defect):

- no `rows` as a column alias — it is reserved and fails with `001003`
- no adjacent string literals (`'a' 'b'` does not concatenate in Snowflake)
- no `--` or `;` **inside** a string literal — either one breaks naive
  comment-stripping and statement-splitting, including this suite's own parsers

Pipeline / rebuild integrity:

- every canonical result table is created by some committed script
- the Streamlit app only reads tables some script actually creates (it once read a
  nonexistent `TENANT_ISOLATION_V1_RESULTS`, silently breaking 2 of 7 tabs)
- every snapshotted eval run is started by some committed script
- eval scripts `WAIT` before snapshotting (a snapshot of a half-finished run
  produced zero metric rows)
- the baseline targets the frozen `FULFILLMENT_SV_V1`, never the mutated view
- `sql/06_semantic_v2.sql` stays **pure DDL** (exactly one substantive statement) so
  the live-drift test can diff it
- teardown drops the account-level tenant roles and both compute pools

Notebook + docs:

- notebook declares `RUNTIME_NAME` and uses a dedicated compute pool
- notebook reads completed runs and does **not** start evaluations
- docs state the correct improved count, the canonical score pair, no superseded
  numbers, and Act 4 uses the `ERROR` column rather than `STATUS`

Rule of thumb: **live tests prove the demo works today; static tests prove it can
be rebuilt.** You need both.

## Proof the suite actually bites

A green suite is worthless if the assertions are vacuous, so the guards were
mutation-tested:

| Mutation | Caught by |
|---|---|
| Inflate the headline optimized score to `0.850` in DEMO_GUIDE.md | `test_07_claims::test_optimized_score_0650` |
| Reintroduce the superseded `0.341` into a doc | `test_09_offline::test_no_superseded_numbers` |

Both mutations turned the suite red and were reverted. If you add a claim to the
docs, add its assertion to `test_07_claims.py` — otherwise it is unguarded.

## When Something Goes Red

| Tier | What broke | Fix |
|---|---|---|
| Offline | SQL file malformed | Edit the `.sql` file |
| Offline | Superseded number in docs | Remove/replace in DEMO_GUIDE.md |
| Offline | YAML config invalid | Fix the YAML |
| Preflight | 390422 | Reconnect VPN |
| Data | Row count changed | Re-run `python/generate_data.py` + `sql/01_load_data.sql` |
| Governance | RAP not filtering | Check RAP body references the correct session variable |
| Semantic | View DDL drifted | Compare GET_DDL output against committed SQL |
| Evals | Score out of band | Re-run the eval, check question set wasn't changed |
| Claims | Doc claims wrong number | Update the doc to match reality, or re-run the eval |
| Repro | Committed SQL doesn't match live | GET_DDL the live object and reconcile |

## Score-Assertion Philosophy

Eval scores come from an LLM judge. Non-determinism means a re-run will not
reproduce a stored score exactly. Therefore:

- **Live tiers** (`evals`, `agents`) assert **ranges** from `EXPECTED_RUNS` in conftest
- **Claims tier** asserts **exact** matches against the specific numbers written in docs

This split catches both: (a) a real regression where scores fall off a cliff, and
(b) doc drift where someone writes "0.80" when reality is "0.75".
