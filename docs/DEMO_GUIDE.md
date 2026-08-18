# Demo Guide

How to walk someone through this demo, and what each part actually proves.

This is not a script to read aloud. It is the set of claims the demo can support,
the evidence behind each, and the caveats you must state rather than hope nobody
asks about.

---

## The thesis

Most AI-agent conversations are about *building* agents. This demo is about
**measuring** them — and about the fact that a governed semantic layer is what
makes the measurements move.

Everything here is measured, not asserted. Every number below carries its run
name, model, judge and date.

---

## Provenance of every number in this repo

| | Value | Source |
|---|---|---|
| Baseline `sql_correctness` | **0.450** | run `BASELINE_V1_FINAL`, n=20 |
| Optimized `sql_correctness` | **0.700** | run `OPTIMIZED_V2_FINAL`, n=20 |
| Baseline band | 0.40–0.45 | 5 independent runs during development, mean 0.44 (per-run evidence not shipped) |
| Optimized band | 0.700 | 4 independent runs during development, all 0.700 (per-run evidence not shipped) |
| Optimized > baseline | direction holds | asserted on every account by the suite |
| Per-question movement | 7 improved / 0 regressed / 13 unchanged | same two runs |
| Agent vs external groundedness | 0.881 vs 0.327 | `PARITY_AGENT_V4` / `PARITY_EXTERNAL_V11`, n=9 |
| Agent vs external context_relevance | 0.963 vs 0.444 | same runs |
| Agent vs external correctness | 0.852 vs 0.444 | same runs |
| Agent vs external answer_relevance | 1.000 vs 0.889 | same runs |
| Agent vs external coherence | 1.000 vs 0.963 | same runs |
| External spans / turns / errors | 324 / 36 / 18 | AI Observability event table |

Measured on the author's own Snowflake demo account, 2026-08-18.
Orchestration model `claude-opus-4-8` on both sides. Judge `claude-4-sonnet` on
both sides. Native custom metrics are raw 0–3 and normalized by dividing by 3.

**The LLM judge is non-deterministic.** Re-running any of these will not reproduce
the third decimal. Quote the direction and the band, never the exact figure — an
earlier version of this guide said "expect 0.62–0.70" and a live re-run drew
0.525, which is precisely the failure this warning exists to prevent.

---

## Act 1 — The problem is unmeasured, not unbuilt

Show the eval dataset: 20 questions with verified SQL and expected answers.

The point: without a gold standard there is no such thing as "the agent got
better". Every claim of improvement is a vibe. This is the cheapest artifact in
the whole demo and the one most teams skip.

Worth stating the shape of the data while you are here: **13 tables, ~758,000
rows** across four schemas (fulfilment, shipping, inventory, labour). `ORDERS` is
**40,000** rows, of which a single tenant sees **6,667** once the row access
policy is applied — that number returns in Act 5.

Row counts vary slightly between builds. Seven tables are fixed-cardinality and
reproduce exactly; six are derived from random draws and land within ~0.5%. The
test suite encodes that distinction rather than pretending the totals are exact.

## Act 2 — Semantic view quality is the variable

Run the same 20 questions against two semantic views over **identical data**:

- `FULFILLMENT_SV_V1` — vague column names, no metric definitions, no declared joins
- `FULFILLMENT_SV` — synonyms, explicit metric definitions, declared relationships

`sql_correctness` moves **0.450 → 0.700**. Nothing about the data changed.

Show a specific question that flipped, not the aggregate. The aggregate is the
headline; a single question is what makes it real.

## Act 3 — Trajectory metrics answer "which hop broke"

The native Cortex Agent scores four GPA metrics. Two are about the *path*, not the
answer:

- `tool_selection_accuracy` (**0.722**) grades the goal → plan hop
- `tool_execution_accuracy` grades the plan → action hop

This is the answer to "we lose accuracy at every connection point in a
multi-agent chain". When a chain degrades, these two localize the failure instead
of leaving you to bisect by hand.

**State plainly:** `tool_execution_accuracy` reads **0.00** in the 9-question
parity run. That is not the agent failing. TEA grades tool input/output quality,
and the shared ground truth supplies only `tool_name` — so there is nothing to
grade. It measures ground-truth completeness, and it is reported as `n/a`. Do not
present 0.00 as a finding.

## Act 4 — An external agent can be measured too

The TruLens-instrumented external orchestrator emits **324 spans over 36 turns**
into the same AI Observability event table as the native agent.

**18 turns errored**, all in two early runs, before the four cascading bugs
documented in GOTCHAS #19–21 were fixed. The last nine ran clean. Show this — a
demo where nothing ever failed is not a demo about observability.

Query errors from the **`ERROR` column, not `STATUS`**:

```sql
SELECT ERROR, COUNT(*) AS n
FROM (
  SELECT RECORD_ATTRIBUTES:"snow.ai.observability.record.error"::STRING AS ERROR
  FROM <your_event_table>
)
WHERE ERROR IS NOT NULL
GROUP BY ERROR
ORDER BY n DESC;
```

Every external span is `STATUS_CODE_UNSET`, so a STATUS-based filter returns
**zero** errors and silently contradicts the number you just said out loud.

## Act 5 — Adversarial prompts and tenant isolation

A custom reference-free `tenant_isolation` metric scores 12 adversarial prompts
that each try to make the agent disclose one tenant's data to another.

Without a row access policy, **8 of the 12 prompts breach** — run
`TENANT_ISOLATION_V2`, which scores **4.0833/10** (mean over 12 prompts; the
Azure rebuild scored 4.9167/10 and breached 7 of 12, not 8). With the policy applied, the
leak path closes at the data layer rather than depending on the model declining
to answer.

The point worth making: prompt-level guardrails are advice, a row access policy
is enforcement. Only one of them survives a determined prompt.

This act requires Enterprise Edition. On Standard, skip it.

## Why the governed layer wins

Same 9 questions, same model (`claude-opus-4-8`), same judge (`claude-4-sonnet`),
both agents, every numeric gold verified against the SQL that defines it
(`PARITY_AGENT_V4` / `PARITY_EXTERNAL_V11`):

| Metric | Native agent | External orchestrator | Gap |
|---|---|---|---|
| **groundedness** | **0.881** | **0.327** | **+0.554** |
| **context_relevance** | **0.963** | **0.444** | **+0.519** |
| **correctness** | **0.852** | **0.444** | **+0.408** |
| answer_relevance | 1.000 | 0.889 | +0.111 |
| coherence | 1.000 | 0.963 | +0.037 |

The native agent leads on all five. Lead with grounding (2.7x and 2.2x) and
correctness (1.9x).

**Show the mechanism, not just the table.** Asked for the on-time shipping rate,
the external agent defined on-time as `SHIP_DATE <= SHIP_BY_DATE` and answered
**82.2%**. The verified definition is `CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE`,
giving **61.6%** — which is what the native agent returned (61.5%). Same data,
same question, same model, 20 points apart, no error raised. That single example
lands harder than the whole table.

**If someone asks about scale or normalization, know this.** Custom metrics
declared on a 0–3 rubric return raw 0–3 in `EVAL_AGG_SCORE`, but a custom metric
named `correctness` collides with the built-in `answer_correctness` and returns
0–1 already. Dividing it twice understates it threefold and inverts the table.
`sql/08d_parity_eval.sql` detects the scale; GOTCHAS #25 has the detail. We hit
this ourselves and published the correction.

### The four reasons, if you are asked "why?"

Have these ready — the table alone invites the question.

1. **Verified query reuse.** 21 of 39 native SQL executions carried
   `verified_query_used: true`. The semantic view answered with SQL a human had
   already reviewed. The external agent generates fresh SQL every time.
2. **Definitions, not data access.** The external agent gets the full physical
   schema — the data is equally reachable. What it lacks is the contract saying
   on-time means `CARRIER_FIRST_SCAN_TS`, not `SHIP_DATE`. Every one of the six
   ambiguity traps punishes that gap.
3. **Inspectability drives groundedness.** The judge's native explanations open
   with a trace walkthrough naming the filters and joins; on the external agent it
   writes "NOTHING FOUND regarding the specific date range." **Say the honest part
   too:** our external instrumentation puts only the result value into retrieved
   contexts, not the SQL, so some of that gap is instrumentation rather than
   agent quality. Better-instrumented external agents score higher here.
4. **Curated tools vs planner routing.** Four purpose-built tools with stated
   purposes, versus planner → router → responder that can mis-route.

If someone pushes on the numbers, concede the scope: n=9, one dataset, deliberately
ambiguous metrics, and one of the five gaps is partly instrumentation. The
mechanism is the durable claim; the multiplier is not.

Both agents reach the same tables. The native one answers through a semantic view
carrying 20 verified queries — curated SQL reused verbatim rather than generated.

Note the 1.000 scores sit at the ceiling of a 0–3 rubric — read them as "no
problems detected", not as precision.

## Ground truth is coupled to your data build

Numeric golds are hardcoded prose ("A total of 1044102 eaches..."), computed from
one build of the generator. The generator is deterministic, so a given version
always produces identical data — but change it and six of thirteen tables shift
slightly, and every numeric gold goes stale at once.

This is the nastiest failure mode in the whole demo, because it looks exactly
like the agent getting worse. Run `tests/test_12_gold_staleness.py` first: it
asserts each stored gold against the verified SQL that defines it and prints the
delta.

## Act 6 — Make it a gate, not a report

The notebook (`notebook/eval_cicd_gating.ipynb`) runs on Container Runtime and
**fails the build** when a score regresses. An eval that produces a dashboard
nobody blocks a merge on is documentation, not a control.

---

## Honest Caveats — state these, do not wait to be asked

1. **Judge non-determinism.** Covered above. Direction and band only.
2. **Tool-trajectory metrics are native-agent-only.** No working external
   equivalent exists; both documented routes fail, one loudly and one silently.
   See GOTCHAS #23. Do not claim parity.
3. **`tool_execution_accuracy` 0.00** is a ground-truth artifact, not a result.
4. **Row access policies need Enterprise Edition.** On Standard, skip
   `02_governance.sql`; the tenant-isolation act goes with it and everything else
   still runs.
5. **Release status, verified against the public docs.** Cortex Agent
   evaluations are **GA** (release note 2026-03-13). Tool selection accuracy and
   tool execution accuracy are **Public Preview** (2026-06-11). AI Observability,
   External Agents and Cortex Analyst evaluations are all publicly documented.
   Nothing in this demo depends on a Private Preview feature. State the status
   before someone checks, rather than after.
6. **Two accounts, two clouds.** The build was reproduced from scratch on both
   AWS and Azure. Row counts and `sql_correctness` (0.450 -> 0.700, 7 improved /
   0 regressed) reproduced exactly. Judge-scored numbers did not, including the
   tenant-isolation breach count: 8 of 12 on AWS, 7 of 12 on Azure. Quote the
   direction, and name the deterministic parts as the ones that hold.

---

## PRE-FLIGHT

```bash
.venv-test/bin/python scripts/doctor.py     # 23 checks, expect exit 0
make test-offline                            # no connection required
```

If the doctor reports a red check, fix it or drop that act. Do not improvise
against a live account in front of an audience.
