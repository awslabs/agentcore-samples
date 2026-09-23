import os
from bedrock_agentcore import BedrockAgentCoreApp
from claude_agent_sdk import (
    query,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ClaudeAgentOptions,
)

os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
os.environ["ANTHROPIC_MODEL"] = "global.anthropic.claude-sonnet-4-6"

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload):
    """
    Invoke the agent with a payload
    """
    user_message = payload.get("prompt", "Hello! How can I help you today?")

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a helpful personal assistant."
            "You have a skill at ./persistent-notes/\n"
            "Read ./persistent-notes/SKILL.md to understand your capabilities.\n"
            "Mandatory: Follow skill NOTES_FILE path to write notes.json file in the proper directory."
        ),
        allowed_tools=["Read", "Write", "Bash", "Grep", "Glob"],
        max_turns=20,
    )

    async for message in query(prompt=user_message, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"Claude: {block.text}")
        elif isinstance(message, ResultMessage) and message.total_cost_usd > 0:
            print(f"\nCost: ${message.total_cost_usd:.4f}")
        yield message


if __name__ == "__main__":
    app.run()
