from bedrock_agentcore import BedrockAgentCoreApp
import httpx
import os

app = BedrockAgentCoreApp()


@app.entrypoint
def entrypoint(payload):
    client = httpx.Client()
    response = client.post(
        f"http://{os.environ['API_URL']}:8080/invocations",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    return response.json()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)  # nosec nosemgrep
