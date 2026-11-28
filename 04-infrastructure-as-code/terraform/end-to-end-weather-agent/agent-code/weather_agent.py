import asyncio
import json
import os
import re
from contextlib import AsyncExitStack, ExitStack, closing
from typing import Any

import boto3
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.tools.browser_client import BrowserClient
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
from botocore.exceptions import BotoCoreError, ClientError
from browser_use import Agent as BrowserAgent
from browser_use import BrowserSession
from browser_use.llm.aws.chat_bedrock import ChatAWSBedrock
from rich.console import Console
from strands import Agent, tool

app = BedrockAgentCoreApp()

console = Console()

# Configuration
BROWSER_ID = os.environ["BROWSER_ID"]
CODE_INTERPRETER_ID = os.environ["CODE_INTERPRETER_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]
RESULTS_BUCKET = os.environ["RESULTS_BUCKET"]
AWS_REGION = os.getenv("AWS_REGION") or os.environ["AWS_DEFAULT_REGION"]
PLANNER_MODEL = "global.anthropic.claude-sonnet-4-6"
BROWSER_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


# Async helper functions
async def run_browser_task(task: str) -> str:
    """Connect to the stack's Browser via signed CDP; always release the session."""
    async with AsyncExitStack() as cleanup:
        client = BrowserClient(AWS_REGION)
        client.start(identifier=BROWSER_ID)
        cleanup.callback(client.stop)
        url, headers = client.generate_ws_headers()
        # CDP supplies its own Host and WebSocket upgrade headers. Forward only SigV4 fields.
        auth_headers = {
            k: v for k, v in headers.items() if k.lower() == "authorization" or k.lower().startswith("x-amz-")
        }
        browser = BrowserSession(cdp_url=url, headers=auth_headers, allowed_domains=["weather.gov", "*.weather.gov"])
        cleanup.push_async_callback(browser.stop)
        model = ChatAWSBedrock(model=BROWSER_MODEL, session=boto3.Session(region_name=AWS_REGION))
        history = await BrowserAgent(task=task, llm=model, browser=browser, use_judge=False).run(max_steps=20)
        forecast = history.final_result()
        if not history.is_successful() or not forecast:
            raise ValueError("No forecast returned")
        return forecast


# Tools for Strands Agent
@tool
async def get_weather_data(city: str) -> dict[str, Any]:
    """Get weather data for a city using browser automation"""
    try:
        task = f"""Instruction: Extract 8-Day Weather Forecast for {city} from weather.gov
            Steps:
                - Go to https://weather.gov.
                - Enter "{city}" into the search box and Click on `GO` to execute the search.
                - On the local forecast page, click the "Printable Forecast" link.
                - Wait for the printable forecast page to load completely.
                - For each day in the forecast, extract these fields:
                    - date (format YYYY-MM-DD) 
                    - high (highest temperature)
                    - low (lowest temperature)
                    - conditions (short weather summary, e.g., "Clear")
                    - wind (wind speed as an integer; use mph or km/h as consistent)
                    - precip (precipitation chance or amount, zero if none)
                - Format the extracted data as a JSON array of daily forecast objects, e.g.:
                    ```json
                    [
                    {{
                        "date": "2025-09-17",
                        "high": 78,
                        "low": 62,
                        "conditions": "Clear",
                        "wind": 10,
                        "precip": 80
                    }},
                    {{
                        "date": "2025-09-18",
                        "high": 82,
                        "low": 65,
                        "conditions": "Partly Cloudy",
                        "wind": 10,
                        "precip": 80

                    }}
                    // ... Repeat for each day ...
                    ]```

                - Return only this JSON array as the final output.

            Additional Notes:
                Use null or 0 if any numeric value is missing.
                Avoid scraping ads, navigation, or unrelated page elements.
                If "Printable Forecast" is missing, fallback to the main forecast page.
                Include error handling (e.g., return an empty array if forecast data isn't found).
                Confirm the city name matches the requested location before returning results. 
        """

        result = await run_browser_task(task)
        return {"status": "success", "content": [{"text": result}]}
    except Exception:  # noqa: BLE001 - Provider errors can contain signed connection details.
        return {"status": "error", "content": [{"text": "Could not retrieve the weather.gov forecast."}]}


@tool
def generate_analysis_code(weather_data: str) -> dict[str, Any]:
    """Generate Python code for weather classification"""
    try:
        query = f"""Create Python code to classify weather days as GOOD/OK/POOR:
        
        Rules: 
        - GOOD: 65-80°F, clear conditions, no rain
        - OK: 55-85°F, partly cloudy, slight rain chance  
        - POOR: <55°F or >85°F, cloudy/rainy
        
        Weather data: 
        {weather_data} 

        Store weather data stored in python variable for using it in python code 

        Return code that outputs list of tuples: [('2025-09-16', 'GOOD'), ('2025-09-17', 'OK'), ...]"""

        agent = Agent(model=PLANNER_MODEL)
        result = agent(query)

        pattern = r"```(?:json|python)\n(.*?)\n```"
        match = re.search(pattern, result.message["content"][0]["text"], re.DOTALL)
        python_code = match.group(1).strip() if match else result.message["content"][0]["text"]

        return {"status": "success", "content": [{"text": python_code}]}
    except Exception as e:  # noqa: BLE001 - Report model and response-format failures to the calling agent.
        return {"status": "error", "content": [{"text": f"Error: {e!s}"}]}


@tool
def execute_code(python_code: str) -> dict[str, Any]:
    """Execute Python code using AgentCore Code Interpreter"""
    try:
        with ExitStack() as cleanup:
            code_client = CodeInterpreter(AWS_REGION)
            code_client.start(identifier=CODE_INTERPRETER_ID)
            cleanup.callback(code_client.stop)
            response = code_client.invoke(
                "executeCode", {"code": python_code, "language": "python", "clearContext": True}
            )
            result = None
            for event in response["stream"]:
                result = event["result"]
            if result is None or result.get("isError"):
                return {"status": "error", "content": [{"text": "Weather analysis failed."}]}
            return {"status": "success", "content": [{"text": json.dumps(result)}]}

    except Exception:  # noqa: BLE001 - Provider details may contain the submitted code or data.
        return {"status": "error", "content": [{"text": "Unable to complete the Code Interpreter session."}]}


@tool
def get_activity_preferences() -> dict[str, Any]:
    """Get activity preferences from memory"""
    try:
        client = MemoryClient(region_name=AWS_REGION)
        response = client.list_events(
            memory_id=MEMORY_ID,
            actor_id="user123",
            session_id="session456",
            max_results=50,
            include_payload=True,
        )

        preferences = response[0]["payload"][0]["blob"] if response else "No preferences found"
        return {"status": "success", "content": [{"text": preferences}]}
    except Exception as e:  # noqa: BLE001 - Report memory retrieval and payload-decoding failures as tool results.
        return {"status": "error", "content": [{"text": f"Error: {e!s}"}]}


@tool
def save_activity_plan(markdown: str) -> dict[str, Any]:
    """Save Markdown recommendations as results.md in the configured results bucket."""
    if not isinstance(markdown, str) or not markdown.strip():
        return {"status": "error", "content": [{"text": "Activity plan must be non-empty Markdown."}]}
    if not RESULTS_BUCKET:
        return {"status": "error", "content": [{"text": "RESULTS_BUCKET must identify the deployed results bucket."}]}

    try:
        with closing(boto3.client("s3", region_name=AWS_REGION)) as client:
            client.put_object(
                Bucket=RESULTS_BUCKET,
                Key="results.md",
                Body=markdown.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
    except (BotoCoreError, ClientError):
        return {"status": "error", "content": [{"text": "Unable to save the activity plan to the results bucket."}]}

    return {"status": "success", "content": [{"text": f"Saved activity plan to s3://{RESULTS_BUCKET}/results.md"}]}


def create_weather_agent() -> Agent:
    """Create the weather agent with all tools"""
    system_prompt = """You are a Weather-Based Activity Planning Assistant.

    When a user asks about activities for a location, follow below stepes Sequentially:
    1. Extract city from user query
    2. Call get_weather_data(city) to get weather information
    3. Call generate_analysis_code(weather_data) to create classification code
    4. Call execute_code(python_code) to get Day Type ( GOOD, OK , POOR ) for forecasting dates. 
    5. Call get_activity_preferences() to get user preferences
    6. Generate Activity Recommendations based on weather and preferences that you have recieved from previous steps
    7. Generate comprehensive Markdown recommendations and call save_activity_plan(markdown) to save results.md in the configured S3 bucket. Confirm that the file was saved only if the tool succeeds.
    
    IMPORTANT: Provide complete recommendations and end your response. Do NOT ask follow-up questions or wait for additional input."""

    return Agent(
        model=PLANNER_MODEL,
        tools=[
            get_weather_data,
            generate_analysis_code,
            execute_code,
            get_activity_preferences,
            save_activity_plan,
        ],
        system_prompt=system_prompt,
        name="WeatherActivityPlanner",
    )


@app.async_task
async def async_main(query=None):
    """Async main function"""
    console.print("🌤️ Weather-Based Activity Planner - Async Version")
    console.print("=" * 30)

    agent = create_weather_agent()

    query = query or "What should I do this weekend in Richmond VA?"
    console.print(f"\n[bold blue]🔍 Query:[/bold blue] {query}")
    console.print("-" * 50)

    try:
        result = await agent.invoke_async(query)

        return {"status": "completed", "result": result.message["content"][0]["text"]}

    except Exception as e:  # noqa: BLE001 - Background task boundary returns model/provider failures to its caller.
        console.print(f"[red]❌ Error: {e}[/red]")
        import traceback

        traceback.print_exc()
        return {"status": "error", "error": str(e)}


@app.entrypoint
async def invoke(payload=None):
    try:
        # change
        query = payload.get("prompt")

        asyncio.create_task(async_main(query))

        msg = (
            "Processing started ... "
            f"You can monitor status in CloudWatch logs at /aws/bedrock-agentcore/runtimes/<agent-runtime-id> ....."
            f"You can see the result at {RESULTS_BUCKET} ...."
        )

        return {"status": "Started", "message": msg}

    except Exception as e:  # noqa: BLE001 - Preserve the request-boundary error response for malformed payloads.
        return {"error": str(e)}


if __name__ == "__main__":
    app.run()
