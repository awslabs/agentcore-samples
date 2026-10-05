#!/usr/bin/env bash
set -euo pipefail

# Usage: ./seed-issues.sh OWNER REPO
# Idempotent: skips any issue whose title already exists in the repo.
# Issues are seeded WITHOUT labels — lab-01 (triage agent) applies them.

OWNER="${1:?Usage: $0 OWNER REPO}"
REPO="${2:?Usage: $0 OWNER REPO}"

create_issue() {
    local title="$1"
    local body="$2"

    # Check if an open issue with this exact title already exists
    existing=$(gh issue list --repo "${OWNER}/${REPO}" --state open \
        --json title --jq '.[].title' --limit 100 2>/dev/null || true)

    if echo "$existing" | grep -qxF "$title"; then
        echo "  [skip] ${title}"
    else
        gh issue create --repo "${OWNER}/${REPO}" \
            --title "$title" \
            --body "$body"
        echo "  [created] ${title}"
    fi
}

echo "Seeding issues in ${OWNER}/${REPO}..."

# ── Bugs (5) ──────────────────────────────────────────────────────────────────

create_issue \
  "POST /tasks always assigns id=1" \
  "The \`create_task\` endpoint never increments \`next_id\`, so every task gets \`id=1\`. This causes get/update/delete by ID to always return or modify the wrong task."

create_issue \
  "DELETE /tasks/:id removes all tasks except the target" \
  "The filter in \`delete_task\` uses \`t['id'] == task_id\` (keeps matching) instead of \`t['id'] != task_id\` (keeps non-matching). Calling delete removes everything except the task you wanted to delete."

create_issue \
  "GET /tasks/stats always reports count of 1 per status" \
  "In \`task_stats()\`, the counter does \`by_status[s] = 1\` instead of incrementing. If there are 5 tasks with status 'todo', stats still reports \`{\"todo\": 1}\`."

create_issue \
  "PUT /tasks/:id does not update the updated_at timestamp" \
  "When a task is updated via PUT, the \`updated_at\` field retains its original value from creation time. It should be set to the current timestamp on each update."

create_issue \
  "GET /tasks?status= filter is case-sensitive" \
  "Filtering tasks by status does an exact string match. Querying \`?status=Done\` returns nothing even if tasks have \`status: 'done'\`. The comparison should be case-insensitive."

# ── Feature requests (3) ──────────────────────────────────────────────────────

create_issue \
  "Add due-date field to tasks" \
  "Users want to set a deadline on each task. The API should accept an optional \`due_date\` (ISO-8601) on POST and PUT, store it, and return it in GET responses. The frontend should display it in the task card."

create_issue \
  "Support task priorities (low / medium / high)" \
  "There is no way to indicate urgency. Add an optional \`priority\` field (values: \`low\`, \`medium\`, \`high\`) to the task model. The frontend should show a coloured badge and allow filtering by priority."

create_issue \
  "Add pagination to GET /tasks" \
  "When there are many tasks, the API returns all of them in one response. Add \`?page=\` and \`?per_page=\` query parameters so clients can fetch tasks in pages. Default page size: 20."

# ── Questions (2) ─────────────────────────────────────────────────────────────

create_issue \
  "How do I export tasks to CSV?" \
  "Is there an endpoint or script to export all tasks as a CSV file? I need to import them into a spreadsheet for reporting."

create_issue \
  "What is the maximum number of tasks the API supports?" \
  "The tasks are stored in memory. Is there an upper limit? What happens when memory runs out — does the server crash or return an error?"

echo ""
echo "Done. View issues: https://github.com/${OWNER}/${REPO}/issues"
