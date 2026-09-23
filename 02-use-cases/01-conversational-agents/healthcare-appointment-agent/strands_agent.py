from strands.models import BedrockModel
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient
from strands import Agent
import logging
import argparse
import os
import utils

# setting parameters
parser = argparse.ArgumentParser(
    prog="strands_agent",
    description="Test Strands Agent with MCP Gateway",
    epilog="Input Parameters",
)

parser.add_argument("--gateway_id", help="Gateway Id")

os.environ["STRANDS_TOOL_CONSOLE_MODE"] = "enabled"

# create boto3 session and client
(boto_session, agentcore_client) = utils.create_agentcore_client()

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

if __name__ == "__main__":
    args = parser.parse_args()

    # Validations
    if args.gateway_id is None:
        raise Exception("Gateway Id is required")

    gatewayEndpoint = utils.get_gateway_endpoint(agentcore_client=agentcore_client, gateway_id=args.gateway_id)
    print(f"Gateway Endpoint: {gatewayEndpoint}")

    jwtToken = utils.get_oath_token(boto_session)
    client = MCPClient(lambda: streamablehttp_client(gatewayEndpoint, headers={"Authorization": f"Bearer {jwtToken}"}))

    bedrockmodel = BedrockModel(
        model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        temperature=0.7,
        streaming=True,
        boto_session=boto_session,
    )

    # Configure the root strands logger
    logging.getLogger("strands").setLevel(logging.INFO)

    # Add a handler to see the logs
    logging.basicConfig(
        format="%(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler()],
    )

    with client:
        tools = client.list_tools_sync()
        agent = Agent(model=bedrockmodel, tools=tools, system_prompt=systemPrompt)

        print("=" * 60)
        print("🗓️  WELCOME TO YOUR HEALTHCARE ASSISTANT  🗓️")
        print("=" * 60)
        print("✨ I can help you with:")
        print("   📅 Check child's immunization history and pending immunization")
        print("   📋 Book appointment for immunization")
        print()
        print("🚪 Type 'exit' to quit anytime")
        print("=" * 60)
        print()

        # Run the agent in a loop for interactive conversation
        while True:
            try:
                user_input = input("👤 You: ").strip()

                if not user_input:
                    print("💭 Please enter a message or type 'exit' to quit")
                    continue

                if user_input.lower() in ["exit", "quit", "bye", "goodbye"]:
                    print()
                    print("=======================================")
                    print("👋 Thanks for using Healthcare Assistant!")
                    print("🎉 Have a great day ahead!")
                    print("=======================================")
                    break

                print("🤖 Healthcarebot: ", end="")
                agent(user_input)
                print()

            except KeyboardInterrupt:
                print()
                print("=======================================")
                print("👋 Healthcare Assistant interrupted!")
                print("🎉 See you next time!")
                print("=======================================")
                break
            except Exception as e:
                print(f"❌ An error occurred: {str(e)}")
                print("💡 Please try again or type 'exit' to quit")
                print()
