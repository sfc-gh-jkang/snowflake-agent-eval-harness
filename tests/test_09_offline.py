"""Offline tests — pure Python, NO Snowflake connection required.

Safe to run on a plane, in CI, or off-VPN. Guards:
  - generate_data.py DATE_ONLY_COLUMNS covers the DATE-declared columns
  - to_date32 actually converts to date
  - Short-pick partial-fill logic can produce partial quantities
  - SQL files have balanced quotes and trailing semicolons
  - eval_configs YAMLs are valid with a metrics list
  - DEMO_GUIDE.md contains mandatory sections and no superseded numbers
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.offline


# ---------------------------------------------------------------------------
# 1. generate_data.py — DATE_ONLY_COLUMNS correctness
# ---------------------------------------------------------------------------

class TestGenerateData:
    """Verify that generate_data.py constants and helpers are sound."""

    def test_date_only_columns_covers_exactly_four(self):
        """DATE_ONLY_COLUMNS must list exactly the 4 tables that have DATE-typed columns."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "generate_data", REPO / "python" / "generate_data.py"
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't run main
        spec.loader.exec_module(mod)

        expected_tables = {"fiscal_calendar_445", "waves", "labor_standards", "zone_rate_cards"}
        assert set(mod.DATE_ONLY_COLUMNS.keys()) == expected_tables

        # Total columns across all tables
        all_cols = []
        for cols in mod.DATE_ONLY_COLUMNS.values():
            all_cols.extend(cols)
        # fiscal_calendar_445: CALENDAR_DATE
        # waves: WAVE_DATE
        # labor_standards: EFFECTIVE_DATE
        # zone_rate_cards: EFFECTIVE_DATE, EXPIRY_DATE
        assert len(all_cols) == 5  # 4 tables but zone_rate_cards has 2

    def test_to_date32_converts_to_date(self):
        """to_date32 must convert timestamp columns to Python date objects."""
        import importlib.util
        import pandas as pd
        spec = importlib.util.spec_from_file_location(
            "generate_data", REPO / "python" / "generate_data.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        df = pd.DataFrame({
            "CALENDAR_DATE": [pd.Timestamp("2025-03-15"), pd.Timestamp("2025-04-01")],
            "OTHER_COL": [1, 2],
        })
        result = mod.to_date32(df, "fiscal_calendar_445")
        # After conversion, values should be date objects, not timestamps
        from datetime import date
        for val in result["CALENDAR_DATE"]:
            assert isinstance(val, date) and not isinstance(val, pd.Timestamp)

    def test_short_pick_partial_fill_diverges(self):
        """The partial-fill logic must produce partial quantities (unit fill != line fill)."""
        import importlib.util
        import numpy as np
        spec = importlib.util.spec_from_file_location(
            "generate_data", REPO / "python" / "generate_data.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Generate a small dataset and check that some lines are partially filled
        item_master = mod.generate_item_master(n_skus=100)
        np.random.seed(42)
        orders, lines = mod.generate_orders_and_lines(item_master, n_orders=500)

        short_lines = lines[lines["LINE_STATUS"] == "SHORT"]
        partial_lines = short_lines[
            (short_lines["QTY_SHIPPED_EACHES"] > 0) &
            (short_lines["QTY_SHIPPED_EACHES"] < short_lines["QTY_ORDERED_EACHES"])
        ]
        # Must have at least SOME partial fills to make the trap work
        assert len(partial_lines) > 0, (
            "No partial fills found — unit fill would equal line fill, "
            "making ambiguity trap 2 degenerate"
        )


# ---------------------------------------------------------------------------
# 2. SQL files — balanced quotes and trailing semicolons
# ---------------------------------------------------------------------------

class TestSqlFiles:
    """Every .sql file must have balanced quotes and end with a semicolon."""

    @pytest.fixture
    def sql_files(self):
        return sorted((REPO / "sql").glob("*.sql"))

    def test_sql_files_exist(self, sql_files):
        assert len(sql_files) >= 10, f"Expected 10+ SQL files, found {len(sql_files)}"

    def test_balanced_single_quotes(self, sql_files):
        for f in sql_files:
            content = f.read_text()
            # Strip block comments and line comments
            stripped = re.sub(r"/\*.*?\*/", "", content, flags=re.S)
            stripped = re.sub(r"--[^\n]*", "", stripped)
            count = stripped.count("'")
            assert count % 2 == 0, (
                f"{f.name}: {count} unbalanced single quotes"
            )

    def test_trailing_semicolon(self, sql_files):
        for f in sql_files:
            content = f.read_text().rstrip()
            # Last non-whitespace, non-comment line should end with ;
            lines = [l.rstrip() for l in content.split("\n") if l.strip() and not l.strip().startswith("--")]
            last_meaningful = lines[-1] if lines else ""
            assert last_meaningful.endswith(";"), (
                f"{f.name}: last statement doesn't end with semicolon: '{last_meaningful[-40:]}'"
            )


# ---------------------------------------------------------------------------
# 3. eval_configs — valid YAML with a metrics list
# ---------------------------------------------------------------------------

class TestEvalConfigs:
    """Every YAML in eval_configs/ must parse and have a metrics list."""

    @pytest.fixture
    def yaml_files(self):
        return sorted((REPO / "eval_configs").glob("*.yaml"))

    def test_yaml_files_exist(self, yaml_files):
        assert len(yaml_files) >= 3, f"Expected 3+ YAML configs, found {len(yaml_files)}"

    def test_all_valid_yaml(self, yaml_files):
        for f in yaml_files:
            content = f.read_text()
            try:
                doc = yaml.safe_load(content)
            except yaml.YAMLError as e:
                pytest.fail(f"{f.name}: invalid YAML: {e}")
            assert isinstance(doc, dict), f"{f.name}: top level is not a dict"

    def test_all_have_metrics_list(self, yaml_files):
        for f in yaml_files:
            doc = yaml.safe_load(f.read_text())
            assert "metrics" in doc, f"{f.name}: missing 'metrics' key"
            assert isinstance(doc["metrics"], list), f"{f.name}: 'metrics' is not a list"
            assert len(doc["metrics"]) >= 1, f"{f.name}: 'metrics' list is empty"


# ---------------------------------------------------------------------------
# 4. DEMO_GUIDE.md — mandatory sections + no superseded numbers
# ---------------------------------------------------------------------------

class TestDemoGuide:
    """DEMO_GUIDE.md must have all mandatory sections and no superseded numbers."""

    @pytest.fixture
    def demo_guide(self):
        path = REPO / "docs" / "DEMO_GUIDE.md"
        assert path.exists(), "docs/DEMO_GUIDE.md does not exist"
        return path.read_text()

    def test_mandatory_sections_present(self, demo_guide):
        required_sections = [
            "PRE-FLIGHT",
            "Honest Caveats",
            "Act 1",
            "Act 2",
            "Act 3",
            "Act 4",
            "Act 5",
            "Act 6",
        ]
        for section in required_sections:
            assert section in demo_guide, (
                f"DEMO_GUIDE.md missing mandatory section/keyword: '{section}'"
            )

    def test_no_superseded_numbers(self, demo_guide):
        """Superseded numbers from earlier iterations must NOT appear.

        Three generations of superseded pairs now:
          0.3421/0.5263 (+53.8%)  -- two different question sets, not comparable
          0.375/0.750             -- baseline mis-pointed at the MUTABLE view
          0.325/0.650 (+100%)     -- baseline measured on a FULFILLMENT_SV_V1 that
                                     failed to load (undeclared ZONE_RATE_CARDS),
                                     which zeroed the 3 cost questions for a
                                     structural reason and flattered the delta
        Canonical is now a BAND: baseline 0.40-0.45, optimized 0.53-0.65 (n=5),
        canonical on-screen runs 0.450 -> 0.700.
        """
        superseded = ["0.341", "0.3421", "0.5263", "53.8%", "0.375", "0.750",
                      "0.933", "0.603", "0.325", "+100%"]
        for num in superseded:
            assert num not in demo_guide, (
                f"DEMO_GUIDE.md contains superseded number: {num}"
            )
        # 0.650 is only wrong as a *headline* claim -- it is a legitimate member
        # of the optimized band, so allow it only where the band is being stated.
        for line_no, line in enumerate(demo_guide.splitlines(), 1):
            if "0.650" not in line:
                continue
            assert re.search(r"band|0\.5[23]\s*[-–]\s*0\.65|run|spread|non-?determin",
                             line, re.I), (
                f"DEMO_GUIDE.md:{line_no} quotes 0.650 outside a band/run context: "
                f"{line.strip()[:100]}"
            )
        # For TSA 0.64/0.65 specifically, check they don't appear as TSA scores.
        # The canonical TSA is 0.633
        tsa_pattern = re.compile(r"TSA[^\n]{0,20}0\.6[45]")
        match = tsa_pattern.search(demo_guide)
        assert match is None, (
            f"DEMO_GUIDE.md contains superseded TSA score: {match.group()}"
        )
