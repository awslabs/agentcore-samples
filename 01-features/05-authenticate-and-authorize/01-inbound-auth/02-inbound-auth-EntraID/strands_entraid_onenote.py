"""
Strands agent for Microsoft OneNote integration via Entra ID 3LO.

This agent uses USER_FEDERATION auth flow to access the Microsoft Graph API
on behalf of an authenticated user, creating and managing OneNote notebooks.

Deployed to AgentCore Runtime by entra_gateway_auth_code.py.
Requires environment variable: scopes (space-separated OneNote scopes)
"""

import asyncio
import json
import os

import requests
from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from oauth2_callback_server import get_oauth2_callback_url
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

os.environ["STRANDS_OTEL_ENABLE_CONSOLE_EXPORT"] = "true"
os.environ["OTEL_PYTHON_EXCLUDED_URLS"] = "/ping,/invocations"

entra_access_token = None  # Global variable to store the access token
tool_name = None


@tool
def create_notebook(name: str) -> str:
    """
    Create a new Microsoft OneNote notebook for the user.
    Required before creating sections or adding content.

    Args:
        name: The display name for the new notebook

    Returns:
        JSON string with the notebook ID
    """
    global entra_access_token, tool_name
    tool_name = "create_notebook"

    if not entra_access_token:
        return json.dumps(
            {
                "auth_required": True,
                "message": f"Entra ID authentication required for {tool_name}. Authorization is being set up.",
            }
        )

    headers = {
        "Authorization": f"Bearer {entra_access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(  # nosec B113
        "https://graph.microsoft.com/v1.0/me/onenote/notebooks",
        headers=headers,
        json={"displayName": name},
    )
    resp.raise_for_status()
    return json.dumps({"notebook_id": resp.json()["id"]})


@tool
def create_notebook_section(notebook_id: str, section_name: str) -> str:
    """
    Create a new section in an existing OneNote notebook.

    Args:
        notebook_id: The ID of the notebook to create the section in
        section_name: The display name for the new section

    Returns:
        JSON string with the section ID
    """
    global entra_access_token, tool_name
    tool_name = "create_notebook_section"

    if not entra_access_token:
        return json.dumps(
            {
                "auth_required": True,
                "message": f"Entra ID authentication required for {tool_name}.",
            }
        )

    headers = {
        "Authorization": f"Bearer {entra_access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(  # nosec B113
        f"https://graph.microsoft.com/v1.0/me/onenote/notebooks/{notebook_id}/sections",
        headers=headers,
        json={"displayName": section_name},
    )
    resp.raise_for_status()
    return json.dumps({"section_id": resp.json()["id"]})


@tool
def add_content_to_notebook_section(section_id: str, page_content: str) -> str:
    """
    Add content to a OneNote section by creating a new page.

    Args:
        section_id: The ID of the section to add content to
        page_content: HTML content for the new page

    Returns:
        JSON string with the URL to the created page
    """
    global entra_access_token, tool_name
    tool_name = "add_content_to_notebook_section"

    if not entra_access_token:
        return json.dumps(
            {
                "auth_required": True,
                "message": f"Entra ID authentication required for {tool_name}.",
            }
        )

    headers = {
        "Authorization": f"Bearer {entra_access_token}",
        "Content-Type": "text/html",
    }
    resp = requests.post(  # nosec B113
        f"https://graph.microsoft.com/v1.0/me/onenote/sections/{section_id}/pages",
        headers=headers,
        data=page_content,
    )
    resp.raise_for_status()
    url = json.loads(resp.text)["links"]["oneNoteWebUrl"]["href"]
    return json.dumps({"oneNoteWebUrl": url})


# ── Agent Setup ────────────────────────────────────────────────────────────────

model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0")
system_prompt = (
    "You are an Agent who helps users put their meeting notes into OneNote notebooks. "
    "Identify the notebook name, section name and content from the user's request. "
    "Return the notebook URL once created."
)
agent = Agent(
    model=model,
    system_prompt=system_prompt,
    tools=[create_notebook, create_notebook_section, add_content_to_notebook_section],
)

app = BedrockAgentCoreApp()


class StreamingQueue:
    def __init__(self):
        self.finished = False
        self.queue = asyncio.Queue()

    async def put(self, item):
        await self.queue.put(item)

    async def finish(self):
        self.finished = True
        await self.queue.put(None)

    async def stream(self):
        while True:
            item = await self.queue.get()
            if item is None and self.finished:
                break
            yield item


queue = StreamingQueue()


async def on_auth_url(url: str):
    print(f"Authorization URL: {url}")
    await queue.put(f"Authorization URL: {url}")


async def agent_task(user_message: str):
    global entra_access_token, tool_name
    try:
        await queue.put("Begin agent execution")
        response = agent(user_message)

        # Extract text response
        response_text = ""
        if isinstance(response.message, dict):
            for item in response.message.get("content", []):
                if isinstance(item, dict) and "text" in item:
                    response_text += item["text"]
        else:
            response_text = str(response.message)

        # Check if authentication is required
        auth_keywords = [
            "authentication",
            "authorize",
            "authorization",
            "auth",
            "sign in",
            "login",
            "permission",
            "credential",
            "auth_required",
        ]
        needs_auth = any(kw.lower() in response_text.lower() for kw in auth_keywords)

        if needs_auth:
            await queue.put(f"Authentication required for {tool_name} access. Starting authorization flow...")
            try:
                entra_access_token = await need_token_3LO_async(access_token=None)
                await queue.put("Authentication successful! Retrying...")
                response = agent(user_message)
            except Exception as auth_err:
                await queue.put(f"Authentication failed: {repr(auth_err)}")

        await queue.put(response.message)
        await queue.put("End agent execution")
    except Exception as e:
        await queue.put(f"Error: {repr(e)}")
    finally:
        await queue.finish()


@requires_access_token(
    provider_name="microsoft_entra_oauth_provider",
    scopes=os.environ.get("scopes", "").split(" "),
    auth_flow="USER_FEDERATION",
    on_auth_url=on_auth_url,
    force_authentication=True,
    callback_url=get_oauth2_callback_url(),
)
async def need_token_3LO_async(*, access_token: str):
    global entra_access_token
    entra_access_token = access_token
    print("Got access token")
    return access_token


@app.entrypoint
async def agent_invocation(payload):
    user_message = payload.get(
        "prompt",
        "No prompt found. Please send a JSON payload with a 'prompt' key.",
    )
    task = asyncio.create_task(agent_task(user_message))

    async def stream_with_task():
        async for item in queue.stream():
            yield item
        await task

    return stream_with_task()


if __name__ == "__main__":
    app.run()
