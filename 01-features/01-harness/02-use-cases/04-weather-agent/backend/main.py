"""FastAPI backend for the Weather Agent web app."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from agent import invoke_agent
from evaluation import run_batch_evaluation
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from observability import get_recent_traces, get_transaction_search_status
from optimization import run_optimization
from pydantic import BaseModel
from resources import ensure_resources
from skills import generate_weather_report
from sse_starlette.sse import EventSourceResponse

# Global state
_state: dict = {}
_sessions: dict[str, list] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _state
    print("[backend] Starting — provisioning AWS resources...")
    _state = await asyncio.to_thread(ensure_resources)
    print(f"[backend] Ready. Harness: {_state['harness_id']}")
    yield
    print("[backend] Shutting down")


app = FastAPI(title="Weather Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class EvalRequest(BaseModel):
    session_id: str


class ReportRequest(BaseModel):
    session_id: str
    city: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "harness_id": _state.get("harness_id")}


@app.get("/api/status")
async def status():
    return {
        "ready": bool(_state.get("harness_id")),
        "harness_id": _state.get("harness_id"),
        "harness_name": _state.get("harness_name"),
        "gateway_id": _state.get("gateway_id"),
        "gateway_name": _state.get("gateway_name"),
        "guardrail_id": _state.get("guardrail_id"),
        "guardrail_name": _state.get("guardrail_name"),
        "region": _state.get("region"),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not _state.get("harness_arn"):
        raise HTTPException(503, "Resources not ready")

    session_id = req.session_id or str(uuid.uuid4()).upper()

    if session_id not in _sessions:
        _sessions[session_id] = []

    _sessions[session_id].append({"role": "user", "content": req.message})

    async def generate():
        full_text = ""
        failed = False
        yield json.dumps({"type": "session_id", "session_id": session_id})

        # The guardrail is passed in so the finished answer is actually screened.
        # It used to be created, billed and shown in the UI header while never
        # being applied to a single response.
        for event in invoke_agent(
            _state["harness_arn"],
            _state["gateway_arn"],
            session_id,
            req.message,
            guardrail_id=_state.get("guardrail_id"),
            guardrail_version=_state.get("guardrail_version"),
        ):
            if event["type"] == "text":
                full_text += event["content"]
            elif event["type"] == "redacted":
                # Store the screened text, not the raw text: otherwise the PII
                # the guardrail just removed would be handed straight back to the
                # model as history on the next turn, and shown by /api/sessions.
                full_text = event["content"]
            elif event["type"] == "error":
                failed = True
            yield json.dumps(event)

        # Only record a reply that exists. A turn that failed produced no text,
        # and appending it anyway left an empty assistant message in the session
        # — which /api/sessions then reported as the session's `last_message`,
        # showing a blank entry for a turn that never worked.
        if full_text:
            _sessions[session_id].append({"role": "assistant", "content": full_text})
        elif failed:
            _sessions[session_id].append({"role": "assistant", "content": "(no response — the turn failed)"})

    return EventSourceResponse(generate(), media_type="text/event-stream")


@app.get("/api/traces")
async def traces(minutes: int = 10):
    result = await asyncio.to_thread(get_recent_traces, _state.get("harness_name"), minutes)
    tx_status = await asyncio.to_thread(get_transaction_search_status)
    return {"traces": result, "transaction_search": tx_status}


@app.post("/api/evaluate")
async def evaluate(req: EvalRequest):
    if not _state.get("harness_id"):
        raise HTTPException(503, "Resources not ready")

    result = await asyncio.to_thread(
        run_batch_evaluation,
        _state["harness_id"],
        _state.get("harness_name"),
    )
    return {"session_id": req.session_id, **result}


@app.post("/api/generate-report")
async def generate_report(req: ReportRequest):
    if not _state.get("harness_arn"):
        raise HTTPException(503, "Resources not ready")

    result = await asyncio.to_thread(
        generate_weather_report,
        _state["harness_arn"],
        _state["harness_id"],
        req.session_id,
        req.city or "the cities discussed",
    )
    return result


@app.post("/api/optimize")
async def optimize():
    if not _state.get("harness_name"):
        raise HTTPException(503, "Resources not ready")

    result = await asyncio.to_thread(run_optimization, _state["harness_name"])
    return result


@app.get("/api/sessions")
async def sessions():
    return {
        sid: {"turns": len(msgs), "last_message": msgs[-1]["content"][:80] if msgs else ""}
        for sid, msgs in _sessions.items()
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
