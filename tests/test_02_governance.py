"""Governance tier: row access policy + tenant role isolation."""

from __future__ import annotations

import pytest

from conftest import (
    DATABASE,
    RAP_NAME,
    RAP_TABLE_COUNT,
    TENANT_ROLES,
)

pytestmark = pytest.mark.governance


# ---------------------------------------------------------------------------
# RAP attachment
# ---------------------------------------------------------------------------


class TestRAPAttachment:
    """RAP is attached to exactly RAP_TABLE_COUNT tables."""

    def test_policy_reference_count(self, q):
        rows = q(
            f"""SELECT DISTINCT REF_ENTITY_NAME
                FROM TABLE({DATABASE}.INFORMATION_SCHEMA.POLICY_REFERENCES(
                    POLICY_NAME => '{RAP_NAME}'))"""
        )
        assert len(rows) == RAP_TABLE_COUNT, (
            f"RAP attached to {len(rows)} tables, expected {RAP_TABLE_COUNT}: "
            f"{[r[0] for r in rows]}"
        )


# ---------------------------------------------------------------------------
# Tenant isolation — each role sees ONLY its own tenant
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Each TENANT_ROLE sees exactly one tenant_id and it's the mapped one."""

    @pytest.mark.parametrize("role,expected_tenant", list(TENANT_ROLES.items()))
    def test_tenant_sees_only_own_data(self, sf, role, expected_tenant):
        """As TENANT_ROLE, GROUP BY TENANT_ID returns exactly one row."""
        cur = sf.cursor()
        try:
            cur.execute(f"USE ROLE {role}")
            cur.execute(
                f"""SELECT DISTINCT TENANT_ID
                    FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"""
            )
            tenants = [r[0] for r in cur.fetchall()]
            assert tenants == [expected_tenant], (
                f"Role {role} sees tenants {tenants}, expected only [{expected_tenant}]"
            )
        finally:
            cur.execute("USE ROLE ACCOUNTADMIN")
            cur.close()

    def test_accountadmin_sees_all_tenants(self, q):
        """ACCOUNTADMIN sees all 6 tenants."""
        rows = q(
            f"SELECT DISTINCT TENANT_ID FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
        )
        assert len(rows) == 6, f"ACCOUNTADMIN sees {len(rows)} tenants, expected 6"

    def test_tenant_results_disjoint(self, sf):
        """The rows visible to each tenant role do not overlap."""
        cur = sf.cursor()
        tenant_orders = {}
        try:
            for role, tid in TENANT_ROLES.items():
                cur.execute(f"USE ROLE {role}")
                cur.execute(
                    f"SELECT ORDER_ID FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
                )
                tenant_orders[role] = {r[0] for r in cur.fetchall()}
        finally:
            cur.execute("USE ROLE ACCOUNTADMIN")
            cur.close()

        roles = list(TENANT_ROLES.keys())
        for i in range(len(roles)):
            for j in range(i + 1, len(roles)):
                overlap = tenant_orders[roles[i]] & tenant_orders[roles[j]]
                assert not overlap, (
                    f"{roles[i]} and {roles[j]} share {len(overlap)} orders"
                )
