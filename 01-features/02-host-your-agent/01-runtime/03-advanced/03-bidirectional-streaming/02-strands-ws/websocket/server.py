import json
import logging
import os

import uvicorn
from agent import handle_websocket_session
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Large-event splitting (adapted from Sonic server for Strands event format)
# ---------------------------------------------------------------------------
MAX_WS_MESSAGE_SIZE = 10000


def split_large_event(event_dict, max_size=MAX_WS_MESSAGE_SIZE):
    """Split a large event into smaller chunks by dividing the audio field.

    For audio events, ensures splits occur at base64 boundaries to avoid
    corruption.  Returns a list of event dicts to send.
    """
    event_json = json.dumps(event_dict)
    event_size = len(event_json.encode("utf-8"))

    if event_size <= max_size:
        return [event_dict]

    event_type = event_dict.get("type", "unknown")

    # Only split events that have an 'audio' field
    if "audio" not in event_dict or not isinstance(event_dict["audio"], str):
        logger.warning(f"Event {event_type} is large ({event_size} bytes) but has no audio field to split")
        return [event_dict]

    audio_content = event_dict["audio"]

    # Measure overhead (everything except the audio content)
    template = {k: v for k, v in event_dict.items() if k != "audio"}
    template["audio"] = ""
    overhead = len(json.dumps(template).encode("utf-8"))

    # Max audio content per chunk (with margin)
    max_content_size = max_size - overhead - 100

    # Align to 4-char boundaries for valid base64 (4 base64 chars = 3 bytes)
    alignment = 4
    max_content_size = (max_content_size // alignment) * alignment

    if max_content_size <= 0:
        logger.warning(f"Cannot split {event_type}: overhead too large")
        return [event_dict]

    chunks = []
    for i in range(0, len(audio_content), max_content_size):
        chunk_audio = audio_content[i : i + max_content_size]

        # Ensure proper base64 padding if needed
        remainder = len(chunk_audio) % 4
        if remainder != 0:
            chunk_audio += "=" * (4 - remainder)

        chunk_event = {k: v for k, v in event_dict.items() if k != "audio"}
        chunk_event["audio"] = chunk_audio
        chunks.append(chunk_event)

    logger.info(f"Split {event_type} event ({event_size} bytes) into {len(chunks)} chunks")
    return chunks


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Strands BidiAgent WebSocket Server")

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
    """
    Health check endpoint required by AgentCore Runtime.
    Returns agent status and timestamp per AgentCore protocol contract.
    """
    import time

    return JSONResponse({"status": "Healthy", "time_of_last_update": int(time.time())})


@app.post("/invocations")
async def invocations(request: dict):
    """
    Traditional request/response endpoint required by AgentCore Runtime protocol.

    This agent is WebSocket-first and requires bidirectional streaming for audio.
    This endpoint provides information about how to connect properly.
    """
    return JSONResponse(
        {
            "message": "This agent requires WebSocket connection for bidirectional audio streaming.",
            "websocket_endpoint": "/ws",
            "instructions": "Connect to /ws endpoint and send a 'config' event with voice settings.",
            "config_event_format": {
                "type": "config",
                "voice": "tiffany",
                "input_sample_rate": 16000,
                "output_sample_rate": 16000,
            },
            "available_voices": ["tiffany", "matthew", "ruth", "gregory", "joanna"],
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Under Runtime V2 the container may have been restored from a deploy-time
    # snapshot, so refresh credentials before doing any AWS work in this session.

    async def chunked_send_json(event_dict):
        """Send output events, splitting large audio payloads into smaller chunks."""
        chunks = split_large_event(event_dict)
        for idx, chunk in enumerate(chunks):
            await websocket.send_json(chunk)
            if len(chunks) > 1:
                chunk_size = len(json.dumps(chunk).encode("utf-8"))
                logger.info(f"Forwarded chunk {idx + 1}/{len(chunks)} to client ({chunk_size} bytes)")

    await handle_websocket_session(websocket, send_output=chunked_send_json)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("PORT", "8080"))

    uvicorn.run(app, host=host, port=port)
