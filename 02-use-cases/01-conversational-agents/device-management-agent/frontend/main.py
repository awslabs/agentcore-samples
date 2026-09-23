"""
Device Management System - Frontend Web Application

This module implements the web-based user interface for the Device Management System
using FastAPI, WebSockets, and Amazon Cognito authentication. It provides a chat-like
interface where users can interact with their IoT devices using natural language.

Key Features:
    - Real-time chat interface using WebSockets
    - Amazon Cognito authentication integration
    - Session management with CSRF protection
    - Responsive web design with Jinja2 templates
    - CORS configuration for secure cross-origin requests
    - Error handling and user-friendly messaging

Architecture:
    - FastAPI: Web framework and API endpoints
    - WebSocket: Real-time communication with Agent Runtime
    - Jinja2: HTML template rendering
    - Session Middleware: Secure session management
    - CORS Middleware: Cross-origin request handling
    - Authentication: Amazon Cognito OAuth integration

Routes:
    GET /: Main chat interface (requires authentication)
    GET /login: Amazon Cognito login page
    GET /simple-login: Simple login form for development
    POST /simple-login: Process simple login
    GET /auth/callback: OAuth callback handler
    GET /logout: User logout
    WebSocket /ws/{client_id}: Real-time chat communication

Environment Variables:
    HOST: Server host (default: 127.0.0.1)
    PORT: Server port (default: 8000)
    CORS_ORIGINS: Allowed CORS origins
    COGNITO_*: Amazon Cognito configuration
    AGENT_ARN: Amazon Bedrock AgentCore runtime ARN

Example:
    Run the application:
    >>> python main.py
    >>> # Access at http://localhost:8000
"""

import os
import json
import logging
import secrets
from typing import List, Dict, Optional
from fastapi import (
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Depends,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import boto3

# Import authentication module
from auth import (
    get_login_url,
    exchange_code_for_tokens,
    validate_token,
    get_current_user,
    login_required,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Device Management Chat Application")

# Add session middleware with a secure random key
app.add_middleware(
    SessionMiddleware,
    secret_key=secrets.token_urlsafe(32),
    max_age=3600,  # 1 hour session
)

# Add CORS middleware
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Set up templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Environment variables
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
AGENT_ARN = os.getenv("AGENT_ARN")

if not AGENT_ARN:
    logger.error("AGENT_ARN environment variable is not set")
    raise ValueError("AGENT_ARN environment variable is required")


# Pydantic Models
class Message(BaseModel):
    """
    Represents a chat message in the conversation.

    Attributes:
        role (str): The role of the message sender ('user' or 'assistant')
        content (str): The message content/text
    """

    role: str
    content: str


class ChatRequest(BaseModel):
    """
    Request model for chat API endpoints.

    Attributes:
        messages (List[Message]): List of messages in the conversation
    """

    messages: List[Message]


class ConnectionManager:
    """
    Manages WebSocket connections for real-time chat functionality.

    This class handles WebSocket connection lifecycle, message routing,
    and session management for the chat interface. Each client connection
    is identified by a unique client_id and can maintain conversation
    context through runtime session IDs.

    Attributes:
        active_connections (Dict[str, WebSocket]): Map of client_id to WebSocket
        session_ids (Dict[str, str]): Map of client_id to runtime_session_id
    """

    def __init__(self):
        """Initialize the connection manager with empty connection pools."""
        self.active_connections: Dict[str, WebSocket] = {}
        self.session_ids: Dict[str, str] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        """
        Accept a new WebSocket connection and register the client.

        Args:
            websocket (WebSocket): The WebSocket connection to accept
            client_id (str): Unique identifier for the client connection
        """
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.session_ids[client_id] = None

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.session_ids:
            del self.session_ids[client_id]

    async def send_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)

    def get_session_id(self, client_id: str) -> Optional[str]:
        return self.session_ids.get(client_id)

    def set_session_id(self, client_id: str, session_id: str):
        self.session_ids[client_id] = session_id


manager = ConnectionManager()


def parse_streaming_response(content):
    """Parse streaming response content to extract the final response text"""
    try:
        logger.debug(f"Parsing streaming content: {len(content)} characters")

        # Split content by lines and look for the final response
        lines = content.strip().split("\n")
        final_response = ""
        accumulated_text = ""

        # Look for the final complete message at the end
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue

            try:
                # Try to parse as JSON directly (for AgentCore responses)
                if line.startswith("{") and line.endswith("}"):
                    json_data = json.loads(line)
                # Handle SSE format with 'data: ' prefix
                elif line.startswith("data: "):
                    json_str = line[6:].strip()
                    if not json_str:
                        continue
                    json_data = json.loads(json_str)
                else:
                    continue

                # Look for the final complete response first
                if isinstance(json_data, dict):
                    # Check for complete type with final_response (highest priority)
                    if json_data.get("type") == "complete" and "final_response" in json_data:
                        final_response = json_data["final_response"]
                        logger.debug("Found complete type with final_response")
                        break

                    # Check for message content with complete response
                    elif "message" in json_data:
                        message = json_data["message"]
                        if isinstance(message, dict) and "content" in message:
                            content_list = message["content"]
                            if isinstance(content_list, list):
                                text_parts = []
                                for item in content_list:
                                    if isinstance(item, dict) and "text" in item:
                                        text_parts.append(item["text"])
                                if text_parts:
                                    candidate_response = " ".join(text_parts)
                                    # Only use if it's a substantial response (likely the final one)
                                    if len(candidate_response) > 200:
                                        final_response = candidate_response
                                        logger.debug("Found substantial message content")
                                        break

            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse JSON line: {line[:100]}... Error: {e}")
                continue
            except Exception as e:
                logger.debug(f"Error processing line: {e}")
                continue

        # If no final response found, accumulate text chunks
        if not final_response:
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                try:
                    if line.startswith("{") and line.endswith("}"):
                        json_data = json.loads(line)
                    elif line.startswith("data: "):
                        json_str = line[6:].strip()
                        if not json_str:
                            continue
                        json_data = json.loads(json_str)
                    else:
                        continue

                    if isinstance(json_data, dict):
                        # Check for streaming text chunks
                        if "event" in json_data:
                            event = json_data["event"]
                            if isinstance(event, dict):
                                # Handle contentBlockDelta events
                                if "contentBlockDelta" in event:
                                    delta = event["contentBlockDelta"]
                                    if isinstance(delta, dict) and "delta" in delta:
                                        delta_data = delta["delta"]
                                        if isinstance(delta_data, dict) and "text" in delta_data:
                                            accumulated_text += delta_data["text"]

                        # Check for chunk data
                        elif "data" in json_data and isinstance(json_data["data"], str):
                            accumulated_text += json_data["data"]

                except json.JSONDecodeError:
                    continue
                except Exception:
                    continue

        # Return the best response we found
        if final_response:
            logger.info(f"Extracted final response: {len(final_response)} characters")
            return final_response
        elif accumulated_text:
            logger.info(f"Using accumulated text: {len(accumulated_text)} characters")
            return accumulated_text
        else:
            logger.warning("No response text found in streaming data")
            return f"No parseable response found. Raw content sample: {content[:500]}..."

    except Exception as e:
        logger.error(f"Error parsing streaming response: {str(e)}")
        return f"Error parsing response: {str(e)}"


def format_response_text(text):
    """Format response text for better readability in the UI"""
    if not text:
        return ""

    try:
        # Clean up the text first
        text = text.strip()

        # Try to parse as JSON if it looks like JSON
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                parsed = json.loads(text)

                # If it's a list of devices, format it nicely
                if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                    # Check if this looks like a device list
                    if all("device_id" in item or "name" in item for item in parsed):
                        result = "📱 **Device List:**\n\n"
                        for i, item in enumerate(parsed, 1):
                            name = item.get("name", "Unknown Device")
                            device_id = item.get("device_id", item.get("id", "Unknown ID"))
                            status = item.get("connection_status", item.get("status", "Unknown"))

                            # Add status emoji
                            status_emoji = {
                                "Connected": "🟢",
                                "Disconnected": "🔴",
                                "Updating": "🟡",
                                "Dormant": "🟠",
                                "Maintenance": "🔧",
                            }.get(status, "⚪")

                            result += f"**{i}. {name}** {status_emoji}\n"
                            result += f"   • ID: `{device_id}`\n"

                            if "model" in item:
                                result += f"   • Model: {item['model']}\n"
                            if "ip_address" in item:
                                result += f"   • IP: {item['ip_address']}\n"
                            if "connection_status" in item:
                                result += f"   • Status: {item['connection_status']}\n"
                            if "firmware_version" in item:
                                result += f"   • Firmware: {item['firmware_version']}\n"
                            if "last_connected" in item:
                                # Format the timestamp nicely
                                timestamp = item["last_connected"]
                                if "T" in timestamp:
                                    date_part = timestamp.split("T")[0]
                                    time_part = timestamp.split("T")[1].split(".")[0]
                                    result += f"   • Last Connected: {date_part} at {time_part}\n"
                                else:
                                    result += f"   • Last Connected: {timestamp}\n"

                            result += "\n"

                        return result.strip()

                # For other JSON data, pretty print with indentation
                return f"```json\n{json.dumps(parsed, indent=2)}\n```"

            except json.JSONDecodeError:
                # Not valid JSON, continue with regular formatting
                pass

        # Replace escaped characters
        text = text.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")

        # Format bullet points consistently
        lines = text.split("\n")
        formatted_lines = []

        for line in lines:
            line = line.strip()
            if not line:
                formatted_lines.append("")
                continue

            # Convert numbered lists to bullet points
            if line and len(line) > 2 and line[0].isdigit() and line[1:3] in [". ", ") "]:
                line = "• " + line.split(". ", 1)[1] if ". " in line else "• " + line.split(") ", 1)[1]

            # Ensure consistent bullet formatting
            elif line.startswith("- "):
                line = "• " + line[2:]

            # Format key-value pairs nicely
            elif ":" in line and not line.startswith("  ") and not line.startswith("•"):
                parts = line.split(":", 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    key = parts[0].strip()
                    value = parts[1].strip()
                    line = f"**{key}:** {value}"

            formatted_lines.append(line)

        result = "\n".join(formatted_lines)

        # Clean up excessive whitespace
        while "\n\n\n" in result:
            result = result.replace("\n\n\n", "\n\n")

        return result.strip()

    except Exception as e:
        logger.error(f"Error formatting response: {str(e)}")
        return text  # Return original text if formatting fails


def create_agentcore_client(auth_token=None):
    """Create AgentCore client and boto session"""
    # Create boto session
    boto_session = boto3.Session(region_name=AWS_REGION)

    # Create bedrock-agentcore client
    agentcore_client = boto_session.client("bedrock-agentcore", region_name=AWS_REGION)

    return agentcore_client


# Routes
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time chat with streaming support"""
    await manager.connect(websocket, client_id)

    try:
        # Create AgentCore client
        agentcore_client = create_agentcore_client()

        while True:
            data = await websocket.receive_text()
            user_message = data.strip()

            if not user_message:
                await manager.send_message(json.dumps({"error": "Empty message"}), client_id)
                continue

            try:
                # Get current session ID for this client
                session_id = manager.get_session_id(client_id)

                # Invoke the agent with retry logic
                from botocore.exceptions import ClientError

                max_retries = 3
                retry_delay = 1  # Start with 1 second delay

                for attempt in range(max_retries):
                    try:
                        if session_id is None:
                            # First message in conversation
                            logger.info("Starting new conversation with streaming")
                            boto3_response = agentcore_client.invoke_agent_runtime(
                                agentRuntimeArn=AGENT_ARN,
                                qualifier="DEFAULT",
                                payload=json.dumps({"prompt": user_message}),
                            )
                        else:
                            # Continuing conversation with existing session ID
                            logger.info(f"Continuing conversation with session ID: {session_id}")
                            boto3_response = agentcore_client.invoke_agent_runtime(
                                agentRuntimeArn=AGENT_ARN,
                                qualifier="DEFAULT",
                                payload=json.dumps({"prompt": user_message}),
                                runtimeSessionId=session_id,
                            )
                        # If successful, break out of retry loop
                        break
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "throttlingException" and attempt < max_retries - 1:
                            logger.warning(f"Throttling exception encountered. Retrying in {retry_delay} seconds...")
                            await manager.send_message(
                                json.dumps({"status": "Rate limited. Retrying in {} seconds...".format(retry_delay)}),
                                client_id,
                            )
                            import asyncio

                            await asyncio.sleep(retry_delay)
                            # Exponential backoff
                            retry_delay *= 2
                        else:
                            # Re-raise the exception if we've exhausted retries or it's not a throttling exception
                            raise

                # Update session ID
                if isinstance(boto3_response, dict) and "runtimeSessionId" in boto3_response:
                    new_session_id = boto3_response["runtimeSessionId"]
                    logger.info(f"Received new session ID: {new_session_id}")
                    manager.set_session_id(client_id, new_session_id)
                else:
                    logger.warning("No runtimeSessionId in response")
                    # Keep using the existing session ID if available
                    new_session_id = session_id

                # Handle streaming response from AgentCore
                if isinstance(boto3_response, dict) and "response" in boto3_response:
                    try:
                        response_stream = boto3_response["response"]
                        logger.info(f"Processing streaming response, type: {type(response_stream)}")

                        # Handle StreamingBody properly
                        if hasattr(response_stream, "read"):
                            content = response_stream.read()
                            if isinstance(content, bytes):
                                content = content.decode("utf-8")

                            logger.debug(f"Raw streaming content received: {len(content)} characters")

                            # Parse the streaming content to extract the final response
                            final_response_text = parse_streaming_response(content)

                            if final_response_text:
                                # Send final complete message
                                await manager.send_message(
                                    json.dumps(
                                        {
                                            "response": format_response_text(final_response_text),
                                            "sessionId": new_session_id,
                                            "complete": True,
                                        }
                                    ),
                                    client_id,
                                )
                            else:
                                await manager.send_message(
                                    json.dumps({"error": "No valid response content found in streaming data"}),
                                    client_id,
                                )

                        else:
                            # Fallback: convert to string
                            content = str(response_stream)
                            final_response_text = parse_streaming_response(content)

                            if final_response_text:
                                await manager.send_message(
                                    json.dumps(
                                        {
                                            "response": format_response_text(final_response_text),
                                            "sessionId": new_session_id,
                                            "complete": True,
                                        }
                                    ),
                                    client_id,
                                )
                            else:
                                await manager.send_message(
                                    json.dumps({"error": "No valid response content found"}),
                                    client_id,
                                )

                    except Exception as e:
                        logger.error(f"Error processing streaming response: {str(e)}")
                        await manager.send_message(
                            json.dumps({"error": f"Error processing streaming response: {str(e)}"}),
                            client_id,
                        )
                else:
                    # Fallback to non-streaming response handling
                    logger.warning("No streaming response found, falling back to non-streaming")
                    response_content = str(boto3_response)
                    formatted_response = format_response_text(response_content)

                    await manager.send_message(
                        json.dumps(
                            {
                                "response": formatted_response,
                                "sessionId": new_session_id,
                            }
                        ),
                        client_id,
                    )

            except Exception as e:
                error_message = str(e)
                logger.error(f"Error processing request with agent: {error_message}")

                # Provide more helpful error messages for common issues
                if "throttlingException" in error_message:
                    error_message = "Too many requests. The service is temporarily throttling requests. Please try again in a few moments."
                elif "AccessDeniedException" in error_message:
                    error_message = "Access denied. Please check your AWS credentials and permissions."
                elif "ValidationException" in error_message and "runtimeSessionId" in error_message:
                    error_message = "Invalid session ID. Starting a new conversation."
                    manager.set_session_id(client_id, None)  # Reset the session ID

                await manager.send_message(
                    json.dumps({"error": f"Error processing request with agent: {error_message}"}),
                    client_id,
                )

    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        manager.disconnect(client_id)


# Authentication routes
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint that redirects to login if not authenticated or chat if authenticated"""
    user = await get_current_user(request)
    if not user:
        # Try simple login first as a fallback
        return RedirectResponse(url="/simple-login")
    return templates.TemplateResponse("chat.html", {"request": request, "user": user})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page with Cognito authentication"""
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/")

    login_url = get_login_url()
    return templates.TemplateResponse("login.html", {"request": request, "login_url": login_url})


@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Callback endpoint for Cognito authentication"""
    # Check if there was an error in the authentication process
    if error:
        error_msg = f"Authentication error: {error}"
        if error_description:
            error_msg += f" - {error_description}"
        logger.error(error_msg)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "login_url": get_login_url(), "error": error_msg},
            status_code=400,
        )

    # If no code is provided, return an error
    if not code:
        logger.error("No authorization code provided")
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "login_url": get_login_url(),
                "error": "No authorization code provided",
            },
            status_code=400,
        )

    try:
        # Exchange the authorization code for tokens
        tokens = await exchange_code_for_tokens(code)

        # Validate the ID token
        id_token = tokens["id_token"]
        claims = await validate_token(id_token)

        # Store user info in session
        request.session["user"] = {
            "sub": claims["sub"],
            "email": claims.get("email", ""),
            "name": claims.get("name", ""),
            "access_token": tokens["access_token"],
            "id_token": id_token,
        }

        # Redirect to the main page
        return RedirectResponse(url="/")

    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "login_url": get_login_url(), "error": str(e)},
            status_code=400,
        )


@app.get("/logout")
async def logout(request: Request):
    """Logout endpoint"""
    # Clear the session
    request.session.clear()

    # Create response and clear all authentication cookies
    response = RedirectResponse(url="/simple-login")
    response.delete_cookie("access_token")
    response.delete_cookie("simple_user")

    return response


@app.get("/profile")
async def profile(request: Request, user: dict = Depends(login_required)):
    """User profile endpoint"""
    return {"user": user}


@app.get("/simple-login", response_class=HTMLResponse)
async def simple_login_page(request: Request):
    """Simple login page without Cognito"""
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/")

    # Generate CSRF token
    csrf_token = secrets.token_urlsafe(32)
    request.session["csrf_token"] = csrf_token

    return templates.TemplateResponse("simple_login.html", {"request": request, "csrf_token": csrf_token})


@app.post("/simple-login")
async def simple_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    """Process simple login form"""
    # Validate CSRF token
    session_csrf_token = request.session.get("csrf_token")
    if not session_csrf_token or csrf_token != session_csrf_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    # Clear the used CSRF token
    request.session.pop("csrf_token", None)

    # For demo purposes, accept any username/password
    # In a real application, you would validate against a database or other authentication system

    # Store user info in session
    request.session["user"] = {
        "sub": "simple-user-123",
        "email": username,
        "name": username,
        "access_token": "demo-token",
        "id_token": "demo-token",
    }

    # Redirect to the main page
    return RedirectResponse(url="/", status_code=303)  # 303 See Other is used for POST redirects


if __name__ == "__main__":
    import uvicorn
    import os

    host = os.getenv("HOST", "127.0.0.1")  # Default to localhost for security
    uvicorn.run("main:app", host=host, port=8000, reload=True)
