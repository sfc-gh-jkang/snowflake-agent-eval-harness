# Snowflake Agent Evaluation Harness

A runnable demo that answers one question: **how do you know your AI agent got better?**

It builds two agents over the same synthetic 3PL fulfilment dataset, scores them
against a gold standard, and shows the score move when the only thing that changes
is the quality of the semantic layer underneath them.

Everything here is measured on a real Snowflake account, not asserted. Where a
number appears, the run name, model, judge and date appear with it.

---

## The result

Two independent measurements, both on the same account on 2026-08-18.

**1. Semantic view quality moves Cortex Analyst accuracy.** Same 20 questions, same
judge, same data — the only variable is the semantic view.

| | `sql_correctness` |
|---|---|
| Weak semantic view (vague names, no metrics, no joins) | **0.450** |
| Optimized semantic view (synonyms, metric definitions, declared joins) | **0.700** |

Per-question: 7 improved, 0 regressed, 13 unchanged. Because LLM judges are not
deterministic, both sides were sampled: the weak view landed 0.40–0.45 across 5
runs (mean 0.44), the optimized view returned 0.700 on all 4. **Quote the direction
and the band, not the third decimal.**

**2. The governed layer wins on every metric.** A native Cortex Agent and an
external orchestrator were put on an identical footing — same 9 questions, both
pinned to `claude-opus-4-8`, both graded by `claude-4-sonnet` — and scored on the
same five metrics. Measured on a clean rebuild with every numeric gold verified
against the SQL that defines it:

| Metric | Native Cortex Agent | External orchestrator | Gap |
|---|---|---|---|
| **groundedness** | **0.881** | **0.327** | **+0.554** |
| **context_relevance** | **0.963** | **0.444** | **+0.519** |
| **correctness** | **0.852** | **0.444** | **+0.408** |
| answer_relevance | 1.000 | 0.889 | +0.111 |
| coherence | 1.000 | 0.963 | +0.037 |

Both agents can reach the same tables. The native agent answers through a semantic
view with verified queries — curated SQL reused verbatim rather than generated. The
external one writes its own SQL and has to *guess* every ambiguous definition.

The two grounding metrics show a **2.7x and 2.2x** gap, and correctness is
**1.9x**. `groundedness` asks whether the answer is supported by what was actually
retrieved; `context_relevance` whether the right context was retrieved at all.

The mechanism, caught in the traces. Asked for the on-time shipping rate, the
external agent defined on-time as `SHIP_DATE <= SHIP_BY_DATE` and answered
**82.2%**. The verified definition is `CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE`,
which gives **61.6%** — what the native agent returned (61.5%). Same data, same
question, same model. The external agent picked a defensible-sounding column and
was wrong by 20 points, with no error raised. Asked for eaches shipped it returned
**989,823** against a verified **1,044,102**, having joined through `SHIPMENTS`
instead of `ORDERS`.

That is the whole thesis: a confident number is not a correct one, and the
semantic layer is what encodes which definition is *the* definition.

### Why the gap exists — four mechanisms, each measured

**1. The native agent reuses curated SQL; the external one writes it every time.**
Of the native agent's SQL executions in these runs, **21 of 39 carried
`verified_query_used: true`** — the semantic view's verified queries answered the
question with SQL a human had already reviewed. The external agent generates fresh
SQL for all nine questions. Reused-and-reviewed beats generated-and-plausible.

```sql
-- count it yourself on your own build
SELECT REGEXP_SUBSTR(RECORD_ATTRIBUTES::VARCHAR,
         'verified_query_used[\\":]{1,12}(true|false)', 1, 1, 'i') AS vq, COUNT(*)
FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS(
  'AGENT_EVAL_DEMO','AI','FULFILLMENT_ANALYST','CORTEX AGENT'))
WHERE RECORD:name::VARCHAR ILIKE '%ExecuteSQL%' GROUP BY 1;
```

**2. Definitions live in the semantic model, and the external agent cannot read
them.** This is the on-time example above. The external agent is given the *full
physical schema* — every table and column, fully qualified — so the data is
equally reachable. What it does not get is the contract saying on-time means
`CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE`. It chose `SHIP_DATE`, a perfectly
sensible-looking column, and was wrong by 20 points. The dataset's six deliberate
ambiguities each punish exactly this.

**3. Groundedness measures what the trace exposes, not just what the agent knew.**
This is the largest gap (+0.554) and the least obvious. The judge's own words on
the native agent begin *"Trace walkthrough: … It executed a SQL query that
calculated on-time rate as cases where carrier_first_scan_ts <= ship_by_date,
filtering for tenant T…"* — the filters, join and date range are all visible, so
every claim in the answer is verifiable.

On the external agent the same judge writes: *"The source contains
`TOTAL_UNITS_SHIPPED: 989823` which matches the quantity claim … However, **NOTHING
FOUND** regarding the specific date range."* Its retrieved context is a bare result
value.

**Be honest about the cause here: part of that is our instrumentation.**
`_retrieval_attributes` in `python/external_sim/tools.py` puts only
`str(ret.result)` into `RETRIEVED_CONTEXTS` — the number, not the SQL that produced
it. A better-instrumented external agent that also emitted its generated SQL and
filters would score higher on groundedness without becoming any more correct. So
read the groundedness gap as *"the native agent's reasoning is inspectable by
default, and yours is only as inspectable as you instrument it"* — which is a real
and useful finding, but not the same claim as "the external agent is worse."

**4. Curated tools versus a planner that has to route.** The native agent picks
among four purpose-built tools whose descriptions state what each is for. The
external orchestrator runs planner → router → responder and can route a question
to the wrong tool; `tool_selection_accuracy` on the native side is 0.722, and
there is no working equivalent metric for the external agent at all (GOTCHAS #23).

**What this does and does not prove.** It shows a governed semantic layer with
verified queries produces more correct and more inspectable answers than
unaided text-to-SQL over the same tables, at n=9 on one dataset with deliberately
ambiguous metrics. It does not prove a general ratio, and one of the five gaps is
partly instrumentation. Quote the mechanism, not the multiplier.

**A correction worth reading if you saw an earlier version of this table.** An
earlier revision reported correctness as 0.284 native vs 0.444 external — an
inversion — and built a long explanation around it. That was an arithmetic error
on our side, not a finding. Custom metrics declared on a 0–3 rubric return the
raw 0–3 value, but a custom metric named `correctness` collides with the built-in
`answer_correctness` and returns a value already normalized to 0–1. We divided it
by 3 anyway, understating it threefold. `sql/08d_parity_eval.sql` now detects the
scale instead of assuming it, and GOTCHAS #25 documents the collision. The lesson
generalizes: in a demo about measurement, the measurement code needs the same
scrutiny as the thing being measured.

**3. Guardrails are advice; a row access policy is enforcement.** A custom
reference-free `tenant_isolation` metric scores 12 adversarial prompts that each
try to make the agent disclose one tenant's data to another. Without a row access
policy, **8 of the 12 breach** (run `TENANT_ISOLATION_V2`, mean 4.0833/10 on the
AWS build; the Azure rebuild scored 4.9167/10 and breached 7 of 12). With
the policy applied, the leak path closes at the data layer instead of depending on
the model choosing to decline.

---

## Why the dataset is built the way it is

The 13 tables contain six deliberate metric ambiguities. Each has more than one
defensible answer, so an agent that picks the wrong one is not *obviously* wrong —
it returns a confident number:

| Trap | The ambiguity |
|---|---|
| On-time rate | first carrier scan, or delivery date? |
| Fill rate | order, line, or unit? |
| Units shipped | eaches, cartons, or lines? |
| Cost per shipment | requires the zone rate-card join and the fuel surcharge |
| Fiscal period 7 | a 4-4-5 calendar, not July |
| Active SKU | had movement, or exists in the catalogue? |

---

## What it demonstrates

| Capability | How |
|---|---|
| Semantic views | two versions of the same model, deliberately weak vs optimized |
| Cortex Analyst evaluations | `sql_correctness` against 20 verified queries |
| Cortex Agent evaluations | `EXECUTE_AI_EVALUATION` with the 4 agent GPA metrics |
| Custom eval metrics | 5 custom metrics replicating the AI Observability set on the agent |
| AI Observability + TruLens | an external orchestrator traced and scored in the same event table |
| Row access policies | tenant isolation enforced beneath the agent |
| Cortex Search | the unstructured half of the corpus (SOPs, exception playbooks) |
| Notebooks on Container Runtime | a CI/CD gate that fails the build on regression |
| Streamlit in Snowflake | an 8-tab observability app over both agents |

---

## Independently reproduced

The whole build was run from scratch, following `docs/SETUP.md` from step 1, on
**two independent Snowflake accounts on two different cloud providers**:

| | Build A | Build B |
|---|---|---|
| Account | AWS us-east-1 | **Azure East US 2** |
| Rows loaded | 757,723 | **757,723** |
| Analyst baseline | 0.45 | **0.45** |
| Analyst optimized | 0.70 | **0.70** |
| Per-question movement | 7 / 0 / 13 | **7 / 0 / 13** |
| Doctor | 23/23 GO | **23/23 GO** |
| Test suite | 171 passed, 0 failed | **166 passed, 5 skipped, 0 failed** |

Every object was created new on both. The row count is byte-identical because the
generator is deterministic (numpy seed 42) — verified byte-identical across
independent runs, and now across clouds.

**The headline result reproduced exactly on both**: 0.450 → 0.700, seven
questions improved, none regressed.

Two honest limits on the word "reproduced". The tenant-isolation breach count did
**not** reproduce exactly — 8 of 12 on AWS, 7 of 12 on Azure — because it is a
judge-scored count, not a deterministic one. And the **native** agent's parity
metrics were never run on the second account at all: Azure's `AGENT_V4` carries
only the four GPA metrics, so `groundedness`, `context_relevance`, `coherence` and
`answer_relevance` for the native side exist on AWS only. The native-vs-external
gap in this README is therefore a single-account measurement. What did reproduce
across clouds on the judge-scored side is the **external** agent's five metrics,
tabulated below.

Judge-scored metrics land close but not identical across clouds, which is the
expected behaviour:

| External agent metric | AWS | Azure |
|---|---|---|
| context_relevance | 0.444 | 0.444 |
| correctness | 0.444 | 0.370 |
| groundedness | 0.327 | 0.291 |
| coherence | 0.963 | 0.926 |
| answer_relevance | 0.889 | 0.815 |

That is why this repo quotes directions and bands rather than decimals.


Detail on the remaining claims, both builds:

| Claim | Documented | Reproduced | |
|---|---|---|---|
| Tenant isolation breaches | 8 of 12 | **7 of 12** | judge noise, band asserted |
| External agent metrics | 5 × 9 | **5 × 9 computed** | incl. the retrieval-gated pair |
| External `context_relevance` | 0.444 | **0.444** | exact |
| External `groundedness` | 0.327 | **0.291** | within noise |
| Native parity metrics | AWS only | **not run on Azure** | single-account measurement |
| CI/CD gate notebook | fails on regression | **blocks** no-lift, regression, below-floor | |

### The failure that mattered most: a stale gold, not a worse agent

`correctness` scored **0.21** on the first rebuild against a documented 0.926.
That was not the agent getting worse. Asked for eaches shipped it answered
**1,044,102**, exactly what the verified SQL returns on that data — but the stored
gold still said **1,046,649**, computed from an earlier version of the data
generator. A correct answer was marked wrong, nine times, across both agents.

Three stale golds were found and corrected against live verified SQL (eaches
shipped, orders in fiscal period 7, on-time rate for T001), and both halves of
the comparison were re-run. With correct golds the native agent scores
`correctness` **0.852** against the external agent's 0.444 — it leads on all five
metrics.

A second bug surfaced on the way: we briefly reported that comparison as
*inverted* (0.284 vs 0.444) because a custom metric named `correctness` returns
an already-normalized 0–1 value while our other custom metrics return raw 0–3,
and we divided it by 3 regardless. See GOTCHAS #25. Both bugs are documented
rather than quietly fixed, because both are the kind a reader will hit.

**Ground truth in this repo contains hardcoded numeric answers, so it is coupled
to the data build it was computed from.** The generator is deterministic (numpy
seed 42), so a given generator version always produces identical data — verified
byte-identical across independent runs. But change the generator and six of the
thirteen tables shift by a fraction of a percent, and every numeric gold goes
stale at once.

This is the nastiest class of bug in an eval harness, because it is
indistinguishable from a genuine quality regression until you check the golds
against the data. `tests/test_12_gold_staleness.py` now asserts every stored
numeric gold against the verified SQL that defines it, and fails with the delta
spelled out. If you rebuild and see correctness collapse, run that test first.

Note which metric was immune: `sql_correctness` reproduced perfectly, because it
compares generated SQL against verified SQL rather than comparing numbers. The
metrics that compare values are the ones coupled to the data.


---

## Quickstart

### What this costs before you start

This demo consumes real Snowflake credits. Nothing here is free-tier, and the
bill is dominated by LLM-as-a-judge calls rather than by compute:

| Cost driver | A full build |
|---|---|
| LLM-judge scorings | **~230** across six evaluation runs (each is an `AI_COMPLETE` call) |
| Agent + orchestrator turns | ~60 (native agent evals plus 9 external turns) |
| Warehouse | one XSMALL, ~45–60 min of mostly-idle wall clock |
| Compute pools | **two** (notebook + Streamlit) that keep billing until suspended or dropped |
| Storage | ~13 MB of parquet, negligible |

Credit rates vary by edition, region and contract, so this repo deliberately does
not quote a dollar figure. Check your own before and after:

```sql
SELECT WAREHOUSE_NAME, SUM(CREDITS_USED) AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE START_TIME >= DATEADD('day', -1, CURRENT_TIMESTAMP())
GROUP BY 1 ORDER BY credits DESC;
```

Two things worth knowing:

- **The compute pools survive `DROP DATABASE`.** Run
  `sql/AGENT_EVAL_DEMO_TEARDOWN.sql`, which drops them explicitly. Dropping the
  database alone leaves two pools billing indefinitely.
- **You can skip the expensive half.** Steps 1–8 build the data and semantic
  views and cost very little. The eval runs (steps 9 onward) are where the judge
  calls happen. `make test-offline` needs no Snowflake connection at all.

### Prerequisites

Requires a Snowflake account with Cortex enabled, and **Enterprise Edition** for the
row access policy in `sql/02_governance.sql` (that one step is skippable on Standard).

```bash
# 1. Test virtualenv (pytest + connector + pandas).
make venv

# 2. Harness virtualenv. Separate on purpose: trulens-connectors-snowflake pins
#    Python <3.12, so the simulator cannot share the test venv.
python3.11 -m venv .venv-harness
.venv-harness/bin/pip install -r python/external_sim/requirements.txt

# 3. Generate the synthetic dataset (~13 MB of parquet, gitignored).
.venv-test/bin/python python/generate_data.py
.venv-test/bin/python python/generate_ops_corpus.py

# 4. Point at your account. Reads ~/.snowflake/connections.toml — no inline creds.
export SF_CONNECTION=my_snowflake_connection

# 5. Build it. docs/SETUP.md is the ordered, annotated build — follow it rather
#    than improvising, because several eval steps are asynchronous and the file
#    documents where SYSTEM$WAIT is required and why.
#      snow sql -c $SF_CONNECTION -f sql/00_setup.sql
#      ... through sql/09_streamlit.sql

# 6. Confirm the account is demo-ready (23 checks).
.venv-test/bin/python scripts/doctor.py
```

Then:

| Command | What it does |
|---|---|
| `make test` | full suite against the live account |
| `make test-offline` | offline tests only, no Snowflake connection needed |
| `make claims` | audits documented numbers against live evidence |
| `make harness` | re-runs the external orchestrator, emitting traces |
| `make score` | scores the external orchestrator (needs the harness venv) |
| `make doctor` | 23-check go/no-go |
| `make teardown` | **destructive** — drops all demo objects |

---

## Read this before you trust any number

`docs/GOTCHAS.md` is the most useful file here. It documents 25 traps found while
building this, each with the symptom, the root cause and the fix. A sample:

- **A declared table with no join path is worse than an absent one.** It fails at
  *plan time* with an HTTP 500 rather than being ignored. Two valid join routes to
  the same table killed an entire eval run — not one question, all of them.
- **`INVOCATION_COMPLETED` contains the substring `COMPLETED`.** Any status poll
  that substring-matches returns the instant the app finishes running, long before
  the judge has scored anything, and reports zero scores on a healthy run.
- **An external agent emits no `retrieval` span unless you ask for one**, and two of
  the five judge metrics read retrieval attributes. They do not error — they simply
  never appear, silently dropping you from 5 metrics to 3.
- **`AI_COMPLETE` returns a JSON-encoded string**, so the value carries literal
  `\n` rather than newlines. Executing it verbatim fails to compile every time.
- **`CREATE OR REPLACE AGENT` destroys all AI observability history.** Snapshot your
  eval results to tables or you will lose them.
- **Tool names in agent ground truth must be snake_case** (`fulfillment_data`), not
  the display names in the agent spec (`"Fulfillment Data"`). Using display names
  scores `tool_selection_accuracy` 0.00 across the board.

---

## Honest limitations

- **Judge scores are not deterministic.** Every headline number here was sampled
  across multiple runs and is quoted as a direction plus a band.
- **Release status:** Cortex Agent evaluations are GA; tool selection and tool
  execution accuracy are Public Preview. Everything this demo uses is publicly
  documented on `docs.snowflake.com` — no Private Preview features.
- **Tool-trajectory metrics are native-agent-only.** `tool_selection_accuracy` and
  `tool_execution_accuracy` have no working equivalent for external agents. Both
  documented routes were tried and both fail silently — see GOTCHAS #23. Do not
  claim parity.
- **`tool_execution_accuracy` reads 0.00 in the shared-question run**, because the
  ground truth supplies only `tool_name` and TEA grades tool input/output quality.
  That measures ground-truth completeness, not the agent. It is reported as `n/a`
  rather than as a score.
- **Metrics that hit 1.000** sit at the ceiling of a 0–3 rubric. Treat those as
  "no problems detected" rather than as precise measurements.
- **`correctness` depends on ground truth matching your data build**, and on
  reading the right scale — a custom metric named `correctness` returns 0–1 while
  other customs return raw 0–3 (GOTCHAS #25). It is a valid discriminator once
  both are right, but `groundedness` and `context_relevance` are more robust
  because they do not compare numbers.
- **Ground truth contains hardcoded numeric answers**, so it is coupled to the
  data generator version. Three golds were found stale during an independent
  rebuild and corrected; `tests/test_12_gold_staleness.py` now guards all three
  against the verified SQL that defines them. If you change the generator,
  expect to recompute golds.
- **Reproduced on two accounts across two clouds** (AWS and Azure). The
  deterministic parts — row counts, `sql_correctness`, the breach count —
  reproduced exactly. Judge-scored metrics landed within a few points but not
  identical, which is why they are quoted as bands.
- The dataset is entirely synthetic. It models a 3PL fulfilment operation and
  contains no real customer data.
- **The carrier rate cards are invented.** The corpus uses real carrier names
  (FEDEX, UPS, USPS, DHL, XPO, ONTRAC) as categorical values because an agent
  needs plausible categories to reason over, but every rate, surcharge and
  accessorial in `python/generate_ops_corpus.py` is fabricated. Do not read them
  as any carrier's published or negotiated pricing.

---

## Repository Owner

- **Owner:** John Kang (john.kang@snowflake.com / [@sfc-gh-jkang](https://github.com/sfc-gh-jkang))
- **Access requests:** email the owner, or open an issue
- **License:** Apache-2.0

Provided as-is, as a reference implementation. Not an officially supported
Snowflake product.
