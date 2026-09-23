"""
MCP (Model Context Protocol) Integration with AgentCore Harness.

Demonstrates how to connect Harness agents to external MCP servers:
  Part 1: Basic MCP integration with Exa search
  Part 2: Multiple MCP tools in one invocation
  Part 3: MCP with authentication (env var pattern)
  Part 4: Error handling for MCP calls
  Part 5: Advanced research assistant using MCP + file operations

Usage:
    python mcp_integration.py

Prerequisites:
    - AWS CLI configured with credentials
    - pip install -r ../../requirements.txt
    - AWS_DEFAULT_REGION environment variable set
    - (Optional) MCP_API_KEY environment variable for authenticated examples
"""

import copy
import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
import botocore.exceptions

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import create_harness_role, delete_harness_role

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"

# ── Setup ─────────────────────────────────────────────────────────────────────
control = get_agentcore_control_client()
client = get_agentcore_client()

account_id = boto3.client("sts").get_caller_identity()["Account"]
print(f"Account: {account_id}")

# ── Create IAM Role & Harness ─────────────────────────────────────────────────
# Role creation and harness creation both run inside the try/finally so the
# cleanup below fires even when a step in between fails — otherwise a
# CreateHarness failure (an unavailable model, a role that has not finished
# propagating) would exit leaving the role behind, and the role name is shared
# by every sample in this folder.
print("\n=== Setup: IAM Role & Harness ===")
harness_id = None

try:
    role_arn = create_harness_role()
    print(f"Execution Role ARN: {role_arn}")
    print("Waiting for IAM propagation...")
    time.sleep(10)

    HARNESS_NAME = f"MCPIntegration_{uuid.uuid4().hex[:8]}"
    resp = control.create_harness(harnessName=HARNESS_NAME, executionRoleArn=role_arn)
    harness = resp["harness"]
    harness_id = harness["harnessId"]
    harness_arn = harness["arn"]
    print(f"Harness ID:  {harness_id}")
    print(f"Harness ARN: {harness_arn}")

    poll_harness_status(control, harness_id)
    print("✅ Harness is ready")

    # ── Helper: stream harness response ──────────────────────────────────────────

    def stream_invoke(session_id, prompt, tools, timeout=300):
        """Invoke harness with MCP tools and stream the response."""
        response = client.invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            tools=tools,
            model={"bedrockModelConfig": {"modelId": MODEL_ID}},
            timeoutSeconds=timeout,
        )
        result = {"text": "", "tool_uses": [], "errors": []}
        # A mid-stream failure does not arrive as an event this loop can match on.
        # internalServerException, validationException and runtimeClientError are
        # modelled with `exception: true`, so botocore marks the frame 400 and raises
        # EventStreamError out of the iterator instead of yielding a dict (see
        # botocore/eventstream.py, EventStream._parse_event). Without a catch here a
        # single failed tool call aborts the whole demo at Part 1 and the reader never
        # sees Parts 2-5 — Part 4 deliberately triggers exactly that failure. Record
        # it and let the demo go on.
        try:
            for event in response["stream"]:
                if "contentBlockStart" in event:
                    start = event["contentBlockStart"].get("start", {})
                    if "toolUse" in start:
                        tool_name = start["toolUse"].get("name", "?")
                        result["tool_uses"].append(tool_name)
                        print(f"\n[Tool: {tool_name}]", flush=True)
                elif "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        result["text"] += delta["text"]
                        print(delta["text"], end="", flush=True)
                elif "messageStop" in event:
                    print("\n")
        except (botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as e:
            # Both base classes are needed, and neither covers the other:
            # EventStreamError subclasses ClientError, while transport failures such as
            # ReadTimeoutError subclass BotoCoreError. Part 4 below relies on this —
            # an invalid MCP URL surfaces as EventStreamError.
            result["errors"].append(str(e))
            print(f"\n❌ Stream error: {type(e).__name__}: {e}")
        return result

    # ── Part 1: Basic MCP Integration — Exa Search ────────────────────────────────
    print("\n=== Part 1: Basic MCP Integration — Exa Search ===")
    session1 = str(uuid.uuid4()).upper()
    print(f"Session: {session1}\n")

    result = stream_invoke(
        session_id=session1,
        prompt=(
            "Search for the latest developments in quantum computing in 2024. "
            "Find 3-5 recent articles and summarize the key breakthroughs."
        ),
        tools=[
            {
                "type": "remote_mcp",
                "name": "exa",
                "config": {"remoteMcp": {"url": EXA_MCP_URL}},
            }
        ],
    )
    print(f"Tools used: {result['tool_uses']}")

    # ── Part 2: Multiple MCP Tools ────────────────────────────────────────────────
    print("\n=== Part 2: Multiple MCP Tools ===")
    session2 = str(uuid.uuid4()).upper()
    print(f"Session: {session2}\n")

    result = stream_invoke(
        session_id=session2,
        prompt=(
            "Compare search results from different sources about 'AWS re:Invent 2024 announcements'. "
            "What were the major announcements?"
        ),
        tools=[
            {
                "type": "remote_mcp",
                "name": "exa_search",
                "config": {"remoteMcp": {"url": EXA_MCP_URL}},
            },
            # Add other MCP servers here as needed:
            # {
            #     "type": "remote_mcp",
            #     "name": "brave_search",
            #     "config": {"remoteMcp": {"url": "https://mcp.brave.com/api"}},
            # },
        ],
    )
    print(f"Tools used: {result['tool_uses']}")

    # ── Part 3: MCP with Authentication ──────────────────────────────────────────
    print("\n=== Part 3: MCP with Authentication (env var pattern) ===")
    api_key = os.getenv("MCP_API_KEY", "")

    if api_key:
        # `headers` is a real member of remoteMcp (a map of string to string, for
        # "custom headers to include when connecting to the remote MCP server"), so
        # pass the key rather than leaving the line commented out — a config printed
        # with no credential in it under the heading "configured with the API key" is
        # the one thing this part exists to show.
        # Which header to use is the server's choice, not AgentCore's: bearer tokens go
        # in Authorization, while many MCP servers want x-api-key instead. Check your
        # server's docs and adjust the name below.
        tools_config = [
            {
                "type": "remote_mcp",
                "name": "authenticated_search",
                "config": {
                    "remoteMcp": {
                        "url": EXA_MCP_URL,
                        "headers": {"Authorization": f"Bearer {api_key}"},
                    }
                },
            }
        ]
        # Never echo the key itself, not even a prefix — sample output routinely
        # ends up in terminal scrollback, CI logs and screenshots. Confirm that
        # the variable was picked up and stop there.
        #
        # That applies to the config dump below too, now that it carries a real
        # credential. Print a deep copy with every header value replaced — the shape
        # of the request stays visible, the secret does not leave the process. Every
        # value is masked regardless of header name, so switching to x-api-key below
        # needs no matching change here.
        redacted = copy.deepcopy(tools_config)
        redacted_headers = redacted[0]["config"]["remoteMcp"]["headers"]
        for header_name in redacted_headers:
            redacted_headers[header_name] = "<redacted>"
        print(f"✅ MCP configured with the API key from MCP_API_KEY ({len(api_key)} chars, value not logged)")
        print(f"Tool config: {json.dumps(redacted, indent=2)}")
    else:
        print("⚠️  No MCP_API_KEY found. Skipping authenticated example.")
        print("   Set it with: export MCP_API_KEY='your-key-here'")

    # ── Part 4: Error Handling ────────────────────────────────────────────────────
    print("\n=== Part 4: Error Handling — Invalid MCP URL ===")
    session4 = str(uuid.uuid4()).upper()
    print(f"Testing with invalid MCP URL (session: {session4})\n")

    try:
        result = stream_invoke(
            session_id=session4,
            prompt="Search for 'test query'",
            tools=[
                {
                    "type": "remote_mcp",
                    "name": "invalid",
                    "config": {"remoteMcp": {"url": "https://invalid-mcp-url.example.com/mcp"}},
                }
            ],
            timeout=60,
        )
        # This is where the expected failure lands now. stream_invoke records a
        # mid-stream error instead of letting it abort the demo, so a bad URL comes
        # back as an entry in result["errors"] with no answer text — which is also
        # what makes the count below worth printing. Before, the exception escaped
        # stream_invoke and this line was unreachable: every real run went to the
        # except clause, so `Errors encountered:` could only ever have printed 0.
        if result["errors"]:
            print(f"✅ Expected error for invalid MCP URL ({len(result['errors'])}):")
            for err in result["errors"]:
                print(f"   {err[:200]}")
        else:
            print("⚠️  No error surfaced — the invalid URL was accepted. Response:")
            print(f"   {result['text'][:200]}")
    except Exception as e:  # noqa: BLE001 - demo deliberately surfaces any error type
        # Still a live path, just not the usual one: invoke_harness can reject the
        # request before the stream opens (a malformed tool config, a throttle), and
        # that raises out of the call itself rather than out of the iterator.
        print(f"✅ Expected error for invalid MCP URL: {type(e).__name__}")
        print(f"   {str(e)[:200]}")

    # ── Part 5: Advanced — Research Assistant ────────────────────────────────────
    print("\n=== Part 5: Advanced — Research Assistant ===")
    research_session = str(uuid.uuid4()).upper()
    print(f"Research session: {research_session}\n")

    research_prompt = """
    Research topic: "Generative AI trends in enterprise adoption for 2024"

    Please:
    1. Search for recent articles and reports about enterprise AI adoption
    2. Identify the top 5 trends
    3. For each trend, provide:
       - Brief description
       - Key statistics or data points
       - Notable companies or use cases
    4. Save the report as a structured JSON file at /tmp/ai_trends_report.json

    Format the JSON as:
    {
      "topic": "...",
      "date": "...",
      "trends": [{"name": "...", "description": "...", "statistics": [...], "examples": [...]}],
      "sources": [...]
    }
    """

    result = stream_invoke(
        session_id=research_session,
        prompt=research_prompt,
        tools=[
            {
                "type": "remote_mcp",
                "name": "exa",
                "config": {"remoteMcp": {"url": EXA_MCP_URL}},
            }
        ],
        timeout=300,
    )

    # Retrieve the report from the agent's VM.
    # `cat ... 2>/dev/null || echo '{}'` used to hide the only failure that matters
    # here: if the agent never wrote the file, cat's error was discarded and the
    # fallback emitted `{}`, which is valid JSON. json.loads then succeeded, the
    # JSONDecodeError branch never ran, and the sample printed "Research Report
    # Generated:" followed by an empty object — a green tick for a report that does
    # not exist. Ask for the file plainly instead and keep both halves of the answer:
    # stderr says why it is missing, and the exit code says whether to trust stdout.
    print("\n--- Retrieving research report from VM ---")
    report_data = ""
    report_stderr = ""
    report_exit_code = None
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=research_session,
        body={"command": "cat /tmp/ai_trends_report.json"},
    )
    for event in resp["stream"]:
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                if "stdout" in delta:
                    report_data += delta["stdout"]
                if "stderr" in delta:
                    report_stderr += delta["stderr"]
            elif "contentStop" in chunk:
                report_exit_code = chunk["contentStop"].get("exitCode")

    if report_exit_code:
        # Non-zero: the file is missing or unreadable. Common and not fatal — the
        # agent may have answered in prose without writing the file it was asked for.
        print(f"⚠️  Report file not retrieved (exit {report_exit_code}).")
        if report_stderr.strip():
            print(f"   {report_stderr.strip()}")
        print("   The agent may have answered inline instead of writing the file.")
    else:
        try:
            report = json.loads(report_data)
            print("✅ Research Report Generated:")
            print(json.dumps(report, indent=2)[:2000])  # show first 2000 chars
        except json.JSONDecodeError as e:
            print(f"⚠️  Report file retrieved but is not valid JSON: {e}")
            print(f"Raw output (first 500 chars): {report_data[:500]}")

finally:
    # ── Cleanup ────────────────────────────────────────────────────────────────────
    # Delete each resource independently: harness_id is None if CreateHarness
    # itself failed, and a delete_harness error must not skip the role deletion —
    # a leftover role is what makes the next run of any sample in this folder
    # behave unpredictably, since they all share one role name.
    print("\n=== Cleanup ===")
    if harness_id:
        try:
            control.delete_harness(harnessId=harness_id)
            print(f"Deleted harness: {harness_id}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"Warning: could not delete harness {harness_id}: {e}")

    delete_harness_role()
    print("Done.")
