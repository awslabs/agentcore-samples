"""
Pipecat Voice Agent WebSocket Server (Nova Sonic)

FastAPI server with IMDS credential management and WebSocket endpoint.
Uses Amazon Nova Sonic for native speech-to-speech via Pipecat's
AWSNovaSonicLLMService — no separate STT or TTS needed.

Follows the same server pattern as 01-bedrock-sonic-ws/02-strands-ws/03-langchain-transcribe-polly-ws agents
(FastAPI + IMDS credentials + /ws endpoint) so it works with the
standard HTML client and AgentCore deployment.

Based on:
- pipecat/examples/foundational/40-aws-nova-sonic.py
- existing workspace server patterns
"""

import asyncio
import logging
import os

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnStoppedMessage,
)
from pipecat.runner.types import RunnerArguments  # noqa: F401
from pipecat.services.aws.nova_sonic.llm import AWSNovaSonicLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IMDS credential helpers (same pattern as other sample servers)
# ---------------------------------------------------------------------------

_credential_refresh_task = None


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
    """Retrieve IAM role credentials from EC2 IMDS."""
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
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretAccessKey"],
            "token": creds["Token"],
            "expiration": creds["Expiration"],
        }
    except Exception as e:
        result["error"] = str(e)
    return result


async def refresh_credentials_periodically():
    """Background task to refresh IMDS credentials before expiry."""
    while True:
        try:
            result = get_credentials_from_imds()
            if result["success"]:
                creds = result["credentials"]
                os.environ["AWS_ACCESS_KEY_ID"] = creds["access_key"]
                os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secret_key"]
                os.environ["AWS_SESSION_TOKEN"] = creds["token"]
                logger.info("Credentials refreshed successfully")
            else:
                logger.warning("Credential refresh failed")
        except Exception:
            logger.warning("Credential refresh error")
        await asyncio.sleep(300)  # Refresh every 5 minutes


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = (
    "You are a friendly banking assistant for AnyBank. "
    "Help customers with account inquiries, transactions, mortgages, and general questions. "
    "Be warm, conversational, and concise. Keep responses to two or three sentences."
)


# ---------------------------------------------------------------------------
# Tool callbacks
# ---------------------------------------------------------------------------


async def get_account_balance(params: FunctionCallParams):
    account_id = params.arguments.get("account_id", "unknown")
    await params.result_callback({"account_id": account_id, "balance": "$4,231.56", "currency": "USD"})


async def get_recent_transactions(params: FunctionCallParams):
    await params.result_callback(
        {
            "transactions": [
                {
                    "date": "2026-03-12",
                    "description": "Coffee Shop",
                    "amount": "-$4.50",
                },
                {
                    "date": "2026-03-11",
                    "description": "Direct Deposit",
                    "amount": "+$2,500.00",
                },
                {
                    "date": "2026-03-10",
                    "description": "Grocery Store",
                    "amount": "-$67.23",
                },
            ]
        }
    )


async def get_mortgage_rates(params: FunctionCallParams):
    await params.result_callback(
        {
            "rates": {
                "30_year_fixed": "6.75%",
                "15_year_fixed": "5.99%",
                "5_1_arm": "6.25%",
            }
        }
    )


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

tools = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="get_account_balance",
            description="Get the balance for a customer bank account",
            properties={
                "account_id": {
                    "type": "string",
                    "description": "The customer account ID",
                }
            },
            required=["account_id"],
        ),
        FunctionSchema(
            name="get_recent_transactions",
            description="Get recent transactions for a customer account",
            properties={
                "account_id": {
                    "type": "string",
                    "description": "The customer account ID",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of transactions to return",
                },
            },
            required=["account_id"],
        ),
        FunctionSchema(
            name="get_mortgage_rates",
            description="Get current mortgage interest rates",
            properties={},
            required=[],
        ),
    ]
)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def lifespan(app_instance):
    global _credential_refresh_task
    # Try IMDS credentials (AgentCore environment)
    result = get_credentials_from_imds()
    if result["success"]:
        creds = result["credentials"]
        os.environ["AWS_ACCESS_KEY_ID"] = creds["access_key"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = creds["secret_key"]
        os.environ["AWS_SESSION_TOKEN"] = creds["token"]
        logger.info(f"Initial credentials loaded via {result['method_used']}")
        _credential_refresh_task = asyncio.create_task(refresh_credentials_periodically())
    else:
        logger.info("IMDS not available — using environment variable credentials")
    yield


app = FastAPI(title="Pipecat Nova Sonic Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "healthy"})


@app.post("/start")
async def start():
    """Return the local WebSocket URL for the Pipecat client.

    When deployed to AgentCore, the client uses client.py (signing server)
    to get a presigned wss:// URL instead of hitting this endpoint.
    """
    port = int(os.getenv("PORT", "8081"))
    return JSONResponse({"ws_url": f"ws://localhost:{port}/ws"})


@app.post("/invocations")
async def invocations():
    return JSONResponse(
        {
            "agent": "pipecat-nova-sonic",
            "status": "running",
            "model": "amazon.nova-2-sonic-v1:0",
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info(f"WebSocket connected: {websocket.client}")

    try:
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
                serializer=ProtobufFrameSerializer(),
            ),
        )

        llm = AWSNovaSonicLLMService(
            secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            region=os.getenv("AWS_REGION", "us-east-1"),
            session_token=os.getenv("AWS_SESSION_TOKEN"),
            settings=AWSNovaSonicLLMService.Settings(
                voice="tiffany",
                system_instruction=SYSTEM_INSTRUCTION,
            ),
        )

        llm.register_function("get_account_balance", get_account_balance, cancel_on_interruption=False)
        llm.register_function(
            "get_recent_transactions",
            get_recent_transactions,
            cancel_on_interruption=False,
        )
        llm.register_function("get_mortgage_rates", get_mortgage_rates, cancel_on_interruption=False)

        context = LLMContext(tools=tools)
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

        pipeline = Pipeline(
            [
                transport.input(),
                user_aggregator,
                llm,
                transport.output(),
                assistant_aggregator,
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Pipecat client connected — starting conversation")
            context.add_message(
                {
                    "role": "user",
                    "content": "Please introduce yourself as AnyBank's voice assistant.",
                }
            )
            await task.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Pipecat client disconnected")
            await task.cancel()

        @user_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
            timestamp = f"[{message.timestamp}] " if message.timestamp else ""
            logger.info(f"Transcript: {timestamp}user: {message.content}")

        @assistant_aggregator.event_handler("on_assistant_turn_stopped")
        async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
            timestamp = f"[{message.timestamp}] " if message.timestamp else ""
            logger.info(f"Transcript: {timestamp}assistant: {message.content}")

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket session error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    logger.info(f"Starting Pipecat Nova Sonic server on 0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104
