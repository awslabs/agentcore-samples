"""
LangChain Voice Agent WebSocket Server

FastAPI server with IMDS credential management and WebSocket endpoint.
Agent logic lives in agent.py (following the strands pattern).
"""

import logging
import os
import time

import uvicorn
from agent import handle_websocket_session
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
app = FastAPI(title="LangChain Voice Agent WebSocket Server")

# Set ALLOWED_ORIGINS env var (comma-separated) to restrict in production
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping")
async def ping():
    """Health check endpoint required by AgentCore Runtime."""
    return JSONResponse({"status": "Healthy", "time_of_last_update": int(time.time())})


@app.post("/invocations")
async def invocations(request: dict):
    """Traditional request/response endpoint required by AgentCore Runtime protocol."""
    return JSONResponse(
        {
            "message": "This agent uses the LangChain sandwich architecture (STT > Agent > TTS) "
            "and requires a WebSocket connection for bidirectional audio streaming.",
            "websocket_endpoint": "/ws",
            "architecture": "sandwich (STT: Amazon Transcribe, Agent: LangChain + Nova 2 Lite, TTS: Amazon Polly)",
            "config_event_format": {
                "type": "config",
                "voice": "Joanna",
                "input_sample_rate": 16000,
                "output_sample_rate": 16000,
            },
            "available_voices": ["Joanna", "Matthew", "Ruth", "Gregory", "Ivy"],
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Under Runtime V2 the container may have been restored from a deploy-time
    # snapshot, so refresh credentials before doing any AWS work in this session.

    await handle_websocket_session(websocket)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
