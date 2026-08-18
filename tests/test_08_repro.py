"""Repro: committed .sql reproduces the live objects."""

from __future__ import annotations

import re

import pytest
import yaml

from conftest import AI_SCHEMA, DATABASE, REPO, ddl_normalize


pytestmark = pytest.mark.repro


FILE_TO_OBJECT = {
    "sql/04b_semantic_v1_frozen.sql": ("SEMANTIC VIEW", f"{AI_SCHEMA}.FULFILLMENT_SV_V1"),
    "sql/04c_shipping_sv.sql": ("SEMANTIC VIEW", f"{AI_SCHEMA}.SHIPPING_SV"),
    "sql/06_semantic_v2.sql": ("SEMANTIC VIEW", f"{AI_SCHEMA}.FULFILLMENT_SV"),
}


def _extract_create_statement(sql_text: str) -> str:
    """Extract the CREATE OR REPLACE statement from a SQL file."""
    match = re.search(
        r"(CREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW\b.*)",
        sql_text,
        re.S | re.I,
    )
    if match:
        return match.group(1)
    return sql_text


class TestCommittedSqlMatchesLive:
    """For each key SQL file, the committed CREATE statement matches the live object."""

    def _get_live_ddl(self, q, obj_type: str, obj_name: str) -> str:
        rows = q(f"SELECT GET_DDL('{obj_type}', '{obj_name}')")
        assert rows and rows[0][0], f"Could not fetch live DDL for {obj_name}"
        return rows[0][0]

    def test_v1_frozen_matches_live(self, q):
        filename = "sql/04b_semantic_v1_frozen.sql"
        committed = (REPO / filename).read_text()
        committed_create = _extract_create_statement(committed)
        live_ddl = self._get_live_ddl(q, "SEMANTIC VIEW", f"{AI_SCHEMA}.FULFILLMENT_SV_V1")
        assert ddl_normalize(committed_create) == ddl_normalize(live_ddl), (
            f"{filename} does not match live object FULFILLMENT_SV_V1"
        )

    def test_shipping_sv_matches_live(self, q):
        filename = "sql/04c_shipping_sv.sql"
        committed = (REPO / filename).read_text()
        committed_create = _extract_create_statement(committed)
        live_ddl = self._get_live_ddl(q, "SEMANTIC VIEW", f"{AI_SCHEMA}.SHIPPING_SV")
        assert ddl_normalize(committed_create) == ddl_normalize(live_ddl), (
            f"{filename} does not match live object SHIPPING_SV"
        )

    def test_v2_matches_live(self, q):
        filename = "sql/06_semantic_v2.sql"
        committed = (REPO / filename).read_text()
        committed_create = _extract_create_statement(committed)
        live_ddl = self._get_live_ddl(q, "SEMANTIC VIEW", f"{AI_SCHEMA}.FULFILLMENT_SV")
        assert ddl_normalize(committed_create) == ddl_normalize(live_ddl), (
            f"{filename} does not match live object FULFILLMENT_SV"
        )


class TestNoPlaceholders:
    """No .sql file still contains a placeholder INSTEAD OF real DDL.

    The check looks for files that claim to have DDL but actually only
    contain a comment saying 'the full CREATE ... is the current live DDL'
    (meaning the DDL was never captured). Mentions of the word 'placeholder'
    in historical comments or documentation strings do NOT count.
    """

    PLACEHOLDER_PATTERNS = [
        # This exact phrase means the file was never filled in
        "the full create or replace statement is the current live ddl",
        # Generic TODO markers
        "todo: capture ddl",
        "todo: paste ddl here",
    ]

    def test_no_placeholders_in_sql(self):
        sql_dir = REPO / "sql"
        failures = []
        for sql_file in sorted(sql_dir.glob("*.sql")):
            content = sql_file.read_text().lower()
            for pattern in self.PLACEHOLDER_PATTERNS:
                if pattern in content:
                    failures.append(f"{sql_file.name} contains placeholder: '{pattern}'")
        assert not failures, "Placeholder content found:\n" + "\n".join(failures)


class TestEvalConfigsValid:
    """Every eval_configs/*.yaml parses and its referenced dataset/table exists."""

    def test_all_configs_parse(self):
        """All YAML files parse without error."""
        config_dir = REPO / "eval_configs"
        for f in sorted(config_dir.glob("*.yaml")):
            with open(f) as fh:
                data = yaml.safe_load(fh)
            assert data is not None, f"{f.name} parsed as None"

    def test_referenced_tables_exist(self, q):
        """Each config that references a table_name should point to an existing table."""
        config_dir = REPO / "eval_configs"
        for f in sorted(config_dir.glob("*.yaml")):
            with open(f) as fh:
                config = yaml.safe_load(fh)
            dataset_block = config.get("dataset", {})
            if not dataset_block:
                continue
            table_name = dataset_block.get("table_name")
            if table_name:
                parts = table_name.split(".")
                if len(parts) == 3:
                    db, schema, tbl = parts
                    rows = q(
                        f"SELECT COUNT(*) FROM {db}.INFORMATION_SCHEMA.TABLES "
                        f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{tbl}'"
                    )
                    assert rows[0][0] > 0, (
                        f"Config {f.name} references table {table_name} which does not exist"
                    )


class TestDeployedStreamlitMatchesRepo:
    """The STAGED Streamlit source must match the committed file.

    This is a LIVE drift check, and it exists because the drift actually
    happened: on 2026-08-14 the deployed app on the primary demo account was 424 lines while
    the repo file was 454, and the deployed copy still read
    EVAL.TENANT_ISOLATION_V1_RESULTS -- a table that does not exist on that
    account. Two of the seven tabs were silently broken (Eval History rendered
    empty via a bare except; Tenant Isolation showed "table not found").

    Root cause: sql/09_streamlit.sql creates the stage and the STREAMLIT object
    but the PUT that uploads the source is only a COMMENT, so nothing in the
    repo ever pushes the file. CREATE OR REPLACE STREAMLIT happily points at
    whatever stale bytes are already on the stage and reports success.

    Static tests cannot catch this: the repo file was correct the whole time.
    """

    STAGE_DIR = f"@{DATABASE}.OPS.STREAMLIT_STAGE/observability"
    STAGE_PATH = f"{STAGE_DIR}/observability_app.py"

    def test_staged_app_matches_committed_app(self, sf, tmp_path):
        local = (REPO / "streamlit" / "observability_app.py").read_text()

        cur = sf.cursor()
        try:
            cur.execute(f"GET {self.STAGE_PATH} 'file://{tmp_path}/'")
        finally:
            cur.close()

        downloaded = tmp_path / "observability_app.py"
        assert downloaded.exists(), (
            f"Could not download {self.STAGE_PATH} -- the Streamlit app source is "
            "not on the stage. Run the PUT in docs/SETUP.md step 15."
        )

        staged = downloaded.read_text()
        if staged != local:
            raise AssertionError(
                "Deployed Streamlit source has DRIFTED from the repo "
                f"(staged {len(staged.splitlines())} lines vs repo "
                f"{len(local.splitlines())} lines).\n"
                "Re-deploy:\n"
                "  snow sql -c $CONN -q \"USE DATABASE AGENT_EVAL_DEMO; PUT "
                "'file://$PWD/streamlit/observability_app.py' "
                "@AGENT_EVAL_DEMO.OPS.STREAMLIT_STAGE/observability "
                "AUTO_COMPRESS=FALSE OVERWRITE=TRUE;\"\n"
                "  snow sql -c $CONN -f sql/09_streamlit.sql"
            )

    def test_staged_pyproject_matches_committed(self, sf, tmp_path):
        """pyproject.toml pins Streamlit 1.61.1 and MUST be on the stage.

        Container runtime pre-installs nothing when a pyproject.toml is present,
        so a missing or stale copy either fails with "Failed to get the version
        of the Streamlit library" or silently serves an old Streamlit -- which is
        what produced the original `hide_index` TypeError on warehouse runtime.
        """
        local = (REPO / "streamlit" / "pyproject.toml").read_text()

        cur = sf.cursor()
        try:
            cur.execute(f"GET {self.STAGE_DIR}/pyproject.toml 'file://{tmp_path}/'")
        finally:
            cur.close()

        downloaded = tmp_path / "pyproject.toml"
        assert downloaded.exists(), (
            "pyproject.toml is NOT on the Streamlit stage -- container runtime "
            "will fail to install Streamlit. See docs/SETUP.md step 15."
        )
        assert downloaded.read_text() == local, (
            "Staged pyproject.toml has drifted from the repo; re-run the PUT and "
            "then sql/09_streamlit.sql."
        )
