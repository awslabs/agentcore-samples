# Import required libraries for Bedrock AgentCore
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent

# Initialize the AgentCore application
app = BedrockAgentCoreApp()

# Create the AI agent instance
agent = Agent()


@app.entrypoint
def invoke(payload, context):
    """
    Main entry point for the agent.

    Args:
        payload: Dictionary containing the 'prompt' key with user input
        context: Runtime context information

    Returns:
        Dictionary with the agent's response message
    """
    # Extract the user prompt from the payload
    user_message = payload.get("prompt", "Hello!")

    # Process the message with the agent
    result = agent(user_message)

    # Return the response in the expected format
    return {"result": result.message}


if __name__ == "__main__":
    # Run the agent application
    app.run()
