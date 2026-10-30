"""Stub agent for testing the warm pool on AgentCore Runtime V2.

Startup work (everything before app.run()) runs once, before the snapshot.
"""
import json
import secrets
import time

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Startup: load small static data into memory so every restored session has it
PROMPTS = {"system": "You are a helpful agent."}

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    start = time.time()                    # per request, never at startup
    if payload.get("action") == "warmup":
        return {"warm": True, "handler_ms": round((time.time() - start) * 1000)}
    request_id = secrets.token_hex(16)     # per request, from the operating system
    # replace with your agent loop (model and tool calls)
    result = {"echo": payload.get("prompt", ""), "request_id": request_id}
    return {"result": result, "handler_ms": round((time.time() - start) * 1000)}


app.run()   # /ping answers only after this line
