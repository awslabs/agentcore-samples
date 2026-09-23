import logging
import os
import traceback

from fastapi import WebSocket, WebSocketDisconnect

from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel

logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = """You are a friendly companion having a casual chat. Be warm, conversational, and natural. Keep responses concise and engaging."""


def get_system_prompt() -> str:
    """Get the default system prompt for the banking assistant."""
    return DEFAULT_SYSTEM_PROMPT


async def handle_websocket_session(websocket: WebSocket, default_gateway_arns: list, send_output=None):
    """
    Handle a WebSocket session: wait for config event, initialize agent, and run.

    Args:
        websocket: The accepted WebSocket connection.
        default_gateway_arns: Gateway ARNs from environment (used as fallback).
        send_output: Optional async callable for sending output events. Defaults to websocket.send_json.
    """
    agent = None
    output_fn = send_output or websocket.send_json

    logger.info("New WebSocket connection")
    logger.info("⏳ Waiting for config event from client...")

    try:
        # Wait for initial config event
        config, api_key, system_prompt = await _wait_for_config(websocket)
        if config is None:
            return

        # Initialize agent from config
        agent = _create_agent(
            config,
            default_gateway_arns,
            api_key=api_key,
            system_prompt=system_prompt,
        )
        logger.info("✅ Agent initialized successfully")  # config details logged in _wait_for_config

        # Send acknowledgment back to client
        await websocket.send_json(
            {
                "type": "system",
                "message": "Configuration applied. Agent ready.",
            }
        )

        # Define input handler
        async def handle_websocket_input():
            """Handle incoming messages from the client, filtering config, text, and audio."""
            while True:
                message = await websocket.receive_json()

                # Handle subsequent config events (not allowed after initialization)
                if message.get("type") == "config":
                    logger.info("⚠️ Config event received after initialization - ignoring")
                    await websocket.send_json(
                        {
                            "type": "system",
                            "message": "Configuration can only be set once per session. Please reconnect to change settings.",
                        }
                    )
                    continue

                # Check if it's a text message from the client
                elif message.get("type") == "text_input":
                    text = message.get("text", "")
                    logger.info("Received text input")
                    await agent.send(text)
                    continue

                # Audio and other events - pass through to agent
                else:
                    return message

        # Start the agent with the input handler
        await agent.run(inputs=[handle_websocket_input], outputs=[output_fn])

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        # Ignore AWS CRT cancelled future errors during cleanup
        if "InvalidStateError" in type(e).__name__ or "CANCELLED" in str(e):
            logger.warning("Ignoring CRT cleanup error")
        else:
            logger.error("Session error: %s", type(e).__name__)
            traceback.print_exc()
            try:
                await output_fn({"type": "error", "message": str(e)})
            except Exception:
                pass
    finally:
        logger.info("Connection closed")


async def _wait_for_config(
    websocket: WebSocket,
) -> tuple[dict | None, str | None, str | None]:
    """Wait for the initial config event from the client.

    Returns (config_dict, api_key, system_prompt) — sensitive and
    user-provided text fields are kept separate so the config dict
    stays free of tainted data for CodeQL compliance.
    """
    while True:
        message = await websocket.receive_json()

        if message.get("type") == "config":
            voice = message.get("voice", "tiffany")
            input_sr = message.get("input_sample_rate", 16000)
            output_sr = message.get("output_sample_rate", 16000)
            model_id = message.get("model_id", "amazon.nova-2-sonic-v1:0")
            region = message.get("region", "us-east-1")
            gateway_arns = message.get("gateway_arns", None)

            logger.info("📥 Received config event")

            config = {
                "voice": voice,
                "input_sample_rate": input_sr,
                "output_sample_rate": output_sr,
                "model_id": model_id,
                "region": region,
                "gateway_arns": gateway_arns,
            }
            return (
                config,
                message.get("api_key", None),
                message.get("system_prompt", None),
            )
        else:
            logger.warning("⚠️ Expected config event, got unexpected message type")
            await websocket.send_json({"type": "system", "message": "Please send config event first"})


def _create_agent(
    config: dict,
    default_gateway_arns: list,
    api_key: str = None,
    system_prompt: str = None,
) -> BidiAgent:
    """Create and return a BidiAgent from the given config."""
    # Use gateway ARNs from config if provided, otherwise use environment defaults
    effective_gateway_arns = config["gateway_arns"] if config["gateway_arns"] else default_gateway_arns
    effective_system_prompt = system_prompt if system_prompt else get_system_prompt()

    if config["gateway_arns"]:
        num_gateways = len(config["gateway_arns"])
        logger.info("   Gateways: %d from config event", num_gateways)
    else:
        logger.info("   Gateways: %d from environment", len(default_gateway_arns))

    logger.info("🎤 Initializing agent...")

    model = _create_model(config, effective_gateway_arns, api_key=api_key)

    return BidiAgent(
        model=model,
        tools=[],
        system_prompt=effective_system_prompt,
    )


def _create_model(config: dict, effective_gateway_arns: list, api_key: str = None):
    """Create the appropriate BidiModel based on model_id."""
    model_id = config["model_id"]

    # Nova Sonic
    if model_id.startswith("amazon.nova"):
        return BidiNovaSonicModel(
            client_config={"region": config.get("region", "us-east-1")},
            model_id=model_id,
            provider_config={
                "audio": {
                    "input_rate": config["input_sample_rate"],
                    "output_rate": config["output_sample_rate"],
                    "voice": config["voice"],
                }
            },
            mcp_gateway_arn=effective_gateway_arns,
        )

    # OpenAI Realtime
    elif model_id.startswith("gpt-"):
        logger.info("Using OpenAI RealTime Model")
        try:
            from strands.experimental.bidi.models.openai_realtime import (
                BidiOpenAIRealtimeModel,
            )
        except ImportError:
            raise RuntimeError("OpenAI Realtime support not installed. Run: pip install 'strands-agents[bidi-openai]'")

        openai_key = api_key or os.getenv("OPENAI_API_KEY")
        if not openai_key:
            raise RuntimeError("OpenAI API key is required. Provide it via config or OPENAI_API_KEY env var.")

        return BidiOpenAIRealtimeModel(
            model_id=model_id,
            provider_config={
                "audio": {
                    "voice": config["voice"],
                }
            },
            client_config={"api_key": openai_key},
            mcp_gateway_arn=effective_gateway_arns,
        )

    # Gemini Live
    elif model_id.startswith("gemini"):
        logger.info("Using Gemini Live Model")
        try:
            from strands.experimental.bidi.models.gemini_live import BidiGeminiLiveModel
        except ImportError:
            raise RuntimeError("Gemini Live support not installed. Run: pip install 'strands-agents[bidi-gemini]'")

        google_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise RuntimeError("Google API key is required. Provide it via config or GOOGLE_API_KEY env var.")

        return BidiGeminiLiveModel(
            model_id=model_id,
            provider_config={
                "audio": {
                    "input_rate": config["input_sample_rate"],
                    "output_rate": config["output_sample_rate"],
                }
            },
            client_config={"api_key": google_key},
            mcp_gateway_arn=effective_gateway_arns,
        )

    else:
        raise RuntimeError(f"Unsupported model_id: {model_id}")
