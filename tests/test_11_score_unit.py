"""Offline unit tests for python/external_sim/score.py.

WHY THIS FILE EXISTS
--------------------
`_metrics_done` is a pure string predicate that decides whether an evaluation
run has finished COMPUTING METRICS, as opposed to merely finishing its
invocation. It has already caused a real, visible defect: `score.py` reported

    NO SCORES. Metrics did not compute

and exited, while four metrics were still computing server-side and landed
ninety seconds later. The cause was a substring collision -- testing for
"COMPLETED" also matches "INVOCATION_COMPLETED", which is true the instant the
app finishes running and long before any judge has scored anything. It is the
same class of bug as GOTCHAS #17, on a different API.

These tests need no Snowflake connection, no VPN and no credits, so there is no
excuse for the predicate being unguarded. They run in the default test venv and
should stay fast.

Note the import: score.py sets TRULENS_OTEL_TRACING and imports trulens, which
is pinned to Python 3.9-3.11 and lives only in .venv-harness. The predicate is
therefore loaded from source with importlib rather than imported normally, so
these tests pass in .venv-test (3.14) as well.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SCORE_PY = pathlib.Path(__file__).parent.parent / "python" / "external_sim" / "score.py"


def _load_predicate():
    """Extract and compile `_metrics_done` without importing trulens.

    Parses score.py, pulls out just that function definition, and execs it in an
    empty namespace. This keeps the test honest -- it tests the shipped source,
    not a copy pasted into the test file, which would drift.
    """
    tree = ast.parse(SCORE_PY.read_text())
    fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "_metrics_done"),
        None,
    )
    assert fn is not None, (
        "_metrics_done not found in score.py. If it was renamed, update this "
        "test rather than deleting it -- the substring trap it guards is real."
    )
    ns: dict = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SCORE_PY), "exec"), ns)
    return ns["_metrics_done"]


metrics_done = _load_predicate()


# Every documented RunStatus value, from
# https://docs.snowflake.com/en/sql-reference/functions/execute_ai_evaluation
# Only the two genuine completion states may return True.
@pytest.mark.parametrize(
    "status,expected",
    [
        ("RunStatus.CREATED", False),
        ("RunStatus.INVOCATION_IN_PROGRESS", False),
        # The two that broke it: both CONTAIN "COMPLETED" but mean
        # "the app ran", not "the judge scored".
        ("RunStatus.INVOCATION_COMPLETED", False),
        ("RunStatus.INVOCATION_PARTIALLY_COMPLETED", False),
        ("RunStatus.COMPUTATION_IN_PROGRESS", False),
        ("RunStatus.COMPLETED", True),
        ("RunStatus.PARTIALLY_COMPLETED", True),
        ("RunStatus.CANCELLED", False),
        ("RunStatus.FAILED", False),
    ],
)
def test_metrics_done_per_documented_status(status, expected):
    assert metrics_done(status) is expected, (
        f"_metrics_done({status!r}) returned {metrics_done(status)}, expected "
        f"{expected}. Returning True too early makes score.py read results "
        "before the judge has written them and report NO SCORES on a healthy run."
    )


def test_invocation_completed_is_not_treated_as_done():
    """The exact regression, asserted on its own so the failure message is plain."""
    assert metrics_done("RunStatus.INVOCATION_COMPLETED") is False, (
        "INVOCATION_COMPLETED contains the substring COMPLETED. Treating it as "
        "finished is the bug that produced a false 'NO SCORES' report on "
        "EXTERNAL_SCORED_V2."
    )


def test_bare_completed_still_recognised():
    """Guard the opposite failure: a predicate so strict nothing ever completes."""
    assert metrics_done("RunStatus.COMPLETED") is True
    assert metrics_done("COMPLETED") is True


def test_combined_status_string_is_done():
    """Some payloads carry several states; a real completion must still win."""
    assert metrics_done("INVOCATION_COMPLETED, COMPUTATION_COMPLETED") is True


def test_replace_order_is_not_load_bearing():
    """Both INVOCATION_ variants must be stripped regardless of ordering.

    Documents *why* the implementation is safe: "PARTIALLY" sits between the two
    tokens, so neither string is a substring of the other and the order of the
    two .replace() calls cannot matter. If someone later rewrites this with a
    single regex, this test still holds them to the same behaviour.
    """
    for s in ("INVOCATION_PARTIALLY_COMPLETED INVOCATION_COMPLETED",
              "INVOCATION_COMPLETED INVOCATION_PARTIALLY_COMPLETED"):
        assert metrics_done(s) is False, f"{s!r} should not count as done"


def test_score_py_run_name_default_is_not_a_fixed_literal():
    """The default --run-name must be generated, never hardcoded.

    Runs are immutable and a metric cannot be recomputed for an existing run, so
    a fixed default is wrong on the second invocation. The original default was
    literally the name of the run that died with a TypeError, which made the
    zero-argument invocation the one guaranteed to fail.
    """
    src = SCORE_PY.read_text()
    assert 'default="EXTERNAL_SCORED_V1"' not in src, (
        "score.py --run-name defaults to EXTERNAL_SCORED_V1, the run that FAILED. "
        "Use a timestamped default."
    )
    assert "EXTERNAL_SCORED_{datetime.now()" in src or "%Y%m%d" in src, (
        "score.py --run-name default should be timestamped so every invocation "
        "gets a fresh, valid run name."
    )
