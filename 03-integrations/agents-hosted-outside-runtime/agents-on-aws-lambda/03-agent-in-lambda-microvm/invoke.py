"""
Launch a MicroVM from the built image, invoke the Strands agent through the
per-VM HTTPS endpoint, and print CloudWatch links so you can verify that
telemetry landed in AgentCore Observability.

The invocation model:
  1. run_microvm returns a dedicated HTTPS `endpoint` per VM.
  2. Every request to that endpoint carries an X-aws-proxy-auth header
     minted by create_microvm_auth_token. Without the header you get 403.
  3. Requests route to port 8080 unless you override with X-aws-proxy-port.

We POST 3 prompts back-to-back to generate enough telemetry for the
dashboard to have something to show.

Run:
    python3 scripts/run_and_invoke.py
"""
from __future__ import annotations

import json
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

import boto3
import botocore.exceptions

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import config  # noqa: E402  (top-level module — same folder as this script)


# --------------------------------------------------------------------------

def step(msg: str) -> None:
    print(f"\n\033[1;36m▶ {msg}\033[0m")

def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")

def info(msg: str) -> None:
    print(f"  · {msg}")

def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")


# --------------------------------------------------------------------------
# Launch
# --------------------------------------------------------------------------

def run_microvm() -> dict[str, Any]:
    step("1/4 Launch MicroVM")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)

    # AWS-managed ingress connector: makes the per-VM endpoint reachable
    # from any authenticated caller on the public internet.
    ingress = (
        f"arn:aws:lambda:{config.REGION}:aws:network-connector:"
        f"aws-network-connector:ALL_INGRESS"
    )

    r = mvm.run_microvm(
        imageIdentifier=config.image_arn(),
        executionRoleArn=config.exec_role_arn(),
        ingressNetworkConnectors=[ingress],
        idlePolicy={
            "maxIdleDurationSeconds": 900,   # 15 min idle before suspend
            "suspendedDurationSeconds": 600,  # keep suspended snapshot 10 min
            "autoResumeEnabled": True,
        },
        maximumDurationInSeconds=config.MICROVM_MAX_DURATION_SECS,
    )
    ok(f"microvmId = {r['microvmId']}  state={r['state']}")
    return r


def wait_running(microvm_id: str) -> dict[str, Any]:
    step("2/4 Poll GetMicrovm until RUNNING")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)
    last = None
    started = time.time()
    while True:
        r = mvm.get_microvm(microvmIdentifier=microvm_id)
        state = r["state"]
        if state != last:
            info(f"[{int(time.time()-started):>4}s] state={state}")
            last = state
        if state == "RUNNING":
            ok(f"endpoint: {r['endpoint']}")
            return r
        if state in ("TERMINATED", "FAILED"):
            raise SystemExit(f"MicroVM {microvm_id} state={state}: {r.get('stateReason')}")
        time.sleep(5)


# --------------------------------------------------------------------------
# Invoke
# --------------------------------------------------------------------------

def mint_token(microvm_id: str) -> dict[str, str]:
    step("3/4 Mint auth token")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)
    r = mvm.create_microvm_auth_token(
        microvmIdentifier=microvm_id,
        expirationInMinutes=30,
        allowedPorts=[{"port": 8080}],
    )
    # authToken is a map<string, string>. Every entry is a header to attach
    # to outbound requests. Log the header names but never the values.
    headers = dict(r["authToken"])
    ok(f"token headers: {sorted(headers.keys())}")
    return headers


def _post(endpoint: str, path: str, body: dict[str, Any],
          headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    # The RunMicrovm response returns the endpoint as a bare hostname
    # (e.g. "xxxx.lambda-microvm.us-east-1.on.aws"). Add the scheme.
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=endpoint.rstrip("/") + path,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            **headers,
        },
    )
    # The endpoint is served by AWS Lambda's front door with a public cert.
    # Use the default SSL context (verified).
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=60) as resp:  # noqa: S310
        data = resp.read().decode("utf-8")
        return resp.status, (json.loads(data) if data else {})


def invoke_agent(endpoint: str, token_headers: dict[str, str]) -> None:
    step("4/4 Invoke agent through per-VM endpoint")

    # Session ID that flows through OTEL baggage and links every prompt in
    # this batch as one AgentCore session.
    session_id = f"demo-{uuid.uuid4().hex[:12]}"
    session_hdrs = {
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        **token_headers,
    }
    info(f"session_id = {session_id}")

    prompts = [
        "What are 3 things to do in Tokyo?",
        "How much does a typical week-long Tokyo trip cost?",
        "What's the best month to visit Tokyo?",
    ]

    for i, p in enumerate(prompts, 1):
        info(f"[{i}/{len(prompts)}] POST /invoke  prompt={p!r}")
        try:
            status, body = _post(endpoint, "/invoke", {"prompt": p}, session_hdrs)
        except urllib.error.HTTPError as e:
            warn(f"HTTP {e.code}: {e.reason} — {e.read()[:200]!r}")
            continue
        ok(f"HTTP {status}  duration_ms={body.get('duration_ms')}")
        resp = (body.get("response") or "")[:200].replace("\n", " ")
        info(f"    response: {resp}…")
        time.sleep(1)


# --------------------------------------------------------------------------
# Verification links
# --------------------------------------------------------------------------

def print_verification_links(microvm_id: str) -> None:
    r = config.REGION
    acct = config.account_id()
    lg = config.AGENT_LOG_GROUP.replace("/", "$252F")

    print("\n\033[1mVerify telemetry landed in AgentCore Observability\033[0m")
    print(f"  Give it ~1-2 minutes for OTLP to flush, then open:\n")
    print("  1. GenAI Observability dashboard (agent-level view)")
    print(
        f"     https://{r}.console.aws.amazon.com/cloudwatch/home?region={r}"
        f"#gen-ai-observability/agent-core"
    )
    print("  2. Agent log group (structured logs + spans)")
    print(
        f"     https://{r}.console.aws.amazon.com/cloudwatch/home?region={r}"
        f"#logsV2:log-groups/log-group/{lg}"
    )
    print("  3. X-Ray Trace map (session waterfall)")
    print(
        f"     https://{r}.console.aws.amazon.com/cloudwatch/home?region={r}"
        f"#xray/service-map"
    )
    print(f"\n  MicroVM ID: {microvm_id}")
    print(
        f"  Terminate when done: "
        f"aws --region {r} lambda-microvms terminate-microvm "
        f"--microvm-identifier {microvm_id}"
    )
    print(f"  Or run: python3 cleanup.py")


# --------------------------------------------------------------------------

def main() -> None:
    print("\033[1mMicroVM + AgentCore Observability demo — run & invoke\033[0m")
    print(f"account={config.account_id()}  region={config.REGION}")

    launch = run_microvm()
    microvm_id = launch["microvmId"]
    running = wait_running(microvm_id)
    endpoint = running["endpoint"]

    token_headers = mint_token(microvm_id)
    invoke_agent(endpoint, token_headers)

    print_verification_links(microvm_id)


if __name__ == "__main__":
    main()
