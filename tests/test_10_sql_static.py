"""Static guards on the committed SQL — these catch REBUILD regressions.

Why this file exists, and why it is `offline`:

`test_04_semantic.py` has a 392700 guard, but it inspects the LIVE objects. That
passes happily while a committed build script is broken, because during the build
several defects were fixed on the live object and never written back to the file.
The concrete miss: `sql/04_semantic_v1.sql` created `SHIPPING_SV` WITHOUT
declaring `CARRIER_SCANS.SCAN_ID` as a logical column, so a fresh rebuild would
have silently reintroduced error 392700 — the live tests would still have been
green, because the live object was fine.

So: live tests prove the demo works TODAY. These prove it can be REBUILT. Both
are needed, and these run with no Snowflake connection at all.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = [pytest.mark.offline]

REPO = pathlib.Path(__file__).resolve().parent.parent
SQL = REPO / "sql"

# Files that contain CREATE SEMANTIC VIEW statements we care about.
SEMANTIC_SQL_FILES = [
    "04_semantic_v1.sql",
    "04b_semantic_v1_frozen.sql",
    "04c_shipping_sv.sql",
    "06_semantic_v2.sql",
]


def _semantic_blocks(text: str):
    """Yield (object_name, block_text) for each CREATE SEMANTIC VIEW."""
    parts = re.split(r"(?=CREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW)", text, flags=re.I)
    for part in parts:
        m = re.match(r"CREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW\s+(\S+)", part, re.I)
        if m:
            yield m.group(1).split(".")[-1].upper(), part


def _key_columns(block: str) -> dict[str, list[str]]:
    """logical_table -> [columns named in its PRIMARY KEY]."""
    out: dict[str, list[str]] = {}
    for tbl, cols in re.findall(
        r"^\s*(\w+)\s+as\s+\S+\s+primary\s+key\s*\(([^)]+)\)", block, re.I | re.M
    ):
        out[tbl.upper()] = [c.strip().upper() for c in cols.split(",")]
    return out


def _declared_physical_columns(block: str) -> dict[str, set[str]]:
    """logical_table -> {PHYSICAL column names exposed as logical columns}.

    Matches `TABLE.PHYSICAL as ALIAS`. We compare on the PHYSICAL name because a
    key column satisfies the rule even when aliased (ITEM_MASTER.SKU as IM_SKU is
    fine); it is the *physical* column that must be projected.
    """
    out: dict[str, set[str]] = {}
    for tbl, phys, _alias in re.findall(r"^\s*(\w+)\.(\w+)\s+as\s+(\w+)", block, re.I | re.M):
        out.setdefault(tbl.upper(), set()).add(phys.upper())
    return out


@pytest.mark.parametrize("filename", SEMANTIC_SQL_FILES)
def test_key_columns_are_declared_as_logical_columns(filename):
    """Every PRIMARY KEY column must also be projected as a logical column.

    Guards error 392700: 'Unique key column X defined in table Y is not defined
    as a logical column on the table'. CREATE SEMANTIC VIEW succeeds regardless,
    so this defect is latent until Cortex Analyst touches the view — which is
    exactly why it needs a static test.
    """
    path = SQL / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    text = path.read_text()

    violations = []
    for obj, block in _semantic_blocks(text):
        keys = _key_columns(block)
        declared = _declared_physical_columns(block)
        for tbl, cols in keys.items():
            for col in cols:
                if col not in declared.get(tbl, set()):
                    violations.append(f"{filename}::{obj}: {tbl}.{col} is a key but not a logical column")

    assert not violations, (
        "These would fail at Cortex Analyst runtime with error 392700 even though "
        "CREATE SEMANTIC VIEW succeeds:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize("filename", SEMANTIC_SQL_FILES)
def test_relationship_columns_are_declared(filename):
    """Columns used in RELATIONSHIPS must also exist as logical columns."""
    path = SQL / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    text = path.read_text()

    violations = []
    for obj, block in _semantic_blocks(text):
        declared = _declared_physical_columns(block)
        # e.g.  EXCEPTIONS_TO_ORDERS as EXCEPTIONS(ORDER_ID) references ORDERS(ORDER_ID)
        for left_tbl, left_cols, right_tbl, right_cols in re.findall(
            r"^\s*\w+\s+as\s+(\w+)\s*\(([^)]+)\)\s*references\s+(\w+)\s*\(([^)]+)\)",
            block, re.I | re.M,
        ):
            for tbl, cols in ((left_tbl, left_cols), (right_tbl, right_cols)):
                for col in (c.strip().upper() for c in cols.split(",")):
                    if col not in declared.get(tbl.upper(), set()):
                        violations.append(f"{filename}::{obj}: {tbl}.{col} used in a relationship but not declared")

    assert not violations, "Undeclared relationship columns:\n  " + "\n  ".join(violations)


def test_shipping_sv_scan_id_regression():
    """Explicit regression test for the exact defect that was missed.

    `sql/04_semantic_v1.sql` shipped without CARRIER_SCANS.SCAN_ID, so rebuilding
    from source would have recreated SHIPPING_SV with the latent 392700 bug while
    every live test stayed green. Pin it so it cannot come back.
    """
    text = (SQL / "04_semantic_v1.sql").read_text()
    blocks = {obj: b for obj, b in _semantic_blocks(text)}
    assert "SHIPPING_SV" in blocks, "04_semantic_v1.sql should create SHIPPING_SV"
    assert re.search(r"CARRIER_SCANS\.SCAN_ID\s+as\s+", blocks["SHIPPING_SV"], re.I), (
        "SHIPPING_SV in 04_semantic_v1.sql must declare CARRIER_SCANS.SCAN_ID as a "
        "logical column, or a fresh rebuild reintroduces error 392700"
    )


def test_teardown_drops_account_level_roles():
    """Teardown must remove the tenant roles, not just the database.

    DROP DATABASE does not remove account-level roles, so the original teardown
    leaked TENANT_ALDERWOOD and TENANT_BELLWEATHER on every rebuild cycle.
    """
    text = (SQL / "AGENT_EVAL_DEMO_TEARDOWN.sql").read_text().upper()
    assert "DROP DATABASE" in text
    assert "DROP WAREHOUSE" in text
    for role in ("TENANT_ALDERWOOD", "TENANT_BELLWEATHER"):
        assert re.search(rf"DROP\s+ROLE\s+(IF\s+EXISTS\s+)?{role}", text), (
            f"teardown must drop account-level role {role} — DROP DATABASE will not"
        )


def test_notebook_deploy_declares_runtime_name():
    """Container Runtime needs RUNTIME_NAME, not just COMPUTE_POOL.

    Verified 2026-08-13: with COMPUTE_POOL set and RUNTIME_NAME omitted, the
    CREATE succeeds and DESCRIBE even reports compute_pool, but the notebook
    runs on WAREHOUSE runtime (runtime_environment_version 'WH-RUNTIME-2.0',
    code_warehouse 'SYSTEM$STREAMLIT_NOTEBOOK_WH'). The deliverable was
    specified as Container Runtime, so the clause must stay.

    SHOW NOTEBOOKS exposes neither column -- only DESCRIBE NOTEBOOK does.
    """
    sql = (SQL / "08_notebook.sql").read_text()
    assert "RUNTIME_NAME" in sql, (
        "08_notebook.sql lost RUNTIME_NAME -- a rebuild would silently produce a "
        "WAREHOUSE-runtime notebook while still claiming Container Runtime"
    )
    assert "COMPUTE_POOL" in sql, "08_notebook.sql lost COMPUTE_POOL"


def test_notebook_uses_dedicated_compute_pool():
    """The notebook must own its pool, not borrow an unrelated demo's.

    It originally ran on TUTORIAL_COMPUTE_POOL, which belongs to another demo
    on this account. A Container Runtime notebook occupies a WHOLE node, so
    sharing means this demo's teardown (or a rebuild) can evict someone else's
    running service, and their teardown can evict ours mid-demo.

    Also asserts the deploy script CREATEs the pool: without it a fresh
    rebuild on a clean account produces a notebook pointing at a pool that
    does not exist, which fails only when someone tries to run it.
    """
    sql = (SQL / "08_notebook.sql").read_text().upper()

    assert "TUTORIAL_COMPUTE_POOL" not in sql, (
        "08_notebook.sql is back on the borrowed TUTORIAL_COMPUTE_POOL"
    )
    assert re.search(r"COMPUTE_POOL\s*=\s*AGENT_EVAL_DEMO_NB_POOL", sql), (
        "08_notebook.sql must bind the notebook to AGENT_EVAL_DEMO_NB_POOL"
    )
    assert re.search(r"CREATE\s+COMPUTE\s+POOL", sql), (
        "08_notebook.sql must create AGENT_EVAL_DEMO_NB_POOL — a rebuild on a clean "
        "account would otherwise reference a nonexistent pool"
    )
    # SYSADMIN owns the notebook, so it needs USAGE on the pool or the
    # notebook cannot start.
    assert re.search(
        r"GRANT[^;]*USAGE[^;]*ON\s+COMPUTE\s+POOL\s+AGENT_EVAL_DEMO_NB_POOL[^;]*SYSADMIN",
        sql,
    ), "SYSADMIN must be granted USAGE on AGENT_EVAL_DEMO_NB_POOL"


def test_teardown_drops_compute_pool():
    """A compute pool is account-level, so DROP DATABASE leaves it behind.

    Same leak class as the tenant roles: the pool survives teardown and keeps
    billing every time something resumes it.
    """
    text = (SQL / "AGENT_EVAL_DEMO_TEARDOWN.sql").read_text().upper()
    assert re.search(
        r"DROP\s+COMPUTE\s+POOL\s+(IF\s+EXISTS\s+)?AGENT_EVAL_DEMO_NB_POOL", text
    ), "teardown must drop AGENT_EVAL_DEMO_NB_POOL — DROP DATABASE will not"


def test_notebook_baseline_targets_frozen_v1():
    """The CI/CD gate must score baseline on the FROZEN view.

    Both runs originally used analyst_evaluation_config.yaml, which points at
    FULFILLMENT_SV -- now v2. That scored v2 twice, so lift came out ~0.00 and
    the notebook FAILED its own MIN_IMPROVEMENT assertion when executed. A
    demo whose gate always fails is worse than no gate.
    """
    import json

    nb_path = REPO / "notebook" / "eval_cicd_gating.ipynb"
    src = json.dumps(json.loads(nb_path.read_text()))

    assert "FULFILLMENT_SV_V1" in src, (
        "notebook baseline no longer references the frozen FULFILLMENT_SV_V1"
    )
    # Superseded run names must not be reintroduced into the account.
    for bad in ("'BASELINE_V1'", "'OPTIMIZED_V2'"):
        assert bad not in src, f"notebook reintroduces superseded run name {bad}"
    # Canonical demo snapshots must not be clobbered by a notebook demo run.
    for bad in ("EVAL.OPTIMIZED_V2_RESULTS", "EVAL.BASELINE_V1_FINAL_RESULTS"):
        assert f"TABLE {bad}" not in src, f"notebook would overwrite {bad}"


def test_notebook_does_not_start_evaluations():
    """A notebook CANNOT start an evaluation -- it must only READ completed runs.

    Platform constraint, verified live 2026-08-14 across five notebook runs:
    EXECUTE_AI_EVALUATION('START') from inside a notebook registers the run and
    then never executes it. Status stays CREATED indefinitely and NO error is
    raised, so the notebook hangs until its poll ceiling and dies with a
    misleading TimeoutError. The same call from a stored procedure fails loudly
    and names the mechanism: "contains a function with side effects
    [SYSTEM$CORTEX_ANALYST_CREATE_ANALYST_EVAL_OPTIMIZATION]".

    Role is NOT the discriminator -- an ACCOUNTADMIN-owned notebook stalls too,
    while the identical call from a SQL worksheet completes in ~3.5 minutes.

    So this is the regression guard: if someone reintroduces a START call the
    notebook silently becomes a 10-minute hang in front of an audience.
    """
    import json
    import re

    nb_path = REPO / "notebook" / "eval_cicd_gating.ipynb"
    nb = json.loads(nb_path.read_text())

    for i, cell in enumerate(nb["cells"]):
        if cell["cell_type"] != "code":
            continue
        body = "".join(cell["source"])
        # Strip comments: the config cell documents this constraint in prose and
        # must be allowed to name the function it is warning about.
        code_only = "\n".join(
            ln for ln in body.splitlines() if not ln.strip().startswith(("#", "--"))
        )
        assert not re.search(r"EXECUTE_AI_EVALUATION\s*\(", code_only), (
            f"notebook cell {i} calls EXECUTE_AI_EVALUATION -- a notebook cannot "
            f"start or poll an evaluation; it stalls at status CREATED with no error"
        )

    # The gate must read the two canonical completed runs.
    src = json.dumps(nb)
    for run in ("BASELINE_V1_FINAL", "OPTIMIZED_V2_FINAL"):
        assert run in src, f"notebook must read canonical completed run {run}"


# ---------------------------------------------------------------------------
# Guards added after rebuilding the entire demo from scratch on a second
# account (a second account, 2026-08-14). Every one of these corresponds to a defect
# that a from-scratch build actually hit and the live tests could not see.
# ---------------------------------------------------------------------------


def _sql_files():
    return sorted(SQL.glob("*.sql"))


def _strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def test_no_reserved_word_rows_alias():
    """`AS rows` is a syntax error (001003) and exits the whole script non-zero.

    Both 01_load_data.sql and 03_search.sql ended their verification query with
    `COUNT(*) AS rows`. Every COPY INTO above it succeeded, so the data was fine
    and nobody noticed the script was returning exit code 1.
    """
    offenders = []
    for path in _sql_files():
        body = _strip_sql_comments(path.read_text())
        if re.search(r"\bAS\s+rows\b", body, re.I):
            offenders.append(path.name)
    assert not offenders, (
        f"`AS rows` is reserved and fails with 001003, in: {offenders}. "
        "Use row_count."
    )


def test_no_adjacent_string_literals():
    """Snowflake does not concatenate adjacent literals the way Python does.

    `COMMENT ON ... IS 'part one ' 'part two'` is a 001003 syntax error, which
    is why 08_external_agent.sql had never run successfully as a whole file.
    Use one literal or explicit ||.
    """
    offenders = []
    for path in _sql_files():
        lines = path.read_text().splitlines()
        for i in range(len(lines) - 1):
            a, b = lines[i].rstrip(), lines[i + 1].strip()
            if a.lstrip().startswith("--") or b.startswith("--"):
                continue
            # A literal ending a line immediately followed by a literal, and the
            # first is not part of a comma-separated list (VALUES rows etc).
            if a.endswith("'") and b.startswith("'") and not a.endswith(",'"):
                offenders.append(f"{path.name}:{i + 1}")
    assert not offenders, (
        "Adjacent string literals are a 001003 syntax error in Snowflake: "
        f"{offenders}"
    )


# Tables the demo's own surfaces read. If a script does not create these, a
# rebuilt account renders an empty Evaluations tab and Act 6 dies on a missing
# table -- which is exactly what happened: these four were built by hand on
# the primary demo account and no committed script produced them.
CANONICAL_RESULT_TABLES = [
    "BASELINE_V1_FINAL_RESULTS",
    "OPTIMIZED_V2_FINAL_RESULTS",
    "AGENT_V4_RESULTS",
    "TENANT_ISOLATION_V2_RESULTS",
]


def test_canonical_result_tables_are_created_by_some_script():
    all_sql = "\n".join(p.read_text() for p in _sql_files())
    missing = [
        t for t in CANONICAL_RESULT_TABLES
        if not re.search(rf"CREATE\s+OR\s+REPLACE\s+TABLE\s+EVAL\.{t}\b", all_sql, re.I)
    ]
    assert not missing, (
        f"No committed SQL creates {missing}. The Streamlit app and the Act 6 "
        "notebook read these by name, so a rebuild would break."
    )


def test_streamlit_reads_only_tables_some_script_creates():
    """Close the loop the other way: everything the app reads must be buildable."""
    app = (REPO / "streamlit" / "observability_app.py").read_text()
    all_sql = "\n".join(p.read_text() for p in _sql_files())
    referenced = set(re.findall(r"AGENT_EVAL_DEMO\.EVAL\.([A-Z0-9_]+)", app))
    # `IF NOT EXISTS` counts as creating the table. It was originally omitted,
    # which failed EXTERNAL_SCORED_RESULTS -- a table that deliberately uses
    # IF NOT EXISTS because it ACCUMULATES scored snapshots across runs and must
    # never be dropped by a re-run of its own script. The guard's intent is
    # "some committed script builds this on a fresh account", and IF NOT EXISTS
    # satisfies that; excluding it pushed toward CREATE OR REPLACE, which would
    # have silently discarded every prior run's scores.
    create = (
        r"CREATE\s+(OR\s+REPLACE\s+)?(TEMP\s+)?TABLE\s+"
        r"(IF\s+NOT\s+EXISTS\s+)?(EVAL\.)?{}\b"
    )
    missing = [
        t for t in sorted(referenced)
        if not re.search(create.format(t), all_sql, re.I)
    ]
    assert not missing, (
        f"Streamlit reads EVAL tables that no script creates: {missing}"
    )


def test_every_snapshotted_run_is_started_by_some_script():
    """A snapshot of a run nobody starts is a guaranteed hard failure.

    07_agent.sql snapshotted AGENT_V2, which no script ever started, so the
    file aborted mid-way on every fresh account.
    """
    all_sql = "\n".join(p.read_text() for p in _sql_files())
    started = set(re.findall(r"'run_name'\s*,\s*'([A-Z0-9_]+)'", all_sql))
    snapshotted = set(
        re.findall(r"'(?:SEMANTIC VIEW|CORTEX AGENT)'\s*,\s*'([A-Z0-9_]+)'\s*\)", all_sql)
    )
    orphans = sorted(snapshotted - started)
    assert not orphans, (
        f"These runs are read but never started by any script: {orphans}"
    )


def test_v2_semantic_file_is_pure_ddl():
    """test_08_repro diffs this whole file against the live object.

    Appending anything after the CREATE statement (an eval call, a verification
    SELECT) breaks drift detection. That is why the v2 re-eval lives in
    sql/06b_eval_optimized.sql.
    """
    body = _strip_sql_comments((SQL / "06_semantic_v2.sql").read_text())
    statements = [s.strip() for s in body.split(";") if s.strip()]
    # USE ROLE/DATABASE/SCHEMA/WAREHOUSE are session setup, not content.
    substantive = [s for s in statements if not re.match(r"USE\s+", s, re.I)]
    assert len(substantive) == 1, (
        f"06_semantic_v2.sql must contain exactly one substantive statement (the "
        f"CREATE SEMANTIC VIEW); found {len(substantive)}. Move extra SQL to 06b."
    )


def test_eval_scripts_wait_before_snapshotting():
    """START is asynchronous; snapshotting immediately yields an empty table.

    Every eval script exited 0 while leaving 0-row snapshots behind.
    """
    missing = []
    for name in (
        "05_eval_baseline.sql",
        "06b_eval_optimized.sql",
        "07_agent.sql",
        "08_tenant_isolation_eval.sql",
    ):
        body = (SQL / name).read_text()
        if "EXECUTE_AI_EVALUATION" in body and "SYSTEM$WAIT" not in body:
            missing.append(name)
    assert not missing, (
        f"These start an eval but never wait before snapshotting: {missing}"
    )


def test_baseline_targets_frozen_view_not_mutated_one():
    """The baseline must score the FROZEN v1 view.

    06_semantic_v2.sql replaces FULFILLMENT_SV in place, so a baseline pointed
    at FULFILLMENT_SV is only correct if it runs before 06 -- and the Act 6
    notebook reads the baseline under FULFILLMENT_SV_V1 regardless. Targeting
    the frozen view scored 0.325 exactly on the rebuild; targeting the mutable
    one scored 0.375 and emitted a duplicate metric row.
    """
    body = (SQL / "05_eval_baseline.sql").read_text()
    assert "analyst_evaluation_config_v1.yaml" in body, (
        "05_eval_baseline.sql must use analyst_evaluation_config_v1.yaml, which "
        "targets the frozen FULFILLMENT_SV_V1"
    )
    assert re.search(
        r"GET_ANALYST_AI_EVALUATION_DATA\(\s*'AGENT_EVAL_DEMO',\s*'AI',\s*'FULFILLMENT_SV_V1'",
        body,
    ), "05_eval_baseline.sql must snapshot the baseline from FULFILLMENT_SV_V1"


# ---------------------------------------------------------------------------
# Doc-consistency guards. Two separate incidents made these necessary:
#
# 1. The README's headline paragraph claimed "37.5% -> 75.0%, Nine questions
#    improved" while the code block three lines above it said a different pair.
# 2. Worse: the whole 0.325 -> 0.650 / "+100%, zero regressions" story was
#    measured against a FULFILLMENT_SV_V1 that was structurally INVALID -- its
#    verified queries joined a ZONE_RATE_CARDS it never declared, so the model
#    failed to load and the 3 cost questions scored 0 for the wrong reason. With
#    the view fixed, the baseline rises and the gap narrows.
#
# Canon is now a BAND, measured n=5 per side on the primary demo account (2026-08-17), because a
# single draw of an LLM judge is not a fact: the UNCHANGED v2 view scored
# 0.525, 0.625, 0.650, 0.625, 0.525 across five runs.
# ---------------------------------------------------------------------------

DOC_FILES = ("README.md", "docs/DEMO_GUIDE.md", "docs/PRE_DEMO_CHECKLIST.md", "docs/SETUP.md")

# Canonical on-screen runs (BASELINE_V1_FINAL / OPTIMIZED_V2_FINAL), the primary demo account.
CANON_BASELINE = ("0.450", "0.45", "45.0%")
CANON_OPTIMIZED = ("0.700", "0.70", "70.0%")
# Per-question verdicts computed on 5-run AVERAGES, not a single pair.
CANON_IMPROVED = 7
CANON_REGRESSED = 0

# Numbers that came from the broken-view era. Quoting any of them is the bug
# this guard exists to prevent.
SUPERSEDED_SCORES = ("0.325", "32.5%", "0.650", "65.0%", "+100%", "0.375", "0.750",
                     "37.5%", "75.0%", "0.3421", "0.5263", "53.8%")


def _doc_text(name: str) -> str:
    path = REPO / name
    return path.read_text() if path.exists() else ""


# Markers that mean "this line is RECORDING a wrong number, not claiming it".
# The docs deliberately preserve what the superseded figures were, so the guards
# have to tell an annotation apart from a claim. Checked over a small window
# because these annotations wrap across lines.
_ANNOTATION = re.compile(
    r"supersed|do not quote|no longer|previously|was measured|broken|older|"
    r"pre-2026|failed to load|not comparable|"
    # Band context: a line listing the measured spread legitimately contains
    # values like 0.650 -- that is the point of quoting a band.
    r"determinis|five runs|spread|\bband\b|n=5|per side",
    re.I,
)
_ANNOTATION_WINDOW = 3


def _in_annotation(lines: list[str], idx: int) -> bool:
    """True if line idx (0-based) is inside a superseded-number annotation."""
    lo = max(0, idx - _ANNOTATION_WINDOW)
    return any(_ANNOTATION.search(l) for l in lines[lo: idx + 1])


def test_docs_state_the_correct_improved_count():
    """6 of 20 questions improved on 5-run averages -- not 8 and not 9.

    "8 improved, zero regressions" was the broken-view figure. On the fixed view
    it is 7 improved / 0 regressed / 13 flat.
    """
    patterns = (r"\b9 improved\b", r"\bnine improved\b",
                r"\bNine questions improved\b", r"\b9 questions improved\b",
                r"\bnine questions moved\b",
                r"\b8 improved\b", r"\beight improved\b",
                r"\bEight questions improved\b", r"\b8 questions improved\b")
    # NOTE: "zero regressions" is NOT forbidden any more. It was banned on
    # 2026-08-17 because the then-current model produced 3 real regressions. The
    # join-path fix (ORDERS_TO_TENANT + SHIPMENTS_TO_RATE_CARDS) eliminated the
    # HTTP 500s that caused them, and the canonical pair now measures 7 improved /
    # 0 regressed / 13 flat. The claim is true again, so the guard must not block it.
    bad = []
    for name in DOC_FILES:
        lines = _doc_text(name).splitlines()
        for idx, line in enumerate(lines):
            if _in_annotation(lines, idx):
                continue
            for pattern in patterns:
                if re.search(pattern, line, re.I):
                    bad.append(f"{name}:{idx + 1} -> {pattern}")
    assert not bad, (
        f"Docs state a superseded improved/regressed count as a live claim; live "
        f"data says {CANON_IMPROVED} improved / {CANON_REGRESSED} regressed on "
        f"5-run averages. Offenders: {bad}"
    )


def test_docs_do_not_state_superseded_score_pair():
    """The broken-view scores must not appear next to a scoring claim.

    0.325 -> 0.650 (+100%) is the most quotable pair in the repo's history and
    the most wrong: the baseline was depressed by an invalid semantic view. Also
    covers the older 0.375/0.75 and 0.3421/0.5263 pairs.
    """
    bad = []
    for name in DOC_FILES:
        lines = _doc_text(name).splitlines()
        for idx, line in enumerate(lines):
            if not re.search(r"sql_correctness|questions score|semantic view|baseline|optimized", line, re.I):
                continue
            if _in_annotation(lines, idx):
                continue
            for bad_val in SUPERSEDED_SCORES:
                if bad_val in line:
                    bad.append(f"{name}:{idx + 1} -> {bad_val}")
    assert not bad, (
        "Docs quote a superseded score alongside a scoring claim; canonical is "
        f"{CANON_BASELINE[0]} -> {CANON_OPTIMIZED[0]} (band 0.40-0.45 -> "
        f"0.53-0.65, n=5). Offenders: {bad}"
    )


def test_demo_guide_headline_matches_canonical_scores():
    """The demo guide must carry the canonical pair somewhere, or it is not the
    document a presenter should be reading from."""
    body = _doc_text("docs/DEMO_GUIDE.md")
    assert any(v in body for v in CANON_BASELINE), (
        f"DEMO_GUIDE lost the {CANON_BASELINE[0]} baseline"
    )
    assert any(v in body for v in CANON_OPTIMIZED), (
        f"DEMO_GUIDE lost the {CANON_OPTIMIZED[0]} optimized score"
    )


def test_demo_guide_discloses_judge_nondeterminism():
    """The band is the headline, so the demo guide must say the judge is noisy.

    Without this, a presenter quotes a decimal and a live re-run contradicts it
    on stage -- which is exactly what would have happened with the old
    "expect 0.62-0.70" instruction after a 0.525 draw.
    """
    body = _doc_text("docs/DEMO_GUIDE.md")
    assert re.search(r"non-?determin", body, re.I), (
        "DEMO_GUIDE must disclose that the LLM judge is non-deterministic"
    )
    assert re.search(r"0\.40\s*[-–]\s*0\.45|0\.4\s*[-–]\s*0\.45", body), (
        "DEMO_GUIDE must state the measured baseline band (0.40-0.45)"
    )
    # The optimized side is no longer a wide band: after the join-path fix all
    # four runs returned exactly 0.700, because the HTTP 500s that caused most of
    # the run-to-run swing are gone. Require the score and the run count instead.
    assert "0.700" in body or "0.70" in body, (
        "DEMO_GUIDE must state the measured optimized score (0.700)"
    )


def test_demo_guide_act4_uses_error_column_not_status():
    """Act 4 tells the presenter to say "18 errors" out loud.

    Every EXTERNAL_SIM span is STATUS_CODE_UNSET, so a STATUS-based filter shows
    ZERO errors. The demo guide must hand over an ERROR-column query, or the
    presenter narrates a number the screen contradicts.
    """
    body = _doc_text("docs/DEMO_GUIDE.md")
    assert "18" in body, "DEMO_GUIDE lost the 18-error disclosure"
    assert re.search(r"ERROR IS NOT NULL", body), (
        "Act 4 must include an ERROR IS NOT NULL query so the 18-error claim is "
        "displayable; STATUS-based filtering shows zero errors"
    )


# ---------------------------------------------------------------------------
# Verified-query column guard.
#
# Verified query SQL is resolved against the LOGICAL semantic model, not the
# physical table. FULFILLMENT_SV and FULFILLMENT_SV_V1 both shipped with three
# VQs referencing `s.SHIP_BY_DATE` (s aliased to SHIPMENTS) while SHIPMENTS
# declared only 12 other columns. The physical column exists, so nothing in SQL
# complained -- but Snowsight refused to open either view:
#   "Invalid semantic model yaml. SQL compilation error:
#    invalid identifier 'S.SHIP_BY_DATE'"
#
# It survived because the failure is asymmetric: the v2 ON_TIME_RATE *metric*
# also references SHIP_BY_DATE and worked fine (metric expressions resolve
# against physical columns), and the evals still produced scores. Only the
# verified queries went through the logical model.
# ---------------------------------------------------------------------------

SEMANTIC_VIEW_FILES = (
    "sql/04_semantic_v1.sql",
    "sql/04b_semantic_v1_frozen.sql",
    "sql/04c_shipping_sv.sql",
    "sql/06_semantic_v2.sql",
)


def _declared_logical_columns(ddl: str) -> set[str]:
    """Every TABLE.COLUMN declared as a dimension, fact, or metric."""
    return set(re.findall(r"^\s*([A-Z_0-9]+)\.([A-Z_0-9]+)\s+as\s", ddl, re.M | re.I))


def _vq_alias_references(ddl: str):
    """[(table, column, alias, qualified)] for each <alias>.<COL> inside a VQ,
    resolving the alias via `FROM [db.schema.]TABLE alias`.

    `qualified` is True when the VQ wrote the table fully qualified
    (`DB.SCHEMA.TABLE alias`). That distinction matters: Snowflake accepts BOTH
    forms in verified-query SQL, but they resolve differently.

      bare `ZONE_RATE_CARDS zrc`        -> resolved against the LOGICAL model,
                                          so the view MUST declare that table
      `AGENT_EVAL_DEMO.FULFILLMENT_INTELLIGENCE.FISCAL_CALENDAR_445 fc`
                                       -> resolved physically, bypasses the
                                          logical model, legal even though the
                                          logical alias is FISCAL_CALENDAR

    Both forms are live and working in this repo (04b uses the first for
    FISCAL_CALENDAR, 06 uses the second for the same table), so a guard that
    ignores the difference produces false positives on v2.
    """
    out = []
    for sql_line in re.findall(r"SQL\s+'(.*?)'\)", ddl, re.S):
        # alias -> (table, qualified?), from FROM/JOIN clauses
        aliases = {}
        for prefix, tbl, alias in re.findall(
            r"(?:FROM|JOIN)\s+((?:[A-Z_0-9]+\.)*)([A-Z_0-9]+)\s+([a-z][a-z0-9_]*)",
            sql_line, re.I,
        ):
            aliases[alias.lower()] = (tbl.upper(), bool(prefix))
        for alias, col in re.findall(r"\b([a-z][a-z0-9_]*)\.([A-Z_][A-Z_0-9]*)\b", sql_line):
            hit = aliases.get(alias.lower())
            if hit:
                out.append((hit[0], col.upper(), alias, hit[1]))
    return out


def _split_views(ddl: str):
    """[(view_name, body)] -- a file can define MORE THAN ONE semantic view.

    sql/04_semantic_v1.sql defines both FULFILLMENT_SV and SHIPPING_SV. Analysing
    the file as a whole silently merges their logical models and produces false
    positives (SHIPPING_SV declares ZONE_RATE_CARDS columns; FULFILLMENT_SV does
    not, and its cost VQs legitimately reference a table it never declares).
    """
    marks = [
        (m.start(), m.group(1))
        for m in re.finditer(r"CREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW\s+([A-Z_0-9.]+)", ddl, re.I)
    ]
    out = []
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(ddl)
        out.append((name, ddl[pos:end]))
    return out


@pytest.mark.parametrize("filename", SEMANTIC_VIEW_FILES)
def test_verified_query_columns_are_declared_in_the_model(filename):
    path = REPO / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")

    problems = []
    missing_tables = []
    for view_name, body in _split_views(path.read_text()):
        declared = {(t.upper(), c.upper()) for t, c in _declared_logical_columns(body)}
        known_tables = {t for t, _ in declared}
        for tbl, col, alias, qualified in _vq_alias_references(body):
            if qualified:
                # Fully-qualified physical reference: bypasses the logical model,
                # so neither the table nor its columns need declaring. 06 does this
                # for FISCAL_CALENDAR_445 and is valid live.
                continue
            if tbl not in known_tables:
                # NOT a "cost trap" -- this is a hard defect. An earlier version of
                # this guard skipped here, reasoning that v1 deliberately omits
                # ZONE_RATE_CARDS so its cost queries are unanswerable. That was
                # wrong and it hid a real bug for a day: a verified query naming a
                # table the model does not declare does not merely make THAT
                # question unanswerable, it makes the ENTIRE model fail to load --
                #   "Invalid semantic model yaml. SQL compilation error:
                #    Object 'ZONE_RATE_CARDS' does not exist or not authorized."
                # Snowsight then refuses to open the view at all. If a verified
                # query needs a table, the view must declare it; make the view weak
                # with vague comments, never by omitting a referenced table.
                missing_tables.append(f"{view_name}: {alias} -> {tbl}")
                continue
            if (tbl, col) not in declared:
                problems.append(f"{view_name}: {alias}.{col} -> {tbl}.{col}")

    assert not missing_tables, (
        f"{filename}: verified queries reference TABLES not declared in their "
        f"view's logical model, which makes the whole model fail to load "
        f"('Object ... does not exist or not authorized'): "
        f"{sorted(set(missing_tables))}"
    )
    assert not problems, (
        f"{filename}: verified queries reference columns NOT declared as "
        f"dimensions/facts/metrics on their logical table, which invalidates the "
        f"whole semantic model ('invalid identifier'): {sorted(set(problems))}"
    )


def test_no_comment_delimiters_inside_sql_string_literals():
    """`--` and `;` inside a single-quoted SQL literal break the repo's tooling.

    Snowflake itself parses these correctly, so a deploy succeeds and the bug is
    invisible live. But several checks here (and any naive statement splitter)
    strip `--` to end-of-line and split on `;`:

      comment='... definition -- do NOT use X.'   ->  eats the closing quote,
                                                     reports unbalanced quotes
      comment='... definition; do NOT use X.'     ->  splits one CREATE into two
                                                      statements

    Both were introduced and caught on 2026-08-14 while adding SHIP_BY_DATE to
    the fulfillment views. Write prose with periods instead.
    """
    offenders = []
    for path in _sql_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            # Examine only the inside of single-quoted literals on this line.
            for literal in re.findall(r"'((?:[^']|'')*)'", line):
                if "--" in literal or ";" in literal:
                    offenders.append(f"{path.name}:{lineno}")
                    break
    assert not offenders, (
        "SQL string literals must not contain '--' or ';' -- they break comment "
        f"stripping and statement splitting: {sorted(set(offenders))}"
    )


class TestTeardownScript:
    """Static guards on AGENT_EVAL_DEMO_TEARDOWN.sql.

    These exist because of a real failure. The script's own residue check was
    written as `SELECT COUNT(*) FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))`, which
    is a query and therefore needs an active warehouse -- but by the time it runs,
    the script has already dropped AGENT_EVAL_DEMO_WH. On an account whose
    session had no other warehouse, teardown ended with:

        000606 (57P03): No active warehouse selected in the current session.

    Every DROP had succeeded. Only the self-check failed. That is the worst shape
    a teardown script can take: the destruction worked, but the operator is told
    it errored, so they cannot tell whether compute pools are still billing.

    It also hid on the account where it was developed, because that connection had
    a different default warehouse. One cloud passed, the other failed, same file.
    """

    TEARDOWN = SQL / "AGENT_EVAL_DEMO_TEARDOWN.sql"

    def _executable_sql(self) -> str:
        """Upper-cased script with comment lines removed.

        Every check in this class must run against executable statements only.
        The script's comments quote SQL verbatim to explain past bugs, so any
        naive substring scan of the raw file matches prose.
        """
        raw = self.TEARDOWN.read_text().upper()
        # Strip /* ... */ blocks FIRST -- the file header is one, and it quotes
        # "DROP COMPUTE POOL" while explaining the ordering constraint. Stripping
        # only "--" lines leaves that text in place and the ordering check then
        # compares against a comment. Both comment forms must go.
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        return "\n".join(
            ln for ln in raw.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        )

    def test_teardown_exists(self):
        assert self.TEARDOWN.exists(), f"missing {self.TEARDOWN}"

    def test_verification_needs_no_warehouse(self):
        """No RESULT_SCAN / SELECT-based residue check after the warehouse drop.

        SHOW is metadata-only and needs no compute. Anything that runs as a query
        cannot be relied on here.
        """
        body = self.TEARDOWN.read_text()
        after_wh_drop = body[body.index("DROP WAREHOUSE"):]
        offenders = [
            kw for kw in ("RESULT_SCAN", "LAST_QUERY_ID")
            if re.search(rf"^\s*[^-\s].*{kw}", after_wh_drop, re.M | re.I)
        ]
        assert not offenders, (
            f"{offenders} appears in an executable statement after DROP WAREHOUSE. "
            "That needs an active warehouse, which this script just dropped, so "
            "teardown will report a false failure. Use bare SHOW statements."
        )

    def test_drops_all_six_objects(self):
        """A missed DROP is a silent billing leak, not a cosmetic gap."""
        body = self._executable_sql()
        required = [
            "DROP DATABASE IF EXISTS AGENT_EVAL_DEMO",
            "DROP WAREHOUSE IF EXISTS AGENT_EVAL_DEMO_WH",
            "DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_NB_POOL",
            "DROP COMPUTE POOL IF EXISTS AGENT_EVAL_DEMO_APP_POOL",
            "DROP ROLE IF EXISTS TENANT_ALDERWOOD",
            "DROP ROLE IF EXISTS TENANT_BELLWEATHER",
        ]
        missing = [r for r in required if r not in body]
        assert not missing, (
            f"teardown no longer drops: {missing}. Compute pools and account-level "
            "roles survive DROP DATABASE and keep costing money."
        )

    def test_database_dropped_before_compute_pools(self):
        """DROP COMPUTE POOL fails while a service still runs on it.

        The Streamlit app lives in the database, so the database must go first.
        Reordering these would break teardown only on accounts that actually
        deployed the app -- i.e. it would pass in CI and fail in the field.
        """
        # Comments must be stripped first. The header docstring explains this very
        # constraint in prose, so a naive scan of the whole file finds "DROP
        # COMPUTE POOL" in the comment BEFORE the real DROP DATABASE statement and
        # fails on a correct script. This test caught exactly that on itself.
        body = self._executable_sql()
        assert body.index("DROP DATABASE IF EXISTS AGENT_EVAL_DEMO") < body.index(
            "DROP COMPUTE POOL"
        ), "DROP DATABASE must precede DROP COMPUTE POOL"

    def test_verifies_every_dropped_object(self):
        """Each of the five object kinds gets a real-time SHOW."""
        body = self._executable_sql()
        for probe in (
            "SHOW DATABASES LIKE 'AGENT_EVAL_DEMO'",
            "SHOW WAREHOUSES LIKE 'AGENT_EVAL_DEMO_WH'",
            "SHOW COMPUTE POOLS LIKE 'AGENT_EVAL_DEMO_%_POOL'",
            "SHOW ROLES LIKE 'TENANT_ALDERWOOD'",
            "SHOW ROLES LIKE 'TENANT_BELLWEATHER'",
        ):
            assert probe in body, f"teardown no longer verifies: {probe}"

    def test_does_not_use_account_usage_for_verification(self):
        """ACCOUNT_USAGE lags hours and would report dropped objects as present."""
        assert "ACCOUNT_USAGE" not in self._executable_sql(), (
            "ACCOUNT_USAGE lags by up to a few hours; immediately after a teardown "
            "it still lists the dropped objects, producing a false failure."
        )
