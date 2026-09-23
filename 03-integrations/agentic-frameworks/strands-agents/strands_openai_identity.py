import os
from bedrock_agentcore.identity.auth import requires_api_key
from strands import Agent
from strands.models.openai import OpenAIModel
from strands_tools import calculator
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Global agent variable
agent = None


@requires_api_key(
    provider_name="openai-apikey-provider"  # replace with your own credential provider name
)
async def need_api_key(*, api_key: str):
    print("received api key for async func")
    os.environ["OPENAI_API_KEY"] = api_key


def create_model():
    """Create the OpenAI model with the API key"""
    return OpenAIModel(
        client_args={
            "api_key": os.environ.get("OPENAI_API_KEY"),
        },
        model_id="gpt-4o",
        params={
            "max_tokens": 1000,
            "temperature": 0.7,
        },
    )


app = BedrockAgentCoreApp()


@app.entrypoint
async def strands_agent_open_ai(payload):
    """
    Invoke the agent with a payload
    """
    global agent

    print("Entrypoint called")

    # Get API key if not already set in environment
    if not os.environ.get("OPENAI_API_KEY"):
        print("Attempting to retrieve API key...")
        try:
            await need_api_key(api_key="")
            print("API key retrieved and set in environment")
        except Exception as e:
            print(f"Error retrieving API key: {e}")
            raise
    else:
        print("API key already available in environment")

    # Initialize agent after API key is set
    if agent is None:
        print("Initializing agent with API key...")
        model = create_model()
        agent = Agent(model=model, tools=[calculator])
    user_input = payload.get("prompt")
    print(f"User input: {user_input}")

    try:
        response = agent(user_input)
        print(f"Agent response: {response}")
        return response.message["content"][0]["text"]
    except Exception as e:
        print(f"Error in agent processing: {e}")
        raise


if __name__ == "__main__":
    app.run()
