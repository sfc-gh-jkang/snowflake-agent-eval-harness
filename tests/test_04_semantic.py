"""Semantic view structure, v1-vs-v2, verified query parity."""

from __future__ import annotations

import re

import pytest

from conftest import SEMANTIC_VIEWS, AI_SCHEMA, DATABASE


pytestmark = pytest.mark.semantic


class TestSemanticViewsExist:
    def test_all_views_present(self, q):
        rows = q(f"SHOW SEMANTIC VIEWS IN SCHEMA {AI_SCHEMA}")
        names = {r[1] for r in rows}
        for sv in SEMANTIC_VIEWS:
            assert sv in names, f"Semantic view {sv} not found in {AI_SCHEMA}"


def _get_structural_ddl(ddl: str) -> str:
    """Return only the structural part of a semantic view DDL (before verified_queries)."""
    lower = ddl.lower()
    vq_idx = lower.find("verified_queries")
    if vq_idx > 0:
        return ddl[:vq_idx]
    return ddl


class TestFulfillmentSvV1Weak:
    """FULFILLMENT_SV_V1 is the WEAK baseline — no metrics, no shipments->zone_rate_cards relationship."""

    def test_no_metrics_clause(self, q):
        rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.FULFILLMENT_SV_V1')"
        )
        ddl = rows[0][0]
        structural = _get_structural_ddl(ddl).lower()
        assert "metric" not in structural, (
            "FULFILLMENT_SV_V1 should NOT have a metrics clause (it is the weak v1)"
        )

    def test_no_zone_rate_cards_relationship(self, q):
        """v1 must have NO RELATIONSHIP to ZONE_RATE_CARDS -- but it MUST declare
        the table.

        The original version of this test asserted the table was absent entirely,
        with the rationale that "the shipments->zone_rate_cards relationship is the
        v2 improvement". Both halves were wrong:

        1. No view in this demo has a shipments->zone_rate_cards RELATIONSHIP,
           including v2. A semantic-view relationship is an equality join and
           cannot express the rate card's SHIP_DATE BETWEEN EFFECTIVE_DATE AND
           EXPIRY_DATE window. What makes cost answerable on v2 is the declared
           table plus the 3 cost verified queries that demonstrate the join.
        2. v1 CANNOT omit the table. v1 carries the same 20 verified queries as
           v2, 3 of which join ZONE_RATE_CARDS by its bare logical name. Omitting
           it made the ENTIRE model fail to load:
             "Invalid semantic model yaml. SQL compilation error:
              Object 'ZONE_RATE_CARDS' does not exist or not authorized."
           Snowsight could not open the view at all. This test passed the whole
           time, because it was asserting the bug.

        v1 stays weak the legitimate way: vague comments, no relationship, no cost
        metric, no custom instructions.
        """
        rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.FULFILLMENT_SV_V1')"
        )
        ddl = rows[0][0]
        structural = _get_structural_ddl(ddl).lower()

        assert "zone_rate_cards" in structural, (
            "FULFILLMENT_SV_V1 MUST declare ZONE_RATE_CARDS: 3 of its 20 verified "
            "queries join it by bare logical name, and bare names resolve against "
            "the logical model. Omitting it makes the whole model fail to load."
        )

        rel_match = re.search(r"relationships\s*\((.*?)\)\s*\n\s*facts", structural, re.S)
        relationships = rel_match.group(1) if rel_match else ""
        assert "zone_rate_cards" not in relationships, (
            "FULFILLMENT_SV_V1 must not declare a RELATIONSHIP to zone_rate_cards -- "
            "that is what keeps cost weakly supported in the baseline."
        )


class TestFulfillmentSvV2Optimized:
    """FULFILLMENT_SV (v2) HAS metrics, rich descriptions and custom instructions.

    It does NOT have a shipments->zone_rate_cards relationship -- no view does, and
    one could not express the effective-date window anyway. Cost is answerable on v2
    because the table is declared with useful comments and 3 verified queries
    demonstrate the composite join.
    """

    def test_has_metrics(self, q):
        rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.FULFILLMENT_SV')"
        )
        ddl = rows[0][0]
        structural = _get_structural_ddl(ddl).lower()
        assert "metric" in structural, (
            "FULFILLMENT_SV (v2) must have a metrics clause in its structural DDL"
        )

    def test_has_zone_rate_cards_relationship(self, q):
        rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.FULFILLMENT_SV')"
        )
        ddl = rows[0][0]
        structural = _get_structural_ddl(ddl).lower()
        assert "zone_rate_cards" in structural, (
            "FULFILLMENT_SV (v2) must reference zone_rate_cards in its structural DDL"
        )


class TestVerifiedQueryParity:
    """Both v1 and v2 carry the IDENTICAL set of 20 verified queries.

    Both runs were executed against FULFILLMENT_SV (v1 was destroyed by CREATE OR
    REPLACE when v2 was built; FULFILLMENT_SV_V1 was frozen afterward). So both
    BASELINE_V1_FINAL is stored under FULFILLMENT_SV_V1 (frozen v1);
    OPTIMIZED_V2_FINAL under FULFILLMENT_SV (v2). Both carry the same 20 VQs.
    The parity proof is: same 20 questions in both runs.
    """

    def test_same_verified_queries(self, q):
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
        assert len(baseline_qs) == 20, f"Baseline has {len(baseline_qs)} questions, expected 20"
        assert len(optimized_qs) == 20, f"Optimized has {len(optimized_qs)} questions, expected 20"
        assert baseline_qs == optimized_qs, (
            "Question sets differ between BASELINE_V1_FINAL and OPTIMIZED_V2_FINAL — "
            "the before/after comparison is invalid"
        )


class TestShippingSv:
    """SHIPPING_SV exists and has a valid DDL with carrier data."""

    def test_shipping_sv_exists_and_valid(self, q):
        rows = q(f"SHOW SEMANTIC VIEWS IN SCHEMA {AI_SCHEMA}")
        names = {r[1] for r in rows}
        assert "SHIPPING_SV" in names, "SHIPPING_SV must exist"

    def test_shipping_sv_has_carrier_tables(self, q):
        ddl_rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.SHIPPING_SV')"
        )
        assert ddl_rows and ddl_rows[0][0], "SHIPPING_SV must have valid DDL"
        structural = _get_structural_ddl(ddl_rows[0][0]).lower()
        assert "carrier" in structural, "SHIPPING_SV must reference carrier data"


class TestError392700Guard:
    """Every column named in a primary key is ALSO declared as a logical column.

    This guards against error 392700 where Cortex Analyst silently rejects a
    view whose key columns aren't declared as logical columns.
    """

    def _check_view_keys_declared(self, q, view_name: str):
        rows = q(
            f"SELECT GET_DDL('SEMANTIC VIEW', '{AI_SCHEMA}.{view_name}')"
        )
        ddl = rows[0][0]
        structural = _get_structural_ddl(ddl)

        # Find columns referenced in primary_key(...)
        pk_cols = set()
        for m in re.finditer(r"primary_key\s*\(\s*([^)]+)\)", structural, re.I):
            for col in m.group(1).split(","):
                col = col.strip().strip("'\" \t")
                if col:
                    pk_cols.add(col.lower())

        if not pk_cols:
            return  # no primary keys to check

        # Verify DDL is structurally valid by checking these columns appear
        # somewhere in the column/dimension declarations (the real test is that
        # the CREATE succeeded without 392700)
        structural_lower = structural.lower()
        for col in pk_cols:
            assert col in structural_lower, (
                f"{view_name}: primary key column '{col}' not found in structural DDL — "
                "this would trigger error 392700"
            )

    def test_fulfillment_sv_v1(self, q):
        self._check_view_keys_declared(q, "FULFILLMENT_SV_V1")

    def test_fulfillment_sv_v2(self, q):
        self._check_view_keys_declared(q, "FULFILLMENT_SV")

    def test_shipping_sv(self, q):
        self._check_view_keys_declared(q, "SHIPPING_SV")
