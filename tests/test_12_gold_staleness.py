"""Ground-truth staleness guard.

WHY THIS FILE EXISTS
--------------------
EVAL.EXTERNAL_EVAL_DATASET stores ground truth as prose containing HARDCODED
numeric answers -- "A total of 1044102 eaches were shipped over that window".
Those numbers were computed once, from one build of the synthetic data, using
the verified-query SQL declared in the semantic view.

The generator is deterministic (numpy seed 42), so a given generator VERSION
always produces identical data. But if the generator ever changes, six of the
thirteen tables shift by a fraction of a percent -- and every hardcoded numeric
gold silently becomes wrong.

The failure mode is nasty because it does not look like a data problem. It looks
like the AGENT got worse:

  Measured 2026-08-18 on an independent rebuild, before this guard existed --
  the `correctness` metric on the native agent scored 0.21 against a documented
  0.926. The agent was not wrong. Asked for eaches shipped it answered
  1,044,102, which is EXACTLY what the verified SQL returns on that data. The
  gold still said 1,046,649, computed against an older generator version, so
  the judge marked a correct answer wrong -- nine times over, across two agents.
  It cost a full investigation to find, and from the outside it read as "the
  semantic layer stopped working".

So: assert the golds against what the verified SQL actually returns, and fail
loudly the moment they diverge. A stale gold is an integrity bug, not a rounding
difference -- it inverts the headline comparison this repo exists to make.

If this test fails, do NOT adjust the tolerance. Recompute the gold:
    1. Run the verified-query SQL below against your build.
    2. Update the prose gold in sql/08b_harness_scoring.sql.
    3. Re-run sql/08b, then 08c, then 08d, then re-score the external agent.
    4. Update the reported numbers in README.md and docs/DEMO_GUIDE.md.
"""

from __future__ import annotations

import re

import pytest

from conftest import DATABASE

pytestmark = pytest.mark.evals


# Each entry: gold label, the verified SQL, and the regex that pulls the
# hardcoded number back out of the stored prose. Keep the SQL verbatim from the
# semantic view's VERIFIED QUERIES so the two cannot drift apart.
GOLD_CHECKS = [
    (
        "eaches shipped 2025-03-01..2025-09-30 (VQ_TOTAL_UNITS_SHIPPED)",
        f"""SELECT SUM(ol.QTY_SHIPPED_EACHES)
            FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES ol
            JOIN {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS o
              ON ol.ORDER_ID = o.ORDER_ID
            WHERE o.ORDER_DATE BETWEEN '2025-03-01' AND '2025-09-30'""",
        r"total of (\d+) eaches",
    ),
    (
        "on-time shipping rate T001 2025-03-01..2025-09-30 (VQ_ON_TIME_ALL)",
        f"""SELECT ROUND(
              COUNT(CASE WHEN s.CARRIER_FIRST_SCAN_TS <= o.SHIP_BY_DATE THEN 1 END)::FLOAT
              / COUNT(*), 4) * 10000
            FROM {DATABASE}.SHIPPING_INTELLIGENCE.SHIPMENTS s
            JOIN {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS o
              ON s.ORDER_ID = o.ORDER_ID
            WHERE o.TENANT_ID = 'T001'
              AND o.ORDER_DATE BETWEEN '2025-03-01' AND '2025-09-30'""",
        r"tenant T001 over that window is 0\.(\d{4})",
    ),
    (
        "orders in fiscal period 7 FY2025 (VQ_ORDERS_IN_FISCAL_PERIOD)",
        f"""SELECT COUNT(*)
            FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS o
            JOIN {DATABASE}.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 fc
              ON o.ORDER_DATE::DATE = fc.CALENDAR_DATE
            WHERE fc.FISCAL_YEAR = 2025 AND fc.FISCAL_PERIOD = 7""",
        r"^(\d+) orders were placed",
    ),
]


@pytest.mark.parametrize("label,verified_sql,gold_pattern", GOLD_CHECKS)
def test_numeric_gold_matches_verified_sql(q, label, verified_sql, gold_pattern):
    """A stored numeric gold must equal what the verified SQL returns today."""
    # Match in Python, not in SQL. Passing this regex through REGEXP_LIKE means
    # fighting two escaping layers, and the failure mode there is a SKIPPED test
    # -- a guard that reports success while checking nothing.
    rows = q(f"SELECT GROUND_TRUTH FROM {DATABASE}.EVAL.EXTERNAL_EVAL_DATASET")
    assert rows, "EVAL.EXTERNAL_EVAL_DATASET is empty -- run sql/08b_harness_scoring.sql"

    matches = [
        (r[0], m)
        for r in rows
        if r[0] and (m := re.search(gold_pattern, r[0], re.M | re.I))
    ]
    assert matches, (
        f"No stored gold matches /{gold_pattern}/ across {len(rows)} rows. The "
        "prose was reworded, so this guard is now inert -- fix gold_pattern "
        "rather than leaving it skipping."
    )
    stored = int(matches[0][1].group(1))

    live_rows = q(verified_sql)
    assert live_rows and live_rows[0][0] is not None, f"verified SQL returned nothing: {label}"
    live = int(live_rows[0][0])

    assert stored == live, (
        f"STALE GROUND TRUTH -- {label}\n"
        f"  stored gold : {stored:,}\n"
        f"  verified SQL: {live:,}\n"
        f"  delta       : {abs(stored - live):,} "
        f"({abs(stored - live) / live * 100:.2f}%)\n\n"
        "The agents are probably answering CORRECTLY and being marked wrong. "
        "Recompute the gold in sql/08b_harness_scoring.sql -- do not widen a "
        "tolerance. See this file's docstring for the procedure."
    )
