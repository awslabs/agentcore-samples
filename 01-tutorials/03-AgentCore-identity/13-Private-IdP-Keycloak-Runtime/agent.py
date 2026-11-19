"""Minimal AgentCore Runtime agent for private IdP demo."""

from bedrock_agentcore.runtime import App

app = App()


@app.handler
def handle(prompt: str, **kwargs) -> str:
    return f"Echo from private-IdP-secured agent: {prompt}"


if __name__ == "__main__":
    app.run()
