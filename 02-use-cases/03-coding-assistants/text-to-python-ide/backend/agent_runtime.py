"""
AgentCore Runtime entrypoint for the Text-to-Python IDE agent.

This module exposes the agent via BedrockAgentCoreApp so it can be deployed
as a managed AWS Bedrock AgentCore Runtime service. It runs on port 8080 and
handles invocations from AgentCore alongside the existing FastAPI backend (port 8000).

Supported actions in the invocation payload:
    { "action": "generate_code", "prompt": "...", "session_id": "..." }
    { "action": "execute_code",  "code": "...",   "session_id": "..." }
    { "action": "health" }
"""

import os
import sys
import logging
from contextlib import asynccontextmanager

import boto3
from dotenv import load_dotenv
from bedrock_agentcore.runtime.app import BedrockAgentCoreApp

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import shared agent logic from main.py
# ---------------------------------------------------------------------------
# Add backend directory to path so we can import from main
sys.path.insert(0, os.path.dirname(__file__))

from main import (
    initialize_agents,
    get_or_create_session,
    get_session_agent,
    detect_chart_code,
    execute_chart_code_direct,
    extract_text_from_agent_result,
    extract_image_data,
    prepare_interactive_code,
    guardrail_id,
    guardrail_version,
    MEMORY_ID,
    AGENTCORE_SESSION_AVAILABLE,
)

import main as _main_module
import memory_manager


# ---------------------------------------------------------------------------
# Lifespan — reuse the same AWS + agent initialisation from main.py
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):
    logger.info("AgentCore Runtime starting — initialising AWS credentials and agents...")

    # In AgentCore Runtime the container receives credentials automatically via
    # the IAM execution role (instance metadata / ECS task role). We bypass
    # main.py's profile-based setup and let boto3 resolve credentials itself.
    region = os.getenv("AWS_REGION", "us-east-1")
    session = boto3.Session(region_name=region)

    # Verify credentials are available
    identity = session.client("sts").get_caller_identity()
    logger.info("Running as: %s in %s", identity["Arn"], region)

    # Inject into main module so all shared functions use the same session
    _main_module.aws_session = session
    _main_module.aws_region = region

    # Sync env vars so AgentCore code_session picks up the right region
    os.environ["AWS_REGION"] = region
    os.environ["AWS_DEFAULT_REGION"] = region

    initialize_agents()
    logger.info("AgentCore Runtime ready on /invocations")
    yield
    logger.info("AgentCore Runtime shutting down")


# ---------------------------------------------------------------------------
# BedrockAgentCoreApp instance
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Ping handler — reports HEALTHY / HEALTHY_BUSY automatically via @async_task
# ---------------------------------------------------------------------------
@app.ping
def ping_status():
    from bedrock_agentcore import PingStatus

    agents_ready = _main_module.code_generator_agent is not None and _main_module.code_executor_agent is not None
    return PingStatus.HEALTHY if agents_ready else PingStatus.HEALTHY_BUSY


# ---------------------------------------------------------------------------
# Main entrypoint — receives all invocations from AgentCore
# ---------------------------------------------------------------------------
@app.entrypoint
@app.async_task
async def agent_handler(payload: dict, context=None):
    """
    Unified AgentCore Runtime handler.

    Expected payload shapes:
        Generate code:  { "action": "generate_code", "prompt": "...", "session_id": "..." }
        Execute code:   { "action": "execute_code",  "code": "...",   "session_id": "...",
                          "interactive": false, "inputs": [] }
        Health check:   { "action": "health" }
    """
    # Pull session_id from AgentCore context header if available, else from payload
    session_id = payload.get("session_id")
    if context and context.session_id:
        session_id = context.session_id

    action = payload.get("action", "generate_code")
    logger.info("AgentCore invocation — action=%s session=%s", action, session_id)

    # ------------------------------------------------------------------ health
    if action == "health":
        return {
            "status": "healthy",
            "agents_ready": _main_module.code_generator_agent is not None,
            "model": _main_module._agents_cache.get("current_model_id", "unknown"),
            "aws_region": _main_module.aws_region,
            "guardrails_enabled": bool(guardrail_id and guardrail_version),
            "session_persistence": "agentcore_memory" if (AGENTCORE_SESSION_AVAILABLE and MEMORY_ID) else "in_memory",
        }

    # actor_id — use session_id as actor when no user auth exists
    actor_id = payload.get("actor_id") or session_id or "default_user"

    # ----------------------------------------------------------- generate_code
    if action == "generate_code":
        prompt = payload.get("prompt", "").strip()
        if not prompt:
            return {"success": False, "error": "prompt is required"}

        session = get_or_create_session(session_id)

        # Retrieve relevant long-term memories and inject as context
        memory_context = memory_manager.retrieve_context(actor_id, prompt)

        enhanced_prompt = prompt
        if memory_context:
            enhanced_prompt = f"{memory_context}\n\nUser request: {prompt}"
            logger.info("💡 Injected memory context into prompt")

        # Inject CSV context if the session has an uploaded file
        if session.uploaded_csv:
            enhanced_prompt = (
                f"You have access to a CSV file named '{session.uploaded_csv['filename']}':\n\n"
                f"```csv\n{session.uploaded_csv['content'][:1000]}\n```\n\n"
                f"{enhanced_prompt}"
            )

        gen_agent = get_session_agent("generator", session.session_id, actor_id)
        agent_result = gen_agent(enhanced_prompt)
        generated_code = str(agent_result) if agent_result is not None else ""

        # Save turn to AgentCore Memory
        memory_manager.save_turn(actor_id, session_id, prompt, generated_code)

        import time

        session.conversation_history.append(
            {
                "type": "generation",
                "prompt": prompt,
                "generated_code": generated_code,
                "agent": "agentcore_runtime",
                "timestamp": time.time(),
            }
        )

        return {
            "success": True,
            "action": "generate_code",
            "code": generated_code,
            "session_id": session.session_id,
            "memory_enabled": memory_manager.is_enabled(),
        }

    # ------------------------------------------------------------ execute_code
    if action == "execute_code":
        code = payload.get("code", "").strip()
        if not code:
            return {"success": False, "error": "code is required"}

        interactive = payload.get("interactive", False)
        inputs = payload.get("inputs", [])
        session = get_or_create_session(session_id)

        prepared_code = prepare_interactive_code(code, inputs) if interactive and inputs else code

        is_chart = detect_chart_code(prepared_code)
        session_files = []
        if session.uploaded_csv:
            session_files.append(
                {
                    "filename": session.uploaded_csv["filename"],
                    "content": session.uploaded_csv["content"],
                }
            )

        if is_chart or session_files:
            result_str, images = execute_chart_code_direct(prepared_code, session_files)
            agent_used = "direct_agentcore"
        else:
            exec_agent = get_session_agent("executor", session.session_id, actor_id)
            execution_prompt = (
                f"Execute this Python code using the execute_python_code tool:\n\n"
                f"```python\n{prepared_code}\n```\n\n"
                f"Return the complete output."
            )
            execution_result = exec_agent(execution_prompt)
            result_str = extract_text_from_agent_result(execution_result)
            images = extract_image_data(result_str)
            agent_used = "strands_agentcore"

        # Save execution turn to AgentCore Memory
        memory_manager.save_turn(
            actor_id, session_id, f"Execute code: {code[:200]}", result_str[:500] if result_str else "No output"
        )

        import time

        session.code_history.append(code)
        session.execution_results.append(
            {
                "code": code,
                "result": result_str,
                "agent": agent_used,
                "images": images,
                "timestamp": time.time(),
            }
        )

        return {
            "success": True,
            "action": "execute_code",
            "result": result_str,
            "images": images,
            "session_id": session.session_id,
            "agent_used": agent_used,
            "memory_enabled": memory_manager.is_enabled(),
        }

    return {"success": False, "error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# Local dev entry — python backend/agent_runtime.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("AGENTCORE_RUNTIME_PORT", "8080"))
    logger.info("Starting AgentCore Runtime on port %d", port)
    app.run(port=port)
