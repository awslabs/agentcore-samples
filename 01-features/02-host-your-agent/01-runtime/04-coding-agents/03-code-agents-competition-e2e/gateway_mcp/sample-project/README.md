# Sample Project — Task Manager

A simple task manager app with a Python/Flask backend and vanilla HTML/JS frontend. This project has **intentional bugs** designed to be filed as GitHub issues and resolved through the MCP server.

## Running Locally

### Backend

```bash
cd backend
pip install -r requirements.txt
python app.py
```

The API runs at `http://localhost:5000`.

### Frontend

```bash
cd frontend
python -m http.server 8080
```

Open `http://localhost:8080` in your browser.

## Known Bugs

These are intentional — they should be filed as GitHub issues for the MCP agent to read and fix:

| # | Area | Bug |
|---|------|-----|
| 1 | Backend | `POST /tasks` — task ID is never incremented (all tasks get `id=1`) |
| 2 | Backend | `DELETE /tasks/:id` — filter logic is inverted (deletes everything except the target) |
| 3 | Backend | `PUT /tasks/:id` — `updated_at` timestamp is never refreshed |
| 4 | Backend | `PUT /tasks/:id` — no validation on `status` field (accepts any string) |
| 5 | Backend | `GET /tasks?status=` — case-sensitive comparison (won't match "Done" vs "done") |
| 6 | Backend | `GET /tasks/stats` — counter always sets to 1 instead of incrementing |
| 7 | Frontend | Status cycle skips "doing" — goes directly from "todo" to "done" |
| 8 | Frontend | `formatDate` crashes if `created_at` is null/undefined |
| 9 | Frontend | Error message never hides after being shown |

## Creating Issues on GitHub

After pushing this project to a GitHub repo, create issues using the GitHub API or CLI:

```bash
# Example: create an issue for bug #1
gh issue create \
  --title "POST /tasks always assigns id=1" \
  --body "The create_task endpoint never increments next_id, so every task gets id=1. This causes get/update/delete by ID to always match the first task." \
  --label "bug"
```

Or use the seed script (located in the `gateway_mcp/` root):

```bash
# From the gateway_mcp/ folder
./seed-issues.sh OWNER REPO
```
