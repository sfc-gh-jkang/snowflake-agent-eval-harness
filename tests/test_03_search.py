"""Search tier: Cortex Search services are ACTIVE and serving."""

from __future__ import annotations

import json

import pytest

from conftest import (
    DATABASE,
    SEARCH_SERVICES,
)

pytestmark = pytest.mark.search


# ---------------------------------------------------------------------------
# Service health — both services indexing and serving
# ---------------------------------------------------------------------------


class TestSearchServiceHealth:
    """Both Cortex Search services are ACTIVE."""

    def test_services_exist(self, q):
        rows = q(f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {DATABASE}.AI")
        found = {r[1] for r in rows}
        for svc in SEARCH_SERVICES:
            assert svc in found, f"Search service {svc} not found"

    @pytest.mark.parametrize("service", sorted(SEARCH_SERVICES))
    def test_indexing_state_active(self, q, service):
        rows = q(f"DESCRIBE CORTEX SEARCH SERVICE {DATABASE}.AI.{service}")
        assert len(rows) == 1, f"Service {service} DESCRIBE returned {len(rows)} rows"
        # indexing_state is column index 12
        assert rows[0][12] == "ACTIVE", (
            f"{service} indexing_state is '{rows[0][12]}', expected 'ACTIVE'"
        )

    @pytest.mark.parametrize("service", sorted(SEARCH_SERVICES))
    def test_serving_state_active(self, q, service):
        rows = q(f"DESCRIBE CORTEX SEARCH SERVICE {DATABASE}.AI.{service}")
        assert len(rows) == 1
        # serving_state is column index 14
        assert rows[0][14] == "ACTIVE", (
            f"{service} serving_state is '{rows[0][14]}', expected 'ACTIVE'"
        )


# ---------------------------------------------------------------------------
# Source row counts
# ---------------------------------------------------------------------------


class TestSearchSourceData:
    """Source tables have the expected row counts."""

    def test_item_catalog_source_rows(self, q):
        """ITEM_CATALOG_SEARCH sources 8000 items."""
        rows = q(f"DESCRIBE CORTEX SEARCH SERVICE {DATABASE}.AI.ITEM_CATALOG_SEARCH")
        # source_data_num_rows is column index 11
        assert rows[0][11] == 8000, (
            f"ITEM_CATALOG_SEARCH has {rows[0][11]} source rows, expected 8000"
        )

    def test_ops_knowledge_source_rows(self, q):
        """OPS_KNOWLEDGE_SEARCH sources 63 docs."""
        rows = q(f"DESCRIBE CORTEX SEARCH SERVICE {DATABASE}.AI.OPS_KNOWLEDGE_SEARCH")
        assert rows[0][11] == 63, (
            f"OPS_KNOWLEDGE_SEARCH has {rows[0][11]} source rows, expected 63"
        )


# ---------------------------------------------------------------------------
# Search preview — the Tuesday wave cutoff question hits the exception playbook
# ---------------------------------------------------------------------------


class TestSearchPreview:
    """SEARCH_PREVIEW returns relevant results for known questions."""

    def test_tuesday_wave_cutoff(self, q):
        """The Tuesday-wave-cutoff question returns the exception playbook in top hits."""
        rows = q(
            f"""SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
                '{DATABASE}.AI.OPS_KNOWLEDGE_SEARCH',
                '{{"query": "What is the Tuesday wave cutoff time?", "columns": ["CONTENT", "TITLE"], "limit": 5}}'
            )"""
        )
        assert len(rows) > 0, "SEARCH_PREVIEW returned no rows"
        result = json.loads(rows[0][0])
        assert "results" in result, f"No 'results' key in response: {list(result.keys())}"
        assert len(result["results"]) > 0, "SEARCH_PREVIEW returned empty results"
        # Check that at least one result mentions wave/cutoff/exception in TITLE
        titles = [r.get("TITLE", "") for r in result["results"]]
        all_text = " ".join(titles).lower()
        assert any(
            kw in all_text for kw in ("wave", "cutoff", "exception")
        ), f"Top hit titles don't mention wave/cutoff/exception: {titles}"
