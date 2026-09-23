from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.openai import OpenAI
from llama_index.tools.yahoo_finance import YahooFinanceToolSpec

# Define custom function tools


def multiply(a: float, b: float) -> float:
    """Multiply two numbers and returns the product"""
    return a * b


def add(a: float, b: float) -> float:
    """Add two numbers and returns the sum"""
    return a + b


# Add other predefined tools
finance_tools = YahooFinanceToolSpec().to_tool_list()

finance_tools.extend([multiply, add])

# Create an agent workflow with our calculator tool
agent = FunctionAgent(
    tools=finance_tools,
    llm=OpenAI(model="gpt-4o-mini"),
    system_prompt="You are a helpful assistant.",
)

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
async def main(payload):
    # Run the agent
    response = await agent.run(payload.get("prompt", "What is the current stock price of AMZN?"))
    print(response)
    return response.response.content


# Run the agent
if __name__ == "__main__":
    app.run()
