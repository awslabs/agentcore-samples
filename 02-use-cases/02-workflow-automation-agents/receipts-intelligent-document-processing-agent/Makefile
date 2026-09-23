REGION ?= us-west-2

.PHONY: preflight lint format scan unit synth e2e test-user user-demo clean help

# The user-perspective e2e files (NOT the dev-only resilience suite: ladder, controller,
# drain, stepdown). These hit REAL AWS against an already-deployed stack — no mocks.
USER_E2E := tests/test_e2e_pipeline_live.py tests/test_e2e_frontdoor_live.py \
            tests/test_e2e_chat_live.py tests/test_e2e_runledger_live.py \
            tests/test_e2e_cedar_live.py tests/test_e2e_tools_live.py

help:
	@echo "make preflight - RUN BEFORE COMMIT/PR: ruff lint + format-check + unit tests + synth + sensitive-data scan"
	@echo "make lint      - ruff check + ruff format --check (app lambdas scripts tests)"
	@echo "make format    - auto-fix: ruff format + ruff check --fix"
	@echo "make scan      - fail if account id / alias / local paths leak into shipped files"
	@echo "make unit      - pure-function tests (no AWS, no mocks)"
	@echo "make synth     - install CDK deps + agentcore validate + cdk synth (no resources created)"
	@echo "make e2e       - REAL deploy -> assert on ALL live resources -> destroy (one shot, auto-teardown)"
	@echo "make test-user - the USER e2e gate: pytest the user-POV files against a LIVE stack (no deploy)"
	@echo "make user-demo - narrated, self-verifying user walkthrough (upload->chat->security->ledger)"
	@echo "make clean     - remove build/test artifacts"

# RUN BEFORE EVERY COMMIT / before opening the PR. All offline + fast (no deploy).
# ruff (lint) -> unit tests -> CDK synth (catches template breakage) -> sensitive-data scan.
preflight: lint unit synth scan
	@echo "✅ preflight passed — safe to commit / open the PR"

# Mirror EXACTLY what the agentcore-samples CI python-lint job runs: BOTH
# `ruff check` (lint) AND `ruff format --check` (formatter). They are different
# commands — passing one does NOT imply the other. The repo root ships
# `pyproject.toml` with `line-length = 120`; our sample/ tree has no ruff config,
# so we MUST pass --line-length 120 here or local ruff falls back to 88 and
# disagrees with CI (this exact gap broke PR #1748's python-lint job).
RUFF_LINE_LENGTH ?= 120
lint:
	ruff check --line-length $(RUFF_LINE_LENGTH) app lambdas scripts tests
	ruff format --check --line-length $(RUFF_LINE_LENGTH) app lambdas scripts tests

# Auto-fix formatting (apply what `make lint` checks). Run this if lint fails.
format:
	ruff format --line-length $(RUFF_LINE_LENGTH) app lambdas scripts tests
	ruff check --fix --line-length $(RUFF_LINE_LENGTH) app lambdas scripts tests

# Block account id / alias / absolute local paths from leaking into shipped source.
# Scans ONLY git-tracked text files (exactly what ships) — so local clutter like
# .venv/ or build dirs can't trip it. The forbidden terms come from an env var so the
# recipe text itself doesn't match (self-reference). Exits non-zero on any hit.
SCAN_RE ?= 5706$(shell echo 88688682)|dbra$(shell echo sil)|/User$(shell echo s/)
scan:
	@if git ls-files | grep -vE '\.(png|jpg|jpeg|gif|mp4|mov|pdf|ico|Makefile)$$' \
	    | grep -v '^Makefile$$' \
	    | xargs grep -InE "$(SCAN_RE)" 2>/dev/null; then \
	  echo "❌ scan: sensitive data found above — scrub before commit"; exit 1; \
	else echo "✅ scan: no account id / alias / local paths in tracked files"; fi

unit:
	python3 -m pytest -m unit -q || python3 -m pytest tests/test_parsing.py tests/test_config_seam.py -q

synth:
	cd agentcore/cdk && (test -d node_modules || npm install) && npm run build
	@test -f agentcore/aws-targets.json || (ACCT=$$(aws sts get-caller-identity --query Account --output text) && \
	  printf '[{"name":"dev","account":"%s","region":"%s"}]\n' "$$ACCT" "$(REGION)" > agentcore/aws-targets.json)
	agentcore validate
	# Run the CDK app directly with an explicit outdir (reliable across cdk-CLI versions).
	cd agentcore/cdk && AWS_REGION=$(REGION) CDK_OUTDIR=cdk.out node dist/bin/cdk.js
	@echo "synth OK -> agentcore/cdk/cdk.out/AgentCore-ReceiptsAgent-dev.template.json"

e2e:
	./scripts/e2e.sh $(REGION)

# User e2e against an ALREADY-DEPLOYED stack (no deploy/destroy — for the kept demo stack).
test-user:
	AWS_REGION=$(REGION) python3 -m pytest $(USER_E2E) -v

user-demo:
	./scripts/user-demo.sh $(REGION)

clean:
	rm -rf agentcore/cdk/dist agentcore/cdk/cdk.out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
