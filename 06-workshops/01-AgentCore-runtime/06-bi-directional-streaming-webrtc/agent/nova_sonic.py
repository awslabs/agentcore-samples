"""Nova Sonic bidirectional streaming session.

Manages the full lifecycle of a Nova Sonic conversation:
1. Connect to Bedrock and open a bidirectional stream
2. Configure the session (model params, voice, system prompt)
3. Stream microphone audio in, receive spoken responses out
"""

import asyncio
import base64
import json
import uuid

from loguru import logger

from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
from aws_sdk_bedrock_runtime.config import Config
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from smithy_aws_core.auth.sigv4 import SigV4AuthScheme
from smithy_aws_core.identity.chain import create_default_chain
from smithy_http.aio.aiohttp import AIOHTTPClient

from audio import convert_to_16khz, INPUT_SAMPLE_RATE, OUTPUT_SAMPLE_RATE

MODEL_ID = "amazon.nova-2-sonic-v1:0"
VOICE_ID = "matthew"
SYSTEM_PROMPT = "You are a helpful AI assistant. Keep responses brief and conversational."

# Shared audio format used in both input and output config
_AUDIO_FORMAT = {
    "mediaType": "audio/lpcm",
    "sampleSizeBits": 16,
    "channelCount": 1,
    "encoding": "base64",
    "audioType": "SPEECH",
}


async def _send(stream, event_dict):
    """Send a single event to the Nova Sonic stream."""
    await stream.input_stream.send(
        InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=json.dumps(event_dict).encode("utf-8"))
        )
    )


async def _setup_session(stream, prompt_name):
    """Configure the Nova Sonic session: model params, system prompt, audio format."""

    # 1. Start session with inference parameters
    await _send(
        stream,
        {
            "event": {
                "sessionStart": {
                    "inferenceConfiguration": {
                        "maxTokens": 1024,
                        "topP": 0.9,
                        "temperature": 0.7,
                    }
                }
            }
        },
    )

    # 2. Configure audio output (Nova Sonic -> browser)
    await _send(
        stream,
        {
            "event": {
                "promptStart": {
                    "promptName": prompt_name,
                    "audioOutputConfiguration": {
                        **_AUDIO_FORMAT,
                        "sampleRateHertz": OUTPUT_SAMPLE_RATE,
                        "voiceId": VOICE_ID,
                    },
                }
            }
        },
    )

    # 3. Send system prompt
    system_content = str(uuid.uuid4())
    await _send(
        stream,
        {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": system_content,
                    "type": "TEXT",
                    "interactive": True,
                    "role": "SYSTEM",
                    "textInputConfiguration": {"mediaType": "text/plain"},
                }
            }
        },
    )
    await _send(
        stream,
        {
            "event": {
                "textInput": {
                    "promptName": prompt_name,
                    "contentName": system_content,
                    "content": SYSTEM_PROMPT,
                }
            }
        },
    )
    await _send(
        stream,
        {
            "event": {
                "contentEnd": {
                    "promptName": prompt_name,
                    "contentName": system_content,
                }
            }
        },
    )


async def _start_audio_input(stream, prompt_name, audio_content_name):
    """Tell Nova Sonic to expect audio input in our format."""
    await _send(
        stream,
        {
            "event": {
                "contentStart": {
                    "promptName": prompt_name,
                    "contentName": audio_content_name,
                    "type": "AUDIO",
                    "interactive": True,
                    "role": "USER",
                    "audioInputConfiguration": {
                        **_AUDIO_FORMAT,
                        "sampleRateHertz": INPUT_SAMPLE_RATE,
                    },
                }
            }
        },
    )


async def run_session(audio_in, audio_out, region, pc_id):
    """Run a full Nova Sonic conversation session.

    Args:
        audio_in:  WebRTC MediaStreamTrack (microphone from browser)
        audio_out: OutputTrack (plays Nova Sonic responses to browser)
        region:    AWS region for Bedrock
        pc_id:     Peer connection ID for logging
    """
    logger.info(f"Starting Nova Sonic session for {pc_id}")

    # --- Connect to Bedrock ---
    client = BedrockRuntimeClient(
        Config(
            endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
            region=region,
            aws_credentials_identity_resolver=create_default_chain(AIOHTTPClient()),
            auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
        )
    )
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
    )

    # --- Configure session ---
    prompt_name = str(uuid.uuid4())
    audio_content_name = str(uuid.uuid4())
    await _setup_session(stream, prompt_name)
    await _start_audio_input(stream, prompt_name, audio_content_name)

    # --- Receive responses (runs concurrently with audio sending) ---
    # The ready event gates audio sending until Nova Sonic acknowledges the session.
    # The 0.5s timeout is a fallback in case the first event is delayed.
    ready = asyncio.Event()
    content_roles = {}  # contentId -> role

    async def receive_responses():
        try:
            while True:
                output = await stream.await_output()
                result = await output[1].receive()
                if not (result.value and result.value.bytes_):
                    continue

                event = json.loads(result.value.bytes_.decode("utf-8")).get("event", {})

                if not ready.is_set():
                    ready.set()

                if "contentStart" in event:
                    cs = event["contentStart"]
                    if cid := cs.get("contentId"):
                        content_roles[cid] = cs.get("role", "ASSISTANT")
                elif "audioOutput" in event:
                    audio_out.add_audio(base64.b64decode(event["audioOutput"]["content"]))
                elif "textOutput" in event:
                    to = event["textOutput"]
                    content = to["content"]
                    role = content_roles.get(to.get("contentId"), "ASSISTANT")
                    # Barge-in: Nova Sonic sends this before contentEnd INTERRUPTED
                    if "interrupted" in content and "true" in content:
                        logger.info("Barge-in detected, clearing audio queue")
                        audio_out.clear()
                    else:
                        label = "User" if role == "USER" else "Nova Sonic"
                        logger.info(f"{label}: {content}")
                elif "contentEnd" in event:
                    ce = event["contentEnd"]
                    if ce.get("stopReason") == "INTERRUPTED":
                        audio_out.clear()
                    content_roles.pop(ce.get("contentId"), None)
        except Exception as e:
            logger.error(f"Receive error: {e}")

    recv_task = asyncio.create_task(receive_responses())

    await asyncio.sleep(0.5)
    if not ready.is_set():
        ready.set()
    await ready.wait()
    logger.info("Session ready, streaming audio")

    # --- Stream microphone audio to Nova Sonic ---
    try:
        frame_count = 0
        while True:
            pcm = convert_to_16khz(await audio_in.recv())
            if not pcm:
                continue

            frame_count += 1
            if frame_count % 500 == 0:
                logger.info(f"Sent {frame_count} audio frames")

            await _send(
                stream,
                {
                    "event": {
                        "audioInput": {
                            "promptName": prompt_name,
                            "contentName": audio_content_name,
                            "content": base64.b64encode(pcm).decode("utf-8"),
                        }
                    }
                },
            )
    except Exception as e:
        logger.error(f"Audio send error: {e}")
    finally:
        recv_task.cancel()
        try:
            await stream.close()
        except Exception:
            pass
