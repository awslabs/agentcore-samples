import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp, PingStatus
from strands import Agent
from strands.models import BedrockModel
from tools import (
    analyze_data_quality,
    analyze_sentiment,
    build_metrics_forecast_model,
    calculate_risk_scores,
    cross_reference_data,
    generate_bug_severity_chart,
    generate_metrics_forecast_chart,
    generate_metrics_trend_chart,
    generate_project_timeline_chart,
    generate_team_velocity_chart,
    read_bug_tracker,
    read_meeting_notes,
    read_metrics,
    read_project_status,
    read_team_updates,
    save_report,
    upload_report_to_s3,
)

app = BedrockAgentCoreApp()

# Create the agent with all tools
model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
model = BedrockModel(model_id=model_id)

weekly_update_agent = Agent(
    name="Weekly Update Generator",
    model=model,
    tools=[
        read_project_status,
        read_team_updates,
        read_metrics,
        read_bug_tracker,
        read_meeting_notes,
        analyze_data_quality,
        cross_reference_data,
        analyze_sentiment,
        calculate_risk_scores,
        generate_bug_severity_chart,
        generate_metrics_trend_chart,
        generate_project_timeline_chart,
        generate_team_velocity_chart,
        build_metrics_forecast_model,
        generate_metrics_forecast_chart,
        save_report,
        upload_report_to_s3,
    ],
    system_prompt="""You are a Weekly Update Generator agent. Your role is to:

1. Collect data from multiple sources (projects, team updates, metrics, bugs, meetings)
2. Analyze data quality and cross-reference information
3. Generate visualizations (bug charts, metrics charts, project timeline, team velocity, forecast charts)
4. Synthesize information into a comprehensive markdown report
5. Save the report using file_write tool to weekly_report_output/weekly_report.md
6. Upload the final report and charts to S3 using upload_report_to_s3

When generating reports, follow this structure:

# Weekly Status Update
## Week of [Date]

## 📊 Executive Summary
- Key highlights and concerns from all data sources
- Project status overview, metric trends, bug status

## 🎯 Project Status
- Project data summary

## 👥 Team Highlights  
- Team update summaries

## 📈 Key Performance Indicators
- Metrics data with status
## 🐛 Bug & Issue Tracker
- Bug tracker data

## 📅 Key Meetings & Decisions
- Meeting notes summaries

## ⚠️ Risks & Blockers
- All blockers from projects and team updates

## ✅ Action Items for Next Week
- Action items from team updates and meetings

## 🎯 Next Week Focus
- Top priorities based on critical bugs and at-risk projects

Do not include markdown image syntax (![...]) in the report. The charts are generated and uploaded to S3 separately.

IMPORTANT: After creating the report content, you MUST:
1. Use file_write to save it to 'weekly_report_output/weekly_report.md'
2. Then call upload_report_to_s3 to upload everything to S3

Work systematically through data collection, analysis, visualization, synthesis, saving, and uploading.""",
)

# Track active tasks count for ping handler
_active_task_count = 0

# Task registry: task_id (str) -> {"status": "RUNNING"|"COMPLETED"|"FAILED", "result": str|None}
#
# Holds this agent's own task state, so a caller can retrieve a result after the
# submitting call returned. It lives in the microVM's memory, which means it is
# only reachable from the runtimeSessionId that created the task, and it is lost
# when that session is recycled. Persist it (DynamoDB, S3) for anything beyond a
# demo.
_tasks = {}

# Guards both _tasks and _active_task_count: the entrypoint writes from the
# request thread while generate_report() writes from the background thread.
_tasks_lock = threading.Lock()


def system_busy():
    """Check if system has active tasks."""
    return _active_task_count > 0


@app.ping
def ping():
    """Ping handler to report agent health status."""
    if system_busy():
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY


@app.entrypoint
def agent(payload):
    """
    Invoke the weekly update agent with a payload.
    Supports method routing for ping checks and report generation.
    """
    global _active_task_count

    # Check if this is a ping request
    method = payload.get("method")
    if method == "ping":
        status = "Healthy" if _active_task_count == 0 else "HealthyBusy"
        return {"status": status, "active_tasks": _active_task_count}

    # Status check for a task started earlier on this session: look it up instead
    # of starting another report. Reaching this registry requires the same
    # runtimeSessionId used to submit. Compared as a string so an int decoded by
    # json.loads and a hand-typed string resolve to the same entry.
    if "task_id" in payload:
        query_task_id = str(payload["task_id"])
        with _tasks_lock:
            task = _tasks.get(query_task_id)
        if task is None:
            return {"task_id": query_task_id, "status": "NOT_FOUND"}
        return {
            "task_id": query_task_id,
            "status": task["status"],
            "result": task["result"],
        }

    # Normal report generation request
    user_input = payload.get("prompt")
    print(f"📥 Received request: {user_input}")

    # Start tracking the async task
    task_id = app.add_async_task("weekly_report_generation", {"prompt": user_input})
    # The task ID is a 64-bit hash: routinely negative and above 2**53, so any
    # client parsing JSON numbers as doubles would round it and then fail to
    # find the task. Key and report it as a string to keep the round-trip exact.
    task_key = str(task_id)
    with _tasks_lock:
        _tasks[task_key] = {"status": "RUNNING", "result": None}
        _active_task_count += 1
    print(f"🔄 Started async task: {task_key} (active: {_active_task_count})")

    # Run the agent in a background thread
    def generate_report():
        global _active_task_count
        try:
            print("🤖 Agent is processing...")
            response = weekly_update_agent(user_input)

            # Handle different response types
            if isinstance(response, str):
                result = response
            elif hasattr(response, "message"):
                result = response.message["content"][0]["text"]
            elif isinstance(response, dict):
                result = response.get("message", {}).get("content", [{}])[0].get("text", str(response))
            else:
                result = str(response)

            print("✅ Report generation completed")
            with _tasks_lock:
                _tasks[task_key] = {"status": "COMPLETED", "result": result}
        # Deliberately broad: this is the background thread's top-level handler.
        # Anything the agent raises has to be recorded as FAILED here, otherwise
        # the task sits at RUNNING forever and the caller has no way to tell.
        except Exception as e:  # noqa: BLE001
            import traceback

            print(f"❌ Report generation failed: {e}")
            traceback.print_exc()
            with _tasks_lock:
                _tasks[task_key] = {"status": "FAILED", "result": str(e)}
        finally:
            # Mark task as complete
            app.complete_async_task(task_id)
            with _tasks_lock:
                _active_task_count -= 1
            print(f"✅ Task {task_key} marked as complete (active: {_active_task_count})")

    # Start background thread
    threading.Thread(target=generate_report, daemon=True).start()

    return {
        "message": f"Weekly report generation started (Task ID: {task_key}). Agent status is now BUSY.",
        "task_id": task_key,
        "status": "BUSY",
    }


if __name__ == "__main__":
    app.run()
