#!/usr/bin/env python
"""Invoke the agent both ways and watch the trace cross everything.

    python invoke.py                      # through the Gateway (default)
    python invoke.py --direct             # straight at the runtime, no Gateway
    python invoke.py --both               # compare the two
    python invoke.py --prompt "2+2?"      # custom question
    python invoke.py --repeat 3           # measure cold vs warm
    python invoke.py --attempts 1         # disable the transient retry

Every call sends a fresh W3C `traceparent` and checks whether the span created
inside the EC2 instance carries the SAME trace_id — that is what proves the
end-to-end trace.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".deploy-state.json"

# InvokeAgentRuntime requires a session id of at least 33 characters.
SESSION_MIN_LEN = 33

# Errors the service raises transiently, which are safe to retry on the SAME
# session id. See `invoke_with_retry`.
TRANSIENT = ("InternalServerException", "RuntimeClientError")


def state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        sys.exit(f"{STATE_FILE.name} not found — run `python deploy.py` first")


def traceparent() -> str:
    """W3C header: 00-<trace_id 32hex>-<span_id 16hex>-01 (01 = sampled)."""
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def new_session_id(kind: str) -> str:
    sid = f"invoke-{kind.lower()}-{uuid.uuid4().hex}"
    return sid.ljust(SESSION_MIN_LEN, "0")[:64]


def show(o: dict, sent_tp: str, elapsed: float, via: str) -> bool:
    expected_trace = sent_tp.split("-")[1]
    m = o.get("model") or {}
    ev = (o.get("evidence") or {}).get("imds") or {}
    st = o.get("ebs_state") or {}
    tr = o.get("trace") or {}
    same = tr.get("trace_id") == expected_trace

    question = ((o.get("echo") or {}).get("prompt")
                or o.get("question") or "(not echoed by the agent)")
    print(f"\n  ┌─ response via {via} — {elapsed:.1f}s")
    print(f"  │  question : {str(question)[:70]}")
    print(f"  │  answer   : {str(m.get('answer'))[:120]}")
    print(f"  │  model    : {str(m.get('model'))[:44]}")
    print(f"  │  tokens   : {m.get('tokens')}  (LLM took {m.get('latency_s')}s)")
    print("  ├─ infrastructure")
    print(f"  │  EC2      : {ev.get('instance_id')}  {ev.get('instance_type')}")
    print(f"  │  public IP: {ev.get('public_ipv4')}   ← 404/error = it has none")
    print(f"  │  EBS      : {st.get('state_dir')}  invocation #{st.get('invocations')}"
          f"  persisted={st.get('persisted_from_previous_invocation')}")
    print("  ├─ end-to-end trace")
    print(f"  │  client   → {expected_trace}")
    print(f"  │  agent    → {tr.get('trace_id')}")
    print(f"  └─ SAME TRACE? {'YES ✓' if same else 'NO ✗'}")
    return same


def invoke_with_retry(fn, st: dict, prompt: str, session_id: str,
                      attempts: int = 5) -> tuple[dict | None, str, float]:
    """Retries the transient service-side errors, reusing the SAME session id.

    Only fires on an error, never on a slow call — a real cold start is never
    interrupted. See README → Transient errors for why this is not a botocore retry.
    """
    last: tuple[dict | None, str, float] = (None, "", 0.0)
    for attempt in range(1, attempts + 1):
        o, tp, el, code = fn(st, prompt, session_id)
        if o is not None:
            return o, tp, el
        last = (o, tp, el)
        retryable = code and any(t in code for t in TRANSIENT)
        if retryable and attempt < attempts:
            print(f"     ({code} — transient, retry {attempt}/{attempts - 1} "
                  f"on the same session)", flush=True)
            time.sleep(15)
            continue
        return last
    return last


def via_gateway(st: dict, prompt: str, session_id: str):
    """Returns (payload | None, traceparent, elapsed, error_code | None)."""
    url = f"{st['gateway_url'].rstrip('/')}/{st['target_name']}/invocations"
    creds = boto3.Session(region_name=st["region"]).get_credentials().get_frozen_credentials()
    tp = traceparent()
    body = json.dumps({"prompt": prompt})
    # We deliberately do NOT send Accept: the Gateway does not forward that
    # header (it is restricted), and a runtime on serverProtocol=MCP would
    # require "application/json, text/event-stream" and return 406. With
    # serverProtocol=HTTP there is no such problem.
    h = {"Content-Type": "application/json",
         "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
         "traceparent": tp, "baggage": "origin=invoke.py"}
    req = AWSRequest(method="POST", url=url, data=body, headers=h)
    SigV4Auth(creds, "bedrock-agentcore", st["region"]).add_auth(req)
    r = urllib.request.Request(url, data=body.encode(),
                               headers=dict(req.headers), method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=900) as resp:
            return json.loads(resp.read().decode()), tp, time.time() - t0, None
    except urllib.error.HTTPError as e:
        el = time.time() - t0
        # Through the Gateway the exception name is not in the body; the
        # x-amzn-ErrorType header carries it. A 424 means the agent's container
        # returned a 4xx/5xx — RuntimeClientError, which is worth retrying.
        code = e.headers.get("x-amzn-ErrorType", f"HTTP{e.code}").split(":")[0]
        print(f"\n  HTTP {e.code} ({code}) after {el:.1f}s: {e.read().decode()[:200]}")
        return None, tp, el, code
    except Exception as e:
        el = time.time() - t0
        print(f"\n  {type(e).__name__} after {el:.1f}s")
        return None, tp, el, type(e).__name__


def direct(st: dict, prompt: str, session_id: str):
    """Returns (payload | None, traceparent, elapsed, error_code | None)."""
    dp = data_client(st["region"])
    tp = traceparent()
    t0 = time.time()
    try:
        r = dp.invoke_agent_runtime(
            agentRuntimeArn=st["runtime_arn"],
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt}).encode(),
            contentType="application/json",
            traceParent=tp,                     # NATIVE API parameter
            baggage="origin=invoke.py")
        return json.loads(r["response"].read()), tp, time.time() - t0, None
    except ClientError as e:
        el = time.time() - t0
        code = e.response["Error"]["Code"]
        print(f"\n  {code} after {el:.1f}s: "
              f"{str(e.response['Error'].get('Message'))[:160]}")
        return None, tp, el, code
    except Exception as e:
        el = time.time() - t0
        print(f"\n  {type(e).__name__} after {el:.1f}s")
        return None, tp, el, type(e).__name__


def data_client(region: str, _cache={}):
    """read_timeout=900 is NOT optional: a cold invoke waits for an EC2 instance to
    boot, and botocore's 60s default would abort a healthy cold start."""
    if region not in _cache:
        _cache[region] = boto3.client(
            "bedrock-agentcore", region_name=region,
            config=Config(read_timeout=900, connect_timeout=30,
                          retries={"max_attempts": 0}))
    return _cache[region]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt",
                    default="Use the whoami tool and report exactly what it returns.")
    ap.add_argument("--direct", action="store_true", help="skip the Gateway")
    ap.add_argument("--both", action="store_true", help="test both paths")
    ap.add_argument("--repeat", type=int, default=1,
                    help="N calls on the SAME session (measures cold vs warm)")
    ap.add_argument("--attempts", type=int, default=5,
                    help="attempts per call on transient errors (1 disables the retry)")
    args = ap.parse_args()

    st = state()
    has_gw = bool(st.get("gateway_url") and st.get("target_name"))
    if args.both:
        paths = ([("Gateway", via_gateway)] if has_gw else []) + [("direct", direct)]
    elif args.direct or not has_gw:
        if not args.direct:
            print("(no Gateway in the state — using the direct invoke)")
        paths = [("direct", direct)]
    else:
        paths = [("Gateway", via_gateway)]

    print(f"runtime : {st['runtime_id']}")
    if has_gw:
        print(f"gateway : {st['gateway_url']}/{st['target_name']}/invocations")

    for name, fn in paths:
        sid = new_session_id(name)
        print(f"\n{'='*70}\nPATH: {name}   session: {sid[:40]}…\n{'='*70}")
        lats: list[float] = []
        for n in range(args.repeat):
            if args.repeat > 1:
                print(f"\n  ── call {n+1}/{args.repeat}")
            elif n == 0:
                print("  invoking — the first call can take ~45-75s, be patient…",
                      flush=True)
            o, tp, el = invoke_with_retry(fn, st, args.prompt, sid, args.attempts)
            lats.append(el)
            if o:
                show(o, tp, el, name)
        if len(lats) > 1:
            print(f"\n  latencies: {[round(x,2) for x in lats]}")
            print(f"  1st (cold) {lats[0]:.1f}s  vs  best after {min(lats[1:]):.1f}s")
            print("  note: sessionId ROUTES but does not PIN an instance — calls on the")
            print("        same session can land on different hosts (documented behaviour)")

    print(f"\n{'='*70}")
    print("where to see the telemetry (allow ~1-2 min for propagation):")
    r = st["region"]
    print(f"  GenAI Observability: https://{r}.console.aws.amazon.com/cloudwatch/home"
          f"?region={r}#gen-ai-observability/agent-core")
    print(f"  agent logs         : https://{r}.console.aws.amazon.com/cloudwatch/home"
          f"?region={r}#logsV2:log-groups/log-group/"
          f"$252Faws$252Fbedrock-agentcore$252Fruntimes$252F{st['runtime_id']}-DEFAULT")
    print(f"  spans (aws/spans)  : https://{r}.console.aws.amazon.com/cloudwatch/home"
          f"?region={r}#logsV2:log-groups/log-group/aws$252Fspans")


if __name__ == "__main__":
    main()
