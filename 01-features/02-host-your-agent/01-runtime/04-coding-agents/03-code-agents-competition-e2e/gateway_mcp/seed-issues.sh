#!/usr/bin/env bash
set -euo pipefail

# Usage: ./seed-issues.sh OWNER REPO
# Requires: gh CLI authenticated

OWNER="${1:?Usage: $0 OWNER REPO}"
REPO="${2:?Usage: $0 OWNER REPO}"

echo "Creating bug issues in ${OWNER}/${REPO}..."

gh issue create --repo "${OWNER}/${REPO}" \
  --title "POST /tasks always assigns id=1" \
  --body "The \`create_task\` endpoint never increments \`next_id\`, so every task gets \`id=1\`. This causes get/update/delete by ID to always return or modify the wrong task." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "DELETE /tasks/:id removes all tasks except the target" \
  --body "The filter in \`delete_task\` uses \`t['id'] == task_id\` (keeps matching) instead of \`t['id'] != task_id\` (keeps non-matching). This inverts the logic — calling delete removes everything except the task you wanted to delete." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "PUT /tasks/:id does not update the updated_at timestamp" \
  --body "When a task is updated via PUT, the \`updated_at\` field retains its original value from creation time. It should be set to the current time on each update." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "PUT /tasks/:id accepts any string as status" \
  --body "There is no validation on the \`status\` field. Users can set it to arbitrary strings like \`status: 'banana'\`. Should only allow: \`todo\`, \`doing\`, \`done\`." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "GET /tasks?status= filter is case-sensitive" \
  --body "Filtering tasks by status does an exact string match. Querying \`?status=Done\` returns nothing even if tasks have \`status: 'done'\`. The comparison should be case-insensitive." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "GET /tasks/stats always reports count of 1 per status" \
  --body "In \`task_stats()\`, the counter does \`by_status[s] = 1\` instead of incrementing. If there are 5 tasks with status 'todo', stats still reports \`{\"todo\": 1}\`." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "Frontend: status cycle skips 'doing' state" \
  --body "The \`cycleStatus\` function in \`app.js\` uses \`['todo', 'done']\` as the order array, skipping the 'doing' intermediate state. Should be \`['todo', 'doing', 'done']\`." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "Frontend: formatDate crashes on null created_at" \
  --body "If a task has a missing or null \`created_at\` field, \`formatDate()\` calls \`new Date(undefined)\` which results in 'Invalid Date'. Should handle null gracefully." \
  --label "bug"

gh issue create --repo "${OWNER}/${REPO}" \
  --title "Frontend: error message never disappears" \
  --body "The \`showError()\` function sets \`display: block\` but never hides the error element again. After the first validation error, the message stays visible permanently even after successful operations." \
  --label "bug"

echo ""
echo "Done! Created 9 issues in ${OWNER}/${REPO}."
echo "View them: https://github.com/${OWNER}/${REPO}/issues"
