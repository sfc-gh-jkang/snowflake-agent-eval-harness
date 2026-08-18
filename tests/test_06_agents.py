"""Native and external agent traces, GPA metrics."""

from __future__ import annotations

import os
import pytest

from conftest import NATIVE_AGENT, EXTERNAL_AGENT, EXPECTED_RUNS, DATABASE


pytestmark = pytest.mark.agents


class TestNativeAgent:
    """Native agent FULFILLMENT_ANALYST has observable events."""

    def test_has_events(self, agent_events):
        rows = agent_events(NATIVE_AGENT, "CORTEX AGENT")
        assert len(rows) > 0, (
            f"Native agent {NATIVE_AGENT} has 0 events — "
            "expected 300+ from eval runs"
        )


class TestExternalAgent:
    """External agent EXTERNAL_SIM has observable events — the lock-in claim."""

    def test_has_events(self, agent_events):
        rows = agent_events(EXTERNAL_AGENT, "EXTERNAL AGENT")
        assert len(rows) > 0, (
            f"External agent {EXTERNAL_AGENT} has 0 events — "
            "this is a HARD FAIL because the lock-in claim depends on it"
        )

    def test_adk_span_types_present(self, agent_events):
        """The five ADK pipeline span types are present.

        Deliberately a SUBSET check, not an equality check: tools.py adds a sixth
        type ('retrieval'), asserted separately in
        test_retrieval_span_present_for_scoring. Keep it a subset so adding a
        span type is never a test failure.
        """
        rows = agent_events(
            EXTERNAL_AGENT, "EXTERNAL AGENT", "DISTINCT SPAN_TYPE"
        )
        span_types = {r[0] for r in rows if r[0]}
        expected_types = {"record_root", "agent", "graph_node", "tool", "generation"}
        missing = expected_types - span_types
        assert not missing, (
            f"External agent {EXTERNAL_AGENT} missing ADK span types: {missing}. "
            f"Found: {span_types}"
        )

    def test_retrieval_span_present_for_scoring(self, agent_events):
        """A `retrieval` span MUST exist or two metrics silently vanish.

        This is a scoring guard, not a tracing guard. context_relevance and
        groundedness read RETRIEVAL.QUERY_TEXT / RETRIEVAL.RETRIEVED_CONTEXTS;
        with no retrieval span they do not error, they simply never compute,
        and the external agent quietly drops from 5 of 5 documented metrics to
        3 of 5. Regression here would look like "we just don't have those two
        numbers" rather than like a bug, which is exactly why it needs a test.
        Emitted by the three @instrument(RETRIEVAL) decorators in
        python/external_sim/tools.py. See GOTCHAS #22.
        """
        rows = agent_events(
            EXTERNAL_AGENT, "EXTERNAL AGENT", "DISTINCT SPAN_TYPE"
        )
        span_types = {r[0] for r in rows if r[0]}
        assert "retrieval" in span_types, (
            "No 'retrieval' span on the external agent. context_relevance and "
            "groundedness cannot be computed without it, and they will fail "
            f"SILENTLY. Found span types: {sorted(span_types)}. Fix: keep the "
            "@instrument(span_type=SpanAttributes.SpanType.RETRIEVAL) "
            "decorators on SnowflakeTools.query_analyst / search_items / "
            "search_ops_knowledge."
        )


class TestExternalAgentScored:
    """The external agent is SCORED, not merely traced.

    Traces answer "where did the time go" and "which stage failed". They cannot
    answer "is it any good", which is the question that actually decides
    whether an eval harness is worth adopting. These tests guard the five
    documented server-side metrics landing for the external agent.
    """

    # The scoring run name is a free choice at score.py invocation time, so it
    # cannot be hardcoded: a fresh build that scored under any other name would
    # fail here for no real reason. Override with SF_EXTERNAL_RUN_NAME.
    #
    # The default used to be EXTERNAL_SCORED_V2, which exists on NO account --
    # not even the one the docs describe. The result was three failures out of
    # the box, one of them announcing "the retrieval spans are gone - see GOTCHAS
    # #22", which is a misdiagnosis: the run simply was not there. The default is
    # now the run docs/SETUP.md step 15e actually produces, and an absent run
    # skips with an actionable message instead of failing as a phantom
    # instrumentation regression.
    RUN_NAME = os.environ.get("SF_EXTERNAL_RUN_NAME", "PARITY_EXTERNAL_V11")

    @pytest.fixture(autouse=True)
    def _require_scored_run(self, q):
        """Skip this class cleanly when the named run holds no rows.

        Distinguishing "run absent" from "run present but missing metrics" is the
        whole point: the second is the GOTCHAS #22 retrieval regression and must
        fail loudly; the first just means you scored under a different name.
        """
        rows = q(
            f"""SELECT COUNT(*) FROM {DATABASE}.EVAL.EXTERNAL_SCORED_RESULTS
                WHERE RUN_NAME = '{self.RUN_NAME}'"""
        )
        if not rows or rows[0][0] == 0:
            available = q(
                f"""SELECT DISTINCT RUN_NAME
                    FROM {DATABASE}.EVAL.EXTERNAL_SCORED_RESULTS
                    ORDER BY RUN_NAME"""
            )
            names = [r[0] for r in available] or ["(none)"]
            pytest.skip(
                f"No scored rows for RUN_NAME={self.RUN_NAME}. This is not a "
                f"retrieval regression -- the run does not exist. Runs present: "
                f"{names}. Re-run with SF_EXTERNAL_RUN_NAME=<one of those>, or "
                f"score a new run (make score RUN=<name>)."
            )
    EXPECTED_METRICS = {
        "coherence", "answer_relevance", "groundedness",
        "context_relevance", "correctness",
    }

    def test_all_five_server_side_metrics_computed(self, q):
        """All 5 documented metrics, or the RETRIEVAL instrumentation regressed.

        Before tools.py emitted retrieval spans only 3 of these computed, and
        the two missing ones produced no error at all. Asserting the full set
        is the only way that regression is visible.
        """
        rows = q(
            f"""SELECT METRIC_NAME, COUNT(*) AS n
                FROM {DATABASE}.EVAL.EXTERNAL_SCORED_RESULTS
                WHERE RUN_NAME = '{self.RUN_NAME}'
                GROUP BY METRIC_NAME"""
        )
        found = {r[0] for r in rows}
        missing = self.EXPECTED_METRICS - found
        assert not missing, (
            f"Scored run {self.RUN_NAME} is missing metrics: {sorted(missing)}. "
            f"Found: {sorted(found)}. If context_relevance or groundedness are "
            "missing, the retrieval spans are gone — see GOTCHAS #22."
        )

    def test_every_question_scored_on_every_metric(self, q):
        """9 questions x 5 metrics = 45 rows, no duplicates.

        GET_ANALYST_AI_EVALUATION_DATA has been observed in this repo returning
        duplicate metric rows (21 for 20 questions), which skewed a mean from
        0.700 to 0.6667. persist_scores() dedups with QUALIFY ROW_NUMBER(); this
        asserts the dedup held.
        """
        rows = q(
            f"""SELECT COUNT(*) AS total,
                       COUNT(DISTINCT INPUT_ID || '|' || METRIC_NAME) AS distinct_pairs
                FROM {DATABASE}.EVAL.EXTERNAL_SCORED_RESULTS
                WHERE RUN_NAME = '{self.RUN_NAME}'"""
        )
        total, distinct_pairs = rows[0][0], rows[0][1]
        assert total == distinct_pairs, (
            f"Duplicate metric rows in {self.RUN_NAME}: {total} rows but only "
            f"{distinct_pairs} distinct (INPUT_ID, METRIC_NAME) pairs. Any mean "
            "computed from this table is skewed."
        )
        assert total == 45, (
            f"Expected 45 scored rows (9 questions x 5 metrics), got {total}."
        )

    def test_scores_are_in_unit_range(self, q):
        """Scores must be normalized 0-1; anything else means a scale mismatch."""
        rows = q(
            f"""SELECT MIN(SCORE), MAX(SCORE),
                       SUM(CASE WHEN SCORE IS NULL THEN 1 ELSE 0 END)
                FROM {DATABASE}.EVAL.EXTERNAL_SCORED_RESULTS
                WHERE RUN_NAME = '{self.RUN_NAME}'"""
        )
        lo, hi, nulls = rows[0]
        assert nulls == 0, f"{nulls} NULL scores in {self.RUN_NAME}"
        assert 0.0 <= float(lo) and float(hi) <= 1.0, (
            f"Scores outside [0,1]: min={lo} max={hi}"
        )

    def test_ground_truth_dataset_matches_harness_questions(self, q):
        """The dataset MUST hold exactly the 9 questions run.py invokes.

        Scoring a different question set than the traces show is worse than not
        scoring: the numbers look valid and describe something else. The
        dataset's INPUT_QUERY is what gets sent to the app, so drift between
        this table and run.py's EVAL_QUESTIONS is silent.
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "python/external_sim/run.py"
        tree = ast.parse(src.read_text())
        questions = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "EVAL_QUESTIONS" for t in node.targets
            ):
                questions = {e.value for e in node.value.elts}
        assert questions, "Could not parse EVAL_QUESTIONS from run.py"

        rows = q(f"SELECT INPUT_QUERY FROM {DATABASE}.EVAL.EXTERNAL_EVAL_DATASET")
        dataset = {r[0] for r in rows}

        assert dataset == questions, (
            "EXTERNAL_EVAL_DATASET has drifted from run.py EVAL_QUESTIONS.\n"
            f"  in run.py only: {sorted(questions - dataset)}\n"
            f"  in dataset only: {sorted(dataset - questions)}"
        )


class TestAgentGpaMetrics:
    """All four GPA metrics present and non-null for AGENT_V4."""

    def test_all_metrics_present(self, q):
        rows = q(
            f"""SELECT METRIC_NAME, EVAL_AGG_SCORE
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','AGENT_V4'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        found_metrics = {r[0].lower(): r[1] for r in rows}
        expected_metrics = set(EXPECTED_RUNS["AGENT_V4"].keys())

        for metric in expected_metrics:
            assert metric in found_metrics, (
                f"AGENT_V4 missing metric: {metric}. Found: {list(found_metrics.keys())}"
            )
            assert found_metrics[metric] is not None, (
                f"AGENT_V4 metric {metric} is NULL"
            )


class TestTenantIsolation:
    """Tenant isolation custom metric exists."""

    def test_tenant_isolation_metric_exists(self, q):
        rows = q(
            f"""SELECT METRIC_NAME, METRIC_TYPE
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','TENANT_ISOLATION_V2'))
                WHERE METRIC_NAME IS NOT NULL"""
        )
        metrics = {r[0].lower(): r[1] for r in rows}
        assert "tenant_isolation" in metrics, (
            f"tenant_isolation metric not found. Found: {list(metrics.keys())}"
        )
        # It should be a custom metric type
        assert metrics["tenant_isolation"] is not None


class TestTenantIsolationCustomType:
    """tenant_isolation has METRIC_TYPE='custom'."""

    def test_metric_type_custom(self, q):
        rows = q(
            f"""SELECT METRIC_NAME, METRIC_TYPE
                FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_EVALUATION_DATA(
                    '{DATABASE}','AI','FULFILLMENT_ANALYST','CORTEX AGENT','TENANT_ISOLATION_V2'))
                WHERE LOWER(METRIC_NAME) = 'tenant_isolation'"""
        )
        assert len(rows) > 0, "tenant_isolation metric not found"
        metric_type = rows[0][1]
        assert metric_type is not None and metric_type.lower() == "custom", (
            f"tenant_isolation METRIC_TYPE should be 'custom', got: {metric_type}"
        )
