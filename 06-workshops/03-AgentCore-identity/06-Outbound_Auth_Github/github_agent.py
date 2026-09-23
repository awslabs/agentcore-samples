import os

import json
import asyncio

from typing import Optional
import httpx
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.identity.auth import requires_access_token

# Environment configuration
os.environ["STRANDS_OTEL_ENABLE_CONSOLE_EXPORT"] = "true"
os.environ["OTEL_PYTHON_EXCLUDED_URLS"] = "/ping,/invocations"

# Initialize app
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
    app.logger.info(f"Authorization url: {url}")
    await queue.put(f"Authorization url: {url}")


@tool
def inspect_github_repos() -> str:
    """Inspect and list the user's private GitHub repositories.

    Returns:
        str: A JSON string containing the list of repositories and their details
    """

    # We use nested function so that the tool signature derivation does not consider the access_token parameter
    @requires_access_token(
        provider_name="github-provider",
        scopes=["repo", "read:user"],
        auth_flow="USER_FEDERATION",
        on_auth_url=on_auth_url,
        force_authentication=False,
        callback_url=os.environ["CALLBACK_URL"],
    )
    def inspect_github_repos_tool(access_token: Optional[str] = None) -> str:
        """Inspect and list the user's private GitHub repositories.

        Returns:
            str: A JSON string containing the list of repositories and their details,
                or an authentication required message.
        """
        github_access_token = access_token

        if not github_access_token:
            return json.dumps(
                {
                    "auth_required": True,
                    "message": "GitHub authentication is required. Please wait while we set up the authorization.",
                    "events": [],
                }
            )

        app.logger.info(f"Using GitHub access token: {github_access_token[:10]}...")

        headers = {"Authorization": f"Bearer {github_access_token}"}

        try:
            with httpx.Client() as client:
                # Get user information
                user_response = client.get("https://api.github.com/user", headers=headers)
                user_response.raise_for_status()
                username = user_response.json().get("login", "Unknown")
                app.logger.info(f"✅ User: {username}")

                # Search for user's repositories
                repos_response = client.get(
                    f"https://api.github.com/search/repositories?q=user:{username}",
                    headers=headers,
                )
                repos_response.raise_for_status()
                repos_data = repos_response.json()
                app.logger.info(f"✅ Found {len(repos_data.get('items', []))} repositories")

                repos = repos_data.get("items", [])
                if not repos:
                    return f"No repositories found for {username}."

                # Format repository information
                response_lines = [f"GitHub repositories for {username}:\n"]

                for repo in repos:
                    repo_line = f"📁 {repo['name']}"
                    if repo.get("language"):
                        repo_line += f" ({repo['language']})"
                    repo_line += f" - ⭐ {repo['stargazers_count']}"
                    response_lines.append(repo_line)

                    if repo.get("description"):
                        response_lines.append(f"   {repo['description']}")
                    response_lines.append("")  # Empty line for spacing

                return "\n".join(response_lines)

        except httpx.HTTPStatusError as e:
            return f"GitHub API error: {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error fetching GitHub repositories: {str(e)}"

    return inspect_github_repos_tool()


# Initialize the agent with tools and your preferred model choice
agent = Agent(
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[inspect_github_repos],
    system_prompt="""You are a GitHub assistant. Use the inspect_github_repos tool to fetch private repositories data.
    The inspect_github_repos tool handles token exchange and proper authentication with the GitHub API 
    to obtain private information for the user.""",
)


async def agent_task(user_message: str):
    try:
        await queue.put("Begin agent execution")

        # Call the agent first to see if it needs authentication
        response = await agent.invoke_async(user_message)

        await queue.put(response.message)
        await queue.put("End agent execution")
    except Exception as e:
        await queue.put(f"Error: {str(e)}")
    finally:
        await queue.finish()


@app.entrypoint
async def agent_invocation(payload):
    user_message = payload.get(
        "prompt",
        "No prompt found in input, please guide customer to create a json payload with prompt key",
    )

    # Create and start the agent task
    task = asyncio.create_task(agent_task(user_message))

    # Return the stream, but ensure the task runs concurrently
    async def stream_with_task():
        # Stream results as they come
        async for item in queue.stream():
            yield item

        # Ensure the task completes
        await task

    return stream_with_task()


if __name__ == "__main__":
    app.run()
