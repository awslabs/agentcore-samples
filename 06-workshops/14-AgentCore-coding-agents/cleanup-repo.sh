#!/usr/bin/env bash
set -euo pipefail

# Usage: ./cleanup-repo.sh OWNER REPO
# Resets the workshop repo between runs:
#   1. Closes all open PRs created by the agent
#   2. Deletes all fix/* branches from the remote
#   3. Closes all open issues and re-seeds fresh ones (no labels)

OWNER="${1:?Usage: $0 OWNER REPO}"
REPO="${2:?Usage: $0 OWNER REPO}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Cleaning up ${OWNER}/${REPO}..."
echo ""

# ── 1. Close open PRs ─────────────────────────────────────────────────────────
echo ">>> Closing open pull requests..."
prs=$(gh pr list --repo "${OWNER}/${REPO}" --state open --json number --jq '.[].number')
if [ -z "$prs" ]; then
    echo "  No open PRs."
else
    echo "$prs" | xargs -I{} gh pr close {} --repo "${OWNER}/${REPO}" --delete-branch 2>/dev/null || true
    echo "  Done."
fi
echo ""

# ── 2. Delete remaining fix/* branches ───────────────────────────────────────
echo ">>> Deleting fix/* branches..."
branches=$(gh api "repos/${OWNER}/${REPO}/branches" --jq '.[].name' | grep '^fix/' || true)
if [ -z "$branches" ]; then
    echo "  No fix/* branches found."
else
    echo "$branches" | while read -r branch; do
        gh api -X DELETE "repos/${OWNER}/${REPO}/git/refs/heads/${branch}" 2>/dev/null && \
            echo "  Deleted: ${branch}" || echo "  [skip] ${branch}"
    done
fi
echo ""

# ── 3. Close all open issues and re-seed ─────────────────────────────────────
echo ">>> Closing all open issues..."
issues=$(gh issue list --repo "${OWNER}/${REPO}" --state open --json number --jq '.[].number' --limit 100)
if [ -z "$issues" ]; then
    echo "  No open issues."
else
    echo "$issues" | xargs -I{} gh issue close {} --repo "${OWNER}/${REPO}"
    echo "  Done."
fi
echo ""

echo ">>> Re-seeding issues..."
"${SCRIPT_DIR}/seed-issues.sh" "${OWNER}" "${REPO}"

echo ""
echo "Repo reset. Ready for the next workshop run."
