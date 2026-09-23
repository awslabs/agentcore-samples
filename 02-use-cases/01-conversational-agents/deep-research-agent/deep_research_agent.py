"""
Deep Research Agent — Plan → Search → Reflect → Synthesize Loop.

An intelligent research agent powered by Amazon Bedrock AgentCore and Claude Sonnet 4
that answers complex, multi-faceted questions through an iterative research loop:

  1. PLAN   — break the question into prioritised sub-questions
  2. SEARCH — execute the highest-priority unanswered sub-question via WebSearch
  3. REFLECT — assess gaps; if unresolved and iterations remain, go back to SEARCH
  4. SYNTHESIZE — write a comprehensive, cited answer once confident

Single-shot search fails for questions that require comparing multiple sources,
reconciling conflicting information, or drilling into details revealed by earlier
results. This agent makes the reflect-and-refine loop explicit and configurable.

Prerequisites:
    pip install -r requirements.txt

    Gateway + Web Search configuration is handled automatically:
      - If environment variables are set, they are used directly
      - If not, the agent scans for an existing Gateway in your account
      - If none found, it offers to create one interactively

Optional environment variables:
    AGENTCORE_GATEWAY_URL  — Gateway MCP endpoint (auto-detected if missing)
    COGNITO_DOMAIN         — Cognito domain prefix (auto-detected if missing)
    COGNITO_CLIENT_ID      — Cognito app client ID (auto-detected if missing)
    COGNITO_CLIENT_SECRET  — Cognito app client secret (auto-detected if missing)
    COGNITO_SCOPE          — OAuth scope string (auto-detected if missing)
    BEDROCK_MODEL_ID       — Bedrock inference profile ID or ARN
                             (defaults to us.anthropic.claude-sonnet-4-6)
    DEEP_RESEARCH_MAX_ITER — Maximum search iterations per question (default: 4)
    AWS_DEFAULT_REGION     — AWS region (default: us-east-1)

IAM permissions required:
    bedrock:InvokeModel (for Claude Sonnet 4)
    + provisioning permissions if auto-creating Gateway (see gateway_setup.py)

Usage:
    # Run interactively (auto-detects or provisions gateway, prompts for question)
    python deep_research_agent.py

    # Pass a question directly
    python deep_research_agent.py --query "What are the trade-offs between RAG and fine-tuning for enterprise LLMs?"

    # Increase depth for complex comparative questions
    python deep_research_agent.py --query "..." --max-iter 6
"""

import argparse
import logging
import os

import requests
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

from gateway_setup import ensure_gateway

# ── Configuration ──────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
MAX_SEARCH_ITERATIONS = int(os.getenv("DEEP_RESEARCH_MAX_ITER", "4"))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the AgentCore Runtime app
app = BedrockAgentCoreApp()


# ── Auth helpers ───────────────────────────────────────────────────────────────


def get_oauth_token() -> str:
    """Retrieve a fresh OAuth token from Cognito using client_credentials flow."""
    cognito_domain = os.environ.get("COGNITO_DOMAIN", "")
    cognito_client_id = os.environ.get("COGNITO_CLIENT_ID", "")
    cognito_client_secret = os.environ.get("COGNITO_CLIENT_SECRET", "")
    cognito_scope = os.environ.get("COGNITO_SCOPE", "")
    region = os.environ.get("AWS_DEFAULT_REGION", REGION)

    if not all([cognito_domain, cognito_client_id, cognito_client_secret]):
        raise ValueError(
            "Cognito credentials not configured. Run the agent interactively "
            "to auto-provision, or set COGNITO_DOMAIN, COGNITO_CLIENT_ID, "
            "and COGNITO_CLIENT_SECRET environment variables."
        )
    url = f"https://{cognito_domain}.auth.{region}.amazoncognito.com/oauth2/token"
    resp = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": cognito_client_id,
            "client_secret": cognito_client_secret,
            "scope": cognito_scope,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        error_detail = resp.text
        raise RuntimeError(
            f"Authentication failed (HTTP {resp.status_code}). "
            f"Cognito returned: {error_detail}\n"
            f"  Check your COGNITO_CLIENT_ID and COGNITO_CLIENT_SECRET are correct.\n"
            f"  Token URL: {url}"
        )
    return resp.json()["access_token"]


def create_mcp_transport():
    """Create an authenticated MCP Streamable HTTP transport for the Gateway."""
    gateway_url = os.environ.get("AGENTCORE_GATEWAY_URL", "")
    if not gateway_url:
        raise ValueError(
            "Gateway URL not configured. Run the agent interactively to "
            "auto-provision, or set AGENTCORE_GATEWAY_URL environment variable."
        )
    token = get_oauth_token()
    return streamablehttp_client(
        gateway_url,
        headers={"Authorization": f"Bearer {token}"},
    )


# ── System prompt ──────────────────────────────────────────────────────────────


def build_system_prompt(max_iterations: int) -> str:
    """Build the deep-research system prompt with the configured iteration cap.

    Args:
        max_iterations: Maximum number of search-reflect cycles before forcing synthesis.

    Returns:
        System prompt string for the Strands agent.
    """
    return f"""You are a thorough research analyst with access to real-time web search.

RESEARCH LOOP (repeat up to {max_iterations} iterations):

STEP 1 — PLAN:
  Break the question into sub-questions. List them in priority order.
  Identify what you can answer confidently vs. what requires search.

STEP 2 — SEARCH:
  Execute the highest-priority unanswered sub-question as a web search.
  Keep queries under 200 characters.
  After each search, note: "Learned: <key finding>. Remaining gaps: <list>."

STEP 3 — REFLECT:
  Ask yourself: "Do I have enough to answer the original question confidently?"
  - If NO and iterations remain: formulate the next search based on gaps, go to STEP 2
  - If YES or iterations exhausted: go to STEP 4

STEP 4 — SYNTHESIZE:
  Write a comprehensive answer that:
  - Directly addresses the original question
  - Integrates findings from all search rounds
  - Cites URLs for all factual claims
  - Notes any remaining uncertainties

SHOW YOUR WORK: Make the plan, search queries, and reflections visible in your
response. This helps users understand the research process and verify the results.
"""


# ── Agent factory ──────────────────────────────────────────────────────────────


def create_deep_research_agent(max_iterations: int = MAX_SEARCH_ITERATIONS) -> tuple:
    """Create an MCPClient and a Strands deep-research agent.

    Returns the (mcp_client, agent) pair. The caller is responsible for using
    mcp_client as a context manager.

    Args:
        max_iterations: Maximum search-reflect cycles before forcing synthesis.

    Returns:
        Tuple of (MCPClient, Agent).
    """
    mcp_client = MCPClient(create_mcp_transport)

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        logger.info("Discovered %d tool(s) from Gateway", len(tools))

        model = BedrockModel(
            model_id=MODEL_ID,
            region_name=REGION,
            temperature=0.5,
            max_tokens=4096,
        )
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=build_system_prompt(max_iterations),
        )

    return mcp_client, agent


# ── Response extraction ────────────────────────────────────────────────────────


def extract_text(response) -> str:
    """Extract the text content from a Strands agent response."""
    if hasattr(response, "message"):
        parts = []
        for block in response.message.get("content", []):
            if block.get("text"):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(response)


# ── AgentCore Runtime entrypoint ───────────────────────────────────────────────


@app.entrypoint
def deep_research_runtime(payload):
    """AgentCore Runtime handler — receives a payload and returns the research report.

    In runtime mode, environment variables MUST be pre-configured (no interactive
    provisioning). The runtime expects AGENTCORE_GATEWAY_URL and Cognito vars to
    be set in the container environment.

    Args:
        payload: Dict containing the research question under any of:
                 'prompt', 'query', 'message', or 'inputText'.

    Returns:
        str: The full research report with citations.
    """
    query = (
        payload.get("prompt")
        or payload.get("query")
        or payload.get("message")
        or payload.get("inputText")
        or payload.get("input")
    )

    if not query:
        return "No research question provided. Include your question under the 'prompt' key."

    max_iter = int(payload.get("max_iter", MAX_SEARCH_ITERATIONS))
    logger.info("Deep research request: %s (max_iter=%d)", query, max_iter)

    # In runtime mode, ensure gateway config is available (non-interactive)
    ensure_gateway(interactive=False)

    mcp_client = MCPClient(create_mcp_transport)

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        model = BedrockModel(
            model_id=MODEL_ID,
            region_name=REGION,
            temperature=0.5,
            max_tokens=4096,
        )
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=build_system_prompt(max_iter),
        )
        response = agent(query)

    return extract_text(response)


# ── CLI entrypoint ─────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Deep Research Agent — iterative Plan/Search/Reflect/Synthesize loop via AgentCore Gateway Web Search Tool"
        )
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Research question. If omitted, the agent prompts interactively.",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=MAX_SEARCH_ITERATIONS,
        help=(
            f"Maximum search iterations per question (default: {MAX_SEARCH_ITERATIONS}). "
            "Higher values give deeper results at the cost of more WebSearch + LLM calls. "
            "Recommended: 2 (fast), 4 (balanced), 6+ (deep comparative analysis)."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("AgentCore Web Search Tool — Deep Research Agent")
    print("=" * 60)
    print(f"Max search iterations : {args.max_iter}")
    print(f"Model                 : {MODEL_ID}")

    # ── Ensure Gateway is configured (detect / reuse / provision) ──────────
    ensure_gateway(interactive=True)

    print()
    query = args.query
    if not query:
        query = input("Enter your research question: ").strip()
        if not query:
            print("No question provided. Exiting.")
            return

    print(f"\nResearch question: {query}")
    print("-" * 60)
    print("Running iterative research loop...\n")

    mcp_client = MCPClient(create_mcp_transport)

    try:
        with mcp_client:
            tools = mcp_client.list_tools_sync()
            print(f"Discovered {len(tools)} tool(s) from Gateway\n")

            model = BedrockModel(
                model_id=MODEL_ID,
                region_name=REGION,
                temperature=0.5,
                max_tokens=4096,
            )
            agent = Agent(
                model=model,
                tools=tools,
                system_prompt=build_system_prompt(args.max_iter),
            )
            response = agent(query)
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "401" in error_msg or "400" in error_msg:
            print(f"\n❌ Authentication error: {error_msg}")
            print("\n  Possible causes:")
            print("    • COGNITO_CLIENT_SECRET is incorrect or expired")
            print("    • COGNITO_CLIENT_ID does not match the User Pool client")
            print("    • COGNITO_DOMAIN is wrong")
            print("\n  To fix: re-run the Gateway setup or correct your environment variables.")
        elif "initialization" in error_msg.lower():
            print(f"\n❌ Failed to connect to the Gateway: {error_msg}")
            print("\n  Possible causes:")
            print("    • Invalid Cognito credentials (check COGNITO_CLIENT_SECRET)")
            print("    • Gateway URL is unreachable (check AGENTCORE_GATEWAY_URL)")
            print("    • Gateway is not in READY state")
            print("\n  To fix: verify your environment variables and Gateway status.")
        else:
            print(f"\n❌ Error: {error_msg}")
        raise SystemExit(1)

    print("\n" + "=" * 60)
    print("RESEARCH REPORT")
    print("=" * 60)
    print(extract_text(response))
    print("\n" + "=" * 60)
    print("Deep Research Agent demo complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
