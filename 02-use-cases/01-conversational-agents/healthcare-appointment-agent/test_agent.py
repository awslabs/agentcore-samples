"""Non-interactive test script using the sample prompts from the readme."""

from strands.models import BedrockModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient
from strands import Agent
import logging
import os
import utils

os.environ["STRANDS_TOOL_CONSOLE_MODE"] = "disabled"
logging.getLogger("strands").setLevel(logging.WARNING)

GATEWAY_ID = "healthcare-fhir-gateway-y192aidrp5"

systemPrompt = """
   You are a healthcare agent to book appointments for kids immunization.
    Assume a patient with id adult-patient-001 has logged in 
    and can do the following:
    1/ Enquire about immunization schedule for his/her children
    2/ Book the appointment

    To start with, address the logged in user by his/her name and you can get the name by invoking the tools.
    Never include the patient ids in the response.
    When there are pending (status = not done) immunizations in the schedule the ask for booking the appointment. 
    When asked about the immunization schedule, please first get the child name and date of birth by invoking the right tool with patient id as pediatric-patient-001 and ask the user to confirm the details.
"""

TEST_PROMPTS = [
    "How can you help?",
    "Let us check for immunization schedule first",
    "Please find slots for MMR vaccine around the scheduled date",
    "Yes, please book the first available slot",
]

boto_session, agentcore_client = utils.create_agentcore_client()

gatewayEndpoint = utils.get_gateway_endpoint(agentcore_client=agentcore_client, gateway_id=GATEWAY_ID)
print(f"Gateway Endpoint: {gatewayEndpoint}\n")

jwtToken = utils.get_oath_token(boto_session)
client = MCPClient(lambda: streamablehttp_client(gatewayEndpoint, headers={"Authorization": f"Bearer {jwtToken}"}))

bedrockmodel = BedrockModel(
    model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    temperature=0.7,
    streaming=False,
    boto_session=boto_session,
)

with client:
    tools = client.list_tools_sync()
    print(f"Available MCP tools: {[t.tool_name for t in tools]}\n")

    agent = Agent(model=bedrockmodel, tools=tools, system_prompt=systemPrompt)

    for prompt in TEST_PROMPTS:
        print(f"{'=' * 60}")
        print(f"👤 Prompt: {prompt}")
        print(f"{'=' * 60}")
        response = agent(prompt)
        print(f"🤖 Response: {response}\n")

print("✅ Test complete.")
