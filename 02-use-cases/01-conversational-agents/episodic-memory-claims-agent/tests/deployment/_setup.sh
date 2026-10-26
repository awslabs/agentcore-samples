#!/usr/bin/env bash
#
# Shared setup for deployment tests. Source this, don't execute it.
#
# Provides: REGION, API URLs, token helpers, check/reporting functions.
#

set -euo pipefail

REGION="${TEST_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
_PROJECT_DIR="$(cd "$_SCRIPT_DIR/../.." && pwd)"
_CONFIG_FILE="$_PROJECT_DIR/setup/config.json"

if [[ ! -f "$_CONFIG_FILE" ]]; then
  echo "ERROR: $_CONFIG_FILE not found. Deploy infrastructure first."
  exit 1
fi

CLIENT_ID=$(python3 -c "import json; print(json.load(open('$_CONFIG_FILE'))['cognito']['client_id'])")
ADMIN_API=$(python3 -c "import json; print(json.load(open('$_CONFIG_FILE'))['admin_backend']['api_url'])")
SESSION_API=$(python3 -c "import json; print(json.load(open('$_CONFIG_FILE'))['session_backend']['api_url'])")
REVIEWS_API=$(python3 -c "import json; print(json.load(open('$_CONFIG_FILE'))['reviews_backend']['api_url'])")
RUNTIME_URL=$(python3 -c "import json; print(json.load(open('$_CONFIG_FILE'))['agentcore_runtime']['url'])")

# Demo user credentials (defined in setup/4_create_users.py)
_get_password() {
  case "$1" in
    amy-admin)         echo "AdminPass1!" ;;
    bob-policyholder)  echo "DemoPass1!" ;;
    dana-adjuster)     echo "AdjustPass1!" ;;
    *) echo "ERROR: unknown user $1" >&2; return 1 ;;
  esac
}

get_id_token() {
  local username="$1"
  local password
  password=$(_get_password "$username")
  aws cognito-idp initiate-auth \
    --client-id "$CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters USERNAME="$username",PASSWORD="$password" \
    --region "$REGION" \
    --query "AuthenticationResult.IdToken" \
    --output text
}

get_access_token() {
  local username="$1"
  local password
  password=$(_get_password "$username")
  aws cognito-idp initiate-auth \
    --client-id "$CLIENT_ID" \
    --auth-flow USER_PASSWORD_AUTH \
    --auth-parameters USERNAME="$username",PASSWORD="$password" \
    --region "$REGION" \
    --query "AuthenticationResult.AccessToken" \
    --output text
}

_PASS=0
_FAIL=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "  PASS: $desc"
    _PASS=$((_PASS + 1))
  else
    echo "  FAIL: $desc -- expected '$expected', got '$actual'"
    _FAIL=$((_FAIL + 1))
  fi
}

check_contains() {
  local desc="$1" pattern="$2" text="$3"
  if echo "$text" | grep -qiE "$pattern"; then
    echo "  PASS: $desc"
    _PASS=$((_PASS + 1))
  else
    echo "  FAIL: $desc -- pattern '$pattern' not found"
    _FAIL=$((_FAIL + 1))
  fi
}

check_not_contains() {
  local desc="$1" pattern="$2" text="$3"
  if echo "$text" | grep -qiE "$pattern"; then
    echo "  FAIL: $desc -- pattern '$pattern' found in output"
    _FAIL=$((_FAIL + 1))
  else
    echo "  PASS: $desc"
    _PASS=$((_PASS + 1))
  fi
}

http_code() {
  curl -s -o /dev/null -w "%{http_code}" "$@"
}

http_body() {
  curl -s "$@"
}

http_header() {
  local header_name="$1"; shift
  curl -s -D - -o /dev/null "$@" \
    | grep -i "$header_name" | tr -d '\r' | awk '{print $2}'
}

print_results() {
  echo ""
  echo "  Results: $_PASS passed, $_FAIL failed"
  echo ""
  if [[ $_FAIL -gt 0 ]]; then
    return 1
  fi
}
