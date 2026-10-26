"""
Pretty-print a side-by-side comparison of CLAIMS_INBOUND and CLAIMS_OUTBOUND
emitted by the agent's debug logging (Learning Guide Chapter 4).

Uses `agentcore logs --since <N>m` to fetch logs, extracts the most recent
matching CLAIMS_INBOUND / CLAIMS_OUTBOUND pair, and prints a table showing
which claims stayed the same (user identity) and which changed (audience,
actor, scope).

Run from inside the CLI project folder (e.g., real-world/oboUc1EntraAgent/):
    python ../deploy/compare_obo_claims.py
    python ../deploy/compare_obo_claims.py --since 10m    # wider window
    python ../deploy/compare_obo_claims.py --all-claims   # show every claim

Default behaviour only shows the claims relevant to the OBO story. Use
--all-claims to see everything both tokens carry.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


# Claims we expect to see — ordered by teaching relevance.
# Entra calls the actor "appid" (v1.0) or "azp" (v2.0); we display whichever is present.
INTERESTING_CLAIMS = [
    "sub",  # who the token is about (Entra: PPID, per-app pseudonym)
    "oid",  # stable user identity — THE claim to watch for "same user"
    "tid",  # tenant — should be the same across hops within one tenant
    "aud",  # who the token is FOR — should ROTATE
    "iss",  # who issued it — Entra for both
    "appid",  # v1.0 actor (the client that requested this token)
    "azp",  # v2.0 equivalent of appid
    "scp",  # scopes — should ROTATE (upstream scope → downstream scope)
    "roles",  # app-role claims, if any
    "iat",  # issued-at
    "exp",  # expiration
]


def _fetch_logs(since: str) -> str:
    """Run `agentcore logs --since <since>` and return combined stdout+stderr."""
    try:
        result = subprocess.run(
            ["agentcore", "logs", "--since", since],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError:
        print("ERROR: `agentcore` CLI not found on PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("ERROR: `agentcore logs` timed out after 30s.", file=sys.stderr)
        sys.exit(1)
    return result.stdout + result.stderr


def _extract_claims_pair(log_text: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find the most recent (inbound, outbound) claims pair in the log stream.

    We look for lines of the form:
        ... CLAIMS_INBOUND: {...}
        ... CLAIMS_OUTBOUND: {...}
    and return the most recent pair emitted in the same invocation. "Most
    recent" is approximated by scanning top-to-bottom and keeping track of
    the last inbound seen, then the first outbound that follows it.
    """
    inbound_re = re.compile(r"CLAIMS_INBOUND:\s*(\{.*\})")
    outbound_re = re.compile(r"CLAIMS_OUTBOUND:\s*(\{.*\})")

    pairs: list[tuple[dict, dict]] = []
    last_inbound: dict | None = None

    for line in log_text.splitlines():
        m_in = inbound_re.search(line)
        if m_in:
            try:
                last_inbound = json.loads(m_in.group(1))
            except json.JSONDecodeError:
                last_inbound = None
            continue
        m_out = outbound_re.search(line)
        if m_out and last_inbound is not None:
            try:
                outbound = json.loads(m_out.group(1))
                pairs.append((last_inbound, outbound))
            except json.JSONDecodeError:
                pass
            last_inbound = None

    if not pairs:
        return None
    return pairs[-1]


def _stringify(value: Any, *, max_len: int = 54) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, dict)):
        s = json.dumps(value, separators=(",", " "))
    else:
        s = str(value)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _verdict(inbound_value: Any, outbound_value: Any) -> str:
    if inbound_value is None and outbound_value is None:
        return "—"
    if inbound_value is None and outbound_value is not None:
        return "➕ added"
    if inbound_value is not None and outbound_value is None:
        return "➖ removed"
    if inbound_value == outbound_value:
        return "✓ same"
    return "↔ changed"


def _print_table(inbound: dict, outbound: dict, *, show_all: bool) -> None:
    if show_all:
        keys = sorted(set(inbound) | set(outbound))
    else:
        keys = [k for k in INTERESTING_CLAIMS if k in inbound or k in outbound]

    col_claim_w = max(6, max((len(k) for k in keys), default=6))
    col_val_w = 54
    col_verdict_w = 10

    border = "─" * (col_claim_w + 2 * col_val_w + col_verdict_w + 10)
    print(border)
    print(
        f"  {'claim':<{col_claim_w}}  {'INBOUND (user → agent)':<{col_val_w}}  {'OUTBOUND (agent → Graph)':<{col_val_w}}  {'':<{col_verdict_w}}"
    )
    print(border)
    for k in keys:
        iv = inbound.get(k)
        ov = outbound.get(k)
        print(
            f"  {k:<{col_claim_w}}  {_stringify(iv, max_len=col_val_w):<{col_val_w}}  {_stringify(ov, max_len=col_val_w):<{col_val_w}}  {_verdict(iv, ov):<{col_verdict_w}}"
        )
    print(border)


def _teaching_summary(inbound: dict, outbound: dict) -> None:
    print("\nOBO invariants:")
    user_stable = inbound.get("oid") == outbound.get("oid") and inbound.get("oid") is not None
    aud_rotated = inbound.get("aud") != outbound.get("aud")
    actor_key_in = "appid" if "appid" in inbound else "azp"
    actor_key_out = "appid" if "appid" in outbound else "azp"
    actor_rotated = inbound.get(actor_key_in) != outbound.get(actor_key_out)

    print(f"  {'✓' if user_stable else '✗'} user identity preserved   — same oid on both tokens")
    print(f"  {'✓' if aud_rotated else '✗'} audience rotated          — aud changed from inbound to outbound")
    print(
        f"  {'✓' if actor_rotated else '✗'} actor rotated             — {actor_key_in}/{actor_key_out} changed (frontend → agent)"
    )
    if user_stable and aud_rotated and actor_rotated:
        print("\n  → All three invariants hold. This is OBO doing its job.")
    else:
        print("\n  → One or more invariants do NOT hold. Check your Entra setup.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="5m",
        help="Time window for `agentcore logs --since` (default: 5m).",
    )
    parser.add_argument(
        "--all-claims",
        action="store_true",
        help="Show all claims, not just the OBO-relevant ones.",
    )
    args = parser.parse_args()

    log_text = _fetch_logs(args.since)
    pair = _extract_claims_pair(log_text)
    if pair is None:
        print(
            f"No CLAIMS_INBOUND/CLAIMS_OUTBOUND pair found in the last {args.since}.\n"
            "Make sure:\n"
            "  1. You added the debug logging to agent/agent.py (Learning Guide Ch 4).\n"
            "  2. You redeployed the agent (agentcore deploy -y -v).\n"
            "  3. You invoked the agent from the frontend at least once recently.\n"
            f"Try --since 15m if your last invocation was a while ago.",
            file=sys.stderr,
        )
        sys.exit(1)

    inbound, outbound = pair
    _print_table(inbound, outbound, show_all=args.all_claims)
    _teaching_summary(inbound, outbound)


if __name__ == "__main__":
    main()
