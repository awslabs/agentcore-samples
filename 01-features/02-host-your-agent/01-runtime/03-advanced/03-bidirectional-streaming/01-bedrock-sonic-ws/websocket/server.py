import asyncio
import json
import logging
import os
from datetime import datetime

import requests
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from requests.exceptions import RequestException
from s2s_session_manager import S2sSessionManager

# Configure logging
LOGLEVEL = os.environ.get("LOGLEVEL", "INFO").upper()
logging.basicConfig(level=LOGLEVEL, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


# Global variable to track credential refresh task
credential_refresh_task = None


def get_imdsv2_token():
    """
    Get IMDSv2 token for secure metadata access.

    Returns:
        str: The IMDSv2 token, or None if IMDSv2 is not available
    """
    try:
        response = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        )
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logger.debug(f"IMDSv2 token request failed: {e}")
    return None


def get_credentials_from_imds():
    """
    Manually retrieve IAM role credentials from envrionment Metadata Service.

    This utility method fetches credentials directly from IMDS without using boto3.
    It tries both IMDSv1 and IMDSv2 methods.

    Returns:
        dict: A dictionary containing the credentials or error information
    """
    result = {
        "success": False,
        "credentials": None,
        "role_name": None,
        "method_used": None,
        "error": None,
    }

    try:
        # Try IMDSv2 first
        token = get_imdsv2_token()
        headers = {}

        if token:
            headers["X-aws-ec2-metadata-token"] = token
            result["method_used"] = "IMDSv2"
        else:
            result["method_used"] = "IMDSv1"

        # Get the IAM role name
        role_response = requests.get(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            headers=headers,
            timeout=2,
        )

        if role_response.status_code != 200:
            result["error"] = f"Failed to retrieve IAM role name: HTTP {role_response.status_code}"
            return result

        role_name = role_response.text.strip()
        result["role_name"] = role_name

        # Get the credentials for the role
        creds_response = requests.get(
            f"http://169.254.169.254/latest/meta-data/iam/security-credentials/{role_name}",
            headers=headers,
            timeout=2,
        )

        if creds_response.status_code != 200:
            result["error"] = f"Failed to retrieve credentials for role {role_name}: HTTP {creds_response.status_code}"
            return result

        # Parse the credentials
        credentials = creds_response.json()

        result["success"] = True
        result["credentials"] = {
            "AccessKeyId": credentials.get("AccessKeyId"),
            "SecretAccessKey": credentials.get("SecretAccessKey"),
            "Token": credentials.get("Token"),
            "Expiration": credentials.get("Expiration"),
            "Code": credentials.get("Code"),
            "Type": credentials.get("Type"),
            "LastUpdated": credentials.get("LastUpdated"),
        }

    except RequestException as e:
        result["error"] = f"Request exception: {e!s}"
    except Exception as e:
        result["error"] = f"Unexpected error: {e!s}"

    return result


def refresh_credentials_now():
    """Re-fetch IMDS credentials into the environment before starting a session.

    Credentials must not be relied on from application startup. AgentCore Runtime
    V2 snapshots the container during deployment and restores that snapshot for
    later sessions, so anything captured at startup -- including credentials and
    the refresh timer -- is frozen at deploy time. A session restored hours later
    would otherwise sign requests with expired credentials and Bedrock returns
    403 ExpiredTokenException.

    Called at the start of every session so each one begins with valid credentials.
    """
    # Static credentials supplied by the operator take precedence (local mode).
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SESSION_TOKEN") is None:
        logger.info("Using static credentials from environment, skipping IMDS refresh")
        return True

    imds_result = get_credentials_from_imds()
    if not imds_result["success"]:
        logger.error(f"Could not refresh credentials from IMDS: {imds_result['error']}")
        return False

    creds = imds_result["credentials"]
    os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    os.environ["AWS_SESSION_TOKEN"] = creds["Token"]
    logger.info(f"Credentials refreshed for session (expire {creds.get('Expiration')})")
    return True


async def refresh_credentials_from_imds():
    """
    Background task to periodically refresh credentials from IMDS and update environment variables.
    This ensures the EnvironmentCredentialsResolver always has fresh credentials.
    """
    logger.info("Starting credential refresh background task")

    while True:
        try:
            # Fetch credentials from IMDS
            imds_result = get_credentials_from_imds()

            if imds_result["success"]:
                creds = imds_result["credentials"]

                # Update environment variables
                os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
                os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
                os.environ["AWS_SESSION_TOKEN"] = creds["Token"]

                logger.info("✅ Credentials refreshed from IMD.")

                # Parse expiration time and calculate refresh interval
                # Refresh 5 minutes before expiration
                try:
                    expiration = datetime.fromisoformat(creds["Expiration"].replace("Z", "+00:00"))
                    now = datetime.now(expiration.tzinfo)
                    time_until_expiration = (expiration - now).total_seconds()

                    # Refresh 5 minutes (300 seconds) before expiration, or in 1 hour if expiration is far away
                    refresh_interval = min(max(time_until_expiration - 300, 60), 3600)
                    logger.info(f"   Next refresh in {refresh_interval:.0f} seconds")
                except Exception as e:
                    logger.warning(f"Could not parse expiration time, using default 1 hour refresh: {e}")
                    refresh_interval = 3600

                # Wait until next refresh
                await asyncio.sleep(refresh_interval)
            else:
                logger.error(f"Failed to refresh credentials from IMDS: {imds_result['error']}")
                # Retry in 5 minutes on failure
                await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Credential refresh task cancelled")
            break
        except Exception as e:
            logger.exception("Error in credential refresh task")
            # Retry in 5 minutes on error
            await asyncio.sleep(300)


# Create FastAPI app
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


@app.on_event("startup")
async def startup_event():
    global credential_refresh_task

    logger.info("🚀 Application starting up...")
    logger.info(f"📍 AWS Region: {os.getenv('AWS_DEFAULT_REGION', 'us-east-1')}")

    # Check if credentials are already in environment (local mode)
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        logger.info("✅ Using credentials from environment variables (local mode)")
        logger.info("   Credential refresh task will not be started")
    else:
        # Try to fetch credentials from IMDS and start refresh task
        logger.info("🔄 Attempting to fetch credentials from ENV IMDS...")

        imds_result = get_credentials_from_imds()

        if imds_result["success"]:
            creds = imds_result["credentials"]

            # Set initial credentials in environment
            os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
            os.environ["AWS_SESSION_TOKEN"] = creds["Token"]

            logger.info("✅ Initial credentials loaded from IMDS.")

            # Start background task to refresh credentials
            credential_refresh_task = asyncio.create_task(refresh_credentials_from_imds())
            logger.info("🔄 Credential refresh background task started")
        else:
            logger.error(f"❌ Failed to fetch credentials from IMDS: {imds_result['error']}")
            logger.error("   Application may not function correctly without credentials")


@app.on_event("shutdown")
async def shutdown_event():

    logger.info("🛑 Application shutting down...")

    # Cancel credential refresh task if running
    if credential_refresh_task and not credential_refresh_task.done():
        logger.info("Stopping credential refresh task...")
        credential_refresh_task.cancel()
        try:
            await credential_refresh_task
        except asyncio.CancelledError:
            pass
        logger.info("Credential refresh task stopped")


@app.get("/health")
@app.get("/")
async def health_check():
    logger.info("Health check request received")
    return JSONResponse({"status": "healthy"})


@app.get("/ping")
async def ping():
    logger.debug("Ping endpoint called")
    return JSONResponse({"status": "ok"})


@app.get("/credentials/info")
async def credential_info():
    """Get information about credential configuration (for debugging)"""
    # Determine credential source
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        credential_source = "Environment Variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN)"
        mode = "local"
        note = "Using static credentials from environment variables"
    else:
        credential_source = "ENV IMDS (IMDSv2 preferred, falls back to IMDSv1)"
        mode = "ec2"
        note = "Credentials are automatically refreshed from IMDS by background task"

    return JSONResponse(
        {
            "status": "ok",
            "mode": mode,
            "credential_source": credential_source,
            "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            "note": note,
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    logger.info(f"WebSocket connection attempt from: {websocket.client}")
    logger.debug(f"Headers: {websocket.headers}")

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
                        refresh_credentials_now()

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
