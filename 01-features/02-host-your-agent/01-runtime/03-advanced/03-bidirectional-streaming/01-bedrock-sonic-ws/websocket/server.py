import asyncio
import json
import logging
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from s2s_session_manager import S2sSessionManager

# Configure logging
LOGLEVEL = os.environ.get("LOGLEVEL", "INFO").upper()
logging.basicConfig(level=LOGLEVEL, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


# Global variable to track credential refresh task
app = FastAPI(title="Nova Sonic S2S WebSocket Server")

# Add CORS middleware — set ALLOWED_ORIGINS env var (comma-separated) to restrict in production
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/")
async def health_check():
    logger.info("Health check request received")
    return JSONResponse({"status": "healthy"})


@app.get("/ping")
async def ping():
    logger.debug("Ping endpoint called")
    return JSONResponse({"status": "ok"})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logger.info(f"WebSocket connection attempt from: {websocket.client}")

    # Accept the WebSocket connection
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    stream_manager = None
    forward_task = None

    try:
        # Main message processing loop
        while True:
            try:
                message = await websocket.receive_text()
                logger.debug("Received message from client")

                try:
                    data = json.loads(message)

                    # Handle wrapped body format
                    if "body" in data:
                        data = json.loads(data["body"])

                    if "event" not in data:
                        logger.warning("Received message without event field")
                        continue

                    event_type = next(iter(data["event"].keys()))

                    # Handle session start - create new stream manager
                    if event_type == "sessionStart":
                        logger.info("Starting new session")

                        # Clean up existing session if any
                        if stream_manager:
                            logger.info("Cleaning up existing session")
                            await stream_manager.close()
                        if forward_task and not forward_task.done():
                            forward_task.cancel()
                            try:
                                await forward_task
                            except asyncio.CancelledError:
                                pass

                        # Refresh credentials before each session. Under Runtime V2
                        # the container may have been restored from a snapshot taken
                        # at deploy time, so startup credentials are likely expired.

                        # Create a new stream manager for this connection
                        stream_manager = S2sSessionManager(model_id="amazon.nova-2-sonic-v1:0", region=aws_region)

                        # Initialize the Bedrock stream
                        await stream_manager.initialize_stream()
                        logger.info("Stream initialized successfully")

                        # Start a task to forward responses from Bedrock to the WebSocket
                        forward_task = asyncio.create_task(forward_responses(websocket, stream_manager))

                        # Now send the sessionStart event to Bedrock
                        await stream_manager.send_raw_event(data)
                        logger.info(f"SessionStart event sent to Bedrock {json.dumps(data)}")

                        # Continue to next iteration to process next event
                        continue

                    # Handle session end - clean up resources
                    elif event_type == "sessionEnd":
                        logger.info("Ending session")

                        if stream_manager:
                            await stream_manager.close()
                            stream_manager = None
                        if forward_task and not forward_task.done():
                            forward_task.cancel()
                            try:
                                await forward_task
                            except asyncio.CancelledError:
                                pass
                            forward_task = None

                        # Continue to next iteration
                        continue

                    # Process events if we have an active stream manager
                    if stream_manager and stream_manager.is_active:
                        # Store prompt name and content names if provided
                        if event_type == "promptStart":
                            stream_manager.prompt_name = data["event"]["promptStart"]["promptName"]
                        elif event_type == "contentStart" and data["event"]["contentStart"].get("type") == "AUDIO":
                            stream_manager.audio_content_name = data["event"]["contentStart"]["contentName"]

                        # Handle audio input separately (queue-based processing)
                        if event_type == "audioInput":
                            prompt_name = data["event"]["audioInput"]["promptName"]
                            content_name = data["event"]["audioInput"]["contentName"]
                            audio_base64 = data["event"]["audioInput"]["content"]

                            # Add to the audio queue for async processing
                            stream_manager.add_audio_chunk(prompt_name, content_name, audio_base64)
                        else:
                            # Send other events directly to Bedrock
                            await stream_manager.send_raw_event(data)
                    elif event_type not in ["sessionStart", "sessionEnd"]:
                        logger.warning(f"Received event {event_type} but no active stream manager")

                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON received from WebSocket: {e}")
                    try:
                        await websocket.send_json({"type": "error", "message": "Invalid JSON format"})
                    except Exception:
                        logger.debug("Could not notify client of invalid JSON; connection likely closed")
                except Exception as exp:
                    logger.exception("Error processing WebSocket message")
                    try:
                        await websocket.send_json({"type": "error", "message": str(exp)})
                    except Exception:
                        logger.debug("Could not notify client of processing error; connection likely closed")

            except WebSocketDisconnect as e:
                logger.info(f"WebSocket disconnected: {websocket.client}")
                logger.info(
                    f"Disconnect details: code={getattr(e, 'code', 'N/A')}, reason={getattr(e, 'reason', 'N/A')}"
                )
                if stream_manager and stream_manager.is_active:
                    logger.info("Bedrock stream was still active when WebSocket disconnected")
                break
            except Exception as e:
                logger.exception("WebSocket error")
                break

    except Exception as e:
        logger.exception("WebSocket handler error")
        try:
            await websocket.send_json({"type": "error", "message": "WebSocket handler error"})
        except Exception:
            logger.debug("Could not notify client of handler error; connection likely closed")
    finally:
        # Clean up resources
        logger.info("Cleaning up WebSocket connection resources")

        if stream_manager:
            await stream_manager.close()
        if forward_task and not forward_task.done():
            forward_task.cancel()
            try:
                await forward_task
            except asyncio.CancelledError:
                pass

        try:
            await websocket.close()
        except Exception as e:
            logger.error(f"Error closing websocket: {e}")

        logger.info("Connection closed")


def split_large_event(response, max_size=16000):
    """
    Split a large event into smaller chunks by dividing the content field.
    For audio events, ensures splits occur at sample boundaries to avoid noise.
    Returns a list of events to send.
    """
    event = json.dumps(response)
    event_size = len(event.encode("utf-8"))

    # If event is small enough, return as-is
    if event_size <= max_size:
        return [response]

    # Get event type and data
    if "event" not in response:
        return [response]

    event_type = next(iter(response["event"].keys()))
    event_data = response["event"][event_type]

    # Only split events that have a 'content' field (audioOutput, textOutput, etc.)
    if "content" not in event_data:
        logger.warning(f"Event {event_type} is large ({event_size} bytes) but has no content field to split")
        return [response]

    content = event_data["content"]

    # Calculate how much content we can fit per chunk
    # Create a template event to measure overhead
    template_event = response.copy()
    template_event["event"] = {event_type: event_data.copy()}
    template_event["event"][event_type]["content"] = ""
    overhead = len(json.dumps(template_event).encode("utf-8"))

    # Calculate max content size per chunk (leave some margin)
    max_content_size = max_size - overhead - 100

    # For audio events, align to sample boundaries
    # Base64 encoding: 4 chars = 3 bytes of binary data
    # PCM 16-bit: 2 bytes per sample
    # Must align to multiples of 4 chars for valid base64 (no padding issues)
    if event_type == "audioOutput":
        # Align to 4-char boundaries for complete base64 groups
        # This ensures each chunk is valid base64 without padding issues
        alignment = 4
        max_content_size = (max_content_size // alignment) * alignment
        logger.debug(f"Audio splitting: aligned chunk size to {max_content_size} chars (base64 boundary)")

    # Split content into chunks
    chunks = []
    for i in range(0, len(content), max_content_size):
        chunk_content = content[i : i + max_content_size]

        # For base64 content, ensure proper padding if needed
        if event_type == "audioOutput":
            # Each chunk should be a multiple of 4 chars (already aligned above)
            # But verify and add padding if somehow needed
            remainder = len(chunk_content) % 4
            if remainder != 0:
                # This shouldn't happen due to alignment, but just in case
                padding_needed = 4 - remainder
                chunk_content += "=" * padding_needed
                logger.warning(f"Added {padding_needed} padding chars to audio chunk")

        # Create new event with chunked content
        chunk_event = response.copy()
        chunk_event["event"] = {event_type: event_data.copy()}
        chunk_event["event"][event_type]["content"] = chunk_content

        chunks.append(chunk_event)

    logger.info(f"Split {event_type} event ({event_size} bytes) into {len(chunks)} chunks")
    return chunks


async def forward_responses(websocket: WebSocket, stream_manager):
    """Forward responses from Bedrock to the WebSocket client."""
    try:
        while True:
            # Get next response from the output queue
            response = await stream_manager.output_queue.get()

            # Send to WebSocket
            try:
                # Check if event needs to be split
                event = json.dumps(response)
                event_size = len(event.encode("utf-8"))

                # Get event type for logging
                event_type = next(iter(response.get("event", {}).keys())) if "event" in response else "unknown"

                # Split large events
                if event_size > 10000:
                    logger.warning(f"!!!! Large {event_type} event detected (size: {event_size} bytes) - splitting...")
                    events_to_send = split_large_event(response, max_size=10000)
                else:
                    events_to_send = [response]

                # Send all chunks
                for idx, event_chunk in enumerate(events_to_send):
                    chunk_json = json.dumps(event_chunk)
                    chunk_size = len(chunk_json.encode("utf-8"))

                    await websocket.send_text(chunk_json)

                    if len(events_to_send) > 1:
                        logger.info(
                            f"Forwarded {event_type} chunk {idx + 1}/{len(events_to_send)} to client (size: {chunk_size} bytes)"
                        )
                    else:
                        logger.info(f"Forwarded {event_type} to client (size: {chunk_size} bytes)")

            except Exception as e:
                logger.exception("Error sending response to client")
                # Check if it's a connection error that should break the loop
                error_str = str(e).lower()
                if "closed" in error_str or "disconnect" in error_str:
                    logger.info("WebSocket connection closed, stopping forward task")
                    break
                # For other errors, log but continue trying
                logger.warning("Continuing to forward responses despite error")
    except asyncio.CancelledError:
        logger.debug("Forward responses task cancelled")
    except Exception as e:
        logger.exception("Error forwarding responses")
    finally:
        logger.info("Forward responses task ended")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Nova Sonic S2S WebSocket Server")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        DEBUG = True
        logging.getLogger().setLevel(logging.DEBUG)

    host = os.getenv("HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("PORT", "8080"))

    logger.info(f"Starting Nova Sonic S2S WebSocket Server on {host}:{port}")

    try:
        uvicorn.run(app, host=host, port=port)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
