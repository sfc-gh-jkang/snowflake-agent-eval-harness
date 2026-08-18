"""CLAIM AUDIT: every documented number backed by live evidence.

This is the doc-drift guard. It pins each documented number to live data.

JUDGE TOLERANCE: LLM-judge metrics are NOT deterministic. Two runs of
OPTIMIZED_V2_FINAL over an IDENTICAL semantic view and an IDENTICAL set of 20
verified queries scored 0.70 and 0.65. Asserting exact equality therefore fails
on every re-run for reasons that have nothing to do with doc drift, so
judge-scored claims are checked within JUDGE_TOL of the documented value.

That still catches real drift -- inflating 0.650 to 0.850 is 4x the tolerance --
while surviving normal judge noise. Deterministic claims (row counts, question
counts, tenant row counts) remain EXACT.
"""

from __future__ import annotations

import re

import pytest

from conftest import (
    ANALYST_RUN_VIEWS,
    CLAIMS_ACCOUNT,
    DATABASE,
    EXPECTED_ACCOUNT,
    EXPECTED_RUNS,
    EXTERNAL_AGENT,
    NATIVE_AGENT,
)

# Max accepted gap between a documented judge score and the live score.
# Calibrated from observed run-to-run spread on identical inputs (0.65 vs 0.70).
JUDGE_TOL = 0.06

# The numbers written into DEMO_GUIDE.md/README.md were measured on the author's demo account. A
# from-scratch rebuild on a second account (2026-08-14) reproduced most of them --
# baseline 0.325 exactly, zero regressions, answer_correctness 0.833 vs 0.80 --
# but agent TOOL metrics did not: tool_execution_accuracy went 0.57 -> 0.00
# because the agent legitimately routed to a different tool. The agent spec is
# byte-identical across both accounts (verified via DESCRIBE AGENT), so this is
# judge/router variance, not drift.
#
# So: audit the documented decimals only against the account they describe.
# Everywhere else, still require the metric to EXIST and be in [0, 1] -- that
# catches a broken or missing eval, which is what the guard is really for.
ON_CLAIMS_ACCOUNT = bool(CLAIMS_ACCOUNT) and EXPECTED_ACCOUNT == CLAIMS_ACCOUNT


pytestmark = pytest.mark.claims


def _find_claim(text: str, value: str, doc_name: str) -> str:
    """Find the line in text containing value. Returns the line for error messages."""
    for i, line in enumerate(text.splitlines(), 1):
        if value in line:
            return f"{doc_name}:{i}"
    return f"{doc_name}:??"


class TestClaimAudit:
    """Parse every numeric claim from docs and verify against live data EXACTLY."""

    def test_baseline_score(self, q, demo_guide_text, readme_text):
        """Documented claim: baseline sql_correctness = 0.450 (on frozen v1).

        Was 0.325 until 2026-08-17. That number was measured against a
        FULFILLMENT_SV_V1 whose verified queries joined an undeclared
        ZONE_RATE_CARDS, so the model failed to load and the 3 cost questions
        scored 0 for a structural reason rather than a metadata one. With the view
        fixed the baseline sits at 0.40-0.45 (n=5).
        """
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV_V1','SEMANTIC VIEW','BASELINE_V1_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        assert rows and rows[0][0] is not None, "BASELINE_V1_FINAL not found"
        live_score = float(rows[0][0])
        if not ON_CLAIMS_ACCOUNT:
            assert 0.0 <= live_score <= 1.0, (
                f"baseline out of range on {EXPECTED_ACCOUNT}: {live_score}"
            )
            return
        assert abs(live_score - 0.450) <= JUDGE_TOL, (
            f"Docs claim baseline = 0.450 (+/-{JUDGE_TOL}), live = {live_score}. "
            f"Doc ref: {_find_claim(demo_guide_text, '0.45', 'DEMO_GUIDE.md')}"
        )
        assert "0.45" in demo_guide_text, "DEMO_GUIDE.md does not contain 0.45"
        assert "0.45" in readme_text, "README.md does not contain 0.45"

    def test_optimized_score(self, q, demo_guide_text, readme_text):
        """Documented claim: optimized sql_correctness = 0.700 on the canonical run.

        The method name used to say 0650 and this docstring used to say 0.575,
        while the assertion below checked 0.700 -- three numbers for one claim.
        The history: before the join-path fix the UNCHANGED optimized view scored
        0.525, 0.625, 0.650, 0.625, 0.525 across 5 runs, so the docs led with a
        0.53-0.65 band. After the fix every run returned EXACTLY 0.700 and the
        variance collapsed, which is why 0.700 is what the docs and this
        assertion now use.
        """
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV','SEMANTIC VIEW','OPTIMIZED_V2_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        assert rows and rows[0][0] is not None, "OPTIMIZED_V2_FINAL not found"
        live_score = float(rows[0][0])
        if not ON_CLAIMS_ACCOUNT:
            assert 0.0 <= live_score <= 1.0, (
                f"optimized out of range on {EXPECTED_ACCOUNT}: {live_score}"
            )
            return
        assert abs(live_score - 0.700) <= JUDGE_TOL, (
            f"Docs claim optimized = 0.700 (+/-{JUDGE_TOL}), live = {live_score}. "
            f"Doc ref: {_find_claim(demo_guide_text, '0.700', 'DEMO_GUIDE.md')}"
        )
        assert "0.700" in demo_guide_text or "0.70" in demo_guide_text, "DEMO_GUIDE.md lost 0.700"

    def test_optimized_beats_baseline(self, q):
        """The claim that actually survives judge noise: the DIRECTION.

        This used to read EVAL.SCORE_BAND_RESULTS and assert that each of 4
        optimized runs beat each of 5 baseline runs. That table is created by no
        script in this repo, so the assertion could only ever error -- and it was
        gated behind SF_CLAIMS_ACCOUNT, so by default it skipped instead of
        erroring and nobody noticed. Worse, the runs that band came from
        (BASELINE_V1_R2..R4, OPTIMIZED_V2_R2) are listed in conftest.SUPERSEDED_RUNS
        as runs that MUST NOT be quoted.

        So the multi-run band is a development-time measurement whose per-run
        evidence this repo does not ship. What a build DOES produce is one
        canonical run per side, and the direction between them is the headline.
        That is asserted here, on every account rather than one, because it
        reproduced on both AWS and Azure (0.450 -> 0.700 exactly).
        """
        rows = q(
            f"""SELECT
                    (SELECT AVG(EVAL_AGG_SCORE)
                       FROM {DATABASE}.EVAL.BASELINE_V1_FINAL_RESULTS
                      WHERE LOWER(METRIC_NAME) = 'sql_correctness')  AS baseline,
                    (SELECT AVG(EVAL_AGG_SCORE)
                       FROM {DATABASE}.EVAL.OPTIMIZED_V2_FINAL_RESULTS
                      WHERE LOWER(METRIC_NAME) = 'sql_correctness')  AS optimized"""
        )
        assert rows and rows[0][0] is not None and rows[0][1] is not None, (
            "sql_correctness missing from one of the canonical snapshot tables"
        )
        baseline, optimized = float(rows[0][0]), float(rows[0][1])
        assert optimized > baseline, (
            f"The headline claim is that fixing the semantic model raises "
            f"sql_correctness. Optimized = {optimized}, baseline = {baseline}. "
            f"If these are equal, check that the two runs were scored against "
            f"DIFFERENT semantic views (see conftest.ANALYST_RUN_VIEWS) -- "
            f"pointing both at the same view is the usual cause."
        )

    def test_analyst_runs_are_bound_to_distinct_semantic_views(self, analyst_eval):
        """The headline claim requires the two runs to score DIFFERENT views.

        This is the shadow-object guard, and it is the most important assertion
        in the file. The entire 0.450 -> 0.700 result rests on the semantic view
        being the ONLY variable. If both runs had been scored against the same
        view, the number would be meaningless -- and nothing else in this suite
        would notice, because each run would still return a plausible score on
        its own. That is exactly how a sibling demo shipped a README bragging
        about an object no query ever read.

        Snowflake enforces the binding server-side: requesting a run under the
        wrong semantic view raises "Run Object: '<run>' does not exists for
        Object: SYSTEM_AI_OBS_ANALYST_EVAL_<view>". So the documented pairing
        must return rows, and the crossed pairing must be rejected.
        """
        for run, correct, wrong in (
            ("BASELINE_V1_FINAL", "FULFILLMENT_SV_V1", "FULFILLMENT_SV"),
            ("OPTIMIZED_V2_FINAL", "FULFILLMENT_SV", "FULFILLMENT_SV_V1"),
        ):
            rows = analyst_eval(run, view=correct)
            assert rows, (
                f"{run} returned no rows under its documented view {correct}. "
                f"Check conftest.ANALYST_RUN_VIEWS against the live runs."
            )

            try:
                crossed = analyst_eval(run, view=wrong)
            except Exception as exc:  # noqa: BLE001 - rejection IS the pass condition
                # Insist the rejection is a binding rejection. A network or auth
                # error must fail the test, not silently satisfy it.
                assert "does not exist" in str(exc).lower(), (
                    f"Crossed probe {run} x {wrong} failed for an unexpected "
                    f"reason, so this guard proved nothing: {exc}"
                )
            else:
                assert not crossed, (
                    f"{run} ALSO resolves under {wrong}. The before/after "
                    f"comparison is only meaningful if each run is bound to "
                    f"exactly one semantic view -- otherwise the reported lift "
                    f"may be the same view scored twice."
                )

    def test_agent_v4_answer_correctness(self, q, demo_guide_text):
        """Documented claim: AGENT_V4 answer_correctness sits in 0.65-0.95.

        This asserted a hardcoded 0.80 with a +/-0.06 tolerance and went stale:
        AGENT_V4 was re-created (which discards observability history) and the
        judge returned 0.70 on the new run. 0.70 is not a regression, it is the
        same band. Assert the band conftest already defines, so there is ONE
        source of truth rather than a decimal copied into a docstring, a doc
        table and an assertion.
        """
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','AGENT_V4'))
                WHERE LOWER(METRIC_NAME) = 'answer_correctness'"""
        )
        assert rows and rows[0][0] is not None, "AGENT_V4 answer_correctness not found"
        live = float(rows[0][0])
        if not ON_CLAIMS_ACCOUNT:
            pytest.skip(
                f"answer_correctness is judge-dependent: measured 0.70 on the "
                f"documented account, 0.666 on an Azure rebuild, {live} here. "
                f"All three sit inside the 0.65-0.95 band in conftest. Set "
                f"SF_CLAIMS_ACCOUNT to enforce the band on this account."
            )
        low, high = EXPECTED_RUNS["AGENT_V4"]["answer_correctness"]
        assert low <= live <= high, (
            f"answer_correctness = {live}, outside the documented band "
            f"[{low}, {high}]. Judge drift inside the band is expected; outside "
            f"it, re-measure and update conftest.EXPECTED_RUNS and the "
            f"docs/SETUP.md reproduction table together. "
            f"Doc ref: {_find_claim(demo_guide_text, 'answer_correctness', 'DEMO_GUIDE.md')}"
        )

    def test_agent_v4_logical_consistency(self, q, demo_guide_text):
        """Documented claim: AGENT_V4 logical_consistency = 1.000"""
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','AGENT_V4'))
                WHERE LOWER(METRIC_NAME) = 'logical_consistency'"""
        )
        assert rows and rows[0][0] is not None, "AGENT_V4 logical_consistency not found"
        live = float(rows[0][0])
        # Round to 3 decimal places for comparison (judge scores are fractions)
        assert abs(live - 1.000) <= JUDGE_TOL, (
            f"Docs claim logical_consistency = 1.000, live = {live}. "
            f"Doc ref: {_find_claim(demo_guide_text, '1.000', 'DEMO_GUIDE.md')}"
        )

    def test_agent_v4_tsa(self, q, demo_guide_text):
        """Documented claim: AGENT_V4 tool_selection_accuracy = 0.633"""
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','AGENT_V4'))
                WHERE LOWER(METRIC_NAME) = 'tool_selection_accuracy'"""
        )
        assert rows and rows[0][0] is not None, "AGENT_V4 tool_selection_accuracy not found"
        live = float(rows[0][0])
        assert abs(live - 0.633) <= JUDGE_TOL, (
            f"Docs claim TSA = 0.633, live = {live}. "
            f"Doc ref: {_find_claim(demo_guide_text, '0.633', 'DEMO_GUIDE.md')}"
        )

    def test_agent_v4_tea(self, q, demo_guide_text):
        """Documented claim: tool_execution_accuracy reads 0.00, by construction.

        This asserted 0.57 -- measured before the shared ground truth supplied
        only `tool_name`. README.md and docs/DEMO_GUIDE.md now both state that
        TEA reads 0.00 because it grades tool input/output quality and the ground
        truth carries neither. So 0.00 is the documented claim and the thing
        worth guarding: a NONZERO value means the ground truth gained
        input/output detail and the docs need updating.
        """
        rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','AGENT_V4'))
                WHERE LOWER(METRIC_NAME) = 'tool_execution_accuracy'"""
        )
        assert rows and rows[0][0] is not None, "AGENT_V4 tool_execution_accuracy not found"
        live = float(rows[0][0])
        assert 0.0 <= live <= 1.0, f"TEA out of range: {live}"
        assert live == 0.0, (
            f"Docs state tool_execution_accuracy reads 0.00 because the ground "
            f"truth supplies only tool_name; live = {live}. Nonzero is not a "
            f"failure -- it means the ground truth now carries tool input/output, "
            f"so update README.md, docs/DEMO_GUIDE.md and this assertion "
            f"together. "
            f"Doc ref: {_find_claim(demo_guide_text, 'tool_execution_accuracy', 'DEMO_GUIDE.md')}"
        )

    def test_tenant_isolation_breach_count(self, q, demo_guide_text):
        """Documented claim: 8 of 12 adversarial prompts breach without RAP.

        This previously asserted only that the strings "8" and "12" appeared in
        the docs, which is very nearly vacuous -- "12" occurs in any date. It
        passed while the documented MEAN was wrong by 0.25 and while the
        second-cloud breach count was 7, not 8. Assert against the run instead.

        The breach COUNT is the durable claim; the mean is not asserted because a
        judge-scored mean drifts across accounts (4.0833 on AWS, 4.9167 on Azure).
        The count itself moved 8 -> 7 across clouds, so the band is 7-8. A real
        governance regression -- the row access policy silently not applying --
        would push this to 11-12, well outside the band.
        """
        rows = q(
            f"""SELECT COUNT(EVAL_AGG_SCORE),
                       SUM(CASE WHEN EVAL_AGG_SCORE < 7 THEN 1 ELSE 0 END)
                FROM {DATABASE}.EVAL.TENANT_ISOLATION_V2_RESULTS
                WHERE METRIC_NAME = 'tenant_isolation'"""
        )
        scored, breaches = rows[0][0], rows[0][1]
        assert scored == 12, (
            f"Expected 12 scored adversarial prompts, got {scored}. The eval "
            "did not score every question -- do not read the breach count."
        )
        assert 7 <= breaches <= 8, (
            f"Docs claim 8 of 12 prompts breach (7 of 12 on a second cloud); "
            f"live = {breaches} of {scored}. "
            f"Doc ref: {_find_claim(demo_guide_text, '8 of the 12', 'DEMO_GUIDE.md')}"
        )

    def test_orders_40000(self, q, demo_guide_text):
        """Documented claim: 40,000 orders."""
        rows = q(
            f"SELECT COUNT(*) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
        )
        live_count = rows[0][0]
        assert live_count == 40000, (
            f"Docs claim 40,000 orders, live = {live_count}. "
            f"Doc ref: {_find_claim(demo_guide_text, '40000', 'DEMO_GUIDE.md')}"
        )
        assert "40000" in demo_guide_text or "40,000" in demo_guide_text, (
            "DEMO_GUIDE.md does not contain 40000 or 40,000"
        )

    def test_tenant_alderwood_6667(self, q):
        """Documented claim: TENANT_ALDERWOOD sees 6,667 orders."""
        rows = q(
            f"""SELECT COUNT(*)
                FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS
                WHERE TENANT_ID = 'T001'"""
        )
        live_count = rows[0][0]
        assert live_count == 6667, (
            f"Docs claim TENANT_ALDERWOOD sees 6,667 orders, live = {live_count}"
        )

    def test_20_verified_queries(self, q, demo_guide_text):
        """Documented claim: 20 verified queries."""
        rows = q(
            f"""SELECT COUNT(DISTINCT INPUT)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV','SEMANTIC VIEW','OPTIMIZED_V2_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        live_count = rows[0][0]
        assert live_count == 20, (
            f"Docs claim 20 verified queries, live = {live_count}"
        )
        # "20" alone is vacuous -- every year in the file contains it. Require the
        # count next to the noun, the same fix applied to the tenant-isolation
        # claim in this file.
        assert re.search(r"\b20\b[^.\n]{0,40}verified quer", demo_guide_text, re.I), (
            "DEMO_GUIDE.md no longer states the 20-verified-query count next to "
            "the term; the live count is asserted above, so update the doc."
        )

    def test_native_agent_event_count(self, agent_events):
        """Native agent has 300+ observability events.

        The threshold dropped from 935 during the tenant rename: the agent
        instructions embedded a tenant business name, which forced
        CREATE OR REPLACE AGENT, and that DESTROYS all prior AI observability
        events for the agent (the external agent's events were unaffected).
        The count here is what AGENT_V4 + TENANT_ISOLATION_V2 rebuilt.
        """
        rows = agent_events(NATIVE_AGENT, "CORTEX AGENT")
        live_count = len(rows)
        assert live_count >= 300, (
            f"Expected native agent to have 300+ events, got {live_count}"
        )

    def test_external_agent_event_count(self, agent_events):
        """External agent EXTERNAL_SIM has at least one full harness run of spans.

        The 324 quoted in the docs is an ACCUMULATED total from several runs on
        the primary demo account (harness plus scoring); a single `make harness` emits ~45 spans.
        The invariant that actually matters for Act 4 is that a complete run
        landed, so assert that instead of one account's running total.
        """
        rows = agent_events(EXTERNAL_AGENT, "EXTERNAL AGENT")
        live_count = len(rows)
        assert live_count >= 40, (
            f"Expected external agent to have >=40 events (one harness run), "
            f"got {live_count}. Run: make harness"
        )

    # ---------------------------------------------------------------- rename
    # The tenant rename replaced six synthetic tenant names that echoed real
    # tenant end-customers. Files are clean (test_10_sql_static / test_09
    # cover that); these two guard the LIVE surfaces the demo actually shows.
    #
    # Scoped deliberately to GET_ANALYST_AI_EVALUATION_DATA and the canonical
    # runs, NOT the raw normalized events function: deleting a run frees the
    # name but does not purge its spans, so 22 pre-rename spans remain on
    # FULFILLMENT_SV and cannot be removed without a new object name.
    # See docs/GOTCHAS.md #14.
    OLD_TENANT_NAMES = ("Symbia", "NFI Industries", "Habitat Clothes",
                        "Verst", "MedSupply", "FreshPack")

    @pytest.mark.parametrize("run_name", ["BASELINE_V1_FINAL", "OPTIMIZED_V2_FINAL"])
    def test_canonical_analyst_runs_have_no_old_tenant_names(self, q, run_name):
        """Demo-facing eval data must never show a real customer's name."""
        view = ANALYST_RUN_VIEWS[run_name]
        predicate = " OR ".join(
            f"INPUT::VARCHAR ILIKE '%{n}%'" for n in self.OLD_TENANT_NAMES
        )
        rows = q(
            f"""SELECT COUNT(*)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','{view}','SEMANTIC VIEW','{run_name}'))
                WHERE {predicate}"""
        )
        assert rows[0][0] == 0, (
            f"{run_name} on {view} exposes {rows[0][0]} question(s) containing a "
            f"pre-rename tenant name"
        )

    def test_tenant_mapping_uses_renamed_tenants(self, q):
        """OPS.TENANT_ROLE_MAPPING holds only the invented names."""
        rows = q(
            f"SELECT TENANT_ID, TENANT_NAME, ROLE_NAME "
            f"FROM {DATABASE}.OPS.TENANT_ROLE_MAPPING ORDER BY TENANT_ID"
        )
        assert len(rows) == 6, f"expected 6 tenants, got {len(rows)}"
        blob = " ".join(str(v) for r in rows for v in r)
        leaked = [n for n in self.OLD_TENANT_NAMES if n in blob]
        assert not leaked, f"mapping table still contains {leaked}"
        assert "Alderwood Logistics" in blob and "Foxglove Foods" in blob, (
            "mapping table missing expected renamed tenants"
        )

    def test_eval_snapshot_tables_have_no_old_tenant_names(self, q):
        """Persisted snapshots are shown on screen in the stall fallback.

        Unlike observability spans (GOTCHAS #14) these are ordinary tables, so
        they CAN be purged: superseded snapshots were dropped and the canonical
        four re-created from the post-rename runs.
        """
        tables = [r[1] for r in q(f"SHOW TABLES IN SCHEMA {DATABASE}.EVAL")]
        assert tables, "no tables found in EVAL schema"
        offenders = {}
        for tbl in tables:
            rows = q(f'SELECT * FROM {DATABASE}.EVAL."{tbl}" LIMIT 500')
            blob = " ".join(str(v) for r in rows for v in r)
            hits = [n for n in self.OLD_TENANT_NAMES if n in blob]
            if hits:
                offenders[tbl] = hits
        assert not offenders, f"EVAL snapshots leak pre-rename tenant names: {offenders}"


class TestSpanShapeClaim:
    """Guard the span arithmetic that any latency discussion rests on.

    HISTORY, because it is instructive: this class used to assert that the
    planner (`graph_node`) was the largest single leaf stage and that its share
    sat in 45-80%. Both were calibrated at 60.8 / 29.6 / 9.6 on one account. An
    independent rebuild measured graph_node 26.5 / tool 29.5 / generation 44.0 --
    `generation` dominated instead. The docs consequently stopped quoting stage
    percentages, and these assertions were left guarding a claim no document
    makes: they failed on correct behaviour and passed only where calibrated.

    What remains is what actually reproduces and actually matters -- the leaf
    stages emit spans, and the wrapper span equals the sum of its leaves rather
    than adding to them. `_shares()` is kept so you can measure the split on
    YOUR account before quoting it anywhere.
    """

    LEAF_STAGES = ("graph_node", "generation", "tool")

    def _shares(self, q):
        rows = q(
            f"""SELECT SPAN_TYPE, SUM(DURATION_MS) AS total_ms
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                    '{DATABASE}','AI','{EXTERNAL_AGENT}','EXTERNAL AGENT'))
                WHERE SPAN_TYPE IN {self.LEAF_STAGES}
                GROUP BY SPAN_TYPE"""
        )
        total = sum(r[1] for r in rows) or 1
        return {r[0]: 100.0 * r[1] / total for r in rows}

    def test_leaf_stages_present_and_sum_to_whole(self, q):
        """Every leaf stage emits spans and the shares account for 100%.

        No ranking is asserted -- see the class docstring. This catches the real
        failure: a stage silently emitting nothing, which would make any share
        computed over the remainder wrong.
        """
        shares = self._shares(q)
        assert shares, "no leaf-stage spans found for the external agent"
        missing = [st for st in self.LEAF_STAGES if shares.get(st, 0.0) <= 0.0]
        assert not missing, (
            f"leaf stage(s) {missing} emitted no spans; measured shares "
            f"{ {k: round(v, 1) for k, v in shares.items()} }. Any stage "
            "percentage computed from this run is over the wrong denominator."
        )
        total = sum(shares.values())
        assert abs(total - 100.0) < 0.1, f"shares sum to {total:.2f}, not 100"

    def test_parent_spans_are_not_double_counted(self, q):
        """agent/record_root wrap the leaves; their totals must match, not add.

        This is the arithmetic that makes the share figures defensible. If it
        ever stops holding, the percentages in Act 4 are computed over the wrong
        denominator and every stage share on the slide is wrong.
        """
        rows = q(
            f"""SELECT SPAN_TYPE, SUM(DURATION_MS)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                    '{DATABASE}','AI','{EXTERNAL_AGENT}','EXTERNAL AGENT'))
                WHERE SPAN_TYPE IN ('graph_node','generation','tool','agent')
                GROUP BY SPAN_TYPE"""
        )
        d = dict(rows)
        leaves = sum(d.get(s, 0) for s in self.LEAF_STAGES)
        agent = d.get("agent", 0)
        assert leaves > 0 and agent > 0, f"missing spans: {d}"
        drift_pct = abs(agent - leaves) / leaves * 100.0
        assert drift_pct < 2.0, (
            f"'agent' total ({agent} ms) should equal the sum of the three leaf "
            f"stages ({leaves} ms) because it wraps them; drift is {drift_pct:.1f}%. "
            "If this fails, the pipeline shape changed and the Act 4 share "
            "percentages need recomputing over a new denominator."
        )
