"""
Pipecat Voice Agent WebSocket Server (Nova Sonic)

FastAPI server with per-session SDK credentials and WebSocket endpoint.
Uses Amazon Nova Sonic for native speech-to-speech via Pipecat's
AWSNovaSonicLLMService — no separate STT or TTS needed.

Follows the same server pattern as 01-bedrock-sonic-ws/02-strands-ws/03-langchain-transcribe-polly-ws agents
(FastAPI + per-session SDK credentials + /ws endpoint) so it works with the
standard HTML client and AgentCore deployment.

Based on:
- pipecat/examples/foundational/40-aws-nova-sonic.py
- existing workspace server patterns
"""

import asyncio
import logging
import os

import boto3
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.aws.nova_sonic.llm import AWSNovaSonicLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

app = FastAPI(title="Pipecat Nova Sonic Agent")

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

    # Under Runtime V2 the container may have been restored from a deploy-time
    # snapshot, so refresh credentials before doing any AWS work in this session.
    # Resolve a new SDK session for this connection, never cache secrets in the process environment.

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

        credentials = await asyncio.to_thread(lambda: boto3.Session().get_credentials().get_frozen_credentials())
        llm = AWSNovaSonicLLMService(
            secret_access_key=credentials.secret_key,
            access_key_id=credentials.access_key,
            region=os.getenv("AWS_REGION", "us-east-1"),
            session_token=credentials.token,
            settings=AWSNovaSonicLLMService.Settings(
                model="amazon.nova-2-sonic-v1:0",
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

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception("WebSocket session error")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    logger.info(f"Starting Pipecat Nova Sonic server on 0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)  # nosec B104
