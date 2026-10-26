"""Shared AgentCore Memory helper — per-repo "lessons learned" across tickets.

The orchestrator RECALLS lessons for a repo before invoking the coding agent
(injected into the prompt so the agent skips known pitfalls — saving tokens on
repeated work), and WRITES lessons after a ticket finishes (review findings +
notable gotchas).

Design choices for a deterministic PoC:
  - We write records directly with `batch_create_memory_records` (immediately
    retrievable) rather than relying on `create_event` + asynchronous long-term
    extraction (which lags by minutes — bad for a back-to-back demo).
  - Records are namespaced per repo: lessons/<repo>. Recall is a semantic search
    scoped to that namespace, so lessons from one repo don't leak into another.

Memory is a standalone resource (no Runtime dependency). MEMORY_ID is passed via
environment. If unset, all helpers no-op gracefully so the flow still runs.
"""
import os
import time
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ.get("MEMORY_ID", "")

# Lazily created so importing this module never requires AWS credentials.
_client = None


def _mem():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-agentcore", region_name=REGION)
    return _client


def _namespace(repo: str) -> str:
    """Per-repo namespace. Falls back to a shared bucket if repo is unknown."""
    safe = "".join(c for c in (repo or "shared") if c.isalnum() or c in "-_").lower() or "shared"
    return f"lessons/{safe}"


def enabled() -> bool:
    return bool(MEMORY_ID)


def recall(repo: str, query: str, top_k: int = 3) -> list[str]:
    """Return up to top_k lesson texts relevant to `query` for this repo.

    Never raises — memory is an enhancement, not a hard dependency. On any error
    (no MEMORY_ID, throttling, empty store) returns an empty list.
    """
    if not MEMORY_ID:
        return []
    try:
        resp = _mem().retrieve_memory_records(
            memoryId=MEMORY_ID,
            namespace=_namespace(repo),
            searchCriteria={"searchQuery": query or repo, "topK": top_k},
            maxResults=top_k,
        )
    except Exception as e:
        print(f"[memory] recall failed (continuing without): {e}")
        return []
    lessons = []
    for rec in resp.get("memoryRecordSummaries", []):
        text = (rec.get("content") or {}).get("text", "").strip()
        if text:
            lessons.append(text)
    return lessons


def remember(repo: str, lessons: list[str]) -> int:
    """Write lesson texts as memory records for this repo. Returns count written.

    Never raises — on error returns 0.
    """
    clean = [lesson.strip() for lesson in lessons if lesson and lesson.strip()]
    if not MEMORY_ID or not clean:
        return 0
    ns = _namespace(repo)
    now = time.time()
    records = [
        {
            "requestIdentifier": str(uuid.uuid4()),
            "namespaces": [ns],
            "content": {"text": text[:4000]},
            "timestamp": now,
            "metadata": {"repo": {"stringValue": repo or "shared"}},
        }
        for text in clean
    ]
    try:
        resp = _mem().batch_create_memory_records(memoryId=MEMORY_ID, records=records)
        return len(resp.get("successfulRecords", []))
    except Exception as e:
        print(f"[memory] remember failed (continuing): {e}")
        return 0


def format_for_prompt(lessons: list[str]) -> str:
    """Render recalled lessons as a prompt block, or empty string if none."""
    if not lessons:
        return ""
    bullets = "\n".join(f"- {lesson}" for lesson in lessons)
    return (
        "\n<lessons_learned>\n"
        "From previous work on THIS repository (apply them to avoid repeating mistakes "
        "and to save effort):\n"
        f"{bullets}\n"
        "</lessons_learned>\n"
    )
