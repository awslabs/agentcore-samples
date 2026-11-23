"""Bridge the browser's PCM protocol to the Strands bidirectional SDK."""

import base64
import logging

from fastapi import WebSocket, WebSocketDisconnect
from strands.bidi.agent import BidiAgent
from strands.bidi.models.bedrock import BedrockNovaSonicModel
from strands.bidi.models.google import GoogleGeminiLiveModel
from strands.bidi.models.openai.realtime import OpenAIRealtimeModel
from strands.bidi.types.media import AudioDelta

logger = logging.getLogger(__name__)
DEFAULT_PROMPT = "You are a friendly voice assistant. Keep responses brief. Do not request personal information."


def model_for(config):
    model_id = config.get("model_id", "amazon.nova-2-sonic-v1:0")
    rate = config.get("input_sample_rate", 16000)
    if model_id.startswith("amazon.nova"):
        return BedrockNovaSonicModel(
            model_id=model_id,
            region=config.get("region", "us-east-1"),
            voice=config.get("voice", "matthew"),
            audio={"input": {"sample_rate": rate}, "output": {"sample_rate": config.get("output_sample_rate", 16000)}},
        )
    if model_id.startswith("gpt-"):
        return OpenAIRealtimeModel(
            model_id=model_id,
            transcription_model_id="whisper-1",
            api_key=config.get("api_key"),
            voice=config.get("voice", "alloy"),
        )
    if model_id.startswith("gemini"):
        return GoogleGeminiLiveModel(
            model_id=model_id,
            client_args={"api_key": config["api_key"]} if config.get("api_key") else None,
            audio={"input": {"sample_rate": rate}},
            voice=config.get("voice"),
        )
    raise ValueError("Unsupported voice provider")


def browser_event(event):
    kind = event.get("type")
    if kind == "bidi_audio_delta":
        return {"type": "bidi_audio_stream", "audio": event["audio"], "sample_rate": event["sample_rate"]}
    if kind == "bidi_transcript_block":
        return {"type": "bidi_transcript_stream", "role": event["role"], "text": event["transcript"]}
    if kind == "bidi_barge_in":
        return {"type": "bidi_interruption"}
    if kind == "bidi_response_stop":
        return {"type": "bidi_response_complete"}
    return None


async def handle_websocket_session(websocket: WebSocket, send_output=None):
    send = send_output or websocket.send_json
    try:
        config = await websocket.receive_json()
        if config.get("type") != "config":
            raise ValueError("Expected initial configuration")
        agent = BidiAgent(
            model=model_for(config), tools=[], system_prompt=config.get("system_prompt") or DEFAULT_PROMPT
        )
        await send({"type": "system", "message": "Configuration applied. Agent ready."})

        async def read():
            while True:
                msg = await websocket.receive_json()
                if msg.get("type") == "text_input":
                    return msg["text"]
                if msg.get("type") == "bidi_audio_input" and msg.get("format") == "pcm":
                    return AudioDelta(format="pcm", source={"bytes": base64.b64decode(msg["audio"], validate=True)})
                await send(
                    {"type": "system", "message": "Expected text or PCM audio; reconnect to change configuration."}
                )

        async def write(event):
            payload = browser_event(event)
            if payload is not None:
                await send(payload)

        await agent.run(inputs=[read], outputs=[write])
    except WebSocketDisconnect:
        logger.info("Voice client disconnected")
    except Exception as exc:
        # Provider errors can include authentication metadata; never echo or log their body.
        logger.warning("Voice session stopped (%s)", type(exc).__name__)
        await send({"type": "error", "message": "Voice session failed; check the provider, model and permissions."})
