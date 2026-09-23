"""
LangChain Voice Agent WebSocket Server

FastAPI server with IMDS credential management and WebSocket endpoint.
Agent logic lives in agent.py (following the strands pattern).
"""

import logging
import uvicorn
import os
import json
import asyncio
import time
from datetime import datetime

import requests
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from agent import handle_websocket_session


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment / Gateway config
# ---------------------------------------------------------------------------
MCP_GATEWAY_ARNS = os.getenv("MCP_GATEWAY_ARNS")
MCP_GATEWAY_URLS = os.getenv("MCP_GATEWAY_URLS")

if not MCP_GATEWAY_ARNS or not MCP_GATEWAY_URLS:
    logger.error("❌ MCP Gateway configuration is required!")
    logger.error("   Set MCP_GATEWAY_ARNS and MCP_GATEWAY_URLS environment variables")
    raise RuntimeError("MCP Gateway not configured")

try:
    gateway_arns = json.loads(MCP_GATEWAY_ARNS)
    gateway_urls = json.loads(MCP_GATEWAY_URLS)
    logger.info(f"✅ Loaded {len(gateway_arns)} MCP Gateways")
except json.JSONDecodeError:
    logger.error("❌ Failed to parse MCP Gateway configuration")
    raise RuntimeError("Invalid MCP Gateway configuration")

_credential_refresh_task = None


# ---------------------------------------------------------------------------
# IMDS credential helpers (same pattern as strands server)
# ---------------------------------------------------------------------------


def get_imdsv2_token():
    """Get IMDSv2 token for secure metadata access."""
    try:
        resp = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def get_credentials_from_imds():
    """Retrieve IAM role credentials from EC2 IMDS (tries IMDSv2 first, falls back to IMDSv1)."""
    result = {
        "success": False,
        "credentials": None,
        "role_name": None,
        "method_used": None,
        "error": None,
    }
    try:
        token = get_imdsv2_token()
        headers = {"X-aws-ec2-metadata-token": token} if token else {}
        result["method_used"] = "IMDSv2" if token else "IMDSv1"

        role_resp = requests.get(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            headers=headers,
            timeout=2,
        )
        if role_resp.status_code != 200:
            result["error"] = f"Failed to retrieve IAM role: HTTP {role_resp.status_code}"
            return result

        role_name = role_resp.text.strip()
        result["role_name"] = role_name

        creds_resp = requests.get(
            f"http://169.254.169.254/latest/meta-data/iam/security-credentials/{role_name}",
            headers=headers,
            timeout=2,
        )
        if creds_resp.status_code != 200:
            result["error"] = f"Failed to retrieve credentials: HTTP {creds_resp.status_code}"
            return result

        creds = creds_resp.json()
        result["success"] = True
        result["credentials"] = {
            "AccessKeyId": creds.get("AccessKeyId"),
            "SecretAccessKey": creds.get("SecretAccessKey"),
            "Token": creds.get("Token"),
            "Expiration": creds.get("Expiration"),
        }
    except Exception as e:
        result["error"] = str(e)
    return result


async def refresh_credentials_from_imds():
    """Background task to refresh credentials from IMDS."""
    logger.info("Starting credential refresh task")
    while True:
        try:
            imds_result = get_credentials_from_imds()
            if imds_result["success"]:
                creds = imds_result["credentials"]
                os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
                os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
                os.environ["AWS_SESSION_TOKEN"] = creds["Token"]
                logger.info(f"✅ Credentials refreshed ({imds_result['method_used']})")
                try:
                    expiration = datetime.fromisoformat(creds["Expiration"].replace("Z", "+00:00"))
                    now = datetime.now(expiration.tzinfo)
                    refresh_interval = min(max((expiration - now).total_seconds() - 300, 60), 3600)
                except Exception:
                    refresh_interval = 3600
                await asyncio.sleep(refresh_interval)
            else:
                logger.error(f"Failed to refresh credentials: {imds_result['error']}")
                await asyncio.sleep(300)
        except asyncio.CancelledError:
            logger.info("Credential refresh task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in credential refresh: {e}")
            await asyncio.sleep(300)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="LangChain Voice Agent WebSocket Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    global _credential_refresh_task

    logger.info("🚀 Starting LangChain voice agent server...")
    logger.info(f"📍 Default Region: {os.getenv('AWS_DEFAULT_REGION', 'us-east-1')}")
    logger.info(f"✅ {len(gateway_arns)} Gateway(s) configured:")
    for i, (arn, url) in enumerate(zip(gateway_arns, gateway_urls), 1):
        logger.info(f"   {i}. {url}")
        logger.info(f"      ARN: {arn}")

    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        logger.info("✅ Using credentials from environment (local mode)")
    else:
        logger.info("🔄 Fetching credentials from EC2 IMDS...")
        imds_result = get_credentials_from_imds()
        if imds_result["success"]:
            creds = imds_result["credentials"]
            os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
            os.environ["AWS_SESSION_TOKEN"] = creds["Token"]
            logger.info(f"✅ Credentials loaded ({imds_result['method_used']})")
            _credential_refresh_task = asyncio.create_task(refresh_credentials_from_imds())
            logger.info("🔄 Credential refresh task started")
        else:
            logger.error(f"❌ Failed to fetch credentials: {imds_result['error']}")


@app.on_event("shutdown")
async def shutdown_event():
    global _credential_refresh_task
    logger.info("🛑 Shutting down...")
    if _credential_refresh_task and not _credential_refresh_task.done():
        _credential_refresh_task.cancel()
        try:
            await _credential_refresh_task
        except asyncio.CancelledError:
            pass


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
    await handle_websocket_session(websocket, default_gateway_arns=gateway_arns)


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host=host, port=port)
