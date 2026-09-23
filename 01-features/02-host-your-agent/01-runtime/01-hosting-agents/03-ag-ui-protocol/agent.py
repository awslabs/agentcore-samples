"""
AG-UI Agent — Collaborative Document Generator on AgentCore Runtime.

Uses FastAPI for AG-UI protocol (SSE + WebSocket) with Strands for LLM reasoning.
Streams AG-UI events (text deltas, tool calls, state snapshots) to the client.
"""

import json
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from starlette.responses import JSONResponse
from fastapi.responses import StreamingResponse
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder

app = FastAPI()
_agui_agent = None


def get_agent():
    """Lazy-initialize the AG-UI agent (defers heavy imports to first request)."""
    global _agui_agent
    if _agui_agent is not None:
        return _agui_agent

    from typing import List
    from ag_ui_strands import StrandsAgent, StrandsAgentConfig, ToolBehavior
    from pydantic import BaseModel, Field
    from strands import Agent, tool
    from strands.models.bedrock import BedrockModel

    # ── Shared Document State ────────────────────────────────────────────

    class DocumentSection(BaseModel):
        heading: str = Field(description="Section heading")
        body: str = Field(description="Section body content (markdown)")

    class DocumentMetadata(BaseModel):
        last_modified: str = Field(description="ISO 8601 timestamp")
        version: int = Field(description="Version number")

    class DocumentState(BaseModel):
        title: str = Field(description="Document title")
        sections: List[DocumentSection] = Field(default_factory=list)
        metadata: DocumentMetadata = Field(description="Document metadata")

    # ── Tools ────────────────────────────────────────────────────────────

    @tool
    def research_topic(query: str) -> str:
        """Research a topic and return findings."""
        return (
            f"Research findings for '{query}':\n"
            f"1. Key concepts and definitions\n"
            f"2. Current best practices\n"
            f"3. Common challenges and solutions\n"
            f"4. Recent developments\n"
        )

    @tool
    def generate_outline(topic: str, num_sections: int) -> str:
        """Generate a document outline with section headings."""
        headings = [
            "Introduction",
            "Background",
            "Key Concepts",
            "Analysis",
            "Implementation",
            "Best Practices",
            "Challenges",
            "Case Studies",
            "Future Directions",
            "Conclusion",
        ]
        return json.dumps([headings[i % len(headings)] for i in range(num_sections)])

    @tool
    def update_document(document: DocumentState) -> str:
        """Update the shared document state. Triggers a STATE_SNAPSHOT event."""
        return "Document updated successfully"

    # ── AG-UI Configuration ──────────────────────────────────────────────

    def build_prompt(input_data, user_message: str) -> str:
        state = getattr(input_data, "state", None)
        if isinstance(state, dict) and "title" in state:
            return f"Current document:\n{json.dumps(state, indent=2)}\n\nUser: {user_message}"
        return user_message

    async def state_from_args(context):
        try:
            tool_input = context.tool_input
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input)
            doc = tool_input.get("document", tool_input)
            return doc if isinstance(doc, dict) else None
        except Exception:
            return None

    config = StrandsAgentConfig(
        state_context_builder=build_prompt,
        tool_behaviors={
            "update_document": ToolBehavior(
                skip_messages_snapshot=True,
                state_from_args=state_from_args,
            )
        },
    )

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

    strands_agent = Agent(
        model=model,
        system_prompt=(
            "You are a collaborative document co-authoring assistant. "
            "Use update_document to modify the shared document state. "
            "Always increment the version number on each update."
        ),
        tools=[research_topic, generate_outline, update_document],
    )

    _agui_agent = StrandsAgent(
        agent=strands_agent,
        name="document_agent",
        description="A document co-authoring assistant",
        config=config,
    )
    return _agui_agent


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "ok"})


@app.post("/invocations")
async def invocations(input_data: dict, request: Request):
    """SSE transport — streams AG-UI events."""
    agent = get_agent()
    accept_header = request.headers.get("accept")
    encoder = EventEncoder(accept=accept_header)

    async def event_generator():
        run_input = RunAgentInput(**input_data)
        async for event in agent.run(run_input):
            yield encoder.encode(event)

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket transport — bidirectional AG-UI events."""
    agent = get_agent()
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            input_data = RunAgentInput(**data)
            async for event in agent.run(input_data):
                await websocket.send_json(event.model_dump())
    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close()


if __name__ == "__main__":
    host = os.environ.get("AGENT_RUNTIME_HOST", "0.0.0.0")  # nosec B104
    port = int(os.environ.get("AGENT_PORT", 8080))
    uvicorn.run(app, host=host, port=port, log_level="info")
