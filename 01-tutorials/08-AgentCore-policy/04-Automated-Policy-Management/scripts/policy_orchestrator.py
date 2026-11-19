"""
Policy Orchestration Agent for Automated Policy Management.

The agent uses Strands with a set of purpose-built tools that collectively
automate the end-to-end policy lifecycle:

  1. list_gateway_tools          – discover tools exposed on the Gateway
  2. get_tool_rbac_permissions   – read RBAC requirements from the manifest
  3. generate_nl_policy_statement – convert RBAC -> natural language
  4. generate_cedar_policy        – call NL2Cedar to produce Cedar syntax
  5. request_human_approval       – notify reviewer via SNS, wait for decision
  6. create_policy_in_engine      – commit approved policy to the Policy Engine

A module-level config dict (_CTX) is populated by configure_orchestrator()
before the agent is created, so tool functions can reference it via closure.
"""

import json
import logging
import time

import boto3
import requests
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient

from cedar_utils import rbac_to_natural_language, generate_cedar_from_nl
from human_review import build_review_notification, send_policy_for_review

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level context populated before agent creation
# ---------------------------------------------------------------------------
_CTX: dict = {}


def configure_orchestrator(
    config: dict,
    policy_engine_id: str,
    sns_topic_arn: str,
    region: str,
) -> None:
    """Populate module-level context used by all tool functions."""
    _CTX.update(
        {
            "config": config,
            "policy_engine_id": policy_engine_id,
            "sns_topic_arn": sns_topic_arn,
            "region": region,
            "gateway_arn": config["gateway"]["gateway_arn"],
            "gateway_url": config["gateway"]["gateway_url"],
            "client_info": config["gateway"]["client_info"],
            "rbac_manifest": config.get("rbac_manifest", {}),
            # Stores generated Cedar statements keyed by action_key
            # (avoids passing Cedar text between tools, which is error-prone)
            "generated_cedar": {},
            # Tracks approved Cedar statements keyed by action_key
            "approved_policies": {},
        }
    )


# ---------------------------------------------------------------------------
# Helper: fetch OAuth token
# ---------------------------------------------------------------------------


def _fetch_token() -> str:
    ci = _CTX["client_info"]
    resp = requests.post(
        ci["token_endpoint"],
        data=(
            f"grant_type=client_credentials"
            f"&client_id={ci['client_id']}"
            f"&client_secret={ci['client_secret']}"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Strands tools
# ---------------------------------------------------------------------------


@tool
def list_gateway_tools() -> str:
    """
    List all tools currently exposed on the AgentCore Gateway.
    Returns a JSON array with tool names and descriptions.
    """
    try:
        token = _fetch_token()
        url = _CTX["gateway_url"]
        mcp_client = MCPClient(
            lambda: streamablehttp_client(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        )
        with mcp_client:
            tools = mcp_client.list_tools_sync()
            result = [
                {
                    "tool_name": t.tool_name,
                    "description": getattr(t, "description", ""),
                }
                for t in tools
            ]
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_tool_rbac_permissions(action_key: str) -> str:
    """
    Retrieve the RBAC permission manifest emitted when a tool was attached
    to the Gateway. The manifest describes required roles, data classification,
    and parameter-level constraints.

    Args:
        action_key: Full action key in the form
                    "<TargetName>___<function_name>",
                    e.g. "FinancialReportTarget___get_financial_report"
    """
    manifest = _CTX.get("rbac_manifest", {})
    entry = manifest.get(action_key)
    if entry:
        return json.dumps(entry, indent=2)
    # Try a fuzzy match on function name
    for key, val in manifest.items():
        if action_key in key or key in action_key:
            return json.dumps(val, indent=2)
    return json.dumps(
        {
            "error": f"No RBAC manifest entry found for action_key: {action_key}",
            "available_keys": list(manifest.keys()),
        }
    )


@tool
def generate_nl_policy_statement(action_key: str) -> str:
    """
    Convert the RBAC manifest for a tool into a natural language policy
    statement ready for NL2Cedar processing.

    Args:
        action_key: Full action key, e.g.
                    "FinancialReportTarget___get_financial_report"
    """
    manifest = _CTX.get("rbac_manifest", {})
    entry = manifest.get(action_key)
    if not entry:
        for key, val in manifest.items():
            if action_key in key or key in action_key:
                entry = val
                action_key = key
                break

    if not entry:
        return json.dumps({"error": f"No manifest entry for: {action_key}"})

    nl = rbac_to_natural_language(action_key, entry)
    return json.dumps({"action_key": action_key, "nl_statement": nl})


@tool
def generate_cedar_policy(action_key: str, nl_statement: str) -> str:
    """
    Use the AgentCore NL2Cedar API to generate a Cedar policy statement
    from a natural language description. The Cedar statement is stored
    internally and referenced by action_key in subsequent steps.

    IMPORTANT: This call takes ~20 seconds. Do NOT call it again for the same
    action_key if it has already been called — use the cached result instead.
    Call request_human_approval(action_key) immediately after this returns.

    Args:
        action_key: The tool action key this policy covers.
        nl_statement: Natural language description of the access rule.
    """
    # Return cached Cedar immediately if already generated for this action_key
    cached = _CTX.get("generated_cedar", {}).get(action_key)
    if cached:
        return json.dumps(
            {
                "action_key": action_key,
                "cedar_statement": cached,
                "status": "generated (cached)",
                "next_step": "Call request_human_approval with this action_key",
            }
        )

    try:
        policy_client = PolicyClient(region_name=_CTX["region"])
        cedar_stmt = generate_cedar_from_nl(
            nl_statement=nl_statement,
            policy_engine_id=_CTX["policy_engine_id"],
            gateway_arn=_CTX["gateway_arn"],
            policy_client=policy_client,
            policy_name=f"gen_{int(time.time())}",
        )
        if cedar_stmt:
            print(f"\n[CEDAR GENERATED for {action_key}]\n{cedar_stmt}\n")
            # Store in context so downstream tools can read it by action_key
            _CTX["generated_cedar"][action_key] = cedar_stmt
            return json.dumps(
                {
                    "action_key": action_key,
                    "cedar_statement": cedar_stmt,
                    "status": "generated",
                    "next_step": "Call request_human_approval with this action_key",
                }
            )
        err_msg = (
            "NL2Cedar API returned no Cedar policy. Do not retry — move to next tool."
        )
        print(f"\n[CEDAR ERROR for {action_key}] {err_msg}\n")
        return json.dumps({"error": err_msg, "action_key": action_key})
    except Exception as e:
        err_msg = f"Cedar generation exception: {e}"
        print(f"\n[CEDAR EXCEPTION for {action_key}] {err_msg}\n")
        return json.dumps(
            {
                "error": f"{err_msg}. Do not retry.",
                "action_key": action_key,
            }
        )


@tool
def request_human_approval(action_key: str) -> str:
    """
    Present the previously generated Cedar policy for human review via an SNS
    notification and an interactive prompt. Must be called after
    generate_cedar_policy has been called for this action_key.

    The SNS notification emails the policy details to any subscribed reviewers.
    The interactive prompt pauses execution until the human decides.

    Args:
        action_key: The tool action key whose generated Cedar policy needs review.
    """
    # Read the Cedar statement that was stored by generate_cedar_policy
    cedar_statement = _CTX.get("generated_cedar", {}).get(action_key)
    if not cedar_statement:
        return json.dumps(
            {
                "error": (
                    f"No generated Cedar policy found for '{action_key}'. "
                    "Call generate_cedar_policy first."
                )
            }
        )

    manifest = _CTX.get("rbac_manifest", {})
    manifest_entry = manifest.get(action_key, {})
    nl_statement = rbac_to_natural_language(action_key, manifest_entry)

    # Send SNS notification to any subscribed reviewers
    topic_arn = _CTX.get("sns_topic_arn")
    if topic_arn:
        try:
            send_policy_for_review(
                region=_CTX["region"],
                topic_arn=topic_arn,
                tool_action_key=action_key,
                nl_statement=nl_statement,
                cedar_statement=cedar_statement,
                manifest_entry=manifest_entry,
            )
        except Exception as e:
            logger.warning("Could not send SNS notification: %s", e)

    # Display policy for inline review
    review_text = build_review_notification(
        action_key, nl_statement, cedar_statement, manifest_entry
    )
    print("\n" + review_text)

    # Interactive approval
    while True:
        decision = (
            input(f"\nApprove policy for '{action_key}'? [yes/no]: ").strip().lower()
        )
        if decision in ("yes", "y"):
            # Store approved policy in context — create_policy_in_engine reads it
            _CTX["approved_policies"][action_key] = cedar_statement
            return json.dumps(
                {
                    "action_key": action_key,
                    "decision": "approved",
                    "message": (
                        "Policy approved by human reviewer. "
                        "Call create_policy_in_engine with this action_key."
                    ),
                }
            )
        elif decision in ("no", "n"):
            return json.dumps(
                {
                    "action_key": action_key,
                    "decision": "rejected",
                    "message": "Policy rejected by human reviewer. Policy will not be created.",
                }
            )
        else:
            print("Please enter 'yes' or 'no'.")


@tool
def create_policy_in_engine(action_key: str) -> str:
    """
    Create the approved Cedar policy in the Policy Engine.

    Only call this after request_human_approval has returned 'approved'
    for the given action_key. The Cedar statement is read from internal
    context — do not pass it as a parameter.

    Args:
        action_key: The tool action key whose approved policy should be created.
    """
    # Verify the policy was approved and read the Cedar statement from context
    approved = _CTX.get("approved_policies", {})
    if action_key not in approved:
        return json.dumps(
            {
                "error": (
                    f"Policy for '{action_key}' has not been approved yet. "
                    "Call request_human_approval first."
                )
            }
        )
    cedar_statement = approved[action_key]

    try:
        boto_client = boto3.client(
            "bedrock-agentcore-control", region_name=_CTX["region"]
        )
        policy_engine_id = _CTX["policy_engine_id"]

        # Derive a short policy name from the action key
        safe_name = action_key.replace("___", "_").replace("-", "_").lower()[:60]
        policy_name = f"auto_{safe_name}"

        resp = boto_client.create_policy(
            policyEngineId=policy_engine_id,
            name=policy_name,
            description=f"Auto-generated policy for {action_key} (human-approved)",
            definition={"cedar": {"statement": cedar_statement}},
        )
        policy_id = resp.get("policyId", "unknown")
        # Status starts as CREATING; it becomes ACTIVE within a few seconds
        print(f"   Policy created (status: CREATING → ACTIVE): {policy_id}")

        return json.dumps(
            {
                "action_key": action_key,
                "policy_id": policy_id,
                "policy_name": policy_name,
                "status": "created",
                "message": f"Cedar policy successfully created in Policy Engine with ID: {policy_id}",
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e), "action_key": action_key})


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

ORCHESTRATOR_SYSTEM_PROMPT = """You are an automated policy management agent for Amazon Bedrock AgentCore.

Your mission: ensure every tool on the Gateway has a Cedar access-control policy,
with human approval before any policy is committed.

CRITICAL RULES:
- Process ONE tool at a time, FULLY completing all steps before moving to the next.
- NEVER call multiple tools in parallel or in the same turn.
- NEVER retry generate_cedar_policy — it uses a remote API that takes ~20 seconds.
  If it returns "generated" or "cached", proceed to the next step immediately.
- Do NOT pass Cedar text between tools. Tools communicate via action_key only.

EXACT SEQUENTIAL WORKFLOW — complete all 6 steps for tool 1, then all 6 for tool 2:

Step 1. list_gateway_tools()
  → Note every tool_name containing "Target___". Skip "x_amz_bedrock_agentcore_search".

For EACH tool (one at a time):

Step 2. get_tool_rbac_permissions(action_key=<tool_name>)

Step 3. generate_nl_policy_statement(action_key=<tool_name>)
  → Extract "nl_statement" from the response.

Step 4. generate_cedar_policy(action_key=<tool_name>, nl_statement=<nl_statement>)
  → This takes ~20 seconds. Wait for it. Do NOT retry. Cedar is stored internally.

Step 5. request_human_approval(action_key=<tool_name>)
  → Waits for human input. Read the "decision" field in the response.

Step 6a. If decision == "approved": create_policy_in_engine(action_key=<tool_name>)
Step 6b. If decision == "rejected": skip creation, move to next tool.

After ALL tools are processed, output a summary:
- Policies generated, approved/created, rejected, errors."""


def create_policy_orchestrator_agent(model_id: str = "amazon.nova-lite-v1:0") -> Agent:
    """
    Create and return a Strands Agent equipped with all policy orchestration tools.
    configure_orchestrator() must be called before this.
    """
    model = BedrockModel(model_id=model_id, streaming=True)
    agent = Agent(
        model=model,
        tools=[
            list_gateway_tools,
            get_tool_rbac_permissions,
            generate_nl_policy_statement,
            generate_cedar_policy,
            request_human_approval,
            create_policy_in_engine,
        ],
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
    )
    return agent
