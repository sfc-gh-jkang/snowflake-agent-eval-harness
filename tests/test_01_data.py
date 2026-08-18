"""Data tier: row counts, timestamp sanity, and the six ambiguity traps."""

from __future__ import annotations

import pytest

from conftest import (
    DATABASE,
    EXPECTED_ROWS,
    ROW_COUNT_TOLERANCE_PCT,
    TRAP_TOLERANCE,
    VARIABLE_CARDINALITY_TABLES,
)

pytestmark = pytest.mark.data


# ---------------------------------------------------------------------------
# Row counts. Fixed-cardinality tables must match EXPECTED_ROWS exactly;
# tables whose size comes from random draws are checked within a tolerance so
# the suite passes on any correctly built account, not just the primary demo account.
# ---------------------------------------------------------------------------


class TestRowCounts:
    """Row counts for all 13 tables."""

    @pytest.mark.parametrize("table,expected", list(EXPECTED_ROWS.items()))
    def test_row_count(self, q, table, expected):
        schema_table = table  # e.g. "FULFILLMENT_INTELLIGENCE.ORDERS"
        rows = q(f"SELECT COUNT(*) FROM {DATABASE}.{schema_table}")
        actual = rows[0][0]

        if table in VARIABLE_CARDINALITY_TABLES:
            tol = expected * ROW_COUNT_TOLERANCE_PCT / 100.0
            assert abs(actual - expected) <= tol, (
                f"{table}: expected {expected:,} +/-{ROW_COUNT_TOLERANCE_PCT}% "
                f"({tol:,.0f} rows), got {actual:,}"
            )
        else:
            assert actual == expected, (
                f"{table}: expected {expected:,} rows, got {actual:,}"
            )


# ---------------------------------------------------------------------------
# Timestamp sanity — ORDERS between 2025 and 2027, not garbage like year 55670000
# ---------------------------------------------------------------------------


class TestTimestampSanity:
    """Order dates make sense (2025–2027 range)."""

    def test_order_date_min(self, scalar):
        val = scalar(
            f"SELECT MIN(ORDER_DATE) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
        )
        assert val is not None
        assert val.year >= 2025, f"MIN(ORDER_DATE) year is {val.year}, expected >=2025"

    def test_order_date_max(self, scalar):
        val = scalar(
            f"SELECT MAX(ORDER_DATE) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
        )
        assert val is not None
        assert val.year <= 2027, f"MAX(ORDER_DATE) year is {val.year}, expected <=2027"

    def test_ship_by_date_sane(self, scalar):
        val = scalar(
            f"SELECT MIN(SHIP_BY_DATE) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"
        )
        assert val is not None
        assert val.year >= 2025


# ---------------------------------------------------------------------------
# The six ambiguity traps — measured spreads within TRAP_TOLERANCE
# ---------------------------------------------------------------------------


class TestAmbiguityTraps:
    """The six traps that make the demo's premise work."""

    # Trap 1: on-time — 59.7% (CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE)
    #                  vs 97.7% (CARRIER_FIRST_SCAN_TS <= PROMISED_DELIVERY_DATE)
    def test_on_time_ship_by(self, scalar):
        pct = scalar(
            f"""SELECT ROUND(100.0 *
                SUM(CASE WHEN CARRIER_FIRST_SCAN_TS <= SHIP_BY_DATE THEN 1 ELSE 0 END)
                / COUNT(*), 1)
                FROM {DATABASE}.SHIPPING_INTELLIGENCE.SHIPMENTS
                WHERE CARRIER_FIRST_SCAN_TS IS NOT NULL AND SHIP_BY_DATE IS NOT NULL"""
        )
        assert abs(float(pct) - 59.7) <= TRAP_TOLERANCE, f"on-time (ship_by): {pct}"

    def test_on_time_promised(self, scalar):
        pct = scalar(
            f"""SELECT ROUND(100.0 *
                SUM(CASE WHEN CARRIER_FIRST_SCAN_TS <= PROMISED_DELIVERY_DATE THEN 1 ELSE 0 END)
                / COUNT(*), 1)
                FROM {DATABASE}.SHIPPING_INTELLIGENCE.SHIPMENTS
                WHERE CARRIER_FIRST_SCAN_TS IS NOT NULL
                  AND PROMISED_DELIVERY_DATE IS NOT NULL"""
        )
        assert abs(float(pct) - 97.7) <= TRAP_TOLERANCE, f"on-time (promised): {pct}"

    # Trap 2: fill rate — order 53.1 / line 87.8 / unit 92.3
    def test_fill_rate_order(self, scalar):
        pct = scalar(
            f"""SELECT ROUND(100.0 *
                SUM(CASE WHEN LINES_FILLED = TOTAL_LINES THEN 1 ELSE 0 END)
                / COUNT(*), 1)
                FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDERS"""
        )
        assert abs(float(pct) - 53.1) <= TRAP_TOLERANCE, f"fill rate (order): {pct}"

    def test_fill_rate_line(self, scalar):
        pct = scalar(
            f"""SELECT ROUND(100.0 *
                SUM(CASE WHEN QTY_SHIPPED_EACHES >= QTY_ORDERED_EACHES THEN 1 ELSE 0 END)
                / COUNT(*), 1)
                FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES"""
        )
        assert abs(float(pct) - 87.8) <= TRAP_TOLERANCE, f"fill rate (line): {pct}"

    def test_fill_rate_unit(self, scalar):
        pct = scalar(
            f"""SELECT ROUND(100.0 * SUM(QTY_SHIPPED_EACHES) / SUM(QTY_ORDERED_EACHES), 1)
                FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES"""
        )
        assert abs(float(pct) - 92.3) <= TRAP_TOLERANCE, f"fill rate (unit): {pct}"

    # Trap 3: units — eaches 2,543,380 vs cartons 362,333 vs lines 211,501
    # "eaches" = QTY_ORDERED_EACHES for SHIPPED lines (the ambiguity definition)
    def test_total_eaches(self, scalar):
        val = scalar(
            f"""SELECT SUM(QTY_ORDERED_EACHES)
                FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES
                WHERE LINE_STATUS = 'SHIPPED'"""
        )
        # Derived from ORDER_LINES, so it moves with the generator. 2% band.
        assert abs(int(val) - 2_543_380) <= 2_543_380 * 0.02, f"eaches: {val}"

    def test_total_cartons(self, scalar):
        val = scalar(
            f"SELECT SUM(QTY_CARTONS) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES"
        )
        assert abs(int(val) - 362_333) <= 362_333 * 0.02, f"cartons: {val}"

    def test_total_lines(self, scalar):
        val = scalar(
            f"SELECT COUNT(*) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES"
        )
        assert abs(int(val) - 211_501) <= 211_501 * 0.02, f"lines: {val}"

    # Trap 4: partial lines >0 (so unit fill != line fill)
    def test_partial_lines_exist(self, scalar):
        val = scalar(
            f"""SELECT COUNT(*) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.ORDER_LINES
                WHERE QTY_SHIPPED_EACHES > 0
                  AND QTY_SHIPPED_EACHES < QTY_ORDERED_EACHES"""
        )
        assert int(val) > 0, "No partial lines — unit vs line fill trick won't work"

    # Trap 5: fiscal calendar — coverage gap for early January
    def test_fiscal_calendar_coverage_gap(self, q):
        """Fiscal 4-4-5 starts 2025-01-26: orders Jan 1-25 have no fiscal row."""
        rows = q(
            f"SELECT MIN(CALENDAR_DATE) FROM {DATABASE}.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445"
        )
        min_date = rows[0][0]
        assert min_date is not None
        # Must be Jan 26 or later
        assert min_date.month == 1 and min_date.day >= 26, (
            f"Fiscal calendar starts {min_date}, expected 2025-01-26"
        )

    # Trap 6: active SKU — movement in trailing 30 days from most recent date
    def test_active_sku_definition(self, scalar):
        """Active SKUs have movement in trailing 30 days (relative to data max date)."""
        val = scalar(
            f"""SELECT COUNT(DISTINCT SKU)
                FROM {DATABASE}.INVENTORY_INTELLIGENCE.MOVEMENTS
                WHERE MOVEMENT_DATE >= DATEADD(DAY, -30,
                    (SELECT MAX(MOVEMENT_DATE)
                     FROM {DATABASE}.INVENTORY_INTELLIGENCE.MOVEMENTS))"""
        )
        # There should be active SKUs but fewer than total items (8000)
        assert 0 < int(val) < 8000, f"Active SKU count: {val}"
