#!/usr/bin/env bash
# Deploy, fill, test, or destroy the AgentCore warm pool.
# The agent runtime is managed with boto3 (agent-infra/), the pool with CloudFormation.
#
#   ./deploy.sh up          # agent + deploy + register + fill, end to end
#   ./deploy.sh agent       # build the stub agent and create or update its V2 runtime
#   ./deploy.sh deploy      # deploy the pool stack (no agent reference - it's arn-agnostic)
#   ./deploy.sh register    # add/update a pool: --arn ARN --endpoint NAME --min-pool-size N
#                           # (+ optional --retire-after-s/--max-lifetime-s/--claim-candidates)
#   ./deploy.sh unregister  # stop managing a pool: --arn ARN --endpoint NAME
#   ./deploy.sh agents      # list every registered (arn, endpoint, minPoolSize, overrides)
#   ./deploy.sh configure   # view/set shared settings: --refill-concurrency N --max-adds-per-run N ...
#   ./deploy.sh fill        # sweep now instead of waiting for the schedule
#   ./deploy.sh status      # pool depth per registered (arn, endpoint), and items by status
#   ./deploy.sh test [opts] # parallel load test against the registered pool, e.g. --mode suite
#   ./deploy.sh demo        # local web page: cold vs pooled, side by side (--port, --endpoint)
#   ./deploy.sh endpoint .. # list | versions | create NAME | update NAME --version V | delete NAME
#   ./deploy.sh drain       # stop AVAILABLE sessions (optionally --arn ARN, --endpoint NAME)
#   ./deploy.sh destroy     # remove the pool, its bucket, and the agent this script created
#
# Optional env: AWS_REGION, STACK_NAME (agentcore-warm-pool), AGENT_NAME (warm_pool_stub_agent),
#   AGENT_RUNTIME_ARN (bring your own runtime instead of 'agent'; which pool 'up' registers;
#   only required for 'test'/'drain'/'demo' when more than one pool is registered),
#   POOL_TARGET (10, the minPoolSize 'up' registers the sample agent's DEFAULT endpoint with),
#   RETIRE_AFTER_S (14400) and MAX_LIFETIME_S (28800) configure the *sample agent's own runtime*
#   (idleRuntimeSessionTimeout/maxLifetime) when 'agent'/'up' build it, and are what 'up' passes to
#   'register' so the pool's bookkeeping matches; IDLE_TIMEOUT_S (= RETIRE_AFTER_S). Everything
#   else the pool manager/broker use (REFILL_CONCURRENCY, MAX_ADDS_PER_RUN, CLAIM_CANDIDATES, and
#   RETIRE_AFTER_S/MAX_LIFETIME_S for any other agent) lives in AgentsTable, not an env var - see
#   './deploy.sh configure' and './deploy.sh register --help'.
#   KEEP_AGENT=1 on destroy, YES=1 to skip prompts
set -euo pipefail

STACK_NAME="${STACK_NAME:-agentcore-warm-pool}"
export AGENT_NAME="${AGENT_NAME:-warm_pool_stub_agent}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
RETIRE_AFTER_S="${RETIRE_AFTER_S:-14400}"
IDLE_TIMEOUT_S="${IDLE_TIMEOUT_S:-$RETIRE_AFTER_S}"
MAX_LIFETIME_S="${MAX_LIFETIME_S:-28800}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/.build"
VENV="$HERE/.venv"
PY="$VENV/bin/python"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

need aws; need python3
[[ -n "$REGION" ]] || die "set AWS_REGION"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

account()      { aws sts get-caller-identity --query Account --output text; }
bucket()       { echo "${STACK_NAME}-code-$(account)-${REGION}"; }
stack_exists() { aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1; }
output() {   # output <stack> <key>
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}
confirm() {
  [[ "${YES:-0}" == "1" ]] && return 0
  read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]
}

# ---------- python env (boto3 is always the latest we install) ----------

ensure_venv() {
  if [[ ! -x "$PY" ]]; then
    log "creating virtualenv $VENV"
    python3 -m venv "$VENV"
  fi
  local stamp="$VENV/.requirements"
  local want; want="$(cat "$HERE/agent-infra/requirements.txt" "$HERE/scripts/requirements.txt")"
  if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$want" || -n "${UPGRADE:-}" ]]; then
    log "installing boto3 into $VENV"
    "$PY" -m pip install --quiet --disable-pip-version-check --upgrade pip >&2
    "$PY" -m pip install --quiet --disable-pip-version-check --upgrade \
      -r "$HERE/agent-infra/requirements.txt" -r "$HERE/scripts/requirements.txt" >&2
    echo "$want" > "$stamp"
  fi
}

# ---------- agent runtime (boto3, see agent-infra/) ----------

cmd_agent() {
  need docker
  (( IDLE_TIMEOUT_S >= RETIRE_AFTER_S )) || die "IDLE_TIMEOUT_S must be >= RETIRE_AFTER_S"
  ensure_venv
  local out
  out="$("$PY" "$HERE/agent-infra/deploy.py" \
          --idle-timeout-s "$IDLE_TIMEOUT_S" \
          --max-lifetime-s "$MAX_LIFETIME_S" | tail -n 1)"
  local arn updated
  arn="$("$PY" -c 'import json,sys;print(json.loads(sys.argv[1])["arn"])' "$out")"
  updated="$("$PY" -c 'import json,sys;print(json.loads(sys.argv[1])["updated"])' "$out")"
  if [[ "$updated" == "True" ]] && stack_exists "$STACK_NAME"; then
    log "runtime updated: refilling now (the pool manager retires sessions from the old version)"
    cmd_fill >/dev/null
  fi
  echo "$arn"
}

# ---------- pool ----------

ensure_bucket() {
  local b; b="$(bucket)"
  if ! aws s3api head-bucket --bucket "$b" >/dev/null 2>&1; then
    log "creating code bucket $b"
    if [[ "$REGION" == "us-east-1" ]]; then
      aws s3api create-bucket --bucket "$b" >/dev/null
    else
      aws s3api create-bucket --bucket "$b" \
        --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
    fi
    aws s3api put-public-access-block --bucket "$b" --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
    aws s3api put-bucket-encryption --bucket "$b" --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  fi
  echo "$b"
}

package() {
  need zip
  log "packaging Lambda code"
  rm -rf "$BUILD/pkg" "$BUILD/functions.zip"; mkdir -p "$BUILD/pkg"
  # bundle a recent boto3: the copy in the Lambda runtime may predate the bedrock-agentcore client
  "$PY" -m pip install --quiet --disable-pip-version-check \
    -r "$HERE/src/requirements.txt" -t "$BUILD/pkg" >&2
  cp "$HERE"/src/*.py "$BUILD/pkg/"
  (cd "$BUILD/pkg" && find . -name '__pycache__' -prune -exec rm -rf {} + && zip -qr -X "$BUILD/functions.zip" .)
  local h
  h="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:16])' "$BUILD/functions.zip")"
  echo "functions-$h.zip"
}

cmd_deploy() {
  local b key
  b="$(ensure_bucket)"
  key="$(package)"
  log "uploading s3://$b/$key"
  aws s3 cp --quiet "$BUILD/functions.zip" "s3://$b/$key"
  log "deploying stack $STACK_NAME in $REGION"
  aws cloudformation deploy \
    --stack-name "$STACK_NAME" \
    --template-file "$HERE/cloudformation/warm-pool.yaml" \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --parameter-overrides "CodeBucket=$b" "CodeKey=$key" >&2
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs' --output table
}

cmd_register() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  "$PY" "$HERE/scripts/manage_agents.py" \
    --table "$(output "$STACK_NAME" AgentsTableName)" register "$@"
}

cmd_unregister() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  "$PY" "$HERE/scripts/manage_agents.py" \
    --table "$(output "$STACK_NAME" AgentsTableName)" remove "$@"
}

cmd_agents() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  "$PY" "$HERE/scripts/manage_agents.py" \
    --table "$(output "$STACK_NAME" AgentsTableName)" list
}

cmd_configure() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  "$PY" "$HERE/scripts/manage_agents.py" \
    --table "$(output "$STACK_NAME" AgentsTableName)" configure "$@"
}

cmd_fill() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  log "invoking pool manager"
  mkdir -p "$BUILD"
  aws lambda invoke --function-name "$(output "$STACK_NAME" PoolManagerFunctionName)" \
    --cli-read-timeout 330 --cli-binary-format raw-in-base64-out --payload '{"source":"schedule"}' \
    "$BUILD/fill.json" >/dev/null
  cat "$BUILD/fill.json"; echo
}

cmd_status() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  local pool_tbl agents_tbl
  pool_tbl="$(output "$STACK_NAME" PoolTableName)"
  agents_tbl="$(output "$STACK_NAME" AgentsTableName)"
  ensure_venv
  "$PY" - "$pool_tbl" "$agents_tbl" <<'PY'
import collections, sys
import boto3
pool_table, agents_table = sys.argv[1], sys.argv[2]
ddb = boto3.client("dynamodb")

targets, kwargs = {}, {}
while True:
    r = ddb.scan(TableName=agents_table, **kwargs)
    for it in r["Items"]:
        if not it["SK"]["S"].startswith("ENDPOINT#"):
            continue   # the GLOBAL/CONFIG row
        targets[(it["arn"]["S"], it["endpoint"]["S"])] = int(it["minPoolSize"]["N"])
    if "LastEvaluatedKey" not in r:
        break
    kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

counts, kwargs = collections.Counter(), {}
while True:
    r = ddb.scan(TableName=pool_table, ProjectionExpression="#s, #e, #a",
                 ExpressionAttributeNames={"#s": "status", "#e": "endpoint", "#a": "arn"}, **kwargs)
    for it in r["Items"]:
        if "arn" not in it:
            continue   # pool manager lock item
        key = (it["arn"]["S"], it.get("endpoint", {}).get("S", "DEFAULT"), it.get("status", {}).get("S", "?"))
        counts[key] += 1
    if "LastEvaluatedKey" not in r:
        break
    kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]

print(f"{'arn':60} {'endpoint':14} {'target':>6} {'available':>9} {'in_use':>6} {'other':>5}")
for arn, ep in sorted(set(targets) | {(a, e) for a, e, _ in counts}):
    av, iu = counts[(arn, ep, "AVAILABLE")], counts[(arn, ep, "IN_USE")]
    other = sum(v for (a2, e2, s), v in counts.items() if a2 == arn and e2 == ep) - av - iu
    print(f"{arn:60} {ep:14} {targets.get((arn, ep), '-'):>6} {av:>9} {iu:>6} {other:>5}")
PY
}

# echoes "<arn>\t<endpoint>" when exactly one pool is registered, dies with guidance otherwise -
# so you only need to set AGENT_RUNTIME_ARN when there's more than one pool to choose from.
resolve_pool() {
  ensure_venv
  local tbl rows n
  tbl="$(output "$STACK_NAME" AgentsTableName)"
  rows="$("$PY" - "$tbl" <<'PY'
import sys
import boto3
table = boto3.resource("dynamodb").Table(sys.argv[1])
items, kwargs = [], {}
while True:
    r = table.scan(**kwargs)
    items += r["Items"]
    if "LastEvaluatedKey" not in r:
        break
    kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
for it in items:
    if not it["SK"].startswith("ENDPOINT#"):
        continue   # the GLOBAL/CONFIG row
    print(f"{it['arn']}\t{it['endpoint']}")
PY
)"
  n="$(printf '%s\n' "$rows" | grep -c . || true)"
  case "$n" in
    0) die "no pools registered - run ./deploy.sh register first" ;;
    1) printf '%s\n' "$rows" ;;
    *) die "multiple pools registered - set AGENT_RUNTIME_ARN (see ./deploy.sh agents) to pick one" ;;
  esac
}

cmd_test() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  local arn ep
  if [[ -n "${AGENT_RUNTIME_ARN:-}" ]]; then
    arn="$AGENT_RUNTIME_ARN"
  else
    IFS=$'\t' read -r arn ep < <(resolve_pool)
    log "testing the only registered pool: $arn / ${ep:-DEFAULT}"
  fi
  ensure_venv
  "$PY" "$HERE/scripts/load_test.py" \
    --broker "$(output "$STACK_NAME" SessionBrokerFunctionName)" \
    --runtime-arn "$arn" \
    --table "$(output "$STACK_NAME" PoolTableName)" \
    ${ep:+--endpoint "$ep"} \
    "$@"
}

cmd_demo() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  STACK_NAME="$STACK_NAME" "$PY" "$HERE/ui/server.py" "$@"
}

cmd_endpoint() {
  ensure_venv
  "$PY" "$HERE/agent-infra/endpoint.py" "$@"
}

cmd_drain() {
  stack_exists "$STACK_NAME" || die "stack $STACK_NAME not found"
  ensure_venv
  "$PY" "$HERE/scripts/drain_pool.py" \
    --table "$(output "$STACK_NAME" PoolTableName)" \
    ${AGENT_RUNTIME_ARN:+--arn "$AGENT_RUNTIME_ARN"} "$@"
}

cmd_up() {
  local arn
  if [[ -n "${AGENT_RUNTIME_ARN:-}" ]]; then arn="$AGENT_RUNTIME_ARN"; else arn="$(cmd_agent)"; fi
  cmd_deploy
  # pass through the same lifetime/timeout this agent's runtime was actually built with, so the
  # pool's bookkeeping (retireAt/expiresAt) matches instead of silently using the hardcoded fallback.
  cmd_register --arn "$arn" --endpoint DEFAULT --min-pool-size "${POOL_TARGET:-10}" \
    --retire-after-s "$RETIRE_AFTER_S" --max-lifetime-s "$MAX_LIFETIME_S"
  cmd_fill
}

cmd_destroy() {
  local what="the pool stack $STACK_NAME and its code bucket"
  [[ "${KEEP_AGENT:-0}" == "1" ]] || what="$what, plus runtime $AGENT_NAME and its role and ECR repository"
  confirm "Stop all pool sessions and delete $what?" || exit 1

  if stack_exists "$STACK_NAME"; then
    log "pausing the pool manager so it stops refilling"
    aws lambda put-function-concurrency \
      --function-name "$(output "$STACK_NAME" PoolManagerFunctionName)" \
      --reserved-concurrent-executions 0 >/dev/null
    log "stopping every session the pool started"
    cmd_drain --all
    log "deleting stack $STACK_NAME"
    aws cloudformation delete-stack --stack-name "$STACK_NAME"
    aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
  else
    log "stack $STACK_NAME not found, skipping"
  fi

  local b; b="$(bucket)"
  if aws s3api head-bucket --bucket "$b" >/dev/null 2>&1; then
    log "deleting code bucket $b"
    aws s3 rm --quiet --recursive "s3://$b"
    aws s3api delete-bucket --bucket "$b"
  fi

  if [[ "${KEEP_AGENT:-0}" != "1" ]]; then
    ensure_venv
    "$PY" "$HERE/agent-infra/destroy.py"   # skips a runtime this sample didn't create
  fi
  rm -rf "$BUILD" "$VENV"
  log "destroyed"
}

case "${1:-}" in
  up)      cmd_up ;;
  agent)   cmd_agent ;;
  deploy)  cmd_deploy ;;
  fill)    cmd_fill ;;
  status)  cmd_status ;;
  test)    shift; cmd_test "$@" ;;
  demo)    shift; cmd_demo "$@" ;;
  endpoint) shift; cmd_endpoint "$@" ;;
  register) shift; cmd_register "$@" ;;
  unregister) shift; cmd_unregister "$@" ;;
  agents)  cmd_agents ;;
  configure) shift; cmd_configure "$@" ;;
  drain)   shift; cmd_drain "$@" ;;
  destroy) cmd_destroy ;;
  *) sed -n '2,31p' "$0"; exit 1 ;;
esac
