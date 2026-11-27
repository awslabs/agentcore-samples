"""One tool-free Claude Agent SDK request per AgentCore Runtime invocation."""

import asyncio
import os
from contextlib import aclosing
from pathlib import Path
from tempfile import TemporaryDirectory

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from starlette.exceptions import HTTPException

app = BedrockAgentCoreApp()


def options(workspace):
    # The SDK bundles the CLI in its platform-specific wheel. Use the Bedrock role,
    # never an API key or credentials supplied in a prompt. Keep local state request-scoped.
    return ClaudeAgentOptions(
        model=os.environ["BEDROCK_MODEL_ID"],
        system_prompt="Answer the user's question concisely using text only.",
        tools=[],
        allowed_tools=[],
        permission_mode="dontAsk",
        mcp_servers={},
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=1,
        cwd=workspace,
        env={"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CONFIG_DIR": str(Path(workspace) / ".claude")},
    )


@app.entrypoint
async def answer(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise HTTPException(400, "Expected a nonempty prompt up to 4000 characters")
    if any(
        os.getenv(k)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "CLAUDE_CODE_OAUTH_TOKEN")
    ):
        raise HTTPException(500, "Use the dedicated Bedrock role without a model token")
    try:
        with TemporaryDirectory(prefix="claude-request-") as workspace:
            async with asyncio.timeout(120), aclosing(query(prompt=prompt, options=options(workspace))) as messages:
                async for message in messages:
                    if isinstance(message, ResultMessage):
                        if message.is_error or not isinstance(message.result, str) or not message.result.strip():
                            break
                        return {"response": message.result}
    except Exception:  # noqa: BLE001 -- SDK/subprocess errors can contain prompts or provider details.
        raise HTTPException(502, "Claude did not complete the request") from None
    raise HTTPException(502, "No completed text response")


if __name__ == "__main__":
    app.run()
