#!/usr/bin/env bash
#
# destroy.sh — tear down the AgentCore Web Search Gateway sample.
#
# Usage:
#   ./destroy.sh             # interactive
#   ./destroy.sh --yes       # non-interactive (CI)
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

AUTO_APPROVE=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && AUTO_APPROVE=1

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

command -v terraform >/dev/null || fail "terraform not found."

if [[ ! -f terraform.tfstate ]] && [[ ! -d .terraform ]]; then
  warn "No local state found. Nothing to destroy here."
  warn "If you used a remote backend, run 'terraform init' first."
  exit 0
fi

say "==> Resources currently managed"
terraform state list || true
echo

if [[ "$AUTO_APPROVE" -eq 0 ]]; then
  warn "This permanently deletes the Gateway, its Web Search target, and both IAM roles."
  read -r -p "Destroy everything? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || fail "Aborted."
fi

say "==> terraform destroy"
terraform destroy -input=false -auto-approve

rm -f tfplan
echo
ok "Destroyed. No further Web Search charges will accrue."
