die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found"
}

resolve_trusted_setup_principal() {
  local caller_arn="$1"
  local account_id="$2"
  if [[ "$caller_arn" == *":assumed-role/"* ]]; then
    local caller_role_name
    caller_role_name="$(echo "$caller_arn" | sed 's/.*:assumed-role\///' | cut -d/ -f1)"
    echo "arn:aws:iam::${account_id}:role/${caller_role_name}"
  else
    echo "$caller_arn"
  fi
}

upsert_role() {
  local role_name="$1"
  local description="$2"
  local trust_policy="$3"
  local response

  set +e
  response="$(aws iam create-role \
    --role-name "$role_name" \
    --assume-role-policy-document "$trust_policy" \
    --description "$description" 2>&1)"
  local code=$?
  set -e

  if [[ $code -ne 0 ]]; then
    if echo "$response" | grep -q "EntityAlreadyExists"; then
      echo "Role exists, updating trust policy: $role_name"
    else
      die "Failed to create role $role_name. Check IAM permissions and role naming constraints."
    fi
  else
    echo "Role created: $role_name"
  fi

  aws iam update-assume-role-policy \
    --role-name "$role_name" \
    --policy-document "$trust_policy" >/dev/null
}

put_policy() {
  local role_name="$1"
  local policy_name="$2"
  local policy_document="$3"
  aws iam put-role-policy \
    --role-name "$role_name" \
    --policy-name "$policy_name" \
    --policy-document "$policy_document" >/dev/null
}

role_arn() {
  aws iam get-role --role-name "$1" --query 'Role.Arn' --output text
}

update_env_file() {
  python3 - "$ENV_FILE" "$@" <<'PY'
from pathlib import Path
import sys

env_file = Path(sys.argv[1])
updates = dict(arg.split("=", 1) for arg in sys.argv[2:])
lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
seen = set()
out = []
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key = line.split("=", 1)[0]
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}
