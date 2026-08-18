PY      := .venv-test/bin/python
PYTEST  := .venv-test/bin/pytest
HARNESS_PY := .venv-harness/bin/python

.DEFAULT_GOAL := help

.PHONY: help venv doctor test test-offline claims repro data governance search semantic evals agents harness clean-venv teardown

help:
	@echo "Snowflake Agent Evaluation Harness"
	@echo ""
	@echo "  make doctor        Go/no-go preflight on the target account (<60s)"
	@echo "  make test          Full suite against the live account"
	@echo "  make test-offline  Offline tests only, no Snowflake or VPN (CI-safe)"
	@echo ""
	@echo "  make claims        Claim audit: documented numbers vs live evidence"
	@echo "  make repro         Committed .sql reproduces the live objects"
	@echo "  make data          Row counts + the six ambiguity traps"
	@echo "  make governance    Row access policy + tenant isolation"
	@echo "  make search        Cortex Search services serving"
	@echo "  make semantic      Semantic view structure and v1/v2 parity"
	@echo "  make evals         Persisted evaluation runs and scores"
	@echo "  make agents        Native + external agent traces and GPA metrics"
	@echo ""
	@echo "  make venv          Create/refresh the test virtualenv"
	@echo "  make harness       Re-run the TruLens external-orchestrator simulator (emits traces)"
	@echo "  make teardown      DESTRUCTIVE: drop all demo objects"
	@echo ""
	@echo "NOTE: every target except test-offline needs a live Snowflake"
	@echo "      connection. Set SF_CONNECTION to a name in connections.toml."

venv:
	python3 -m venv .venv-test
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q pytest snowflake-connector-python pandas "pyarrow<24" pyyaml
	@echo "test venv ready"

# The one command to run before demoing.
doctor:
	@$(PY) scripts/doctor.py

test:
	$(PYTEST)

test-offline:
	$(PYTEST) -m offline

claims:
	$(PYTEST) -m claims
repro:
	$(PYTEST) -m repro
data:
	$(PYTEST) -m data
governance:
	$(PYTEST) -m governance
search:
	$(PYTEST) -m search
semantic:
	$(PYTEST) -m semantic
evals:
	$(PYTEST) -m evals
agents:
	$(PYTEST) -m agents

# Regenerates external-agent traces. Needs .venv-harness (Python 3.9-3.11).
harness:
	cd . && TRULENS_OTEL_TRACING=1 $(HARNESS_PY) -m python.external_sim.run

# SCORES the external agent (Act 4 Step 7). Traces alone cannot show quality.
# Defaults to a timestamped run name because runs are immutable and a metric
# cannot be recomputed for an existing run. Pass RUN=<name> to override.
# Requires sql/08b_harness_scoring.sql to have been run once (ground truth).
score:
	cd . && TRULENS_OTEL_TRACING=1 $(HARNESS_PY) -m python.external_sim.score \
	  $(if $(RUN),--run-name $(RUN),)

# Override to target a different account:
#   make teardown SF_CONNECTION=my_second_account
SF_CONNECTION ?= my_snowflake_connection

teardown:
	@echo "This DROPS AGENT_EVAL_DEMO, the warehouse, the 2 tenant roles, and BOTH compute pools."
	@read -p "Type DESTROY to confirm: " ans; \
	  if [ "$$ans" = "DESTROY" ]; then \
	    snow sql -c $(SF_CONNECTION) -f sql/AGENT_EVAL_DEMO_TEARDOWN.sql; \
	  else echo "aborted"; fi

clean-venv:
	rm -rf .venv-test
