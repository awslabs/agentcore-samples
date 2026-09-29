"""Stitch one request together across every log group it touched.

    .venv/bin/python scripts/show_trace.py                  # the most recent request
    .venv/bin/python scripts/show_trace.py --since 30m
    .venv/bin/python scripts/show_trace.py --trace-id Root=1-abc...

A single question crosses four log groups, so reading them separately makes the flow
hard to follow. This correlates on `X-Amzn-Trace-Id`, which the gateway forwards and the
interceptor logs on every line, and prints the hops in order with the token claims at
each step.

It prints CLAIMS only -- `iss`, `aud`, `sub`, `scp`, `act.sub` -- never token material.
That is the same discipline the interceptor follows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
from _common import env, interceptor_name, load_env, region, resource_lambda_name

STEP_ORDER = {
    "intercept.start": 0,
    "intercept.skip": 1,
    "t_tool.cache_hit": 2,
    "idjag.minted": 3,
    "t_tool.minted": 4,
    "intercept.injected": 5,
    "intercept.no_id_token": 6,
    "intercept.exchange_failed": 7,
}

LABELS = {
    "intercept.start": "gateway handed the request to the interceptor",
    "intercept.skip": "not a tool call — passed through untouched",
    "t_tool.cache_hit": "reused a cached resource token (no ID-JAG spent)",
    "idjag.minted": "ID-JAG leg 1 at the ORG server",
    "t_tool.minted": "leg 2 at the RESOURCE AS → T_tool",
    "intercept.injected": "Authorization replaced with T_tool",
    "intercept.no_id_token": "NO ID token on the request — nothing injected",
    "intercept.exchange_failed": "exchange FAILED — request passed through unchanged",
}


def parse_since(text: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not m:
        sys.exit("--since must look like 90s, 15m, 2h or 1d")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def fetch(logs, group: str, start_ms: int) -> list[dict]:
    events: list[dict] = []
    try:
        paginator = logs.get_paginator("filter_log_events")
        for page in paginator.paginate(logGroupName=group, startTime=start_ms):
            events.extend(page.get("events", []))
    except logs.exceptions.ResourceNotFoundException:
        pass
    return events


def structured(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        msg = ev["message"]
        i = msg.find("{")
        if i < 0:
            continue
        try:
            blob = json.loads(msg[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(blob, dict) and "event" in blob:
            blob["_ts"] = ev["timestamp"]
            out.append(blob)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="15m", help="Look-back window (default: %(default)s).")
    ap.add_argument("--trace-id", help="Show only this X-Amzn-Trace-Id.")
    args = ap.parse_args()
    load_env()
    reg = region()
    logs = boto3.client("logs", region_name=reg)
    start_ms = int((time.time() - parse_since(args.since)) * 1000)

    icept = structured(fetch(logs, f"/aws/lambda/{interceptor_name()}", start_ms))
    if not icept:
        print(f"No interceptor activity in the last {args.since}.")
        print(f"  group: /aws/lambda/{interceptor_name()}")
        print("  Make a request first: python scripts/test_chain.py")
        return

    traces: dict[str, list[dict]] = {}
    for entry in icept:
        traces.setdefault(entry.get("trace_id") or "(no trace id)", []).append(entry)

    chosen = [args.trace_id] if args.trace_id else [max(traces, key=lambda k: max(e["_ts"] for e in traces[k]))]
    for trace_id in chosen:
        entries = sorted(traces.get(trace_id, []), key=lambda e: (e["_ts"], STEP_ORDER.get(e["event"], 9)))
        if not entries:
            print(f"no events for trace {trace_id}")
            continue
        print(f"\n═══ trace {trace_id} ═══")
        t0 = entries[0]["_ts"]
        for e in entries:
            offset = e["_ts"] - t0
            print(f"\n  +{offset:>5} ms  {LABELS.get(e['event'], e['event'])}")
            if e["event"] in ("intercept.start", "intercept.skip"):
                print(f"              method={e.get('method')} tool={e.get('tool')}")
            if e["event"] == "idjag.minted":
                print(f"              iss={e.get('iss')}")
                print(f"              aud={e.get('aud')}  sub={e.get('sub')}  ttl={e.get('ttl_s')}s")
            if e["event"] == "t_tool.minted":
                print(f"              iss={e.get('iss')}")
                print(f"              aud={e.get('aud')}  sub={e.get('sub')}")
                print(f"              cid={e.get('cid')}  act.sub={e.get('act_sub')}")
                print(f"              scp={e.get('scp')}  both legs took {e.get('ms')} ms")
            if e["event"] == "intercept.exchange_failed":
                print(f"              error={e.get('error')}")

    print("\n  Other groups for the same request:")
    print(f"    aws logs tail /aws/lambda/{resource_lambda_name()} --region {reg} --since {args.since}")
    runtime = env("AGENT_RUNTIME_NAME", "xaatodoagent")
    print(f"    aws logs tail /aws/bedrock-agentcore/runtimes/{runtime}... --region {reg} --since {args.since}")
    print("\n  Okta's side of the same exchanges: python scripts/show_okta_events.py")


if __name__ == "__main__":
    main()
