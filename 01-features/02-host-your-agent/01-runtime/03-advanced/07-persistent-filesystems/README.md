# Persistent Filesystems on AgentCore Runtime V2

## Overview

AgentCore runtime supports persisting filesystem state across session stop/resume cycles. Files, installed packages, and build artifacts survive session stops without needing external storage like S3 or DynamoDB.

## Runtime V2 in one field

This sample runs on **Runtime V2**, selected with one field on `create_agent_runtime`:

```python
platformVersion="V2"
```

V2 prepares your execution environment once at create/update time, snapshots it, and resumes new execution environments from that snapshot instead of loading your code again.

**The field must be set explicitly** — omitting it does not give you V2, and the create response does not report which platform version you got, so `deploy.py` reads it back with `get_agent_runtime` to confirm. Setting it needs **boto3 1.43.95 or later**. 

## How It Works

Add `filesystemConfigurations` when creating the runtime:

```python
control.create_agent_runtime(
    # ... other params ...
    filesystemConfigurations=[
        {
            'sessionStorage': {
                'mountPath': '/mnt/data',  # must be under /mnt with one subdirectory
            }
        }
    ],
    platformVersion="V2",
)
```

Files written to `/mnt/data` persist across session stop/resume cycles within the same session.

### Session Storage Lifecycle

```
Session Start  →  Write files to /mnt/data  →  Session Stop (microVM shuts down)
                                                     ↓
Session Resume (same session ID)  →  Files still at /mnt/data  →  Continue working
                                                     ↓
Session Terminate  →  Storage released permanently
```

### Constraints

| Constraint | Detail |
|:-----------|:-------|
| Mount path | Must be under `/mnt` with exactly one subdirectory (e.g., `/mnt/data`, `/mnt/workspace`) |
| Scope | Storage is per-session — different session IDs have different storage |
| Lifecycle | Storage persists across stop/resume but is released on session termination |
| Write timing | The mount is **not writable at import time** — see below |

## Writing filesystem code that is safe to snapshot

This is the one thing to get right when combining V2 with a mount.

**Session storage does not exist when your module-level code runs.** Import happens during snapshot preparation, before any session exists, so there is no session mount to write to. `/mnt/data` is present as a directory but is **not writable**: an import-time write raises `PermissionError`.

That is worth stating bluntly, because the natural way to seed a persistent directory is at import:

```python
# DO NOT do this at module level.
import os, json
os.makedirs("/mnt/data", exist_ok=True)
with open("/mnt/data/seed.json", "w") as f:
    json.dump({"seeded": True}, f)
```

Deployed with `platformVersion="V2"`, that exact code gives:

```
CREATE_FAILED — The runtime process exited unexpectedly.
```

The unhandled `PermissionError` kills the process during snapshot preparation, and the failure surfaces minutes later as a create failure that never mentions the filesystem. Nothing you write at import can reach session storage in any case: the snapshot is prepared before any session exists, so there is no session mount to write to.

**Do all filesystem work inside the entrypoint or a tool**, where a session — and therefore its mount — exists:

```python
def _save_notes(notes):
    os.makedirs(STORAGE_PATH, exist_ok=True)   # request time: correct
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=2)
```

The agent in this sample is already written that way: `STORAGE_PATH` and `NOTES_FILE` are module-level *constants* — plain strings, no I/O — and every read and write happens inside a tool. That is why it needs no changes.

Two related consequences of the same mechanism:

- **Anything cached in memory at import is shared, not per-session.** Do not load the notes file into a module-level variable at import and serve requests from it; that value is frozen from the build environment and shared by every restored environment. Read the file per request, as this agent does.
- **Values that must be unique per environment are not.** An id, seed or token generated at import is identical across every environment restored from the same snapshot. Generate them per invocation.

## What This Demo Shows

The `invoke.py` script demonstrates the full lifecycle:

1. **Add notes** — the agent writes notes to `/mnt/data/notes.json`
2. **Stop the session** — the microVM shuts down
3. **Resume the same session** — the microVM restarts with the same session ID
4. **Verify persistence** — the notes are still there

### The Agent Code

The agent uses tools that read/write to the persistent mount:

```python
STORAGE_PATH = "/mnt/data"                      # module level: a constant, no I/O
NOTES_FILE = f"{STORAGE_PATH}/notes.json"


def _load_notes() -> list[dict]:
    try:
        with open(NOTES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_notes(notes: list[dict]):
    os.makedirs(STORAGE_PATH, exist_ok=True)    # request time: the mount exists
    with open(NOTES_FILE, "w") as f:
        json.dump(notes, f, indent=2)


@tool
def add_note(content: str) -> str:
    """Add a new note to persistent storage."""
    notes = _load_notes()
    note = {"id": len(notes) + 1, "content": content,
            "created_at": datetime.now(timezone.utc).isoformat()}
    notes.append(note)
    _save_notes(notes)
    return f"Note #{note['id']} saved: '{content}'"
```

`agent.py` also defines `list_notes` and `delete_note` on the same pattern — every read and
write goes through `_load_notes`/`_save_notes`, which are only ever called from inside a tool.

The file I/O sits inside the tools, not at module level. That is a requirement here, not a stylistic preference.

### The Deploy Script

Two additions over a plain runtime — `filesystemConfigurations` and `platformVersion`:

```python
control.create_agent_runtime(
    agentRuntimeName="persistent_fs_agent",
    agentRuntimeArtifact={...},
    roleArn=role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "HTTP"},
    # ── persistent storage ──
    filesystemConfigurations=[
        {"sessionStorage": {"mountPath": "/mnt/data"}}
    ],
    # ── Runtime V2 ──
    platformVersion="V2",
)
```

`get_agent_runtime` echoes both fields back, so you can confirm the runtime really has the mount and the platform version you asked for. It is the only operation that returns `platformVersion` — create and update do not, and it is absent from `list_agent_runtimes`.

**Budget minutes for create.** Measured on this sample: **about 3.5 minutes** to `READY`, because the snapshot is prepared during the call. Sibling samples with heavier dependency trees took longer still, up to roughly 7 minutes, so treat this as a floor rather than a ceiling. `deploy.py`'s wait loop has no timeout so it simply waits, but any CI job wrapping it needs its own limit raised. Endpoint creation is slower too.

A create can also fail **asynchronously** — the call returns normally, the runtime sits in `CREATING`, and minutes later reports `CREATE_FAILED`. Two reasons matter here, both observed while validating this sample:

| `failureReason` | Meaning |
|:--|:--|
| `The runtime process exited unexpectedly.` | Your code raised during snapshot preparation. On this sample the usual cause is import-time filesystem work — see above |
| `An internal error occurred while processing your request. Please try again.` | Platform-side. Retry the identical create; if a whole region keeps failing this way, try another supported region |

Always read `failureReason` off the `get_agent_runtime` response before assuming your configuration is at fault.

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | Note-taking agent that reads/writes to `/mnt/data/notes.json` |
| `requirements.txt` | Dependencies, including `boto3>=1.43.95` — the floor that makes `platformVersion` available |
| `deploy.py` | Deploys with `filesystemConfigurations` **and `platformVersion="V2"`** |
| `invoke.py` | Adds notes → stops session → resumes → verifies notes persist |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role — see the caveat below |

`deploy.py` and `requirements.txt` are the only files that differ from a non-V2 deployment: one sets the field, the other pins the boto3 that models it. The agent code, invoke script and cleanup script are unchanged.

## Quick Start

```bash
python deploy.py     # Deploy with persistent storage at /mnt/data (several minutes)
python invoke.py     # Run the persistence demo
python cleanup.py    # Clean up — then check the runtime is really gone, see above
```
