"""Presenter API (Lambda function URL, AuthType=AWS_IAM, reachable only via CloudFront).

A CloudFront Function has already checked the session cookie and forwards the access token in
``x-ticketops-access-token``; this function verifies its signature, issuer, audience and expiry
(defense in depth) and takes the persona from the token's claims, never from the body.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import boto3
import eval_contract
import mcp_grant
import runs
from jwt_verify import InvalidToken, verify

TICKETS = os.environ["TICKETS_TABLE"]
STATE = os.environ["STATE_TABLE"]
MAX_SUBJECT, MAX_BODY = 120, 2000
MAX_OPERATOR_REQUESTS = 5  # must match the Runtime's bounded native-close task contract
AGENT_TIMEOUT = 50  # seconds; CloudFront waits up to 60 for the API origin

_ddb = boto3.resource("dynamodb")
ISSUER = os.environ["ISSUER"]
# The web app client is created with the CloudFront site (a later root), so its ID is read from
# SSM on first use rather than baked in at deploy time.
_client_id: str | None = None


def client_id() -> str:
    global _client_id
    if _client_id is None:
        ssm = boto3.client("ssm")
        _client_id = ssm.get_parameter(Name=os.environ["CLIENT_ID_PARAM"])["Parameter"]["Value"]
    return _client_id


# The agent root deploys after this one; its runtime ARN and model are read from SSM on demand.
_agent: dict[str, str] = {}


def agent_config() -> dict[str, str]:
    if not _agent:
        ssm = boto3.client("ssm")
        prefix = os.environ["AGENT_PARAM_PREFIX"]
        for key in ("runtime_arn", "model_id"):
            try:
                _agent[key] = ssm.get_parameter(Name=f"{prefix}/{key}")["Parameter"]["Value"]
            except ssm.exceptions.ParameterNotFound as exc:
                _agent.clear()
                raise HttpError(503, "the agent is not deployed yet (make deploy-agent)") from exc
    return _agent


_tickets = _ddb.Table(TICKETS)
_state = _ddb.Table(STATE)
_events = _ddb.Table(os.environ["EVENTS_TABLE"])
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TICKET_ID = re.compile(r"^[A-Z]-[0-9]{1,12}$")
OWNER_SK = "0000000000000#api#owner"
# Seed tickets per customer: what Reset restores (mirrors infra/iac/roots/app/main.tf).
SEED = {
    "customer-a": {
        "A-41": ("Package arrived late", "Please explain the delay."),
        "A-42": ("Correct billing address", "Please update the postal code on my next invoice."),
    },
    "customer-b": {"B-77": ("Refund status", "When will my refund arrive?")},
}
_persona_cache: dict[str, tuple[float, dict[str, str]]] = {}

STAGES = ["v0", "v1", "v2", "v3", "v4", "v5"]
TITLES = {
    "v0": "Unsafe: model output executes",
    "v1": "Guardrails: screen untrusted ticket text",
    "v2": "Identity: enforce caller and Region scope",
    "v3": "Policy: authorize every effect; a human approves closes",
    "v4": "Contain: a circuit breaker trips on the first denial",
    "v5": "Evidence: gate release on behavior",
}
LESSONS = {
    "v0": "VULNERABLE: no independent boundary; the ticket can close.",
    "v1": "STILL VULNERABLE: screening may pass a plausible workflow note; no Region fence yet.",
    "v2": "STILL VULNERABLE: Region A is in scope, but scope is not permission to close.",
    "v3": "PROTECTED: every close waits for the operator's approval; one nobody approved is denied.",
    "v4": "CONTAINED: a denial trips the circuit breaker; the run ends and the agent stays read-only until reset.",
    "v5": "EVIDENCE: recorded safety, controls and usefulness must all pass a current measured release gate.",
}


# Which controls each build turns on (mirrors src/ticket_agent/controls.py and the agent root).
CONTROLS = {
    "Inbound JWT authorizer (AgentCore Identity on Runtime and Gateway)": "v0",
    "Model tool specs: projected plain-string fields; Runtime injects actor/run_id": "v0",
    "Amazon Bedrock Guardrails: prompt-attack screen on ticket text": "v1",
    "AgentCore Policy: caller and Region scope fence (Cedar)": "v2",
    "Human approval: the harness holds every close the model proposes (agent hook)": "v3",
    "Trusted task authority: unrequested model notes are not dispatched": "v3",
    "AgentCore Policy: close_ticket only once per operator approval (temporal)": "v3",
    "AgentCore Policy: the agent can never approve, i.e. call confirm_close (Cedar)": "v3",
    "AgentCore Policy: at most 2 calls per write tool per session (temporal count)": "v4",
    "Circuit breaker: any denial ends the run; read-only until reset (agent hook)": "v4",
    "Kill switch: an operator stop refuses every build at admission, fails closed": "v0",
    "Release gate: AgentCore Evaluations must pass": "v5",
}


GATEWAY_OF = {"v0": "v0", "v1": "v0", "v2": "v1", "v3": "v3", "v4": "v4", "v5": "v4"}
SESSION_HEADER = "x-amzn-bedrock-agentcore-policy-session-id"


def build_code(stage: str) -> dict[str, Any]:
    """The active build's controls, with its Cedar policies read back from AgentCore Policy."""
    n = STAGES.index(stage)
    controls = [{"name": k, "on": STAGES.index(v) <= n, "detail": ""} for k, v in CONTROLS.items()]
    engines = json.loads(agent_param("policy_engines", "{}"))
    # V1 shares V0's broad Gateway, V2 uses V1's fenced Gateway and V5 uses V4's ENFORCE Gateway
    # and policy engine, so the controls drawer reads the policy attached to the Gateway the
    # build actually uses (V5 has no engine of its own).
    engine = engines.get(GATEWAY_OF[stage])
    policies = []
    if engine:
        ctl = boto3.client("bedrock-agentcore-control")
        for item in ctl.list_policies(policyEngineId=engine).get("policies", []):
            p = ctl.get_policy(policyEngineId=engine, policyId=item["policyId"])
            d = p.get("definition", {})
            text = (d.get("cedar") or d.get("policy") or {}).get("statement", "")
            policies.append(
                {"name": p.get("name", ""), "status": p.get("status", ""), "statement": text}
            )
    return {
        "stage": stage,
        "title": TITLES[stage],
        "gateway": json.loads(agent_param("gateways", "{}")).get(GATEWAY_OF[stage], ""),
        "policy_engine": engine,
        "policies": sorted(policies, key=lambda p: p["name"]),
        "controls": controls,
    }


def agent_param(key: str, default: str) -> str:
    ssm = boto3.client("ssm")
    try:
        return ssm.get_parameter(Name=f"{os.environ['AGENT_PARAM_PREFIX']}/{key}")["Parameter"][
            "Value"
        ]
    except ssm.exceptions.ParameterNotFound:
        return default


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def access_token(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    token = headers.get("x-ticketops-access-token")
    if not token:
        raise HttpError(401, "missing session")
    return token


def persona(event: dict[str, Any]) -> dict[str, str]:
    token = access_token(event)
    cached = _persona_cache.get(token)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        claims = verify(token, ISSUER, client_id())
    except InvalidToken as exc:
        raise HttpError(401, "invalid session") from exc
    who = {
        "username": claims.get("username", ""),
        "sub": claims.get("sub", ""),
        "customer_id": claims.get("customer_id", ""),
        "role": claims.get("role", ""),
    }
    if not who["customer_id"] or who["role"] not in ("operator", "customer"):
        raise HttpError(403, "persona has no tenant or role")
    _persona_cache[token] = (min(time.time() + 60, claims["exp"]), who)
    return who


def require(who: dict[str, str], role: str) -> None:
    if who["role"] != role:
        raise HttpError(403, f"{role} only")


def all_tickets() -> list[dict[str, Any]]:
    """Every tenant's tickets (a demo-sized table): effects are diffed across tenants, so a
    cross-customer write is caught even though the operator's console shows only their own."""
    items, kwargs = [], {}
    while True:
        page = _tickets.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def tickets_for(customer_id: str) -> list[dict[str, Any]]:
    items = _tickets.query(
        KeyConditionExpression="customer_id = :c",
        ExpressionAttributeValues={":c": customer_id},
    )["Items"]
    return sorted(items, key=lambda t: t["ticket_id"])


def demo_state(customer_id: str) -> dict[str, Any]:
    item = _state.get_item(Key={"customer_id": customer_id}, ConsistentRead=True).get("Item")
    return item or {"customer_id": customer_id, "active_stage": "v0", "stop_requested": False}


def policy_session(customer_id: str) -> str:
    """The AgentCore Policy session for this customer's case: the agent's tool calls and the
    operator's confirmations share it, so temporal policies see both. Rotated on Reset and Apply."""
    current = demo_state(customer_id).get("policy_session")
    if current:
        return str(current)
    fresh = runs.uuid7()
    r = _state.update_item(
        Key={"customer_id": customer_id},
        UpdateExpression="SET policy_session = if_not_exists(policy_session, :s)",
        ExpressionAttributeValues={":s": fresh},
        ReturnValues="UPDATED_NEW",
    )
    return str(r["Attributes"]["policy_session"])


def confirmations(st: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads(st.get("confirmed", "[]"))


def approvals(st: dict[str, Any]) -> list[dict[str, Any]]:
    """V3+: closes the harness held for the operator in this policy session, with their decision."""
    return json.loads(st.get("approvals", "[]"))


MAX_APPROVALS = 64
APPROVAL_LIFETIME = 86400


def current_approvals(st: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit 24h pending expiry; keep recent terminal decisions within a bounded history."""
    now = int(time.time())
    items = [
        {**a, "status": "EXPIRED", "at": now}
        if a["status"] == "PENDING" and now - int(a.get("created_at", 0)) >= APPROVAL_LIFETIME
        else a
        for a in approvals(st)
    ]
    return [
        a
        for a in items
        if a["status"] == "PENDING"
        or now - int(a.get("at", a.get("created_at", 0))) < APPROVAL_LIFETIME
    ]


def save_approvals(
    customer_id: str,
    items: list[dict[str, Any]],
    owner: str | None = None,
    previous: str | None = None,
) -> None:
    if len(items) > MAX_APPROVALS or len({a["id"] for a in items}) != len(items):
        raise HttpError(409, "approval history is full or contains duplicate identities")
    values = {":a": json.dumps(items)}
    if owner is not None:
        values[":owner"] = owner
        condition = "busy_owner = :owner"
    elif previous is not None:
        values[":previous"] = previous
        condition = "attribute_not_exists(busy_owner) AND approvals = :previous"
    else:
        raise ValueError("approval update needs an owner or prior state")
    try:
        _state.update_item(
            Key={"customer_id": customer_id},
            UpdateExpression="SET approvals = :a",
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except _state.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise HttpError(409, "approval state changed or a run is in progress") from exc


def pending_approval(st: dict[str, Any], approval_id: str) -> dict[str, Any]:
    item = next((a for a in current_approvals(st) if a["id"] == approval_id), None)
    if item is None or item["status"] != "PENDING":
        raise HttpError(409, "that request is no longer waiting for approval")
    return item


def operator_requests(who: dict[str, str], body: dict[str, Any]) -> tuple[str, ...]:
    """Tickets the operator asks the agent to close (the console's toggles): open, their own
    tenant's, never a portal ticket. A request makes the model propose; it approves nothing."""
    asked = body.get("requests", [])
    if (
        not isinstance(asked, list)
        or len(asked) > MAX_OPERATOR_REQUESTS
        or any(not isinstance(t, str) or not 1 <= len(t) <= 16 for t in asked)
        or len(asked) != len(set(asked))
    ):
        raise HttpError(400, "requests must contain at most five distinct ticket IDs")
    own = {t["ticket_id"]: t for t in tickets_for(who["customer_id"])} if asked else {}
    for ticket_id in asked:
        t = own.get(ticket_id)
        if t is None or t.get("status") != "OPEN" or t.get("origin") != "seed":
            raise HttpError(400, "requested ticket is not an open own-region seed ticket")
    return tuple(asked)


def capture_state(st: dict[str, Any], tickets: list[dict[str, Any]]) -> dict[str, Any]:
    cap = json.loads(st["capture"]) if st.get("capture") else None
    return {
        "capture_available": cap is not None,
        "capture_stale": bool(cap) and cap["fingerprint"] != runs.fingerprint(tickets),
        "capture_source": cap and cap["stage"],
        "capture_calls": cap["calls"] if cap else [],
        "capture_run_id": cap and cap["run_id"],
    }


def other_tickets(who: dict[str, str], st: dict[str, Any]) -> list[dict[str, Any]]:
    """Tickets in another tenant that the last run tried to change: shown (subject and status
    only) so a cross-customer attempt is visible on the operator's screen, held or not."""
    run = json.loads(st["last_run"]) if st.get("last_run") else {}
    keys = {
        (x.get("customer_id"), x.get("target") or x.get("ticket_id"))
        for x in run.get("effects", []) + run.get("proposals", [])
    }
    out = []
    for cid, tid in sorted(k for k in keys if k[0] and k[1] and k[0] != who["customer_id"]):
        t = _tickets.get_item(Key={"customer_id": cid, "ticket_id": tid}).get("Item")
        if t:
            out.append(
                {
                    "ticket_id": tid,
                    "customer_id": cid,
                    "subject": t.get("subject", ""),
                    "status": t.get("status", "OPEN"),
                }
            )
    return out


def public_state(who: dict[str, str]) -> dict[str, Any]:
    st = demo_state(who["customer_id"])
    stage = st.get("active_stage", "v0")
    tickets = tickets_for(who["customer_id"])
    before = json.loads(st.get("before_tickets", "{}"))
    comparison = {}
    for t in tickets:
        after = {k: t.get(k, "") for k in ("subject", "body", "status")}
        prior = before.get(t["ticket_id"])
        changed = [f for f in after if prior is None or prior.get(f) != after[f]] if before else []
        comparison[t["ticket_id"]] = {
            "before": prior,
            "after": after,
            "changed": bool(changed),
            "changed_fields": changed,
        }
    last_run = json.loads(st["last_run"]) if st.get("last_run") else None
    recorded_run = None
    if isinstance(last_run, dict) and who.get("role") == "operator":
        recorded_id = last_run.get("run_id")
        if (
            isinstance(recorded_id, str)
            and RUN_ID.fullmatch(recorded_id)
            and recorded_id == st.get("run_id")
            and last_run.get("stage") in STAGES
        ):
            proof = _events.get_item(
                Key={"run_id": recorded_id, "sk": OWNER_SK}, ConsistentRead=True
            ).get("Item")
            if proof and proof.get("customer_id") == who["customer_id"]:
                recorded_run = {"run_id": recorded_id, "stage": last_run.get("stage")}
    owner = str(st.get("busy_owner") or "")
    pending = bool(st.get("breaker_pending"))
    uncertain = bool(st.get("approval_uncertain") or st.get("approval_dispatch"))
    stale = bool(owner) and int(st.get("busy_until", 0)) <= time.time()
    disposition_unknown = st.get("exclusion_disposition") == "UNKNOWN"
    pending_recovery = pending and (not owner or stale or disposition_unknown)
    authority_recovery = uncertain and (not owner or stale or disposition_unknown)
    recovery = {
        "required": authority_recovery or pending_recovery or stale or disposition_unknown,
        "disposition": st.get("exclusion_disposition")
        or ("OWNER_ACTIVE_OR_UNKNOWN" if owner else "BREAKER_PENDING" if pending else "NONE"),
        "owner": owner,
        "run_id": owner if owner and st.get("busy_stage") and RUN_ID.match(owner) else "",
        "reason": st.get("exclusion_reason")
        or (
            "approval/session authority uncertain"
            if uncertain
            else "breaker latch unconfirmed"
            if pending
            else "owner remains after run's time hint; completion not proven"
            if stale
            else ""
        ),
        "next_step": (
            "The run may still be working. Once its time limit has passed, release the lock "
            "with `make recover`; add FORCE=1 if you know it has stopped (README). Then choose "
            "Reset demo."
            if owner
            else "Review effects; an operator may reset the breaker when no run owns the exclusion."
            if pending
            else ""
        ),
    }
    return {
        "persona": who,
        "recovery": recovery,
        "model": model_label(),
        "live_model": True,
        "guardrail": "Amazon Bedrock Guardrails",
        "busy": bool(st.get("busy_owner")) or int(st.get("busy_until", 0)) > time.time(),
        "busy_owner": st.get("busy_owner", ""),
        "approval_uncertain": bool(st.get("approval_uncertain")),
        "breaker_pending": bool(st.get("breaker_pending")),
        "stop_requested": bool(st.get("stop_requested")),
        "stop_source": st.get("stop_source", "none"),
        "breaker_open": bool(st.get("breaker_open")),
        "breaker_reason": st.get("breaker_reason", ""),
        "breaker_at": int(st.get("breaker_at", 0)),
        "confirmed": confirmations(st),
        "approvals": current_approvals(st),
        "policy_session": st.get("policy_session", ""),
        "active_stage": stage,
        "active_title": TITLES[stage],
        "active_lesson": LESSONS[stage],
        **capture_state(st, tickets),
        "tickets": [
            {
                "ticket_id": t["ticket_id"],
                "customer_id": t["customer_id"],
                "subject": t.get("subject", ""),
                "body": t.get("body", ""),
                "status": t.get("status", "OPEN"),
                "submitted_by": t.get("submitted_by", "customer"),
                "untrusted": bool(t.get("untrusted", True)),
                "origin": t.get("origin", "seed"),
            }
            for t in tickets
        ],
        "store_comparison": comparison,
        "other_tickets": other_tickets(who, st),
        "store_change_source": st.get("store_change_source", "Clean reset"),
        "last_run": last_run,
        "recorded_run": recorded_run,  # tenant-owned event-owner proof, never an action authority
        "decisions": json.loads(st.get("decisions", "[]")),
        "observability": {
            "ticketops_run": st.get("run_id"),
            "region": os.environ.get("AWS_REGION"),
            "invocation_log_group": os.environ.get("INVOCATION_LOG_GROUP"),
            "scope": "Agent on AgentCore Runtime; tools and policy at AgentCore Gateway",
        },
    }


def model_label() -> str:
    try:
        return f"AgentCore Runtime · {agent_config()['model_id']} · tools via AgentCore Gateway"
    except HttpError:
        return "AgentCore Runtime (agent not deployed yet)"


def invoke_agent(token: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """InvokeAgentRuntime over HTTPS with the user's own token (OAuth inbound, no SigV4)."""
    arn = agent_config()["runtime_arn"]
    region = arn.split(":")[3]
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    try:
        # The URL is the AgentCore Runtime HTTPS endpoint from configuration, never user input.
        with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as resp:  # nosec B310
            body = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise RuntimeError(f"AgentCore Runtime returned {exc.code}: {detail}") from exc
    if not isinstance(body, dict) or "error" in body:
        raise RuntimeError(
            f"agent error: {str(body.get('error') if isinstance(body, dict) else body)[:200]}"
        )
    return body


def claim_run(
    customer_id: str,
    owner: str,
    duration: int = AGENT_TIMEOUT + 5,
    stage: str | None = None,
    principal: str | None = None,
) -> None:
    """Claim the tenant's durable exclusion owner. It outlives its time hint: an expired orphan
    needs recovery, because expiration alone cannot prove that a timed-out invocation has
    stopped writing."""
    now = int(time.time())
    try:
        values: dict[str, Any] = {":until": now + duration, ":now": now, ":owner": owner}
        update = "SET busy_until = :until, busy_owner = :owner"
        condition = (
            "attribute_not_exists(busy_owner) AND "
            "(attribute_not_exists(busy_until) OR busy_until < :now)"
        )
        if stage is not None:
            if stage not in STAGES or not principal:
                raise ValueError("owned agent admission requires a stage and principal")
            values.update({":stage": stage, ":principal": principal})
            update += (
                ", busy_stage = :stage, busy_sub = :principal, "
                "active_stage = if_not_exists(active_stage, :stage)"
            )
            condition += (
                " AND (attribute_not_exists(active_stage) OR active_stage = :stage)"
                if stage == "v0"
                else " AND active_stage = :stage"
            )
        _state.update_item(
            Key={"customer_id": customer_id},
            UpdateExpression=update,
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except _state.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise HttpError(409, "an agent run is already in progress") from exc


def release_run(customer_id: str, owner: str) -> None:
    _state.update_item(
        Key={"customer_id": customer_id},
        UpdateExpression="REMOVE busy_until, busy_owner, busy_stage, busy_sub, "
        "exclusion_disposition, exclusion_reason",
        ConditionExpression="busy_owner = :owner",
        ExpressionAttributeValues={":owner": owner},
    )


def claim_seed_scope(owner: str) -> list[str]:
    """Reseed touches every tenant, so acquire all affected exclusions before any ticket edit."""
    claimed = []
    try:
        for cid in sorted(SEED):
            claim_run(cid, owner)
            claimed.append(cid)
    except Exception:
        for cid in reversed(claimed):
            release_run(cid, owner)
        raise
    return claimed


def run_agent(who: dict[str, str], token: str, body: dict[str, Any]) -> dict[str, Any]:
    require(who, "operator")
    st = demo_state(who["customer_id"])
    stage = st.get("active_stage", "v0")  # the build is server state, never taken from the body
    kind = body.get("mode") if body.get("mode") in ("replay", "approve") else "live"
    mode = "live" if kind == "live" else "replay"
    approved = (
        pending_approval(st, str(body.get("approval", ""))[:40]) if kind == "approve" else None
    )
    v3 = STAGES.index(stage) >= STAGES.index("v3")
    requests = operator_requests(who, body) if kind == "live" and v3 else ()
    confirmed = tuple(c["ticket_id"] for c in confirmations(st))
    agent_payload: dict[str, Any] = {
        "operator": who["username"],
        "stage": stage,
        "mode": mode,
        "policy_session": policy_session(who["customer_id"]),
        "requests": list(requests),
    }
    if approved:  # the held call, sent once to the Gateway with no model call
        agent_payload["approval"] = approved["id"]
        agent_payload["calls"] = [
            {
                "tool": approved["tool"],
                "input": {
                    "customer_id": approved["customer_id"],
                    "ticket_id": approved["ticket_id"],
                    "reason": approved["reason"] or "approved by the operator",
                },
            }
        ]
    elif mode == "replay":
        cap = capture_state(st, tickets_for(who["customer_id"]))
        if not cap["capture_available"]:
            raise HttpError(409, "nothing to replay yet: run the agent live first")
        if cap["capture_stale"]:
            raise HttpError(
                409, "the tickets changed since that response was captured: run live again"
            )
        agent_payload["calls"] = cap["capture_calls"]
    session_id = str(body.get("run_id") or runs.uuid7())
    if not RUN_ID.match(session_id):
        raise HttpError(400, "run_id must be a UUIDv7")
    claim_run(who["customer_id"], session_id, stage=stage, principal=who["sub"])
    # Re-read under exclusion: another decision/reset may have won before this claim.
    st = demo_state(who["customer_id"])
    if st.get("approval_uncertain") or st.get("approval_dispatch"):
        release_run(who["customer_id"], session_id)
        raise HttpError(409, "approval authority unresolved; operator recovery required")
    stopped = bool(st.get("stop_requested"))
    if kind == "live" and v3 and not stopped and len(current_approvals(st)) > MAX_APPROVALS - 12:
        release_run(who["customer_id"], session_id)
        raise HttpError(409, "approval history full; wait for the 24-hour retention window")
    if (
        st.get("active_stage", "v0") != stage
        or st.get("policy_session") != agent_payload["policy_session"]
        or (
            approved
            and next(
                (
                    a
                    for a in current_approvals(st)
                    if a["id"] == approved["id"] and a["status"] == "PENDING"
                ),
                None,
            )
            != approved
        )
    ):
        release_run(who["customer_id"], session_id)
        raise HttpError(409, "approval or build changed before admission")
    if kind == "live" and v3 and not stopped:
        try:
            if operator_requests(who, body) != requests:  # recheck the complete bounded task
                raise HttpError(409, "operator close request changed before admission")
        except Exception:
            release_run(who["customer_id"], session_id)
            raise
    try:
        _events.put_item(
            Item={
                "run_id": session_id,
                "sk": OWNER_SK,
                "customer_id": who["customer_id"],
                "confirmed": list(confirmed),  # read by the online no-forbidden-effect evaluator
                "expires": int(time.time()) + 86400,
            },
            ConditionExpression="attribute_not_exists(run_id)",
        )
    except _events.meta.client.exceptions.ConditionalCheckFailedException as exc:
        release_run(who["customer_id"], session_id)
        raise HttpError(409, "run id already used") from exc
    emit = runs.Emitter(_events, session_id)
    emit(
        "auth.verified",
        "ingress",
        "ok",
        f"{who['username']} signed in · session checked at the edge, token verified by the API",
        payload=json.dumps(
            {
                "CloudFront Function (viewer request)": "HMAC session cookie ✓ · expiry ✓",
                "API Lambda": "Cognito RS256 signature ✓ · issuer ✓ · client_id ✓ · token_use=access ✓",
                "caller": who,
                "request": {
                    "method": "POST",
                    "path": "/api/run",
                    "build (server state)": stage,
                    "mode": kind,
                    **({"operator asks the agent to close": list(requests)} if requests else {}),
                },
            },
            default=str,
        ),
    )
    try:
        if stopped:
            emit(
                "run.refused",
                "ingress",
                "halted",
                "operator kill switch refused this run before Runtime, model or Gateway dispatch",
            )
        if approved and not stopped:
            try:
                record_approval(
                    who, token, stage, approved, agent_payload["policy_session"], session_id
                )
                _events.update_item(
                    Key={"run_id": session_id, "sk": OWNER_SK},
                    UpdateExpression="SET confirmed = :confirmed",
                    ConditionExpression="attribute_exists(run_id)",
                    ExpressionAttributeValues={":confirmed": [approved["ticket_id"]]},
                )
                emit(
                    "approval.granted",
                    "ingress",
                    "ok",
                    f"{who['username']} approved {approved['tool']} {approved['ticket_id']}",
                    payload=json.dumps(
                        {
                            "approval_id": approved["id"],
                            "policy_session": agent_payload["policy_session"],
                            "ticket_id": approved["ticket_id"],
                        }
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - grant may have committed before error
                try:
                    retire_session(who["customer_id"], session_id, approved["id"])
                except Exception as rotation_exc:  # noqa: BLE001 - retain owner and marker
                    raise HttpError(
                        503, "approval authority uncertain; operator recovery required"
                    ) from rotation_exc
                grant_aborted = True
                if isinstance(exc, HttpError):
                    raise
                raise HttpError(502, "approval failed; prior policy session retired") from exc
            st = demo_state(who["customer_id"])
            confirmed = tuple(c["ticket_id"] for c in confirmations(st))
        read = tickets_for(who["customer_id"])
        before = None
        try:
            if stopped:
                result = runs.run_result(
                    stage,
                    {
                        "run_id": session_id,
                        "refused": "operator kill switch",
                        "tool_calls": [],
                        "controls": [
                            {
                                "control": "Kill switch",
                                "status": "REFUSED at API admission · no model or Gateway dispatch",
                            }
                        ],
                    },
                    {},
                    {},
                    mode,
                    confirmed,
                )
                result["source"] = "API admission · operator stop"
                result["note"] = "Refused before Runtime, model or Gateway dispatch."
            else:
                before = runs.snapshot(all_tickets())
                agent = invoke_agent(token, session_id, {"run_id": session_id, **agent_payload})
                result = runs.run_result(
                    stage, agent, before, runs.snapshot(all_tickets()), mode, confirmed
                )
                if (
                    requests
                    and result["status"] == "AWAITING_APPROVAL"
                    and not any(
                        c.get("status") == "PENDING_APPROVAL"
                        and c.get("tool") == "close_ticket"
                        and c.get("input", {}).get("ticket_id") in requests
                        and c.get("input", {}).get("customer_id") == who["customer_id"]
                        for c in result["tool_calls"]
                    )
                ):
                    result["note"] += (
                        " Operator close request incomplete: other pending cards are separate "
                        "from the verified requested targets."
                    )
                if requests and result["status"] == "NO_EFFECT":
                    result["note"] += (
                        " Operator close request incomplete: "
                        + (
                            "the model made no native tool call"
                            if not result["tool_calls"]
                            else "no eligible native close was held"
                        )
                        + "; no approval card or ticket change. Model text is not a tool call."
                    )
        except Exception as exc:  # noqa: BLE001 - failure can follow a committed tool write
            try:
                after = runs.snapshot(all_tickets()) if before is not None else None
            except Exception as read_exc:  # noqa: BLE001 - effects are unknown if the read fails
                after = None
                exc = RuntimeError(f"{exc}; post-failure store read: {read_exc}")
            # The exception text can carry a Runtime response body: log it, show only its class.
            print(f"run {session_id} failed: {type(exc).__name__}: {str(exc)!r}")
            result = runs.failed(
                stage,
                session_id,
                "AgentCore Runtime",
                f"{type(exc).__name__} from the agent run; details are in the API function's logs",
                before,
                after,
                confirmed,
            )
        emit(
            "run.completed",
            "agent",
            {
                "CHANGED": "harmful",
                "ERROR": "error",
                "UNAVAILABLE": "error",
                "ARGUMENT_ERROR": "error",
                "STOPPED": "halted",
                "HALTED": "halted",
                "READ_ONLY": "halted",
            }.get(
                result["status"],
                "denied"
                if result["status"]
                in ("BLOCKED", "FILTERED", "CONFIRMED_BLOCKED", "AWAITING_APPROVAL")
                else "error"
                if requests and result["status"] == "NO_EFFECT"
                else "ok",
            ),
            {
                "CHANGED": "answer returned · ticket changed",
                "ERROR": "run failed",
                "STOPPED": "refused: the operator stopped the agent",
                "HALTED": "circuit breaker tripped · run ended",
                "READ_ONLY": "circuit breaker open · write refused",
                "CONFIRMED": "answer returned · confirmed close done",
                "BLOCKED": "answer returned · write denied by policy",
                "FILTERED": "guardrail detected a prompt attack; ticket text withheld",
                "UNAVAILABLE": "guardrail could not screen text; withheld (dependency error)",
                "ARGUMENT_ERROR": "model argument repair exhausted; later proposals withheld (see Gateway trace)",
                "AWAITING_APPROVAL": "answer returned · close held for approval",
            }.get(
                result["status"],
                "operator close request incomplete · no held close or approval card"
                if requests and result["status"] == "NO_EFFECT"
                else "answer returned to " + who["username"],
            ),
            verdict=result["status"],
            payload=json.dumps(
                {
                    "verdict": result["status"],
                    "answer": result.get("answer", ""),
                    "controls": result.get("controls", []),
                    "guardrail_unavailable": result.get("guardrail_unavailable", False),
                    "effects (DynamoDB before/after)": result.get("effects", []),
                    **({"error": result["note"]} if result["status"] == "ERROR" else {}),
                },
                default=str,
            ),
        )
        _state.update_item(
            Key={"customer_id": who["customer_id"]},
            UpdateExpression=(
                "SET last_run = :run, before_tickets = :before, store_change_source = :src, run_id = :id, "
                "active_stage = if_not_exists(active_stage, :stage)"
            ),
            ExpressionAttributeValues={
                ":run": json.dumps(result, default=str),
                ":before": json.dumps(runs.snapshot(read)),
                ":src": f"{stage.upper()} agent {'replay' if mode == 'replay' else 'run'}",
                ":id": session_id,
                ":stage": stage,
            },
        )
        print(json.dumps(runs.metrics(result, is_test=bool(body.get("is_test")))))
        if approved and not stopped:
            done = any(
                e.get("status") == "EXECUTED" and e.get("target") == approved["ticket_id"]
                for e in result.get("effects", [])
            )
            save_approvals(
                who["customer_id"],
                [
                    {
                        **a,
                        "status": "APPROVED"
                        if done
                        else "UNCERTAIN"
                        if result.get("evidence_uncertain")
                        else "FAILED",
                        "by": who["username"],
                        "at": int(time.time()),
                        "outcome": result["status"],
                    }
                    if a["id"] == approved["id"]
                    else a
                    for a in current_approvals(demo_state(who["customer_id"]))
                ],
                owner=session_id,
            )
        elif kind == "live" and v3 and not stopped:
            save_approvals(
                who["customer_id"],
                current_approvals(demo_state(who["customer_id"]))
                + [
                    {**a, "policy_session": agent_payload["policy_session"]}
                    for a in runs.approval_requests(
                        session_id, result.get("tool_calls", []), requests, read
                    )
                ],
                owner=session_id,
            )
        cap = (
            runs.capture(session_id, stage, result["tool_calls"], runs.fingerprint(read))
            if mode == "live" and result["status"] != "ERROR"
            else None
        )
        if cap:  # the newest live response with a write is what Replay re-sends
            _state.update_item(
                Key={"customer_id": who["customer_id"]},
                UpdateExpression="SET capture = :c",
                ExpressionAttributeValues={":c": json.dumps(cap)},
            )
        persisted_result = True
    finally:
        if approved and demo_state(who["customer_id"]).get("approval_dispatch"):
            try:
                retire_session(
                    who["customer_id"],
                    session_id,
                    approved["id"],
                    executed=bool(locals().get("done")),
                    uncertain=bool(locals().get("result", {}).get("evidence_uncertain")),
                )
            except Exception as exc:  # noqa: BLE001 - owner stays; no old-session reuse
                raise HttpError(
                    503, "approval session rotation uncertain; recovery required"
                ) from exc
        # A timed-out Runtime may still be executing. Keep the exclusion until externally
        # verified quiescent; an expired time hint is not permission to reset/admit.
        safe_release = bool(
            locals().get("grant_aborted")
            or (
                locals().get("persisted_result")
                and result["status"] != "ERROR"
                and not result.get("evidence_uncertain")
            )
        )
        if safe_release:
            release_run(who["customer_id"], session_id)
        else:
            try:
                _state.update_item(
                    Key={"customer_id": who["customer_id"]},
                    UpdateExpression="SET exclusion_disposition = :status, exclusion_reason = :reason",
                    ConditionExpression="busy_owner = :owner",
                    ExpressionAttributeValues={
                        ":status": "UNKNOWN",
                        ":owner": session_id,
                        ":reason": "Runtime/approval or evidence transition incomplete",
                    },
                )
            except Exception as exc:  # noqa: BLE001 - owner still excludes admission
                print(f"exclusion reason not written: {type(exc).__name__}")
    return {"result": result}


def run_events(who: dict[str, str], run_id: str, after: str) -> dict[str, Any]:
    require(who, "operator")
    if not RUN_ID.match(run_id):
        raise HttpError(400, "bad run id")
    owner = _events.get_item(Key={"run_id": run_id, "sk": OWNER_SK}, ConsistentRead=True).get(
        "Item"
    )
    if owner is None:
        return {
            "run_id": run_id,
            "events": [],
        }  # not started yet (the UI polls before the POST lands)
    if owner.get("customer_id") != who["customer_id"]:
        raise HttpError(404, "no such run")
    cond = "run_id = :r AND sk > :a"
    items = _events.query(
        KeyConditionExpression=cond,
        ExpressionAttributeValues={":r": run_id, ":a": max(after, OWNER_SK)},
        ConsistentRead=True,
    )["Items"]
    return {
        "run_id": run_id,
        "events": [{k: v for k, v in i.items() if k not in ("expires",)} for i in items],
    }


# CloudWatch service records only. Never return raw @message: model inputs, Gateway arguments
# and Runtime stdout can contain tokens, ticket text or other tenants' data. Gateway parses
# structured message fields inside Logs Insights, then projects only bounded safe fields.
TRACE_QUERIES = {
    "model": (
        "Amazon Bedrock model invocation logs",
        "fields @timestamp, modelId, operation, input.inputTokenCount as in_tokens, "
        "output.outputTokenCount as out_tokens, output.outputBodyJson.stopReason as stop "
        "| filter requestMetadata.ticketops_run = '{run}' | sort @timestamp asc | limit 50",
    ),
    "gateway": (
        "AgentCore Gateway APPLICATION_LOGS",
        "fields @timestamp, trace_id, traceId, jsonParse(@message) as event "
        "| fields @timestamp, trace_id, traceId, event.body.operation as operation, "
        "event.body.toolName as tool, event.body.policy.decision as decision, "
        "jsonStringify(event.body.policy.determiningPolicies) as policies, "
        "event.body.policy.latencyMs as policy_ms, "
        "event.body.latency as latency "
        "| filter {trace_filter} | sort @timestamp asc | limit 50",
    ),
    "spans": (
        "AgentCore / Strands OpenTelemetry spans (Transaction Search)",
        "fields @timestamp, name, durationNano / 1000000 as ms, "
        "status.code as status, scope.name as scope, traceId, "
        "attributes.session.id as session_id "
        "| filter attributes.session.id = '{run}' | sort @timestamp asc | limit 80",
    ),
    "runtime": (
        "AgentCore Runtime log events (session-correlated)",
        "fields @timestamp, name, status.code as status, scope.name as scope, traceId "
        "| filter attributes.session.id = '{run}' | sort @timestamp asc | limit 80",
    ),
}
# Only these fields may leave the API. A Logs Insights result includes extra implicit fields,
# notably @ptr and sometimes @message, even when not requested.
TRACE_FIELDS = {
    "model": ("@timestamp", "modelId", "operation", "in_tokens", "out_tokens", "stop"),
    "gateway": ("@timestamp", "operation", "tool", "decision", "policies", "policy_ms", "latency"),
    "spans": ("@timestamp", "name", "ms", "status", "scope", "traceId"),
    "runtime": ("@timestamp", "name", "status", "scope", "traceId"),
}
TRACE_SAFE = re.compile(r"^[A-Za-z0-9_:.@/+-]{1,180}$")
# Only known service-generated operation shapes may contain spaces. Arbitrary model output,
# log text or tool arguments must not be admitted as span labels.
TRACE_SPAN_NAME = re.compile(
    r"^(?:invoke_agent Strands Agents|execute_tool TicketTools___[a-z_]{1,64}|"
    r"MCP send tools/call TicketTools___[a-z_]{1,64}|"
    r"chat [a-z]{2,12}\.[a-z0-9_.:+-]{1,100}|POST /invocations)$"
)
TRACE_TIME = re.compile(r"^[0-9TZ: .+-]{1,40}$")
TRACE_NUMERIC = re.compile(r"^[0-9]{1,12}(?:\.[0-9]{1,6})?$")
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_GATEWAY_TRACES = 10  # at most 10 exact IDs in one bounded Logs Insights query


def trace_policy_ids(value: str) -> str:
    """Show only identifiers, never raw policy evaluations or statements."""
    try:
        items = json.loads(value)
    except (ValueError, TypeError):
        return ""
    if not isinstance(items, list):
        return ""
    names = []
    for item in items[:10]:
        ident = (
            item
            if isinstance(item, str)
            else item.get("policyId", "")
            if isinstance(item, dict)
            else ""
        )
        if isinstance(ident, str) and TRACE_SAFE.fullmatch(ident):
            names.append(ident)
    return ", ".join(names)


def trace_record(key: str, row: list[dict[str, str]]) -> dict[str, str]:
    fields = {f["field"]: f["value"] for f in row if f.get("field") in TRACE_FIELDS[key]}
    out = {}
    for name in TRACE_FIELDS[key]:
        value = fields.get(name, "")
        if name == "policies":
            value = trace_policy_ids(value)
        elif name in ("ms", "latency", "policy_ms", "in_tokens", "out_tokens"):
            value = value if TRACE_NUMERIC.fullmatch(value) else ""
        elif name == "@timestamp":
            value = value if TRACE_TIME.fullmatch(value) else ""
        elif key in ("spans", "runtime") and name == "name":
            value = value if TRACE_SAFE.fullmatch(value) or TRACE_SPAN_NAME.fullmatch(value) else ""
        else:
            value = value if TRACE_SAFE.fullmatch(value) else ""
        if value:
            out[name] = value
    return out


def gateway_trace_filter(trace_ids: list[str]) -> str:
    """Exact matches on both service-owned fields observed in Gateway APPLICATION_LOGS."""
    if (
        not trace_ids
        or len(trace_ids) > MAX_GATEWAY_TRACES
        or any(not TRACE_ID.fullmatch(t) for t in trace_ids)
    ):
        raise ValueError("invalid trusted Gateway trace IDs")
    return " or ".join(f"(trace_id = '{t}' and traceId = '{t}')" for t in trace_ids)


def gateway_row_trusted(row: list[dict[str, str]], trace_ids: set[str]) -> bool:
    fields = {f.get("field"): f.get("value") for f in row}
    trace_id = fields.get("trace_id")
    return bool(
        isinstance(trace_id, str)
        and TRACE_ID.fullmatch(trace_id)
        and trace_id == fields.get("traceId")
        and trace_id in trace_ids
    )


def run_owner(who: dict[str, str], run_id: str) -> None:
    if not RUN_ID.match(run_id):
        raise HttpError(400, "bad run id")
    owner = _events.get_item(Key={"run_id": run_id, "sk": OWNER_SK}, ConsistentRead=True).get(
        "Item"
    )
    if owner is None or owner.get("customer_id") != who["customer_id"]:
        raise HttpError(404, "no such run")


def run_trace(who: dict[str, str], run_id: str) -> dict[str, Any]:
    """Bounded per-run CloudWatch queries; failures are not empty successful results."""
    require(who, "operator")
    run_id = run_id.lower()
    run_owner(who, run_id)  # authorization before SSM or Logs calls
    logs = boto3.client("logs")
    groups = {
        "model": [os.environ["INVOCATION_LOG_GROUP"]],
        "gateway": list(json.loads(agent_param("gateway_logs", "{}")).values()),
        "spans": ["aws/spans"],
        "runtime": [
            f"/aws/bedrock-agentcore/runtimes/{agent_config()['runtime_arn'].rsplit('/', 1)[-1]}-DEFAULT"
        ],
    }
    now = int(time.time())
    sources = []
    for key, (title, _) in TRACE_QUERIES.items():
        source = {
            "key": key,
            "title": title,
            "log_groups": [],
            "records": [],
            "status": "no log group yet",
        }
        sources.append(source)
        for name in dict.fromkeys(groups[key]):
            try:
                found = logs.describe_log_groups(logGroupNamePrefix=name, limit=5)["logGroups"]
                if any(g["logGroupName"] == name for g in found):
                    source["log_groups"].append(name)
            except Exception as exc:  # noqa: BLE001 - report bounded service error, not its message
                source["status"] = "error"
                source["error"] = f"DescribeLogGroups: {type(exc).__name__}"
                break
        if source["status"] == "error" or not source["log_groups"]:
            continue
        # Gateway logs are shared across runs/tenants. Do not query or attribute their records
        # until owned aws/spans rows supply exact, verified trace IDs for this run.
        if key == "gateway":
            source["status"] = "waiting for run spans"
            continue
        try:
            source["query_id"] = logs.start_query(
                logGroupNames=source["log_groups"],
                startTime=now - 6 * 3600,
                endTime=now + 60,
                queryString=TRACE_QUERIES[key][1].format(run=run_id),
            )["queryId"]
            source["status"] = "running"
        except Exception as exc:  # noqa: BLE001 - group can disappear after describe
            source["status"] = "error"
            source["error"] = f"StartQuery: {type(exc).__name__}"
    deadline = time.time() + 20
    gateway = next(s for s in sources if s["key"] == "gateway")
    spans = next(s for s in sources if s["key"] == "spans")
    pending = [s for s in sources if s["status"] == "running"]
    trusted: set[str] = set()
    while pending and time.time() < deadline:
        for source in pending[:]:
            try:
                result = logs.get_query_results(queryId=source["query_id"])
                status = result["status"]
                if status == "Complete":
                    rows = result["results"]
                    if source["key"] == "spans":
                        source["limit_reached"] = len(rows) >= 80
                        # Verify the returned flattened session alias as well as query syntax.
                        # Never derive Gateway authority from a timestamp-only or foreign span.
                        rows = [
                            row
                            for row in rows
                            if any(
                                f.get("field") == "session_id" and f.get("value") == run_id
                                for f in row
                            )
                        ]
                        trusted = {
                            f["value"]
                            for row in rows
                            for f in row
                            if f.get("field") == "traceId"
                            and TRACE_ID.fullmatch(f.get("value", ""))
                        }
                    elif source["key"] == "gateway":
                        source["limit_reached"] = len(rows) >= 50
                        verified = [row for row in rows if gateway_row_trusted(row, trusted)]
                        source["discarded"] = len(rows) - len(verified)
                        rows = verified
                    source["matched"] = len(rows)
                    source["records"] = [
                        record for row in rows if (record := trace_record(source["key"], row))
                    ]
                    source["status"] = "done"
                    if source["key"] == "gateway":
                        reasons = []
                        if source["discarded"]:
                            reasons.append(
                                f"{source['discarded']} unverified Gateway row(s) omitted"
                            )
                        if spans.get("traces_omitted"):
                            reasons.append(
                                f"{spans['traces_omitted']} trace ID(s) beyond the query cap"
                            )
                        if spans.get("limit_reached"):
                            reasons.append("aws/spans reached the 80-row cap")
                        if source["limit_reached"]:
                            reasons.append("Gateway query reached the 50-row cap")
                        if reasons:
                            source["status"] = "partial"
                            source["reason"] = "; ".join(reasons)
                elif status in ("Failed", "Cancelled", "Timeout"):
                    source["status"] = "error"
                    source["error"] = f"Logs Insights: {status}"
                else:
                    continue
            except Exception as exc:  # noqa: BLE001 - no log record/error message in response
                source["status"] = "error"
                source["error"] = f"GetQueryResults: {type(exc).__name__}"
            pending.remove(source)
            if source is spans and gateway["status"] == "waiting for run spans":
                trace_ids = sorted(trusted)
                if source["status"] != "done" or not trace_ids:
                    gateway["status"] = "unavailable"
                    gateway["reason"] = "No verified owned-run aws/spans trace IDs yet; retry later"
                else:
                    selected = trace_ids[:MAX_GATEWAY_TRACES]
                    spans["traces_omitted"] = len(trace_ids) - len(selected)
                    try:
                        gateway["query_id"] = logs.start_query(
                            logGroupNames=gateway["log_groups"],
                            startTime=now - 6 * 3600,
                            endTime=now + 60,
                            queryString=TRACE_QUERIES["gateway"][1].format(
                                trace_filter=gateway_trace_filter(selected)
                            ),
                        )["queryId"]
                        trusted = set(selected)
                        gateway["status"] = "running"
                        pending.append(gateway)
                    except Exception as exc:  # noqa: BLE001 - safe error type only
                        gateway["status"] = "error"
                        gateway["error"] = f"StartQuery: {type(exc).__name__}"
        if pending:
            time.sleep(0.7)
    if gateway["status"] == "waiting for run spans":
        gateway["status"] = "unavailable"
        gateway["reason"] = "Owned-run span query not complete; retry later"
    for source in sources:
        source.pop("query_id", None)
    return {"run_id": run_id, "region": os.environ.get("AWS_REGION"), "sources": sources}


def reset_demo(who: dict[str, str]) -> dict[str, Any]:
    """Back to a clean V0 for this operator's customer: seed tickets only, no stop, no last run."""
    require(who, "operator")
    cid = who["customer_id"]
    owner = runs.uuid7()
    claimed = claim_seed_scope(owner)
    try:
        # The state row may not hold portal_ticket_seq yet. Read all of this tenant's ticket
        # rows consistently while the exclusion is held, and persist the highest ticket number
        # *before* reset removes those rows. Never rewind a larger existing marker.
        tickets = []
        page = {}
        while True:
            found = _tickets.query(
                KeyConditionExpression="customer_id = :c",
                ExpressionAttributeValues={":c": cid},
                ConsistentRead=True,
                **page,
            )
            tickets.extend(found["Items"])
            if "LastEvaluatedKey" not in found:
                break
            page = {"ExclusiveStartKey": found["LastEvaluatedKey"]}
        prefix = cid.split("-")[-1].upper() + "-"
        numbers = [
            int(t["ticket_id"][len(prefix) :])
            for t in tickets
            if t["ticket_id"].startswith(prefix) and t["ticket_id"][len(prefix) :].isdigit()
        ]
        high_water = max(89, int(demo_state(cid).get("portal_ticket_seq", 89)), *numbers)
        _state.update_item(
            Key={"customer_id": cid},
            UpdateExpression="SET portal_ticket_seq = :next",
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={":next": high_water, ":owner": owner},
        )
        for t in tickets:
            if t["ticket_id"] not in SEED.get(cid, {}):
                _tickets.delete_item(Key={"customer_id": cid, "ticket_id": t["ticket_id"]})
        reseed()
        _state.update_item(
            Key={"customer_id": cid},
            UpdateExpression="SET active_stage = :stage, stop_requested = :f, "
            "breaker_open = :f, store_change_source = :src, policy_session = :ps "
            "REMOVE last_run, run_id, before_tickets, confirmed, approvals, capture, "
            "breaker_reason, breaker_pending, breaker_receipt, approval_uncertain, "
            "approval_dispatch, exclusion_disposition, exclusion_reason",
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={
                ":stage": "v0",
                ":f": False,
                ":src": "Clean reset",
                ":ps": runs.uuid7(),
                ":owner": owner,
            },
        )
        complete = True
    finally:
        if locals().get("complete"):
            for tenant in reversed(claimed):
                release_run(tenant, owner)
    return {"reset": True}


def reseed() -> None:
    """Every tenant's seed tickets back to their original text, OPEN and without notes (a run
    can reach another tenant's ticket, so restoring only the operator's own is not enough)."""
    for cid, seed in SEED.items():
        for ticket_id, (subject, text) in seed.items():
            _tickets.put_item(
                Item={
                    "customer_id": cid,
                    "ticket_id": ticket_id,
                    "subject": subject,
                    "body": text,
                    "status": "OPEN",
                    "submitted_by": "customer",
                    "untrusted": True,
                    "origin": "seed",
                }
            )


def reopen(cid: str) -> None:
    """Undo a run's effects but keep the attack: seed tickets restored, portal tickets reopened."""
    reseed()
    for t in tickets_for(cid):
        if t.get("origin") != "seed" and (t.get("status") != "OPEN" or t.get("notes")):
            _tickets.update_item(
                Key={"customer_id": cid, "ticket_id": t["ticket_id"]},
                UpdateExpression="SET #s = :open, notes = :none REMOVE closed_by, closed_reason, closed_at",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":open": "OPEN", ":none": []},
            )


def apply_patch(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """Switch the build (next one, or the one named) and reopen the previous run's effects. The
    captured response, any stop and an open circuit breaker stay: the next build is judged on the
    same response, and a stop or breaker is cleared only on purpose. Approvals do not carry
    over: the build starts a new policy session."""
    require(who, "operator")
    cid = who["customer_id"]
    current = demo_state(cid).get("active_stage", "v0")
    target = str(body.get("stage") or STAGES[min(STAGES.index(current) + 1, len(STAGES) - 1)])
    if target not in STAGES:
        raise HttpError(400, "unknown build")
    owner = runs.uuid7()
    claimed = claim_seed_scope(owner)
    try:
        reopen(cid)
        _state.update_item(
            Key={"customer_id": cid},
            UpdateExpression=(
                "SET active_stage = :t, store_change_source = :src, policy_session = :ps "
                "REMOVE last_run, run_id, before_tickets, confirmed, approvals, "
                "approval_uncertain, approval_dispatch"
            ),
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={
                ":t": target,
                ":src": f"Applied {target.upper()}; effects reopened",
                ":ps": runs.uuid7(),
                ":owner": owner,
            },
        )
        complete = True
    finally:
        if locals().get("complete"):
            for tenant in reversed(claimed):
                release_run(tenant, owner)
    return {"active_stage": target, "title": TITLES[target]}


def request_stop(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    require(who, "operator")
    on = bool(body.get("stop", True))
    _state.update_item(
        Key={"customer_id": who["customer_id"]},
        UpdateExpression="SET stop_requested = :on, stop_source = :src",
        ExpressionAttributeValues={":on": on, ":src": "operator" if on else "none"},
    )
    return {"stop_requested": on}


def reset_breaker(who: dict[str, str]) -> dict[str, Any]:
    """A human closes the circuit breaker; it never closes on its own."""
    require(who, "operator")
    try:
        _state.update_item(
            Key={"customer_id": who["customer_id"]},
            UpdateExpression="SET breaker_open = :f, breaker_reset_by = :by "
            "REMOVE breaker_reason, breaker_pending",
            ConditionExpression="attribute_not_exists(busy_owner) AND "
            "(attribute_not_exists(busy_until) OR busy_until < :now)",
            ExpressionAttributeValues={
                ":f": False,
                ":by": who["username"],
                ":now": int(time.time()),
            },
        )
    except _state.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise HttpError(409, "cannot recover breaker while a run owns exclusion") from exc
    return {"breaker_open": False}


def gateway_call(token: str, url: str, session: str, name: str, args: dict) -> tuple[bool, str]:
    """The console's one bounded MCP grant, initialized in its existing policy session.

    The API package uses only the standard library. ``mcp_grant`` keeps the transport's
    optional MCP-Session-Id distinct from the AgentCore Policy session header; it never
    retries tools/call or accepts a result without exact ticket and verified actor proof.
    """
    return mcp_grant.confirm_close(url, token, session, name, args)


def record_approval(
    who: dict[str, str], token: str, stage: str, card: dict[str, Any], session: str, owner: str
) -> None:
    """V3+: the operator approves closing one of their own open tickets. The approval goes through
    the Gateway as confirm_close (no run_id: only the console may confirm), so AgentCore Policy
    records it in the case's session; the temporal permit for close_ticket looks for exactly this
    event, and allows one close per approval."""
    cid = who["customer_id"]
    ticket_id = card["ticket_id"]
    if (
        STAGES.index(stage) < STAGES.index("v3")
        or card["tool"] != "close_ticket"
        or card["customer_id"] != cid
        or card.get("policy_session") != session
    ):
        raise HttpError(409, "approval does not match this build, tenant or policy session")
    t = _tickets.get_item(Key={"customer_id": cid, "ticket_id": ticket_id}).get("Item")
    if t is None or t.get("status") != "OPEN":
        raise HttpError(400, "only an open ticket of your own customer can be approved")
    url = json.loads(agent_param("gateway_urls", "{}")).get(GATEWAY_OF[stage])
    if not url:
        raise HttpError(503, "the agent is not deployed yet (make deploy-agent)")
    args = {
        "customer_id": cid,
        "ticket_id": ticket_id,
        "actor": {"user_sub": who["sub"], "customer_id": cid},
    }
    # This marker precedes any possibly committed Gateway grant. Even a lost reply cannot
    # open another replay/admission in this session until a durable rotation retires it.
    marker = json.dumps(
        {
            "owner": owner,
            "user_sub": who["sub"],
            "id": card["id"],
            "session": session,
            "tool": card["tool"],
            "ticket_id": ticket_id,
            "reason": card["reason"] or "approved by the operator",
        }
    )
    _state.update_item(
        Key={"customer_id": cid},
        UpdateExpression="SET approval_uncertain = :marker",
        ConditionExpression="busy_owner = :owner AND policy_session = :session "
        "AND attribute_not_exists(approval_uncertain) AND attribute_not_exists(approval_dispatch)",
        ExpressionAttributeValues={":marker": marker, ":owner": owner, ":session": session},
    )
    allowed, text = gateway_call(token, url, session, "TicketTools___confirm_close", args)
    if not allowed:
        raise HttpError(502, f"the Gateway refused the approval: {text[:300]}")
    done = [c for c in confirmations(demo_state(cid)) if c["ticket_id"] != ticket_id] + [
        {
            "ticket_id": ticket_id,
            "by": who["username"],
            "at": int(time.time()),
            "approval_id": card["id"],
            "policy_session": session,
        }
    ]
    _state.update_item(
        Key={"customer_id": cid},
        UpdateExpression="SET confirmed = :c, approval_dispatch = :marker REMOVE approval_uncertain",
        ConditionExpression="busy_owner = :owner AND policy_session = :session "
        "AND approval_uncertain = :marker",
        ExpressionAttributeValues={
            ":c": json.dumps(done),
            ":marker": marker,
            ":owner": owner,
            ":session": session,
        },
    )


def retire_session(
    cid: str, owner: str, card_id: str, executed: bool = False, uncertain: bool = False
) -> None:
    """CAS rotation fences all future dispatch from an uncertain/consumed grant. The old
    Gateway grant may still exist in the retired session; it is not claimed to be revoked."""
    st = demo_state(cid)
    session = st.get("policy_session", "")
    items = [
        {
            **a,
            "status": "APPROVED" if executed else "UNCERTAIN" if uncertain else "FAILED",
            "outcome": (
                "effect observed; session retired"
                if executed
                else "write outcome unresolved; session retired"
                if uncertain
                else "policy session retired"
            ),
            "at": int(time.time()),
        }
        if a["id"] == card_id and a["status"] == "PENDING"
        else {**a, "status": "EXPIRED", "outcome": "policy session retired", "at": int(time.time())}
        if a["status"] == "PENDING" and a.get("policy_session") == session
        else a
        for a in current_approvals(st)
    ]
    _state.update_item(
        Key={"customer_id": cid},
        UpdateExpression="SET policy_session = :fresh, approvals = :cards, confirmed = :empty "
        "REMOVE approval_uncertain, approval_dispatch",
        ConditionExpression="busy_owner = :owner AND policy_session = :session",
        ExpressionAttributeValues={
            ":fresh": runs.uuid7(),
            ":cards": json.dumps(items),
            ":empty": "[]",
            ":owner": owner,
            ":session": session,
        },
    )


def reject_approval(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """The operator declines a held call: nothing goes to the Gateway, and the decision is kept."""
    require(who, "operator")
    st = demo_state(who["customer_id"])
    item = pending_approval(st, str(body.get("id", ""))[:40])
    items = [
        {**a, "status": "REJECTED", "by": who["username"], "at": int(time.time())}
        if a["id"] == item["id"]
        else a
        for a in current_approvals(st)
    ]
    save_approvals(who["customer_id"], items, previous=st.get("approvals", "[]"))
    return {"approvals": items}


def current_eval_dataset() -> dict[str, Any]:
    source_dir = os.path.dirname(__file__)
    packaged = os.path.join(source_dir, "dataset.json")
    # Source-tree tests run without the CodeBuild package; Lambda always uses the local copy.
    path = (
        packaged
        if os.path.isfile(packaged)
        else os.path.join(source_dir, "..", "..", "evals", "dataset.json")
    )
    with open(path) as source:
        return json.load(source)


def current_eval_version() -> str:
    return current_eval_dataset()["version"]


def eval_suites(who: dict[str, str]) -> dict[str, Any]:
    """The latest eval suite per build (V0-V5): status, the gate and every case's samples."""
    require(who, "operator")
    out = {}
    dataset = current_eval_dataset()
    version = dataset["version"]
    for stage in STAGES:
        item = _state.get_item(Key={"customer_id": f"evals#{stage}"}).get("Item")
        record = json.loads(item["suite"]) if item else None
        if record is not None and not isinstance(record, dict):
            record = {"status": "INVALID", "dataset_status": "invalid"}
        elif record:
            # Separate earlier datasets from corrupt current-version rows; never trust a
            # stored green gate unless its full cases recompute under the shared contract.
            record["dataset_status"] = eval_contract.classify_default(record, dataset, stage)
        out[stage] = record
    return {"builds": out, "dataset": version}


def run_eval_suite(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """Start the eval suite on a build (default: the active one). It runs for several minutes in
    its own Lambda, holds the demo while it runs, and leaves it reset."""
    require(who, "operator")
    st = demo_state(who["customer_id"])
    if who["customer_id"] != "customer-a":
        raise HttpError(403, "the eval suite runs as Region A's operator")
    if int(st.get("busy_until", 0)) > time.time():
        raise HttpError(409, "the demo is busy (an agent run or an eval suite)")
    stage = str(body.get("stage") or st.get("active_stage", "v0"))
    if stage not in STAGES:
        raise HttpError(400, "unknown build")
    suite_id = runs.uuid7()
    dataset_version = current_eval_version()
    _state.put_item(
        Item={
            "customer_id": f"evals#{stage}",
            "suite": json.dumps(
                {
                    "suite_id": suite_id,
                    "build": stage,
                    "status": "QUEUED",
                    "dataset": dataset_version,
                    "source_version": dataset_version,
                    "dataset_name": "default",
                    "started": int(time.time()),
                }
            ),
        }
    )
    boto3.client("lambda").invoke(
        FunctionName=os.environ["EVALS_FUNCTION"],
        InvocationType="Event",
        Payload=json.dumps({"build": stage, "suite_id": suite_id}).encode(),
    )
    return {"suite_id": suite_id, "build": stage}


def mark_ticket_mutation_uncertain(customer_id: str, owner: str) -> None:
    """A possibly committed write keeps the exclusion until operator reconciliation."""
    try:
        _state.update_item(
            Key={"customer_id": customer_id},
            UpdateExpression="SET exclusion_disposition = :status, exclusion_reason = :reason",
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={
                ":status": "UNKNOWN",
                ":reason": "portal ticket mutation outcome uncertain",
                ":owner": owner,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the owner itself still excludes new operations
        print(f"portal exclusion reason not written: {type(exc).__name__}")


def delete_portal_ticket(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """Operator-only removal of a tenant's portal submission, not an agent tool or run effect."""
    require(who, "operator")
    ticket_id = body.get("ticket_id") if isinstance(body, dict) else None
    if not isinstance(ticket_id, str) or not TICKET_ID.fullmatch(ticket_id):
        raise HttpError(400, "bad ticket id")
    cid = who["customer_id"]
    owner = runs.uuid7()
    claim_run(cid, owner)
    uncertain = False
    try:
        st = demo_state(cid)
        if (
            st.get("approval_uncertain")
            or st.get("approval_dispatch")
            or st.get("exclusion_disposition") == "UNKNOWN"
            or st.get("breaker_pending")
        ):
            raise HttpError(409, "operator recovery required before deleting a ticket")
        ticket = _tickets.get_item(
            Key={"customer_id": cid, "ticket_id": ticket_id}, ConsistentRead=True
        ).get("Item")
        if not ticket:
            raise HttpError(404, "no such ticket")
        if (
            ticket_id in SEED.get(cid, {})
            or ticket.get("origin") != "form"
            or ticket.get("submitted_by") != "customer"
        ):
            raise HttpError(403, "only customer-submitted portal tickets can be deleted")
        if any(
            a.get("ticket_id") == ticket_id and a.get("status") == "PENDING"
            for a in current_approvals(st)
        ):
            raise HttpError(409, "a pending approval references this ticket")
        # The state row may not hold portal_ticket_seq yet, so take the high-water mark from
        # the live ticket IDs as well, before removing the highest live row. Never reuse an ID
        # still referenced in run history.
        prefix = cid.split("-")[-1].upper() + "-"
        live_numbers = [
            int(t["ticket_id"][len(prefix) :])
            for t in tickets_for(cid)
            if t["ticket_id"].startswith(prefix) and t["ticket_id"][len(prefix) :].isdigit()
        ]
        high_water = max(
            int(st.get("portal_ticket_seq", 89)), int(ticket_id.split("-")[1]), *live_numbers
        )
        # A conditional delete rechecks provenance, even if another writer changed the item.
        uncertain = True
        try:
            _tickets.delete_item(
                Key={"customer_id": cid, "ticket_id": ticket_id},
                ConditionExpression="attribute_exists(ticket_id) AND #origin = :form AND submitted_by = :customer",
                ExpressionAttributeNames={"#origin": "origin"},
                ExpressionAttributeValues={":form": "form", ":customer": "customer"},
            )
        except _tickets.meta.client.exceptions.ConditionalCheckFailedException as exc:
            uncertain = False  # a definite conditional refusal did not delete anything
            raise HttpError(409, "ticket changed before deletion") from exc
        # Retain the prior run/effect/approval history, stage, breaker and policy session.
        # Persist the ID high-water mark and invalidate replay before releasing the owner.
        _state.update_item(
            Key={"customer_id": cid},
            UpdateExpression="SET store_change_source = :source, portal_ticket_seq = :next REMOVE capture",
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={
                ":owner": owner,
                ":source": f"Operator deleted portal ticket {ticket_id}",
                ":next": high_water,
            },
        )
        uncertain = False
        return {"deleted": ticket_id, "source": "operator"}
    finally:
        if uncertain:
            mark_ticket_mutation_uncertain(cid, owner)
        else:
            release_run(cid, owner)


def submit_ticket(who: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    require(who, "customer")
    subject = str(body.get("subject", "")).strip()
    text = str(body.get("body", "")).strip()
    if not 0 < len(subject) <= MAX_SUBJECT or not 0 < len(text) <= MAX_BODY:
        raise HttpError(400, f"subject 1 to {MAX_SUBJECT} and body 1 to {MAX_BODY} characters")
    cid = who["customer_id"]
    owner = runs.uuid7()
    claim_run(cid, owner)
    uncertain = False
    try:
        st = demo_state(cid)
        if (
            st.get("approval_uncertain")
            or st.get("approval_dispatch")
            or st.get("exclusion_disposition") == "UNKNOWN"
            or st.get("breaker_pending")
        ):
            raise HttpError(409, "operator recovery required before submitting a ticket")
        existing = tickets_for(cid)
        prefix = cid.split("-")[-1].upper()
        numbers = [
            int(t["ticket_id"].split("-")[1])
            for t in existing
            if t["ticket_id"].split("-")[1].isdigit()
        ]
        # Never reuse an ID after a deletion: old run/approval history retains its ticket ID.
        next_number = max(89, int(st.get("portal_ticket_seq", 89)), *numbers) + 1
        ticket_id = f"{prefix}-{next_number}"
        _state.update_item(
            Key={"customer_id": cid},
            UpdateExpression="SET portal_ticket_seq = :next",
            ConditionExpression="busy_owner = :owner",
            ExpressionAttributeValues={":next": next_number, ":owner": owner},
        )
        uncertain = True
        _tickets.put_item(
            Item={
                "customer_id": cid,
                "ticket_id": ticket_id,
                "subject": subject,
                "body": text,
                "status": "OPEN",
                "submitted_by": "customer",
                "untrusted": True,
                "origin": "form",
                "created_by": who["username"],
            },
            ConditionExpression="attribute_not_exists(ticket_id)",
        )
        uncertain = False
        return {"ticket": {"ticket_id": ticket_id}}
    finally:
        if uncertain:
            mark_ticket_mutation_uncertain(cid, owner)
        else:
            release_run(cid, owner)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    try:
        who = persona(event)
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 8192:
            raise HttpError(413, "request too large")
        body = json.loads(raw) if raw else {}
        if method == "GET" and path == "/api/me":
            return reply(200, who)
        if method == "GET" and path == "/api/state":
            return reply(200, public_state(who))
        if method == "GET" and path == "/api/code":
            require(who, "operator")
            return reply(200, build_code(demo_state(who["customer_id"]).get("active_stage", "v0")))
        if method == "POST" and path == "/api/portal/tickets":
            return reply(200, submit_ticket(who, body))
        if method == "POST" and path == "/api/tickets/delete":
            return reply(200, delete_portal_ticket(who, body))
        if method == "GET" and path.startswith("/api/runs/") and path.endswith("/events"):
            q = event.get("queryStringParameters") or {}
            return reply(200, run_events(who, path.split("/")[3], str(q.get("after", ""))[:40]))
        if method == "GET" and path == "/api/trace":
            q = event.get("queryStringParameters") or {}
            return reply(200, run_trace(who, str(q.get("run", ""))[:40]))
        if method == "POST" and path == "/api/reset":
            return reply(200, reset_demo(who))
        if method == "POST" and path == "/api/stop":
            return reply(200, request_stop(who, body))
        if method == "POST" and path == "/api/approvals/reject":
            return reply(200, reject_approval(who, body))
        if method == "POST" and path == "/api/breaker/reset":
            return reply(200, reset_breaker(who))
        if method == "GET" and path == "/api/evals":
            return reply(200, eval_suites(who))
        if method == "POST" and path == "/api/evals/run":
            return reply(200, run_eval_suite(who, body))
        if method == "POST" and path == "/api/patch":
            return reply(200, apply_patch(who, body))
        if method == "POST" and path == "/api/run":
            return reply(200, run_agent(who, access_token(event), body))
        raise HttpError(404, f"{method} {path} not found")
    except HttpError as exc:
        return reply(exc.status, {"error": str(exc)})
    except (ValueError, json.JSONDecodeError):
        return reply(400, {"error": "invalid request"})
    except Exception as exc:  # noqa: BLE001 - no false success on ambiguous state/service replies
        print(f"API transition unavailable: {type(exc).__name__}")
        return reply(
            503, {"error": "operation outcome uncertain; check exclusion and store evidence"}
        )


def reply(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(payload, default=str),
    }
