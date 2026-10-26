#!/usr/bin/env python3
"""Side-by-side demo: default V2 cold start vs a session claimed from the warm pool.

usage: ./deploy.sh demo [--port 8000] [--endpoint DEFAULT]

Each run is one new conversation: a single prompt, then release/stop. Timings come
back split into claim (pool only), cold-start overhead (end-to-end invoke minus
the agent's own handler_ms), and agent time (handler_ms, the model call).
"""
import argparse
import json
import os
import pathlib
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.config import Config

STACK_NAME = os.environ.get("STACK_NAME", "agentcore-warm-pool")
ENDPOINT = "DEFAULT"    # set from --endpoint at startup
HERE = pathlib.Path(__file__).resolve().parent

cfg = Config(max_pool_connections=32, read_timeout=120, retries={"mode": "standard", "max_attempts": 3})
outputs = {o["OutputKey"]: o["OutputValue"] for o in
           boto3.client("cloudformation").describe_stacks(StackName=STACK_NAME)["Stacks"][0]["Outputs"]}
BROKER = outputs["SessionBrokerFunctionName"]
TABLE = outputs["PoolTableName"]
AGENTS_TABLE = outputs["AgentsTableName"]
lam = boto3.client("lambda", config=cfg)
ac = boto3.client("bedrock-agentcore", config=cfg)
ddb = boto3.client("dynamodb", config=cfg)


def resolve_arn():
    """AGENT_RUNTIME_ARN if set, else the one registered agent - same rule deploy.sh's
    cmd_test/cmd_drain use, so the demo only needs it spelled out when it's actually ambiguous."""
    env = os.environ.get("AGENT_RUNTIME_ARN")
    if env:
        return env
    arns, kwargs = set(), {}
    while True:
        resp = ddb.scan(TableName=AGENTS_TABLE, **kwargs)
        for it in resp["Items"]:
            if it.get("SK", {}).get("S", "").startswith("ENDPOINT#"):
                arns.add(it["arn"]["S"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    if len(arns) == 1:
        return arns.pop()
    raise SystemExit(f"{len(arns)} agents registered - set AGENT_RUNTIME_ARN (see ./deploy.sh agents)")


RUNTIME_ARN = resolve_arn()


def ms(seconds):
    return round(seconds * 1000)


def broker(body):
    out = json.loads(lam.invoke(FunctionName=BROKER, Payload=json.dumps(body).encode())["Payload"].read())
    if isinstance(out, dict) and "errorMessage" in out:
        raise RuntimeError(out["errorMessage"])
    return out


def pool_depth():
    return ddb.query(TableName=TABLE, IndexName="available", Select="COUNT",
                     KeyConditionExpression="#p = :p", ExpressionAttributeNames={"#p": "pool"},
                     ExpressionAttributeValues={":p": {"S": f"{RUNTIME_ARN}#{ENDPOINT}"}})["Count"]


def run(mode, prompt):
    user = f"ui-{uuid.uuid4().hex[:12]}"
    claim_ms, pooled = 0, False
    if mode == "pool":
        t = time.perf_counter()
        c = broker({"action": "claim", "userId": user, "arn": RUNTIME_ARN, "endpoint": ENDPOINT})
        claim_ms, sid, pooled = ms(time.perf_counter() - t), c["sessionId"], bool(c["pooled"])
    else:
        sid = f"cold-{uuid.uuid4().hex}"
    try:
        t = time.perf_counter()
        r = ac.invoke_agent_runtime(agentRuntimeArn=RUNTIME_ARN, runtimeSessionId=sid, qualifier=ENDPOINT,
                                    payload=json.dumps({"prompt": prompt}).encode())
        body = json.loads(r["response"].read())
        invoke_ms = ms(time.perf_counter() - t)
    finally:
        if mode == "pool":
            broker({"action": "release", "userId": user, "sessionId": sid,
                   "arn": RUNTIME_ARN, "endpoint": ENDPOINT})
        else:
            ac.stop_runtime_session(agentRuntimeArn=RUNTIME_ARN, runtimeSessionId=sid, qualifier=ENDPOINT)
    agent_ms = int(body.get("handler_ms", 0))
    result = body.get("result")
    if not isinstance(result, str):   # the stub agent returns a dict; bring-your-own agents may differ
        result = json.dumps(result)
    return {"mode": mode, "sessionId": sid, "pooled": pooled, "claim_ms": claim_ms,
            "startup_ms": invoke_ms - agent_ms, "agent_ms": agent_ms,
            "total_ms": claim_ms + invoke_ms, "result": result}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/depth":
            return self._send(200, {"depth": pool_depth()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, {"error": "not found"})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if req.get("mode") not in ("cold", "pool"):
            return self._send(400, {"error": "mode must be cold or pool"})
        try:
            self._send(200, run(req["mode"], str(req.get("prompt") or "Hello")[:2000]))
        except Exception as exc:  # noqa: BLE001 - surface any failure as a 500, never crash the server
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--endpoint", default="DEFAULT", help="runtime endpoint; it needs a registered pool (./deploy.sh register)")
    a = ap.parse_args()
    ENDPOINT = a.endpoint
    print(f"runtime {RUNTIME_ARN} (endpoint {ENDPOINT})\nopen http://localhost:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
