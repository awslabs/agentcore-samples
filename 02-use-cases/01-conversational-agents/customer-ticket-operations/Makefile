.PHONY: test lint preflight

# The agent's own pins (strands, mcp) are needed to import it under test.
test:
	uv run --extra aws --with-requirements src/ticket_agent/requirements.txt python -m pytest

lint:
	uv run --with 'ruff==0.16.8' ruff check src tests scripts functions && uv run --with 'ruff==0.16.8' ruff format --check src tests scripts functions
	sh -n infra/codebuild/build.sh
	for r in $(ROOTS); do (cd $$r && terraform fmt -check -diff >/dev/null) || { echo "terraform fmt: $$r"; exit 1; }; done

preflight: test lint

# ══════════════════════════════════════════════════════════════════════════
# AWS deployment (Terraform roots in infra/iac, remote builds in CodeBuild)
#   make whoami     PROFILE=<aws-profile>
#   make deploy-all PROFILE=<aws-profile>
# ══════════════════════════════════════════════════════════════════════════
PROJECT  ?= ticketops
ENV      ?= demo
REGION   ?= us-east-1
PROFILE  ?=

ifneq ($(REGION),us-east-1)
$(error This sample deploys to us-east-1 only)
endif

# The named profile is the only credential source: static keys in the shell would override it.
export AWS_PROFILE        := $(PROFILE)
export AWS_REGION         := $(REGION)
export AWS_DEFAULT_REGION := $(REGION)
unexport AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

# The target account comes from the AWS profile.
ACCOUNT := $(shell aws sts get-caller-identity --profile $(PROFILE) --query Account --output text 2>/dev/null)
export TF_VAR_account_id := $(ACCOUNT)
BACKEND_ARGS = -backend-config="$(TFBACKEND)" \
  -backend-config="bucket=$(PROJECT)-$(ENV)-tf-state-$(ACCOUNT)-$(REGION)" \
  -backend-config="kms_key_id=alias/$(PROJECT)-$(ENV)-tf-state-key"

TFVARS    := $(CURDIR)/infra/iac/envs/$(ENV).tfvars
AUTO_APPROVE ?= -auto-approve
TFBACKEND := $(CURDIR)/infra/iac/envs/$(ENV).tfbackend

# ── Terraform wrappers ────────────────────────────────────────────────────
define tf_plan
	@test -n "$(PROFILE)" || { echo "set PROFILE=<aws-profile>"; exit 1; }
	@echo "══ Plan $(1) [$(ENV)] ══"
	cd $(1) && env -u AWS_BEARER_TOKEN_BEDROCK terraform init -input=false -reconfigure $(BACKEND_ARGS) \
	         && env -u AWS_BEARER_TOKEN_BEDROCK terraform plan -var-file="$(TFVARS)"
endef

define tf_apply
	@test -n "$(PROFILE)" || { echo "set PROFILE=<aws-profile>"; exit 1; }
	@echo "══ Apply $(1) [$(ENV)] ══"
	cd $(1) && env -u AWS_BEARER_TOKEN_BEDROCK terraform init -input=false -reconfigure $(BACKEND_ARGS) \
	         && env -u AWS_BEARER_TOKEN_BEDROCK terraform apply -input=false $(AUTO_APPROVE) -var-file="$(TFVARS)"
endef

define tf_destroy
	@test -n "$(PROFILE)" || { echo "set PROFILE=<aws-profile>"; exit 1; }
	@echo "══ Destroy $(1) [$(ENV)] ══"
	cd $(1) && env -u AWS_BEARER_TOKEN_BEDROCK terraform init -input=false -reconfigure $(BACKEND_ARGS) \
	         && env -u AWS_BEARER_TOKEN_BEDROCK terraform destroy -input=false $(AUTO_APPROVE) -var-file="$(TFVARS)"
endef

ROOTS := infra/iac/roots/foundation/build infra/iac/roots/foundation/observability infra/iac/roots/guardrails infra/iac/roots/identity infra/iac/roots/app infra/iac/roots/web infra/iac/roots/agent infra/iac/roots/evals

# ── Roots ─────────────────────────────────────────────────────────────────
plan-build-infra:    ; $(call tf_plan,infra/iac/roots/foundation/build)
deploy-build-infra:  ; $(call tf_apply,infra/iac/roots/foundation/build)
destroy-build-infra: ; $(call tf_destroy,infra/iac/roots/foundation/build)

# Remote build: upload the source tree (tracked + untracked, minus .gitignore'd files) and let
# CodeBuild package every Lambda, the web bundle and the agent image. Nothing is built locally.
build:
	@git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "run from a git clone: the build packages tracked and untracked files, minus ignored ones"; exit 1; }
	@set -e; BUCKET=$$(aws ssm get-parameter --name /$(PROJECT)/$(ENV)/build/artifacts_bucket --query Parameter.Value --output text); \
	PROJ=$$(aws ssm get-parameter --name /$(PROJECT)/$(ENV)/build/codebuild --query Parameter.Value --output text); \
	SRC=$$(mktemp -d)/source.zip; git ls-files -co --exclude-standard | zip -q -@ "$$SRC"; \
	aws s3 cp --only-show-errors "$$SRC" "s3://$$BUCKET/source/latest.zip"; \
	ID=$$(aws codebuild start-build --project-name "$$PROJ" --query build.id --output text); echo "CodeBuild $$ID"; \
	while :; do ST=$$(aws codebuild batch-get-builds --ids "$$ID" --query 'builds[0].buildStatus' --output text); \
	  [ "$$ST" = IN_PROGRESS ] || break; sleep 10; done; \
	aws logs tail /aws/codebuild/$$PROJ --log-stream-names "$${ID#*:}" --format short 2>/dev/null | grep -E '^.{20}(build |\[Container\] .*(FAILED|error))' || true; \
	echo "status: $$ST"; [ "$$ST" = SUCCEEDED ]

plan-observability:    ; $(call tf_plan,infra/iac/roots/foundation/observability)
deploy-observability:  ; $(call tf_apply,infra/iac/roots/foundation/observability)
destroy-observability: ; $(call tf_destroy,infra/iac/roots/foundation/observability)

plan-guardrails:    ; $(call tf_plan,infra/iac/roots/guardrails)
deploy-guardrails:  ; $(call tf_apply,infra/iac/roots/guardrails)
destroy-guardrails: ; $(call tf_destroy,infra/iac/roots/guardrails)


plan-identity:    ; $(call tf_plan,infra/iac/roots/identity)
deploy-identity:  ; $(call tf_apply,infra/iac/roots/identity)
destroy-identity: ; $(call tf_destroy,infra/iac/roots/identity)

plan-app:    ; $(call tf_plan,infra/iac/roots/app)
deploy-app:  ; $(call tf_apply,infra/iac/roots/app)
destroy-app: ; $(call tf_destroy,infra/iac/roots/app)

plan-web:    ; $(call tf_plan,infra/iac/roots/web)
deploy-web:  ; $(call tf_apply,infra/iac/roots/web)
destroy-web: ; $(call tf_destroy,infra/iac/roots/web)

plan-agent:    ; $(call tf_plan,infra/iac/roots/agent)
deploy-agent:  ; $(call tf_apply,infra/iac/roots/agent)
destroy-agent: ; $(call tf_destroy,infra/iac/roots/agent)

plan-evals:    ; $(call tf_plan,infra/iac/roots/evals)
deploy-evals:  ; $(call tf_apply,infra/iac/roots/evals)
destroy-evals: ; $(call tf_destroy,infra/iac/roots/evals)

# V5 release gate: run evals/dataset.json through one build on AWS and score it with AgentCore
# Evaluations (10 to 15 minutes; resets the demo). Exit code 0 only if the gate passes.
BUILD ?= v5
eval: check-profile
	uv run --extra aws python scripts/eval_gate.py --build $(BUILD) --project $(PROJECT) --env $(ENV) --region $(REGION)
# Every build in turn, for the console's V0-V5 matrix (60 to 90 minutes). Gate failures are expected
# for the early builds, so this target reports them without stopping.
eval-all: check-profile
	@for b in v0 v1 v2 v3 v4 v5; do uv run --extra aws python scripts/eval_gate.py --build $$b --project $(PROJECT) --env $(ENV) --region $(REGION) || true; done

# Separate variance study, not the release gate or the console's per-build matrix.
eval-k10: check-profile
	uv run --extra aws python scripts/eval_gate.py --build $(BUILD) --dataset k10 --project $(PROJECT) --env $(ENV) --region $(REGION)

# Release a demo that a failed run left locked ("Recovery required" in the console), then press
# Reset demo. A lock is released once its run's time is up; FORCE=1 releases it now.
FORCE ?=
recover: check-profile
	uv run --extra aws python scripts/recover.py --project $(PROJECT) --env $(ENV) --region $(REGION) $(if $(filter 1,$(FORCE)),--force)

# ── Composite targets ──────────────────────────────────────────────────────
deploy-all:  whoami ensure-backend deploy-build-infra build deploy-observability deploy-guardrails deploy-identity deploy-app deploy-web deploy-agent deploy-evals
	@echo; echo "Demo URL: $$(aws ssm get-parameter --name /$(PROJECT)/$(ENV)/web/url --query Parameter.Value --output text)"; $(MAKE) -s users
destroy-all: check-profile destroy-evals destroy-agent destroy-web destroy-app destroy-identity destroy-guardrails destroy-observability destroy-build-infra

# Persona passwords live only in Secrets Manager.
users: check-profile
	@for u in presenter alice carol bob; do echo "  $$u: aws secretsmanager get-secret-value --secret-id $(PROJECT)/$(ENV)/personas/$$u --query SecretString --output text --profile $(PROFILE) --region $(REGION)"; done

# Show the demo URL and every persona's sign-in in a colored table (prints passwords to your terminal).
creds: check-profile
	@URL=$$(aws ssm get-parameter --name /$(PROJECT)/$(ENV)/web/url --query Parameter.Value --output text 2>/dev/null || echo "not deployed"); \
	for u in presenter alice carol bob; do aws secretsmanager get-secret-value --secret-id $(PROJECT)/$(ENV)/personas/$$u --query SecretString --output text; done \
	| URL="$$URL" uv run python -c 'import json,os,sys; \
	rows=[json.loads(l) for l in sys.stdin if l.strip()]; \
	B,D,R,O,P,G,C="\033[1m","\033[2m","\033[0m","\033[38;2;255;98;0m","\033[38;2;255;77;139m","\033[38;2;110;231;80m","\033[38;2;104;173;255m"; \
	col={"operator":G,"customer":P,"presenter":O}; where={"operator":"/  (console)","customer":"/portal/","presenter":"/  (one sign-in, switch in ⚙)"}; \
	h=("user","password","role","customer","sign in at"); \
	cells=[(r["username"],r["password"],r.get("role",""),r.get("customer_id",""),where.get(r.get("role"),"")) for r in rows]; \
	w=[max(len(x) for x in c) for c in zip(h,*cells)]; \
	line=lambda l,m,r_: D+l+m.join("─"*(n+2) for n in w)+r_+R; \
	fmt=lambda vals,cs: D+"│"+R+(D+"│"+R).join(" "+c+v.ljust(n)+R+" " for v,n,c in zip(vals,w,cs))+D+"│"+R; \
	print("\n  "+B+"Demo"+R+"  "+C+os.environ["URL"]+R+"\n"); \
	print("  "+line("┌","┬","┐")); print("  "+fmt(h,[B]*5)); print("  "+line("├","┼","┤")); \
	[print("  "+fmt(c,[B+O,"",col.get(c[2],""),"",D])) for c in cells]; \
	print("  "+line("└","┴","┘")+"\n")'

validate-infra:
	@for r in $(ROOTS); do echo "══ validate $$r"; \
	  (cd $$r && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; done
	terraform fmt -recursive -check infra/iac

# check-profile is silent (read-only targets); whoami also says where a deploy is going.
check-profile:
	@test -n "$(PROFILE)" || { echo "set PROFILE=<aws-profile>"; exit 1; }
	@test -n "$(ACCOUNT)" || { echo "AWS profile $(PROFILE) has no working credentials"; exit 1; }

whoami: check-profile
	@echo "deploying to account $(ACCOUNT) ($(PROFILE), $(REGION))"

# Create the state backend on first use; a no-op once the stack exists.
ensure-backend:
	@aws cloudformation describe-stacks --stack-name $(PROJECT)-$(ENV)-tf-backend --region $(REGION) >/dev/null 2>&1 \
	  && echo "state backend $(PROJECT)-$(ENV)-tf-backend exists" || $(MAKE) -s bootstrap

# ── Bootstrap (one-time per environment) ──────────────────────────────────
bootstrap: whoami
	aws cloudformation deploy \
	  --template-file infra/iac/bootstrap/tf-backend.yml \
	  --stack-name $(PROJECT)-$(ENV)-tf-backend \
	  --parameter-overrides ProjectName=$(PROJECT) Environment=$(ENV) \
	  --capabilities CAPABILITY_NAMED_IAM \
	  --region $(REGION)
	aws cloudformation update-termination-protection \
	  --enable-termination-protection \
	  --stack-name $(PROJECT)-$(ENV)-tf-backend \
	  --region $(REGION)
	aws cloudformation describe-stacks \
	  --stack-name $(PROJECT)-$(ENV)-tf-backend \
	  --query 'Stacks[0].Outputs' \
	  --output table \
	  --region $(REGION)

# Teardown: disable termination protection, then delete stack.
# S3 bucket is retained (DeletionPolicy: Retain) even after stack deletion.
bootstrap-destroy: check-profile
	aws cloudformation update-termination-protection \
	  --no-enable-termination-protection \
	  --stack-name $(PROJECT)-$(ENV)-tf-backend \
	  --region $(REGION)
	aws cloudformation delete-stack \
	  --stack-name $(PROJECT)-$(ENV)-tf-backend \
	  --region $(REGION)

.PHONY: whoami check-profile ensure-backend build plan-build-infra deploy-build-infra destroy-build-infra bootstrap bootstrap-destroy validate-infra deploy-all destroy-all users creds \
        plan-identity deploy-identity destroy-identity plan-app deploy-app destroy-app \
        plan-agent deploy-agent destroy-agent plan-evals deploy-evals destroy-evals eval eval-all eval-k10 recover \
        plan-web deploy-web destroy-web \
        plan-observability deploy-observability destroy-observability \
        plan-guardrails deploy-guardrails destroy-guardrails
