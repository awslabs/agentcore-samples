"""Elicitation and sampling over MCP 2026-07-28 multi round-trip requests (MRTR).

Part 1 sends each round with boto3, on a new runtime session each time, so every round reaches a
different microVM. Part 2 makes the same calls with the MCP Python SDK v2 client, which runs the
rounds for you. Sampling requests are answered by Claude Haiku 5.5 on Amazon Bedrock.

Usage: python invoke.py [--interactive]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import boto3

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "helpers"))
from deploy_utils import load_config
from dynamo_utils import FinanceDB
from mcp_client import LATEST, McpClient, new_session_id, runtime_url, sdk_http_client, show

MODEL = "us.anthropic.claude-haiku-5-5"
USER = "alice"
# A 2026-07-28 client declares on every request what it can answer.
CAPABILITIES = {"elicitation": {"form": {}}, "sampling": {}}
INTERACTIVE = False


def answer_form(message: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Answer an elicitation form, from the keyboard with --interactive, otherwise with fixed answers."""
    fields = schema["properties"]
    print(f"   server asks: {message!r}  fields: {list(fields)}")
    if INTERACTIVE:
        typed = {"number": float, "boolean": lambda v: v.lower().startswith("y")}
        return {
            name: typed.get(f.get("type"), str)(input(f"   {f.get('description', name)}: "))
            for name, f in fields.items()
        }
    if "confirm" in fields:
        return {"confirm": True}
    return {"amount": 18.75, "description": "Taxi to the airport", "category": "transport"}


def complete_with_bedrock(params: dict[str, Any], region: str) -> dict[str, Any]:
    """Answer a sampling/createMessage request with the client's own model."""
    messages = [{"role": m["role"], "content": [{"text": m["content"]["text"]}]} for m in params["messages"]]
    response = boto3.client("bedrock-runtime", region_name=region).converse(
        modelId=MODEL,
        messages=messages,
        system=[{"text": params["systemPrompt"]}] if params.get("systemPrompt") else [],
        inferenceConfig={"maxTokens": params.get("maxTokens", 300)},
        # The server's maxTokens is small; with thinking on, the reasoning would use most of it.
        additionalModelRequestFields={"thinking": {"type": "disabled"}},
    )
    text = "".join(block["text"] for block in response["output"]["message"]["content"] if "text" in block)
    stop = "maxTokens" if response["stopReason"] == "max_tokens" else "endTurn"
    return {"role": "assistant", "content": {"type": "text", "text": text}, "model": MODEL, "stopReason": stop}


def call_with_rounds(arn: str, region: str, tool: str, arguments: dict[str, Any]) -> None:
    """Call a tool, answering each input_required round, with a new runtime session per round."""
    retry: dict[str, Any] = {}
    for round_number in range(1, 6):
        mcp = McpClient(arn, region, session_id=new_session_id(), capabilities=CAPABILITIES)
        reply = mcp.call_tool(tool, arguments, **retry)
        if reply.result.get("resultType") != "input_required":
            show(f"round {round_number}: result", reply)
            return
        requests = reply.result["inputRequests"]
        print(f"── round {round_number}: input_required  {[r['method'] for r in requests.values()]}")
        responses = {}
        for key, request in requests.items():
            if request["method"] == "elicitation/create":
                content = answer_form(request["params"]["message"], request["params"]["requestedSchema"])
                responses[key] = {"action": "accept", "content": content}
            elif request["method"] == "sampling/createMessage":
                print(f"   server asks the client's LLM: {request['params']['messages'][0]['content']['text'][:120]}…")
                responses[key] = complete_with_bedrock(request["params"], region)
        # The retry repeats the call with the answers and the sealed requestState, on a new session.
        retry = {"inputResponses": responses, "requestState": reply.result["requestState"]}
    sys.exit("too many rounds")


async def with_sdk_client(arn: str, region: str) -> None:
    from mcp import types
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async def on_elicitation(context: Any, params: types.ElicitRequestParams) -> types.ElicitResult:
        return types.ElicitResult(action="accept", content=answer_form(params.message, params.requested_schema))

    async def on_sampling(context: Any, params: types.CreateMessageRequestParams) -> types.CreateMessageResult:
        result = complete_with_bedrock(params.model_dump(by_alias=True, exclude_none=True), region)
        return types.CreateMessageResult.model_validate(result)

    http = sdk_http_client(region, new_session_id())
    transport = streamable_http_client(runtime_url(arn, region), http_client=http, terminate_on_close=False)
    async with (
        http,
        Client(transport, mode=LATEST, elicitation_callback=on_elicitation, sampling_callback=on_sampling) as client,
    ):
        result = await client.call_tool("add_expense_interactive", {"user_alias": USER})
        print(f"── SDK add_expense_interactive\n   {json.dumps(result.structured_content)}")
        result = await client.call_tool("analyze_spending", {"user_alias": USER})
        print(f"── SDK analyze_spending\n   {json.dumps(result.structured_content)[:400]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interactive", action="store_true", help="type the answers to the forms yourself")
    INTERACTIVE = parser.parse_args().interactive
    config = load_config(HERE)
    arn, region = config["runtime_arn"], config["region"]

    FinanceDB(config["table"]).clear_user(USER)  # start every run from the same data
    seed = McpClient(arn, region, session_id=new_session_id())
    seed.call_tool("set_budget", {"user_alias": USER, "category": "food", "monthly_limit": 200})
    seed.call_tool("add_expense", {"user_alias": USER, "amount": 230, "description": "Groceries", "category": "food"})

    print("═══ Part 1: MRTR rounds with boto3, a new session (and microVM) per round ═══")
    print("\nElicitation: add_expense_interactive")
    call_with_rounds(arn, region, "add_expense_interactive", {"user_alias": USER})
    print("\nSampling (deprecated): analyze_spending")
    call_with_rounds(arn, region, "analyze_spending", {"user_alias": USER})

    print("\n═══ Part 2: the MCP Python SDK v2 client runs the rounds for you ═══")
    asyncio.run(with_sdk_client(arn, region))
