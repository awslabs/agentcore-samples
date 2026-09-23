# AgentCore memory Dashboard

A React + FastAPI dashboard for browsing AgentCore memory resources (events, turns, long-term records) through a UI.

## Features

- Enter `memoryId`, `actorId`, `sessionId` at runtime via the UI — no code changes needed
- Browse short-term events and conversation turns
- Query long-term records by namespace with content filtering

## Prerequisites

- Node.js 16+
- Python 3.8+
- AWS credentials configured (see the [AWS CLI setup docs](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-configure.html))
- IAM permissions: `bedrock-agentcore:ListMemoryRecords`, `ListEvents`, `GetLastKTurns`, `RetrieveMemories`, `GetMemoryStrategies`

## Setup

```bash
cd 01-features/04-manage-context-of-your-agent/memory/03-integrations/04-memory-browser

# Frontend
npm install

# Backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit if you need a non-default AWS profile or region
cd ..
```

## Run

```bash
npm run dev   # starts backend (127.0.0.1:8000) and frontend (localhost:3000)
```

Open http://localhost:3000, enter your `memoryId` and `actorId`, click **Configure**.

## Notes

- Backend binds to `127.0.0.1` by default. Set `BACKEND_HOST=0.0.0.0` in `backend/.env` only if you need network access.
- Region auto-detects from the AWS CLI profile. Override with `AWS_REGION` in `backend/.env`.
- API docs: http://localhost:8000/docs

## Troubleshooting

| Symptom | Check |
|---|---|
| Backend won't start | Virtualenv is activated; `pip install -r requirements.txt` succeeded |
| Frontend can't reach backend | Backend is running on port 8000 |
| AWS permission errors | `aws sts get-caller-identity` and the IAM permissions above |
| "memory ID not found" | Confirm the ID exists in the selected region |

## Running the Python Scripts

Navigate into each sub-folder and run the scripts:

```bash
pip install -r requirements.txt  # if present
```

```bash
# backend/
python backend/app.py
```

