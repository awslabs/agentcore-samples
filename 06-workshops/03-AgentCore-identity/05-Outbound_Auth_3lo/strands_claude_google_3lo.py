import os
import datetime
import json
import asyncio

from typing import Optional

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.identity.auth import requires_access_token
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Environment configuration
os.environ["STRANDS_OTEL_ENABLE_CONSOLE_EXPORT"] = "true"
os.environ["OTEL_PYTHON_EXCLUDED_URLS"] = "/ping,/invocations"

# Required OAuth2 scope for Google Calendar API
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

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


@tool(
    name="Get_calendar_events_today",
    description="Retrieves the calendar events for the day from your Google Calendar",
)
async def get_calendar():
    @requires_access_token(
        provider_name="google-cal-provider",
        scopes=SCOPES,
        auth_flow="USER_FEDERATION",
        on_auth_url=on_auth_url,
        force_authentication=True,
        callback_url=os.environ["CALLBACK_URL"],
    )
    async def get_calendar_events_today(access_token: Optional[str] = "") -> str:
        google_access_token = access_token
        # Check if we already have a token
        if not google_access_token:
            app.logger.info("Missing access token")
            return json.dumps(
                {
                    "auth_required": True,
                    "message": "Google Calendar authentication is required. Please wait while we set up the authorization.",
                    "events": [],
                }
            )

        # Create credentials from the provided access token
        creds = Credentials(token=google_access_token, scopes=SCOPES)
        try:
            service = build("calendar", "v3", credentials=creds)
            # Call the Calendar API
            today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            # We use a fixed Time Zone. In a real application this would be
            # derived from the user interacting with the agent
            tz = "00:00"
            today_end = today_start.replace(hour=23, minute=59, second=59)
            time_min = today_start.strftime(f"%Y-%m-%dT00:00:00-{tz}")
            time_max = today_end.strftime(f"%Y-%m-%dT23:59:59-{tz}")

            events_result = (
                service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])

            if not events:
                return json.dumps({"events": []})  # Return empty events array as JSON

            return json.dumps({"events": events})  # Return events wrapped in an object
        except HttpError as error:
            error_message = str(error)
            return json.dumps({"error": error_message, "events": []})
        except Exception as e:
            error_message = str(e)
            return json.dumps({"error": error_message, "events": []})

    app.logger.info("Run tool")
    try:
        return await get_calendar_events_today()
    except Exception as e:
        app.logger.info(e)


# Initialize the agent with tools and your preferred model choice
agent = Agent(
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[get_calendar],
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
    app.logger.info(os.environ["CALLBACK_URL"])

    # Return the stream, but ensure the task runs concurrently
    async def stream_with_task():
        # Stream results as they come
        async for item in queue.stream():
            yield item

        # Ensure the task completes
        await task

    return stream_with_task()


if __name__ == "__main__":
    app.logger.info("Starting")
    app.run()
