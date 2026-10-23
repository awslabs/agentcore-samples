"""Step 5 (consumer): a Strands agent finds the tool through the registry's MCP endpoint.

Every registry has an MCP endpoint, https://agent-registry.<region>.api.aws/registry/<id>/mcp,
that offers its search as MCP tools (search_discoverable_registry_records,
list_discoverable_registry_records, batch_get_discoverable_registry_record). The agent connects
with the consumer role's credentials, signed with SigV4 (service agent-registry) by
mcp-proxy-for-aws; the role needs agent-registry:InvokeRegistryMcp. A Claude model on Amazon
Bedrock (MODEL_ID) reads the request and decides which tool calls to make and which custom metadata
filters to send. The script prints every tool call, a short summary of each result, and the
answer, and fails unless the answer names payments-ledger-mcp.

Any MCP client, for example Kiro with mcp-proxy-for-aws, can use the same endpoint.
"""

import json

from common import MODEL_ID, RECORD_NAME, REGION, need, session_for
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient

SYSTEM_PROMPT = """You help developers find approved tools in an AWS Agent Registry.
Always call search_discoverable_registry_records, and put every constraint the user states in its
`filter` argument, using these fields:
- customMetadata.environment: prod, staging or dev
- customMetadata.businessUnit: for example payments or marketing
- customMetadata.securityScanStatus: PENDING, PASSED or FAILED
- customMetadata.dataClassification: public, internal, confidential or restricted
Combine conditions with $and, for example
{"$and": [{"customMetadata.environment": {"$eq": "prod"}}, {"customMetadata.businessUnit": {"$eq": "payments"}}]}.
Answer with the record name, its description and the metadata that matches the request. If it
helps, run the same search once without the filter and say which field rules out each near miss."""
PROMPT = "Find a production MCP server for payments ledger lookups that passed its security scan."
SHOWN = ("environment", "businessUnit", "securityScanStatus", "dataClassification")

registry_id = need("registry_id", "01_define_schema")
session = session_for("consumer")
endpoint = f"https://agent-registry.{REGION}.api.aws/registry/{registry_id}/mcp"


def print_tool_call(event):
    print(f"tool call: {event.tool_use['name']} {json.dumps(event.tool_use['input'])}")


def summarize(record):
    m = record.get("customMetadata")
    return record["name"] + (f" ({', '.join(f'{k}={m.get(k)}' for k in SHOWN)})" if m else "")


def print_tool_result(event):
    text = "".join(c.get("text", "") for c in event.result["content"])
    if event.result["status"] == "error":
        print(f"tool error: {text}")
        return
    # Search returns a JSON array of records, list and batch_get return {"registryRecords": [...]},
    # and a search with no matches returns plain text.
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"tool result: {text}")
        return
    records = data if isinstance(data, list) else data.get("registryRecords", [])
    print(f"tool result: {len(records)} records: " + "; ".join(summarize(r) for r in records))


mcp = MCPClient(
    lambda: aws_iam_streamablehttp_client(
        endpoint=endpoint, aws_service="agent-registry", aws_region=REGION, credentials=session.get_credentials()
    )
)
with mcp:
    tools = mcp.list_tools_sync()
    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, boto_session=session),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
        # Strands runs a turn's tool calls concurrently by default, which prints every call before
        # any result. One at a time keeps each result under its call.
        tool_executor=SequentialToolExecutor(),
    )
    agent.hooks.add_callback(BeforeToolCallEvent, print_tool_call)
    agent.hooks.add_callback(AfterToolCallEvent, print_tool_result)
    print(f"MCP endpoint: {endpoint}")
    print(f"tools: {', '.join(t.tool_name for t in tools)}")
    print(f"model: {MODEL_ID}")
    print(f"prompt: {PROMPT}\n")
    answer = str(agent(PROMPT))

print(f"\nanswer:\n{answer}")
if RECORD_NAME not in answer:
    raise SystemExit(f"the agent's answer doesn't name {RECORD_NAME}")
