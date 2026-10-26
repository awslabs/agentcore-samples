"""
Run the Egress-Controlled Code Execution demo end to end.

Reads ``runtime_config.json`` (written by ``deploy.py``) and drives the full
supervisor -> agent -> broker -> egress path in one session:

    1. start_broker      (trusted egress proxy)
    2. start_agent       (untrusted sandbox, no network of its own)
    3. configure_broker  (set the egress allowlist at runtime)
    4. status            (both containers running)
    5. ping_domain ALLOW (amazon.com     -> permitted, ping executes)
    6. ping_domain DENY  (aws.amazon.com -> blocked by the broker allowlist)

The allow-vs-deny contrast is the security boundary this sample demonstrates. Both
domains are Amazon's: the allowlist entry ``amazon.com`` is an exact match, so the
subdomain ``aws.amazon.com`` is denied — showing the policy is a strict allowlist, not a
substring/suffix match.

Usage:
    python invoke.py
"""

import json
import sys
import uuid

import boto3

CONFIG_FILE = "runtime_config.json"

# Allowlist applied at runtime via configure_broker (dynamic egress policy).
# Exact-match only (fnmatch); use a glob like "*.amazon.com" to allow subdomains.
ALLOWED_PING_DOMAINS = ["amazon.com"]


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: {CONFIG_FILE} not found. Run: python deploy.py")
        sys.exit(1)


def make_invoker(runtime, runtime_arn, session_id):
    """Return an ``invoke(command, params)`` bound to one runtime session."""

    def invoke(command, params=None, show=True):
        body = {"command": command, "params": params or {}}
        resp = runtime.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            contentType="application/json",
            accept="application/json",
            runtimeSessionId=session_id,
            payload=json.dumps(body).encode("utf-8"),
        )
        result = json.loads(resp["response"].read().decode("utf-8"))
        if show:
            print(f"\n$ {command} {json.dumps(params or {})}")
            print(json.dumps(result, indent=2))
        return result

    return invoke


def main():
    config = load_config()
    region = config["region"]
    runtime = boto3.client("bedrock-agentcore", region_name=region)

    # One session id (>= 33 chars), reused so every command hits the same
    # supervisor session and its running containers.
    session_id = f"egress-coding-exec-demo-{uuid.uuid4().hex}"
    assert len(session_id) >= 33, "runtimeSessionId must be at least 33 characters"
    print(f"Session id: {session_id}")

    invoke = make_invoker(runtime, config["runtime_arn"], session_id)

    # 1. Start the broker (trusted egress proxy)
    resp = invoke("start_broker", {"image_uri": config["broker_image"]})
    assert resp.get("status") == "ok", f"start_broker failed: {resp}"

    # 2. Start the agent (untrusted sandbox)
    resp = invoke("start_agent", {"image_uri": config["agent_image"]})
    assert resp.get("status") == "ok", f"start_agent failed: {resp}"

    # 3. Configure the broker's egress allowlist (at runtime, no restart)
    resp = invoke("configure_broker", {"allowed_ping_domains": ALLOWED_PING_DOMAINS})
    assert resp.get("status") == "ok", f"configure_broker failed: {resp}"
    assert "allowed_ping_domains" in resp.get("result", {}).get("updated", []), resp

    # 4. Status — both containers should be running
    resp = invoke("status")
    assert resp.get("broker") == "running", f"broker not running: {resp}"
    assert resp.get("agent") == "running", f"agent not running: {resp}"

    # 5. ALLOW: broker lets the request through and the ping executes.
    #    We do NOT assert ICMP reachability (exit_code 0): inside the microVM the
    #    echo replies may not complete, which is a network-path artifact, not a
    #    policy result.
    allow = invoke("ping_domain", {"domain": "amazon.com", "count": 3, "timeout": 5})
    assert allow.get("status") == "ok", f"expected ok, got: {allow}"
    allow_result = allow.get("result", {})
    assert "not in ping allowlist" not in allow_result.get("stderr", ""), f"unexpectedly denied: {allow_result}"
    assert "PING amazon.com" in allow_result.get("stdout", ""), f"ping did not execute: {allow_result}"
    print("\nALLOW: broker PERMITTED the request and ping executed against amazon.com.")

    # 6. DENY: aws.amazon.com is also Amazon's, but the allowlist entry "amazon.com"
    #    is an exact match, so the subdomain is blocked before any ping runs. The
    #    agent wraps the POLICY_DENIED error into a ping-style result (status stays
    #    "ok", exit_code -1, stderr carries the denial).
    deny = invoke("ping_domain", {"domain": "aws.amazon.com", "count": 3, "timeout": 5})
    assert deny.get("status") == "ok", f"unexpected envelope: {deny}"
    deny_result = deny.get("result", {})
    assert "not in ping allowlist" in deny_result.get("stderr", ""), f"expected a policy denial, got: {deny_result}"
    print("DENY: broker BLOCKED the ping to aws.amazon.com (not in the allowlist).")

    print("\n✓ Demo complete. Tear down with: python cleanup.py")


if __name__ == "__main__":
    main()
