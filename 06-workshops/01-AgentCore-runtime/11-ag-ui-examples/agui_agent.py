"""Collaborative Document Generator — AGUI Protocol Agent.

Uses FastAPI for AGUI protocol (SSE + WebSocket) since BedrockAgentCoreApp
does not support AGUI streaming. Lazy initialization defers heavy imports
to first request.
"""

import json
import os
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from starlette.responses import JSONResponse
from fastapi.responses import StreamingResponse
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
_agui_agent = None


def get_agent():
    global _agui_agent
    if _agui_agent is not None:
        return _agui_agent

    from typing import List
    from ag_ui_strands import StrandsAgent, StrandsAgentConfig, ToolBehavior
    from pydantic import BaseModel, Field
    from strands import Agent, tool
    from strands.models.bedrock import BedrockModel

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

    @tool
    def research_topic(query: str) -> str:
        """Research a topic and return findings."""
        return (
            f"Research findings for '{query}':\n\n"
            f"1. Key concepts and definitions related to {query}.\n"
            f"2. Current best practices and industry standards.\n"
            f"3. Common challenges and recommended solutions.\n"
            f"4. Recent developments and emerging trends.\n"
            f"5. Expert recommendations and actionable insights.\n\n"
            f"Use these findings to draft detailed document sections."
        )

    @tool
    def generate_outline(topic: str, num_sections: int) -> str:
        """Generate a document outline with section headings."""
        base = [
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
        return json.dumps([base[i % len(base)] for i in range(num_sections)])

    @tool
    def update_document(document: DocumentState) -> str:
        """Update the shared document state. Triggers STATE_SNAPSHOT."""
        return "Document updated successfully"

    def build_document_prompt(input_data, user_message: str) -> str:
        state_dict = getattr(input_data, "state", None)
        if isinstance(state_dict, dict) and "title" in state_dict:
            return f"Current document state:\n{json.dumps(state_dict, indent=2)}\n\nUser request: {user_message}"
        return user_message

    async def document_state_from_args(context):
        try:
            tool_input = context.tool_input
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input)
            doc_data = tool_input.get("document", tool_input)
            if isinstance(doc_data, dict):
                return doc_data
            if hasattr(doc_data, "model_dump"):
                return doc_data.model_dump()
            return None
        except Exception:
            return None

    shared_state_config = StrandsAgentConfig(
        state_context_builder=build_document_prompt,
        tool_behaviors={
            "update_document": ToolBehavior(
                skip_messages_snapshot=True,
                state_from_args=document_state_from_args,
            )
        },
    )

    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    )

    strands_agent = Agent(
        model=model,
        system_prompt=(
            "You are a collaborative document co-authoring assistant. "
            "You help users create, edit, and organize documents in real time.\n\n"
            "Tools:\n"
            "- research_topic: gather information on a topic\n"
            "- generate_outline: create document structure\n"
            "- update_document: modify the shared document (triggers STATE_SNAPSHOT)\n\n"
            "Always use update_document to add/edit/reorganize sections. "
            "The document has a title, sections (heading + body), and metadata "
            "(last_modified, version). Increment version on each update."
        ),
        tools=[research_topic, generate_outline, update_document],
    )

    _agui_agent = StrandsAgent(
        agent=strands_agent,
        name="document_agent",
        description="A document co-authoring assistant",
        config=shared_state_config,
    )
    return _agui_agent


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "ok"})


@app.post("/invocations")
async def invocations(input_data: dict, request: Request):
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
        import traceback

        traceback.print_exc()
        await websocket.close()


if __name__ == "__main__":
    host = os.environ.get("AGENT_RUNTIME_HOST", "0.0.0.0")  # nosec B104 - binding to all interfaces required for container runtime
    port = int(os.environ.get("AGENT_PORT", 8080))
    uvicorn.run(app, host=host, port=port, log_level="info")
