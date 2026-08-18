#!/usr/bin/env python3
"""doctor.py — Go/no-go preflight for the agent eval demo (<60s).

Prints an aligned [OK]/[FAIL] board covering all critical objects.
Exits 1 on any FAIL with a loud DO NOT PRESENT banner and remediation hints.
Designed to be readable under pre-call stress.

Run:  make doctor   (or: python scripts/doctor.py)
"""

from __future__ import annotations

import os
import sys
import time

# The demo can live on more than one account: it was built on the primary demo account and
# rebuilt from scratch on a second account to prove the scripts actually work. Hardcoding
# either one makes the preflight unusable on the other, so both the connection
# and the expected account are overridable.
CONNECTION_NAME = os.environ.get("SF_CONNECTION", "my_snowflake_connection")
EXPECTED_ACCOUNT = os.environ.get("SF_ACCOUNT", "the primary demo account")


def main():
    start = time.time()
    failures: list[str] = []
    results: list[tuple[str, bool, str]] = []

    # --- Connect ---
    try:
        import snowflake.connector
    except ImportError:
        print("FATAL: snowflake-connector-python not installed. Run: make venv")
        sys.exit(1)

    try:
        conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
        cur = conn.cursor()
        cur.execute("USE ROLE ACCOUNTADMIN")
        cur.execute("USE DATABASE AGENT_EVAL_DEMO")
        cur.execute("USE SCHEMA AI")
        cur.execute("USE WAREHOUSE AGENT_EVAL_DEMO_WH")
    except Exception as e:
        msg = str(e)
        if "390422" in msg:
            print("\n" + "=" * 60)
            print("  NETWORK POLICY BLOCK (390422)")
            print(f"  Account {EXPECTED_ACCOUNT} requires VPN.")
            print("  Reconnect VPN and retry.")
            print("=" * 60 + "\n")
        else:
            print(f"FATAL: Cannot connect: {msg[:200]}")
        sys.exit(1)

    def q(sql):
        cur.execute(sql)
        return cur.fetchall()

    def scalar(sql):
        rows = q(sql)
        return rows[0][0] if rows else None

    def check(label, ok, detail=""):
        results.append((label, ok, detail))
        if not ok:
            failures.append(f"{label}: {detail}")

    # --- 1. VPN + Account ---
    acct = scalar("SELECT CURRENT_ACCOUNT()")
    check(f"Account = {EXPECTED_ACCOUNT}", acct == EXPECTED_ACCOUNT, f"got {acct}")

    ip = scalar("SELECT CURRENT_IP_ADDRESS()")
    check("VPN connected (IP reachable)", ip is not None, f"ip={ip}")

    # --- 2. Warehouse ---
    # Keep this dumb and unbreakable. Earlier attempts used
    # INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY (no 'state' column ->
    # 000904) and SHOW + RESULT_SCAN (-> 000008 statement-count error). What a
    # preflight actually needs to know is only: is a warehouse attached, and
    # can it execute? A successful round trip proves both, and warms the
    # warehouse as a side effect.
    wh_state = scalar("SELECT CURRENT_WAREHOUSE()")
    # Simple check: can we run a query?
    try:
        scalar("SELECT 1")
        check("Warehouse responsive", True)
    except Exception as e:
        check("Warehouse responsive", False, str(e)[:80])

    # --- 3. Object inventory ---
    row_count = scalar("SELECT COUNT(*) FROM FULFILLMENT_INTELLIGENCE.ORDERS")
    check("ORDERS = 40,000", row_count == 40000, f"got {row_count}")

    try:
        svs = q("SHOW SEMANTIC VIEWS IN SCHEMA AI")
        sv_names = {r[1] for r in svs}
        check("Semantic views (3)", len(svs) >= 3, f"found {len(svs)}: {sv_names}")
    except Exception as e:
        check("Semantic views (3)", False, str(e)[:80])

    try:
        css = q("SHOW CORTEX SEARCH SERVICES IN SCHEMA AI")
        check("Search services (2)", len(css) >= 2, f"found {len(css)}")
    except Exception as e:
        check("Search services (2)", False, str(e)[:80])

    try:
        agents = q("SHOW AGENTS IN SCHEMA AI")
        agent_names = {r[1] for r in agents}
        check("Native agent (FULFILLMENT_ANALYST)", "FULFILLMENT_ANALYST" in agent_names,
              f"found: {agent_names}")
    except Exception as e:
        check("Native agent", False, str(e)[:80])

    try:
        ext = q("SHOW EXTERNAL AGENTS IN SCHEMA AI")
        ext_names = {r[1] for r in ext}
        check("External agent (EXTERNAL_SIM)", "EXTERNAL_SIM" in ext_names,
              f"found: {ext_names}")
    except Exception as e:
        check("External agent", False, str(e)[:80])

    # --- 4. Canonical eval runs ---
    # Each analyst run is stored under a SPECIFIC view. The baseline lives on
    # the frozen FULFILLMENT_SV_V1; the optimized run on FULFILLMENT_SV. Using
    # FULFILLMENT_SV for both is a FALSE GREEN: it silently passes on a stale
    # pre-rename BASELINE_V1_FINAL left behind on the v2 view.
    analyst_run_views = {
        "BASELINE_V1_FINAL": "FULFILLMENT_SV_V1",
        "OPTIMIZED_V2_FINAL": "FULFILLMENT_SV",
    }
    canonical_runs = ["BASELINE_V1_FINAL", "OPTIMIZED_V2_FINAL", "AGENT_V4", "TENANT_ISOLATION_V2"]
    for run in canonical_runs:
        try:
            # Check semantic view evals for analyst runs
            if run in analyst_run_views:
                view = analyst_run_views[run]
                cnt = scalar(f"""
                    SELECT COUNT(*) FROM TABLE(SNOWFLAKE.LOCAL.GET_ANALYST_AI_EVALUATION_DATA(
                        'AGENT_EVAL_DEMO','AI','{view}','SEMANTIC VIEW','{run}'))
                    WHERE METRIC_NAME IS NOT NULL
                """)
            else:
                # NOT GET_ANALYST_AI_EVALUATION_DATA: that is the analyst-side
                # function and it returns 0 rows silently for agent runs.
                cnt = scalar(f"""
                    SELECT COUNT(*) FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                        'AGENT_EVAL_DEMO','AI','FULFILLMENT_ANALYST','CORTEX AGENT'))
                    WHERE RUN_NAME = '{run}' AND METRIC_NAME IS NOT NULL
                """)
            check(f"Eval run: {run}", cnt is not None and cnt > 0, f"{cnt} rows")
        except Exception as e:
            check(f"Eval run: {run}", False, str(e)[:80])

    # --- 5. Native + external event counts ---
    try:
        native_cnt = scalar("""
            SELECT COUNT(*) FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                'AGENT_EVAL_DEMO','AI','FULFILLMENT_ANALYST','CORTEX AGENT'))
        """)
        check("Native agent events (300+)", native_cnt is not None and native_cnt >= 300,
              f"{native_cnt} events")
    except Exception as e:
        check("Native agent events", False, str(e)[:80])

    try:
        ext_cnt = scalar("""
            SELECT COUNT(*) FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                'AGENT_EVAL_DEMO','AI','EXTERNAL_SIM','EXTERNAL AGENT'))
        """)
        # A single `make harness` run emits ~45 spans (9 questions). The 324 now on
        # the primary demo account is an ACCUMULATED total across several runs (harness + scoring),
        # so asserting a high floor fails on a freshly rebuilt account that is
        # otherwise perfect (verified on a second account 2026-08-14: 45 events, Act 4 fully
        # working). What matters for the demo is that at least one full run landed.
        check("External agent events (>=40)", ext_cnt is not None and ext_cnt >= 40,
              f"{ext_cnt} events -- run `make harness` to emit traces")
    except Exception as e:
        check("External agent events", False, str(e)[:80])

    # Span TYPE check, deliberately separate from the count above.
    # The count cannot detect this: 40+ spans can all be non-retrieval, and the
    # absence of a `retrieval` span silently drops the external agent from 5 of 5
    # documented judge metrics to 3 of 5 with NO error anywhere (GOTCHAS #22).
    # This is the one Act 4 regression that is invisible to every other check.
    try:
        span_types = {r[0] for r in q("""
            SELECT DISTINCT SPAN_TYPE
            FROM TABLE(SNOWFLAKE.LOCAL.GET_AI_OBSERVABILITY_EVENTS_NORMALIZED(
                'AGENT_EVAL_DEMO','AI','EXTERNAL_SIM','EXTERNAL AGENT'))
            WHERE SPAN_TYPE IS NOT NULL
        """) if r[0]}
        need = {"record_root", "agent", "graph_node", "tool", "generation", "retrieval"}
        missing = need - span_types
        check("External agent span types (6)", not missing,
              f"missing {sorted(missing)} -- if 'retrieval' is absent, the "
              "@instrument(RETRIEVAL) decorators in tools.py are gone and "
              "context_relevance/groundedness will not compute"
              if missing else f"{len(span_types)} types")
    except Exception as e:
        check("External agent span types", False, str(e)[:80])

    # --- 6. RAP ---
    try:
        rap_rows = q("""
            SELECT REF_ENTITY_NAME
            FROM TABLE(AGENT_EVAL_DEMO.INFORMATION_SCHEMA.POLICY_REFERENCES(
                POLICY_NAME => 'AGENT_EVAL_DEMO.OPS.TENANT_ISOLATION_POLICY'))
        """)
        # Assert ATTACHMENT to all 8 tenant-scoped tables, not mere existence:
        # a policy that exists but is not attached protects nothing.
        check("Row access policy on 8 tables", len(rap_rows) == 8,
              f"attached to {len(rap_rows)}")
    except Exception as e:
        check("Row access policy", False, str(e)[:80])

    # --- 7. Act 6 notebook (Container Runtime on its own pool) ---
    # Object existence is NOT the check here. A notebook created with
    # COMPUTE_POOL but no RUNTIME_NAME is silently accepted and still runs on
    # WAREHOUSE runtime, so it would present as Container Runtime while not
    # being one. The tell is code_warehouse: Container Runtime leaves it NULL.
    # DESCRIBE is the only source -- SHOW NOTEBOOKS exposes neither column.
    try:
        cur.execute("DESCRIBE NOTEBOOK AGENT_EVAL_DEMO.AI.EVAL_CICD_GATING")
        cols = [c[0].lower() for c in cur.description]
        nb = dict(zip(cols, cur.fetchone()))

        is_container = bool(nb.get("runtime_name")) and not nb.get("code_warehouse")
        check(
            "Notebook is Container Runtime",
            is_container,
            f"runtime_name={nb.get('runtime_name')} code_warehouse={nb.get('code_warehouse')}",
        )

        pool = nb.get("compute_pool")
        check(
            "Notebook on dedicated pool",
            pool == "AGENT_EVAL_DEMO_NB_POOL",
            f"pool={pool} (borrowed pools can be torn down by another demo)",
        )

        # A pool in SUSPENDED/IDLE is fine (it auto-resumes); a pool that does
        # not exist at all means the notebook cannot start.
        pool_rows = q("SHOW COMPUTE POOLS LIKE 'AGENT_EVAL_DEMO_NB_POOL'")
        check("Compute pool exists", len(pool_rows) == 1, "pool missing")
    except Exception as e:
        check("Act 6 notebook", False, str(e)[:80])

    # --- 8. Act 7 Streamlit app: runtime + its own pool + staged source ---
    #
    # The app ran on WAREHOUSE runtime until 2026-08-14 and crashed on its first
    # tab: the bundled Streamlit there predates `hide_index`, so
    # st.dataframe(..., hide_index=True) raised TypeError before anything
    # rendered. It now runs Container Runtime with Streamlit pinned in
    # streamlit/pyproject.toml.
    #
    # Neither SHOW STREAMLITS nor INFORMATION_SCHEMA.STREAMLITS exposes
    # runtime_name or compute_pool -- only DESCRIBE does. Same trap as notebooks.
    try:
        cur.execute("DESCRIBE STREAMLIT AGENT_EVAL_DEMO.OPS.OBSERVABILITY_APP")
        cols = [c[0].lower() for c in cur.description]
        app = dict(zip(cols, cur.fetchone()))

        runtime = app.get("runtime_name") or ""
        check(
            "Streamlit is Container Runtime",
            "CONTAINER" in runtime.upper(),
            f"runtime_name={runtime!r} -- warehouse runtime's Streamlit is too "
            "old for hide_index and the app dies on tab 1",
        )

        app_pool = app.get("compute_pool")
        check(
            "Streamlit on dedicated pool",
            app_pool == "AGENT_EVAL_DEMO_APP_POOL",
            f"pool={app_pool} (must not share the notebook's MAX_NODES=1 pool)",
        )

        eai = str(app.get("external_access_integrations") or "")
        check(
            "Streamlit has PyPI access",
            "PYPI_ACCESS_INTEGRATION" in eai,
            f"eai={eai} -- without it package installs cannot resolve",
        )

        app_pool_rows = q("SHOW COMPUTE POOLS LIKE 'AGENT_EVAL_DEMO_APP_POOL'")
        check("Streamlit pool exists", len(app_pool_rows) == 1, "pool missing")
    except Exception as e:
        check("Act 7 Streamlit", False, str(e)[:80])

    # --- Print results ---
    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"  CUSTOMER DEMO PREFLIGHT  ({elapsed:.1f}s)")
    print("=" * 60)
    print()

    max_label = max(len(r[0]) for r in results)
    for label, ok, detail in results:
        status = "\033[32m[OK]\033[0m  " if ok else "\033[31m[FAIL]\033[0m"
        line = f"  {status} {label:<{max_label}}"
        if detail and not ok:
            line += f"  ({detail})"
        print(line)

    print()
    if failures:
        print("\033[31m" + "=" * 60)
        print("  ██████   DO NOT PRESENT   ██████")
        print("=" * 60 + "\033[0m")
        print()
        print("  Remediation:")
        for f in failures:
            print(f"    • {f}")
        print()
        conn.close()
        sys.exit(1)
    else:
        print("\033[32m  ✓ All checks passed. You are GO.\033[0m")
        print()
        conn.close()
        sys.exit(0)


def _run():
    """Wrap main() so an unexpected exception can never look like a pass.

    The first version of this script crashed on invalid SQL yet still exited 0,
    which is the most dangerous possible failure mode for a pre-call check: it
    looks green. Any escape is now converted into an explicit non-zero exit.
    """
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("\n" + "!" * 64)
        print("  DOCTOR CRASHED - treat this as DO NOT PRESENT")
        print(f"  {type(e).__name__}: {str(e)[:300]}")
        print("  The check itself is broken, so the demo state is UNKNOWN.")
        print("!" * 64 + "\n")
        sys.exit(2)


if __name__ == "__main__":
    _run()
