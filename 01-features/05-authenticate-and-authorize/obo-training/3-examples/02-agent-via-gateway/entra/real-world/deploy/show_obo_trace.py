"""
Show a clean, per-invocation view of the OBO chain from the deployed agent.

Runs `agentcore logs` in the runtime folder, filters to the OBOTRACE:
markers our agent emits at each hop, deduplicates the JSON/text double
emissions AgentCore produces for every log record, and prints one line
per hop in chronological order.

Companion to LEARNING_GUIDE.md Chapters 2, 3, and 4.

Usage (from real-world/, one level above the runtime folder):
    python deploy/show_obo_trace.py             # last 3 minutes
    python deploy/show_obo_trace.py --since 10m
    python deploy/show_obo_trace.py --raw       # don't dedupe or reformat

Prerequisites:
  - AGENT_RUNTIME_NAME is set in .env (populated by 00_create_entra_apps.py).
  - `agentcore` CLI is installed and authenticated.
  - The agent has been invoked at least once in the given time window.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


MARKER = "OBOTRACE:"

# .env keys that hold Entra app IDs we can label in the trace output.
# Order matters only for the legend printout — longest-string wins first
# during substitution, which prevents accidental partial matches.
APP_LABELS = [
    ("FRONTEND_CLIENT_ID", "FrontendApp"),
    ("AGENT_CLIENT_ID", "AgentApp"),
    ("GATEWAY_CLIENT_ID", "GatewayApp"),
]

# Other well-known values worth labeling if they show up in tokens.
STATIC_LABELS = {
    "https://graph.microsoft.com": "MicrosoftGraph",
    "https://graph.microsoft.com/.default": "MicrosoftGraph/.default",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="5m",
        help="Time window to search (e.g. 3m, 30m, 1h). Default: 5m.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip dedup/reformat and just show every matching line.",
    )
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=500,
        help="Maximum log lines to fetch from agentcore (before filtering).",
    )
    args = parser.parse_args()

    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")

    runtime_name = os.environ.get("AGENT_RUNTIME_NAME", "").strip()
    if not runtime_name:
        print("ERROR: AGENT_RUNTIME_NAME is not set in .env.", file=sys.stderr)
        sys.exit(1)

    runtime_dir = real_world_root / runtime_name
    if not (runtime_dir / "agentcore" / "agentcore.json").exists():
        print(
            f"ERROR: {runtime_dir}/agentcore/agentcore.json not found.\n"
            "       Has the agent been scaffolded + deployed? See README.md steps 6–10.",
            file=sys.stderr,
        )
        sys.exit(1)

    proc = subprocess.run(
        ["agentcore", "logs", "--since", args.since, "-n", str(args.limit)],
        cwd=str(runtime_dir),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            f"ERROR: `agentcore logs` failed (exit {proc.returncode}):\n{proc.stderr.strip() or proc.stdout.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build a substitution map: known Entra app IDs → friendly labels.
    # Sort by longest key so specific matches win over generic ones.
    substitutions: list[tuple[str, str]] = []
    for env_key, label in APP_LABELS:
        value = os.environ.get(env_key, "").strip()
        if value:
            substitutions.append((value, label))
    substitutions.extend((k, v) for k, v in STATIC_LABELS.items())
    substitutions.sort(key=lambda kv: len(kv[0]), reverse=True)

    def annotate(msg: str) -> str:
        """Annotate known IDs with their friendly label: `Label (raw-id)`.

        Preserves the raw ID so learners can verify the mapping against .env
        without cross-referencing the legend. Case-insensitive match; keeps
        the source form for the substituted content.
        """
        out = msg
        for source, label in substitutions:
            out = re.sub(
                re.escape(source),
                f"{label} ({source})",
                out,
                flags=re.IGNORECASE,
            )
        return out

    # Extract every OBOTRACE line. AgentCore emits each log record twice —
    # once as JSON with a "body" field, and once as plain text — so dedupe
    # on message body.
    messages: list[str] = []
    for line in proc.stdout.splitlines():
        m = re.search(rf"{re.escape(MARKER)}\s*(.+?)(?:\"|$)", line)
        if not m:
            continue
        msg = m.group(1).rstrip('"').strip()
        if not msg:
            continue
        messages.append(msg)

    if args.raw:
        for line in messages:
            print(annotate(line))
        return

    # Dedupe consecutive-identical entries (JSON+text pairs) while preserving
    # order. Different invocations can produce the same message body, so we
    # only collapse consecutive dupes, not global.
    unique: list[str] = []
    for msg in messages:
        if unique and unique[-1] == msg:
            continue
        unique.append(msg)

    if not unique:
        print(
            f"No OBOTRACE markers found in the last {args.since}.\n"
            f"Steps to try:\n"
            f"  1. Click 'Ask agent' in the browser once, then re-run.\n"
            f"  2. Widen the window: python deploy/show_obo_trace.py --since 30m\n"
            f"  3. Confirm the agent code is deployed with the OBOTRACE lines:\n"
            f"       grep OBOTRACE {runtime_dir}/app/{runtime_name}/main.py\n"
            f"       # Expect: 5 matches. If 0, redeploy: agentcore deploy -y -v",
            file=sys.stderr,
        )
        sys.exit(2)

    # Group messages by "invocation": every time we see "T_user received"
    # start a new invocation block. Prints hops with 1-based indexing per
    # invocation.
    invocations: list[list[str]] = []
    current: list[str] = []
    for msg in unique:
        if msg.startswith("T_user received") and current:
            invocations.append(current)
            current = []
        current.append(msg)
    if current:
        invocations.append(current)

    # Legend: which .env keys map to which labels used in the trace output.
    # The IDs themselves are also inlined in each annotated line for easy
    # verification without needing to scroll back to the legend.
    print("Legend — labels used in the trace are sourced from .env:")
    for env_key, label in APP_LABELS:
        value = os.environ.get(env_key, "").strip()
        if value:
            print(f"  {label:<12} <- {env_key} = {value}")
    print(f"  {'user oid':<12} = unique per user; unchanged across every token in the chain")
    print()
    print("Format below: aud=<Label> (<raw-id>) — the raw ID is preserved so you can")
    print("cross-check against `.env` without leaving this output.")
    print()

    for i, group in enumerate(invocations, 1):
        title = f"─── Invocation {i} " + "─" * (60 - len(f"Invocation {i}"))
        print(title)
        for j, msg in enumerate(group, 1):
            print(f"  [{j}] {annotate(msg)}")
        print()

    total = sum(len(g) for g in invocations)
    print(f"Found {total} OBOTRACE line(s) across {len(invocations)} invocation(s) in the last {args.since}.")
    print()
    print("What to watch for:")
    print("  * `aud` rotates:  AgentApp -> GatewayApp -> (graph audience, invisible from the agent)")
    print("  * `azp` rotates:  FrontendApp -> AgentApp -> GatewayApp   (the actor chain)")
    print("  * `oid` STAYS THE SAME across every T_* — that's user identity propagation")


if __name__ == "__main__":
    main()
