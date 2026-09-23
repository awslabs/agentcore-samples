"""Skills — generate weather forecast reports as XLSX spreadsheets.

Tries Git-based skill fetching first (no container needed). Falls back to
path-based approach with Node.js container if Git parameter is not supported.
"""

import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ParamValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from utils.client import get_agentcore_control_client, get_agentcore_client

from resources import REGION

NODE_CONTAINER = "public.ecr.aws/docker/library/node:slim"
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

_skill_installed: dict[str, bool] = {}


def _run_command(client, harness_arn: str, session_id: str, cmd: str) -> str:
    """Run a shell command on the agent VM."""
    output = ""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": cmd},
    )
    for event in resp["stream"]:
        if "chunk" in event and "contentDelta" in event["chunk"]:
            delta = event["chunk"]["contentDelta"]
            if "stdout" in delta:
                output += delta["stdout"]
            if "stderr" in delta:
                output += delta["stderr"]
    return output


def _build_prompt(city: str) -> str:
    return (
        f"Create an Excel spreadsheet with a 7-day weather forecast for {city}. "
        f"The first row must be a title: '{city} - 7-Day Weather Forecast' merged across all columns. "
        "Then include columns for: Day, Condition, High (°F), Low (°F), Wind (mph), Humidity (%), UV Index. "
        "Add realistic weather data that varies day to day. "
        "Include a summary row at the bottom with averages. "
        "Apply formatting: bold title, bold headers, alternating row colors, conditional formatting "
        "(red for high temps > 90°F, blue for low temps < 40°F). "
        "Save it as /tmp/weather_forecast.xlsx"
    )


def _download_file(client, harness_arn: str, session_id: str) -> str:
    """Download the generated xlsx file as base64."""
    b64_data = ""
    resp = client.invoke_agent_runtime_command(
        agentRuntimeArn=harness_arn,
        runtimeSessionId=session_id,
        body={"command": "base64 /tmp/weather_forecast.xlsx 2>/dev/null"},
    )
    for event in resp["stream"]:
        if "chunk" in event and "contentDelta" in event["chunk"]:
            delta = event["chunk"]["contentDelta"]
            if "stdout" in delta:
                b64_data += delta["stdout"]
    return b64_data.strip().replace("\n", "")


def _try_git_skill(client, harness_arn: str, session_id: str, city: str) -> str | None:
    """Try invoking with Git-based skill fetch (newer boto3 only)."""
    try:
        response = client.invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            skills=[{"git": {"url": "https://github.com/anthropics/skills", "path": "skills/xlsx"}}],
            messages=[{"role": "user", "content": [{"text": _build_prompt(city)}]}],
            model={"bedrockModelConfig": {"modelId": MODEL_ID}},
            timeoutSeconds=300,
        )
        agent_text = ""
        for event in response["stream"]:
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    agent_text += delta["text"]
            elif "internalServerException" in event:
                print(f"[skills] Stream error: {event['internalServerException']}")
                return None
        print(f"[skills] Git skill completed. Agent response length: {len(agent_text)}")
        return agent_text
    except ParamValidationError:
        print("[skills] Git-based skill not supported, falling back to path approach")
        return None
    except Exception as e:
        print(f"[skills] Git-based skill exception: {type(e).__name__}: {e}")
        return None


def _install_skill_path(client, control, harness_id: str, harness_arn: str, session_id: str) -> bool:
    """Install xlsx skill via shell (requires Node.js container)."""
    if _skill_installed.get(session_id):
        return True

    # Attach Node.js container if needed
    print("[skills] Checking container...")
    harness_info = control.get_harness(harnessId=harness_id)
    current_artifact = harness_info["harness"].get("environmentArtifact", {})
    has_container = bool(current_artifact.get("containerConfiguration", {}).get("containerUri"))

    if not has_container:
        print(f"[skills] Attaching Node.js container: {NODE_CONTAINER}")
        control.update_harness(
            harnessId=harness_id,
            environmentArtifact={
                "optionalValue": {"containerConfiguration": {"containerUri": NODE_CONTAINER}}
            },
        )
        for _ in range(24):
            status = control.get_harness(harnessId=harness_id)["harness"]["status"]
            if status == "READY":
                break
            time.sleep(5)

    # Install skill
    print("[skills] Installing xlsx skill via npx...")
    _run_command(
        client, harness_arn, session_id,
        "apt-get update -qq && apt-get install git -y -qq > /dev/null 2>&1 && "
        "npx skills add https://github.com/anthropics/skills --skill xlsx --yes 2>&1 | tail -3"
    )

    # Verify
    verify = _run_command(client, harness_arn, session_id, "ls .agents/skills/xlsx/ 2>/dev/null && echo OK || echo MISSING")
    if "OK" in verify:
        _skill_installed[session_id] = True
        print("[skills] Skill installed successfully")
        return True
    print("[skills] Skill installation failed")
    return False


def generate_weather_report(harness_arn: str, harness_id: str, session_id: str, city: str = "the cities discussed") -> dict:
    """Generate a weather forecast XLSX report. Tries Git skill first, falls back to path."""
    try:
        return _generate_report_inner(harness_arn, harness_id, session_id, city)
    except Exception as e:
        print(f"[skills] Unhandled exception: {type(e).__name__}: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _generate_report_inner(harness_arn: str, harness_id: str, session_id: str, city: str) -> dict:
    client = get_agentcore_client(config=Config(read_timeout=360))
    control = get_agentcore_control_client()

    # Try Git-based skill first (simpler, no container needed)
    print("[skills] Trying Git-based skill fetch...")
    agent_text = _try_git_skill(client, harness_arn, session_id, city)

    if agent_text is not None:
        # Git skill ran — check if file was generated
        print(f"[skills] Git skill completed. Agent text length: {len(agent_text)}")
        b64_clean = _download_file(client, harness_arn, session_id)
        if b64_clean:
            return {
                "success": True,
                "file_data": b64_clean,
                "filename": "weather_forecast.xlsx",
                "agent_response": agent_text[:500],
            }
        print("[skills] Git skill ran but no file generated, falling back to path approach")

    # Fallback: install skill via path
    if not _install_skill_path(client, control, harness_id, harness_arn, session_id):
        return {"success": False, "error": "Failed to install xlsx skill"}

    # Invoke with path-based skill
    print("[skills] Invoking with path-based skill...")
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        skills=[{"path": ".agents/skills/xlsx"}],
        messages=[{"role": "user", "content": [{"text": _build_prompt(city)}]}],
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        timeoutSeconds=300,
    )
    agent_text = ""
    for event in response["stream"]:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                agent_text += delta["text"]

    # Download the file
    b64_clean = _download_file(client, harness_arn, session_id)

    if b64_clean:
        return {
            "success": True,
            "file_data": b64_clean,
            "filename": "weather_forecast.xlsx",
            "agent_response": agent_text[:500] if agent_text else "",
        }
    else:
        return {
            "success": False,
            "error": "No file generated",
            "agent_response": agent_text[:500] if agent_text else "",
        }
