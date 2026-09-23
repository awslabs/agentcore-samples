"""Minimal WebRTC Voice Agent with Nova Sonic.

FastAPI server that bridges WebRTC audio from the browser to Nova Sonic
via KVS TURN servers. Exposes a single /invocations endpoint that handles
ICE config, WebRTC offer/answer, and ICE candidate exchange.
"""

import argparse
import os
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import kvs
from audio import OutputTrack
from nova_sonic import run_session

load_dotenv(override=True)

CHANNEL_NAME = os.getenv("KVS_CHANNEL_NAME", "voice-agent-minimal")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

# Active peer connections, keyed by pc_id
peer_connections = {}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    kvs.init(CHANNEL_NAME, AWS_REGION)
    yield
    for pc in peer_connections.values():
        await pc.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/ping")
async def ping():
    """Health check for AgentCore Runtime."""
    return {"status": "Healthy", "time_of_last_update": int(time.time())}


@app.post("/invocations")
async def invocations(request: dict, background_tasks: BackgroundTasks):
    """Main endpoint — routes ICE config, offer/answer, and ICE candidate actions."""
    action = request.get("action")

    if action == "ice_config":
        return _handle_ice_config()
    elif action == "offer":
        return await _handle_offer(request.get("data", {}), background_tasks)
    elif action == "ice_candidate":
        return await _handle_ice_candidate(request.get("data", {}))
    elif action == "disconnect":
        return await _handle_disconnect(request.get("data", {}))

    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _handle_ice_config():
    """Return KVS TURN/STUN server credentials for the browser."""
    return {
        "iceServers": [
            {
                "urls": server["Uris"],
                "username": server.get("Username"),
                "credential": server.get("Password"),
            }
            for server in kvs.get_ice_servers(AWS_REGION, client_id="web-client")
        ]
    }


async def _handle_offer(data, background_tasks):
    """Accept a WebRTC offer, create a peer connection, return an answer."""
    ice_servers = kvs.get_rtc_ice_servers(AWS_REGION, client_id="server", turn_only=data.get("turnOnly", False))

    # Create peer connection with output audio track
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    audio_out = OutputTrack()
    pc.addTrack(audio_out)

    pc_id = f"pc_{len(peer_connections)}"
    peer_connections[pc_id] = pc

    # When browser's audio track arrives, start Nova Sonic session
    @pc.on("track")
    async def on_track(track):
        if track.kind == "audio":
            background_tasks.add_task(run_session, track, audio_out, AWS_REGION, pc_id)

    @pc.on("iceconnectionstatechange")
    async def on_ice_state():
        logger.info(f"ICE state: {pc.iceConnectionState}")

    # SDP offer/answer exchange
    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {
        "pc_id": pc_id,
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
    }


async def _handle_disconnect(data):
    """Close and remove a peer connection."""
    pc = peer_connections.pop(data.get("pc_id"), None)
    if pc:
        await pc.close()
    return {"status": "success"}


async def _handle_ice_candidate(data):
    """Add trickled ICE candidates to an existing peer connection."""
    pc = peer_connections.get(data.get("pc_id"))
    if not pc:
        return {"status": "success"}

    for candidate_data in data.get("candidates", []):
        try:
            # Strip "candidate:" prefix that browsers include
            raw = candidate_data.get("candidate", "")
            if raw.startswith("candidate:"):
                raw = raw.split(":", 1)[1]

            candidate = candidate_from_sdp(raw)
            candidate.sdpMid = candidate_data.get("sdp_mid")
            candidate.sdpMLineIndex = candidate_data.get("sdp_mline_index")
            await pc.addIceCandidate(candidate)
        except Exception as e:
            logger.error(f"ICE candidate error: {e}")

    return {"status": "success"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("-v", "--verbose", action="count")
    args = parser.parse_args()

    logger.remove(0)
    logger.add(sys.stderr, level="TRACE" if args.verbose else "DEBUG")
    uvicorn.run(app, host=args.host, port=args.port)
