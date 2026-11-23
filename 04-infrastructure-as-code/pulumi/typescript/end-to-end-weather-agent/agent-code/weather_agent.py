from strands import Agent, tool
from strands_tools import use_aws
from typing import Dict, Any
import json
import os
import asyncio
from contextlib import suppress

from bedrock_agentcore.tools.browser_client import BrowserClient
from browser_use import Agent as BrowserAgent
from browser_use.browser.session import BrowserSession
from browser_use.browser import BrowserProfile
from langchain_aws import ChatBedrockConverse
from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter
from bedrock_agentcore.memory import MemoryClient
from rich.console import Console
import re

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

console = Console()

# Configuration - All required, no defaults
BROWSER_ID = os.getenv("BROWSER_ID")
CODE_INTERPRETER_ID = os.getenv("CODE_INTERPRETER_ID")
MEMORY_ID = os.getenv("MEMORY_ID")
RESULTS_BUCKET = os.getenv("RESULTS_BUCKET")
AWS_REGION = os.getenv("AWS_REGION")

# Validate required environment variables
required_vars = {
    "BROWSER_ID": BROWSER_ID,
    "CODE_INTERPRETER_ID": CODE_INTERPRETER_ID,
    "MEMORY_ID": MEMORY_ID,
    "RESULTS_BUCKET": RESULTS_BUCKET,
    "AWS_REGION": AWS_REGION,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    raise EnvironmentError(
        f"Required environment variables not set: {', '.join(missing)}"
    )


# Async helper functions
async def run_browser_task(browser_session, bedrock_chat, task: str) -> str:
    """Run a browser automation task using browser_use"""
    try:
        console.print(f"[blue]🤖 Executing browser task:[/blue] {task[:100]}...")

        agent = BrowserAgent(task=task, llm=bedrock_chat, browser=browser_session)

        result = await agent.run()
        console.print("[green]✅ Browser task completed successfully![/green]")

        if "done" in result.last_action() and "text" in result.last_action()["done"]:
            return result.last_action()["done"]["text"]
        else:
            raise ValueError("NO Data")

    except Exception as e:
        console.print(f"[red]❌ Browser task error: {e}[/red]")
        raise


async def initialize_browser_session():
    """Initialize Browser-use session with AgentCore WebSocket connection"""
    try:
        client = BrowserClient(AWS_REGION)
        client.start(identifier=BROWSER_ID)

        ws_url, headers = client.generate_ws_headers()
        console.print(f"[cyan]🔗 Browser WebSocket URL: {ws_url[:50]}...[/cyan]")

        browser_profile = BrowserProfile(
            headers=headers,
            timeout=150000,
        )

        browser_session = BrowserSession(
            cdp_url=ws_url, browser_profile=browser_profile, keep_alive=True
        )

        console.print("[cyan]🔄 Initializing browser session...[/cyan]")
        await browser_session.start()

        bedrock_chat = ChatBedrockConverse(
            model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            region_name=AWS_REGION,
        )

        console.print("[green]✅ Browser session initialized and ready[/green]")
        return browser_session, bedrock_chat, client

    except Exception as e:
        console.print(f"[red]❌ Failed to initialize browser session: {e}[/red]")
        raise


# Tools for Strands Agent
@tool
async def get_weather_data(city: str) -> Dict[str, Any]:
    """Get weather data for a city using browser automation"""
    browser_session = None

    try:
        console.print(f"[cyan]🌐 Getting weather data for {city}[/cyan]")

        (
            browser_session,
            bedrock_chat,
            browser_client,
        ) = await initialize_browser_session()

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

        result = await run_browser_task(browser_session, bedrock_chat, task)

        if browser_client:
            browser_client.stop()

        return {"status": "success", "content": [{"text": result}]}

    except Exception as e:
        console.print(f"[red]❌ Error getting weather data: {e}[/red]")
        return {
            "status": "error",
            "content": [{"text": f"Error getting weather data: {str(e)}"}],
        }

    finally:
        if browser_session:
            console.print("[yellow]🔌 Closing browser session...[/yellow]")
            with suppress(Exception):
                await browser_session.close()
            console.print("[green]✅ Browser session closed[/green]")


@tool
def generate_analysis_code(weather_data: str) -> Dict[str, Any]:
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

        agent = Agent()
        result = agent(query)

        pattern = r"```(?:json|python)\n(.*?)\n```"
        match = re.search(pattern, result.message["content"][0]["text"], re.DOTALL)
        python_code = (
            match.group(1).strip() if match else result.message["content"][0]["text"]
        )

        return {"status": "success", "content": [{"text": python_code}]}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Error: {str(e)}"}]}


@tool
def execute_code(python_code: str) -> Dict[str, Any]:
    """Execute Python code using AgentCore Code Interpreter"""
    try:
        code_client = CodeInterpreter(AWS_REGION)
        code_client.start(identifier=CODE_INTERPRETER_ID)

        response = code_client.invoke(
            "executeCode",
            {"code": python_code, "language": "python", "clearContext": True},
        )

        for event in response["stream"]:
            code_execute_result = json.dumps(event["result"])

        analysis_results = json.loads(code_execute_result)
        console.print("Analysis results:", analysis_results)

        return {"status": "success", "content": [{"text": str(analysis_results)}]}

    except Exception as e:
        return {"status": "error", "content": [{"text": f"Error: {str(e)}"}]}


@tool
def get_activity_preferences() -> Dict[str, Any]:
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

        preferences = (
            response[0]["payload"][0]["blob"] if response else "No preferences found"
        )
        return {"status": "success", "content": [{"text": preferences}]}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"Error: {str(e)}"}]}


def create_weather_agent() -> Agent:
    """Create the weather agent with all tools"""
    system_prompt = f"""You are a Weather-Based Activity Planning Assistant.

    When a user asks about activities for a location, follow below stepes Sequentially:
    1. Extract city from user query
    2. Call get_weather_data(city) to get weather information
    3. Call generate_analysis_code(weather_data) to create classification code
    4. Call execute_code(python_code) to get Day Type ( GOOD, OK , POOR ) for forecasting dates. 
    5. Call get_activity_preferences() to get user preferences
    6. Generate Activity Recommendations based on weather and preferences that you have recieved from previous steps
    7. Generate the comprehensive Markdown file (results.md) and store it in S3 Bucket :  {RESULTS_BUCKET} through use_aws tool. 
    
    IMPORTANT: Provide complete recommendations and end your response. Do NOT ask follow-up questions or wait for additional input."""

    return Agent(
        tools=[
            get_weather_data,
            generate_analysis_code,
            execute_code,
            get_activity_preferences,
            use_aws,
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
        os.environ["BYPASS_TOOL_CONSENT"] = "True"
        result = agent(query)

        return {"status": "completed", "result": result.message["content"][0]["text"]}

    except Exception as e:
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

    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    app.run()
