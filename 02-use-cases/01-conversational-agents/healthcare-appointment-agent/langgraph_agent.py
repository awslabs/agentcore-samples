from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain.chat_models import init_chat_model
import asyncio
from dotenv import load_dotenv
import argparse
import os
import utils

load_dotenv()

# setting parameters
parser = argparse.ArgumentParser(
    prog="strands_agent",
    description="Test Strands Agent with MCP Gateway",
    epilog="Input Parameters",
)

parser.add_argument("--gateway_id", help="Gateway Id")

# create boto3 session and client
(boto_session, agentcore_client) = utils.create_agentcore_client()

bedrock_client = boto_session.client("bedrock-runtime", region_name=os.getenv("aws_default_region"))


async def main(gateway_endpoint, jwt_token):
    client = MultiServerMCPClient(
        {
            "healthcare": {
                "url": gateway_endpoint,
                "transport": "streamable_http",
                "headers": {"Authorization": f"Bearer {jwt_token}"},
            }
        }
    )

    tools = await client.get_tools()

    LLM = init_chat_model(
        client=bedrock_client,
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        model_provider="bedrock_converse",
        temperature=0.7,
    )
    # print(LLM)

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

    agent = create_react_agent(LLM, tools, prompt=systemPrompt)
    history = ""

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

            history = history + "User: " + user_input

            async for message_chunk, metadata in agent.astream(
                {"messages": [("human", user_input), ("developer", history)]},
                stream_mode="messages",
            ):
                if message_chunk.content:
                    for content in message_chunk.content:
                        if "text" in content:
                            print(content["text"], end="", flush=True)

                            history = history + "AI Message Chunk: " + content["text"]

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


if __name__ == "__main__":
    args = parser.parse_args()

    # Validations
    if args.gateway_id is None:
        raise Exception("Gateway Id is required")

    gatewayEndpoint = utils.get_gateway_endpoint(agentcore_client=agentcore_client, gateway_id=args.gateway_id)
    print(f"Gateway Endpoint: {gatewayEndpoint}")

    jwtToken = utils.get_oath_token(boto_session)
    asyncio.run(main(gatewayEndpoint, jwtToken))
