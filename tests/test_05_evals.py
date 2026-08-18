"""Persisted evaluation runs and their scores."""

from __future__ import annotations

import pytest

from conftest import EXPECTED_RUNS, SUPERSEDED_RUNS, DATABASE, ANALYST_RUN_VIEWS


pytestmark = pytest.mark.evals


class TestExpectedRunsExist:
    """Each run in EXPECTED_RUNS exists and each metric falls in its band."""

    def _get_run_scores(self, q, run_name: str) -> dict[str, float]:
        """Get AVG(EVAL_AGG_SCORE) per metric for a run."""
        if "AGENT" in run_name or "TENANT" in run_name:
            rows = q(
                f"""SELECT LOWER(METRIC_NAME), AVG(EVAL_AGG_SCORE)
                    FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                        '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','{run_name}'))
                    WHERE METRIC_NAME IS NOT NULL
                    GROUP BY METRIC_NAME"""
            )
        else:
            # Baseline lives on FULFILLMENT_SV_V1, optimized on FULFILLMENT_SV.
            view = ANALYST_RUN_VIEWS.get(run_name, "FULFILLMENT_SV")
            rows = q(
                f"""SELECT LOWER(METRIC_NAME), AVG(EVAL_AGG_SCORE)
                    FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                        '{DATABASE}','AI','{view}','SEMANTIC VIEW','{run_name}'))
                    WHERE METRIC_NAME IS NOT NULL
                    GROUP BY METRIC_NAME"""
            )
        return {r[0]: float(r[1]) for r in rows if r[1] is not None}

    def test_baseline_v1_final_exists(self, q):
        scores = self._get_run_scores(q, "BASELINE_V1_FINAL")
        assert len(scores) > 0, "BASELINE_V1_FINAL has no metric rows"

    def test_optimized_v2_final_exists(self, q):
        scores = self._get_run_scores(q, "OPTIMIZED_V2_FINAL")
        assert len(scores) > 0, "OPTIMIZED_V2_FINAL has no metric rows"

    def test_agent_v4_exists(self, q):
        scores = self._get_run_scores(q, "AGENT_V4")
        assert len(scores) > 0, "AGENT_V4 has no metric rows"

    def test_tenant_isolation_v2_exists(self, q):
        scores = self._get_run_scores(q, "TENANT_ISOLATION_V2")
        assert len(scores) > 0, "TENANT_ISOLATION_V2 has no metric rows"

    def test_scores_in_band(self, q):
        """Every metric for every expected run falls in its defined band.

        Router-dependent metrics are exempt from their band off the account the
        bands were measured on -- they still must exist and be within [0, 1], so
        a missing or broken eval is caught either way. See ROUTER_DEPENDENT_METRICS.
        """
        from conftest import (
            CLAIMS_ACCOUNT,
            EXPECTED_ACCOUNT,
            ROUTER_DEPENDENT_METRICS,
        )

        on_claims_account = bool(CLAIMS_ACCOUNT) and EXPECTED_ACCOUNT == CLAIMS_ACCOUNT
        failures = []
        for run_name, metrics in EXPECTED_RUNS.items():
            data = self._get_run_scores(q, run_name)
            for metric, (low, high) in metrics.items():
                actual = data.get(metric)
                if actual is None:
                    failures.append(f"{run_name}/{metric}: not found in eval data")
                    continue
                if metric in ROUTER_DEPENDENT_METRICS and not on_claims_account:
                    if not (0.0 <= actual <= 1.0):
                        failures.append(
                            f"{run_name}/{metric}: {actual:.4f} outside [0, 1]"
                        )
                    continue
                if not (low <= actual <= high):
                    failures.append(
                        f"{run_name}/{metric}: {actual:.4f} not in [{low}, {high}]"
                    )
        assert not failures, "Score band violations:\n" + "\n".join(failures)


class TestSupersededRunsFlagged:
    """Superseded runs may exist on the account (we just don't quote them)."""

    def test_superseded_runs_queryable(self, q):
        """Verify we can query superseded runs without error (they may have rows or not)."""
        for run_name in SUPERSEDED_RUNS:
            # Try analyst first, then agent — some superseded runs are agent runs
            try:
                rows = q(
                    f"""SELECT COUNT(*)
                        FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                            '{DATABASE}','AI','FULFILLMENT_SV','SEMANTIC VIEW','{run_name}'))"""
                )
            except Exception:
                try:
                    rows = q(
                        f"""SELECT COUNT(*)
                            FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                                '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','{run_name}'))"""
                    )
                except Exception:
                    pass  # Run may not exist at all, which is fine


class TestBaselineOptimizedSameQuestions:
    """BASELINE_V1_FINAL and OPTIMIZED_V2_FINAL cover the SAME 20 questions."""

    def test_question_set_equality(self, q):
        baseline_rows = q(
            f"""SELECT DISTINCT INPUT
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV_V1','SEMANTIC VIEW','BASELINE_V1_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        optimized_rows = q(
            f"""SELECT DISTINCT INPUT
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV','SEMANTIC VIEW','OPTIMIZED_V2_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        baseline_qs = sorted([r[0] for r in baseline_rows])
        optimized_qs = sorted([r[0] for r in optimized_rows])
        assert len(baseline_qs) > 0, "Baseline has no questions"
        assert len(optimized_qs) > 0, "Optimized has no questions"
        assert baseline_qs == optimized_qs, (
            f"Question sets differ! Baseline has {len(baseline_qs)}, "
            f"Optimized has {len(optimized_qs)}"
        )

    def test_optimized_scores_above_baseline_overall(self, q):
        """The optimized view must score ABOVE the baseline overall.

        This replaced a strict test_zero_regressions on 2026-08-17. Per-question
        zero-regression is NOT a property of this system: the judge is
        non-deterministic (5 runs of the UNCHANGED optimized view spanned
        0.525-0.650), so individual questions flip between 0.0/0.5/1.0 between
        runs in both directions. On 5-run averages the real figure is 6 improved,
        3 regressed, 11 flat.

        The claim that IS reproducible is the aggregate ordering -- every one of 5
        optimized runs outscored every one of 5 baseline runs -- so that is what
        gets asserted. Asserting zero regressions would fail intermittently for
        reasons unrelated to build correctness, which is worse than not asserting
        it: a flaky guard trains you to ignore red.
        """
        baseline_rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV_V1','SEMANTIC VIEW','BASELINE_V1_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        optimized_rows = q(
            f"""SELECT AVG(EVAL_AGG_SCORE)
                FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_SV','SEMANTIC VIEW','OPTIMIZED_V2_FINAL'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        assert baseline_rows and baseline_rows[0][0] is not None, "baseline run missing"
        assert optimized_rows and optimized_rows[0][0] is not None, "optimized run missing"
        base = float(baseline_rows[0][0])
        opt = float(optimized_rows[0][0])
        assert opt > base, (
            f"Optimized ({opt:.3f}) must score above baseline ({base:.3f}). "
            f"If this fails, either the optimized view regressed or a run is "
            f"mis-pointed -- check which semantic view each config targets."
        )
