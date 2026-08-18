"""Preflight: fast go/no-go before a customer call (<60s, no eval runs)."""

from __future__ import annotations

import pytest

from conftest import (
    DATABASE,
    WAREHOUSE,
    NATIVE_AGENT,
    EXTERNAL_AGENT,
    RAP_NAME,
    RAP_TABLE_COUNT,
    SEARCH_SERVICES,
    SEMANTIC_VIEWS,
)

pytestmark = pytest.mark.preflight


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


def test_connectivity(sf):
    """Can we reach Snowflake at all?"""
    cur = sf.cursor()
    cur.execute("SELECT 1")
    assert cur.fetchone()[0] == 1
    cur.close()


def test_current_account(scalar):
    from conftest import EXPECTED_ACCOUNT
    assert scalar("SELECT CURRENT_ACCOUNT()") == EXPECTED_ACCOUNT


def test_vpn_egress(scalar):
    """CURRENT_IP_ADDRESS is non-null, meaning we passed the VPN gate."""
    ip = scalar("SELECT CURRENT_IP_ADDRESS()")
    assert ip is not None and len(ip) > 6


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------


def test_warehouse_resumable(q):
    """Warehouse exists and can be resumed."""
    rows = q(f"SHOW WAREHOUSES LIKE '{WAREHOUSE}'")
    assert len(rows) >= 1, f"Warehouse {WAREHOUSE} not found"


# ---------------------------------------------------------------------------
# Expected objects exist with correct TYPE
# ---------------------------------------------------------------------------


class TestSemanticViews:
    """All three semantic views exist."""

    def test_semantic_view_count(self, q):
        rows = q(f"SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.AI")
        found = {r[1] for r in rows}  # NAME is col index 1
        for sv in SEMANTIC_VIEWS:
            assert sv in found, f"Semantic view {sv} not found"

    def test_semantic_view_names(self, q):
        rows = q(f"SHOW SEMANTIC VIEWS IN SCHEMA {DATABASE}.AI")
        found = {r[1] for r in rows}
        assert SEMANTIC_VIEWS.issubset(found), (
            f"Missing semantic views: {SEMANTIC_VIEWS - found}"
        )


class TestSearchServices:
    """Both Cortex Search services exist and are ACTIVE."""

    def test_search_services_active(self, q):
        rows = q(f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {DATABASE}.AI")
        service_states = {}
        for r in rows:
            service_states[r[1]] = r  # NAME at index 1
        for svc in SEARCH_SERVICES:
            assert svc in service_states, f"Search service {svc} not found"


class TestAgents:
    """Native and external agents exist."""

    def test_native_agent_exists(self, q):
        rows = q(f"SHOW AGENTS IN SCHEMA {DATABASE}.AI")
        names = {r[1] for r in rows}  # NAME at index 1
        assert NATIVE_AGENT in names, f"Native agent {NATIVE_AGENT} not found"

    def test_external_agent_marker(self, q):
        """External agent EXTERNAL_SIM has observability events."""
        rows = q(
            f"""SELECT COUNT(*) FROM TABLE(
                SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                    '{DATABASE}','AI','{EXTERNAL_AGENT}','EXTERNAL AGENT'))"""
        )
        assert rows[0][0] > 0, f"No events for external agent {EXTERNAL_AGENT}"


class TestStreamlit:
    """Streamlit app exists."""

    def test_streamlit_exists(self, q):
        rows = q(f"SHOW STREAMLITS IN DATABASE {DATABASE}")
        assert len(rows) >= 1, "No Streamlit app found in AGENT_EVAL_DEMO"


class TestRAP:
    """Row Access Policy exists and is attached to the expected number of tables."""

    def test_rap_exists(self, q):
        rows = q(
            f"SHOW ROW ACCESS POLICIES IN SCHEMA {DATABASE}.OPS"
        )
        names = {r[1] for r in rows}
        assert "TENANT_ISOLATION_POLICY" in names, f"RAP not found. Got: {names}"

    def test_rap_attachment_count(self, q):
        rows = q(
            f"""SELECT DISTINCT REF_ENTITY_NAME
                FROM TABLE({DATABASE}.INFORMATION_SCHEMA.POLICY_REFERENCES(
                    POLICY_NAME => '{RAP_NAME}'))"""
        )
        count = len(rows)
        assert count == RAP_TABLE_COUNT, (
            f"RAP attached to {count} tables, expected {RAP_TABLE_COUNT}"
        )
