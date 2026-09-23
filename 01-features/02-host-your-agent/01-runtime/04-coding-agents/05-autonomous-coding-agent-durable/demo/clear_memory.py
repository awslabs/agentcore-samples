#!/usr/bin/env python3
"""clear_memory.py — wipe the AgentCore Memory lessons for a clean demo baseline.

The orchestrator writes per-repo "lessons learned" to AgentCore Memory after each
ticket (namespace lessons/<repo>) and recalls them on the next run. For a fresh demo
you usually want to start with an empty store so "0 lessons recalled" shows on ticket 1
and the memory panel fills up live as tickets finalize.

This deletes every record in the target namespace(s) and confirms the RECALL path
(retrieve_memory_records — the exact call the orchestrator uses) returns empty. The
ListMemoryRecords index is eventually consistent and can return already-deleted ids
for a short while, so we loop, treat ResourceNotFound as success, and trust recall as
the source of truth.

Usage:
    /tmp/poc-venv/bin/python demo/clear_memory.py                 # clears lessons/rainbow (+ /shared)
    /tmp/poc-venv/bin/python demo/clear_memory.py rainbow myrepo  # clear specific repo namespaces

Reads deploy/config.env for MEMORY_ID + AWS_REGION (same as serve.py).
"""
import os
import sys
import time

import boto3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = {}
with open(os.path.join(ROOT, "deploy", "config.env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            CFG[k] = v

REGION = CFG.get("AWS_REGION", "us-east-1")
MEMORY_ID = CFG.get("MEMORY_ID", "")

# Repos whose namespaces we clear. Default: the demo repo "rainbow" plus the "shared"
# fallback bucket used when a ticket has no repo. Override by passing repo names as args.
REPOS = sys.argv[1:] or ["rainbow", "shared"]


def _namespace(repo: str) -> str:
    """Mirror shared/memory.py::_namespace so we target the exact keys the orchestrator wrote."""
    safe = "".join(c for c in (repo or "shared") if c.isalnum() or c in "-_").lower() or "shared"
    return f"lessons/{safe}"


def _list(c, ns: str) -> list:
    recs, tok = [], None
    while True:
        kw = {"memoryId": MEMORY_ID, "namespace": ns, "maxResults": 100}
        if tok:
            kw["nextToken"] = tok
        r = c.list_memory_records(**kw)
        recs += r.get("memoryRecordSummaries", []) or r.get("memoryRecords", [])
        tok = r.get("nextToken")
        if not tok:
            break
    return recs


def _recall_count(c, ns: str) -> int:
    """What the orchestrator actually sees — the real "is it empty?" check."""
    r = c.retrieve_memory_records(
        memoryId=MEMORY_ID, namespace=ns,
        searchCriteria={"searchQuery": "lessons", "topK": 10}, maxResults=10,
    )
    return len([x for x in r.get("memoryRecordSummaries", []) if (x.get("content") or {}).get("text")])


def main() -> int:
    if not MEMORY_ID:
        print("[clear-memory] MEMORY_ID not set in deploy/config.env — nothing to clear.")
        return 0
    c = boto3.client("bedrock-agentcore", region_name=REGION)
    namespaces = [_namespace(r) for r in REPOS]
    print(f"[clear-memory] memory={MEMORY_ID} region={REGION} namespaces={namespaces}")

    deleted = 0
    for ns in namespaces:
        for _ in range(8):
            try:
                recs = _list(c, ns)
            except Exception as e:  # namespace never used / not found → nothing to clear
                if "ResourceNotFound" in type(e).__name__ or "ValidationException" in type(e).__name__:
                    break
                print(f"[clear-memory]   list error on {ns}: {e}")
                break
            if not recs:
                break
            for x in recs:
                rid = x.get("memoryRecordId") or x.get("recordId") or x.get("id")
                try:
                    c.delete_memory_record(memoryId=MEMORY_ID, memoryRecordId=rid)
                    deleted += 1
                except c.exceptions.ResourceNotFoundException:
                    pass  # already gone; stale list index
                except Exception as e:
                    print(f"[clear-memory]   delete error {rid}: {e}")
            time.sleep(3)  # let the index catch up before re-listing

    # Source of truth: the recall path must be empty.
    remaining = {ns: _recall_count(c, ns) for ns in namespaces}
    print(f"[clear-memory] deleted {deleted} record(s); recall now: {remaining}")
    if any(v for v in remaining.values()):
        print("[clear-memory] WARNING: recall still returns records — index may be lagging, re-run if needed.")
        return 1
    print("[clear-memory] ✓ memory clear — clean demo baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
