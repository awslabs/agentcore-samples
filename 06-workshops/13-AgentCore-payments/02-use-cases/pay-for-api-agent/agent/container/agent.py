"""Pay for API — AgentCore Runtime buyer agent.

A minimal Strands Agent, wrapped in a FastAPI ``/invocations`` endpoint so it
conforms to the AgentCore Runtime contract. The agent has exactly one tool —
``http_request`` from ``strands-agents-tools`` — and relies on the
``AgentCorePaymentsPlugin`` (from ``bedrock-agentcore``) to transparently
handle HTTP 402 → ``ProcessPayment`` → retry.

No private keys. No manual x402 assembly. The caller supplies the payment
context (manager ARN, instrument ID, session ID, vendor-level user ID) on
every invocation, mirroring the pattern in ``agentcore-payments/payment-agent``.

Runtime invocation contract:

    POST /invocations
    {
        "prompt":          "Tell me one fact about space",
        "sellerUrl":       "https://example.com/",
        "managerArn":      "arn:aws:bedrock-agentcore:…:payment-manager/…",
        "instrumentId":    "payment-instrument-…",
        "sessionId":       "payment-session-…",
        "paymentUserId":   "<CDP UUID | Privy DID>",
        "region":          "us-west-2"          # optional, defaults to AWS_REGION
    }

Health endpoint:

    GET /ping  →  {"status": "ok"}
"""

from __future__ import annotations

# ── ADOT auto-instrumentation (must run before any other imports) ──
# These env vars tell AWS Distro for OpenTelemetry how to export traces
# and logs to CloudWatch via the ADOT collector that AgentCore Runtime
# injects into the container. Setting them at the top of the module is
# required because some OTEL libraries read env at import time.
import os

os.environ.setdefault("AGENT_OBSERVABILITY_ENABLED", "true")
os.environ.setdefault("OTEL_PYTHON_DISTRO", "aws_distro")
os.environ.setdefault("OTEL_PYTHON_CONFIGURATOR", "aws_configurator")
os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
os.environ.setdefault("OTEL_TRACES_EXPORTER", "otlp")
os.environ.setdefault("OTEL_LOGS_EXPORTER", "otlp")
# Metrics disabled — traces + logs cover the observability surface we care
# about (payment calls, tool use, HTTP requests). Enable if you need them.
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

try:
    from opentelemetry.instrumentation.auto_instrumentation._load import (
        _load_configurators,
        _load_distro,
        _load_instrumentors,
    )

    _distro = _load_distro()
    _distro.configure()
    _load_configurators()
    _load_instrumentors(_distro)
except Exception as _otel_err:  # noqa: BLE001 — ADOT optional for local dev
    import sys

    print(f"[WARN] ADOT auto-instrumentation skipped: {_otel_err}", file=sys.stderr)

# ── Standard imports ──
import logging

import boto3
import botocore.exceptions
import fastapi
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pay-for-api-agent")

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
# Claude Sonnet 4.5 cross-region inference profile (US).
MODEL_ID = os.environ.get(
    "MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# AgentCore Memory — if set, every invocation threads through an
# AgentCoreMemorySessionManager keyed on (memory_id, actor_id=paymentUserId,
# session_id=per-invocation). The CDK stack sets this in the container's
# environment; if the variable is missing the agent runs without memory.
MEMORY_ID = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID", "")

# AgentCorePaymentsPlugin gate. Defaults to enabled in the container — the
# runtime is isolated and the notebook is driving the invocation. Flip to
# "0" / "false" to fall back to a no-payments agent for debugging.
ENABLE_PAYMENTS_PLUGIN = os.environ.get("ENABLE_PAYMENTS_PLUGIN", "1").lower() in (
    "1",
    "true",
    "yes",
)

# AgentCore Payments vended-log delivery gate. When enabled and a
# `managerArn` is supplied on the first invocation, the agent configures
# CloudWatch Logs vended delivery for that Manager. Idempotent — re-runs
# are no-ops. Defaults to enabled.
ENABLE_VENDED_LOG_DELIVERY = os.environ.get("ENABLE_VENDED_LOG_DELIVERY", "1").lower() in ("1", "true", "yes")

# Track Manager ARNs we have already configured vended delivery for, so
# the agent doesn't re-call the control-plane on every invocation.
_VENDED_LOG_DELIVERY_CONFIGURED: set[str] = set()

SYSTEM_PROMPT = (
    "You are a research agent powered by Amazon Bedrock AgentCore Payments.\n"
    "\n"
    "Your only tool is `http_request`. Use it to fetch paid facts from the\n"
    "Fun Facts API. Each `GET` returns exactly one fact and costs $0.01 in\n"
    "USDC. The AgentCore Payments plugin pays on your behalf — you never\n"
    "handle private keys, assemble payment headers, or retry failed calls.\n"
    "\n"
    "SELLER CONTRACT\n"
    "  Endpoint:          GET <seller>/facts?topic=<topic>\n"
    "  Supported topics:  space, oceans, ai, payments\n"
    "                     (any other value falls back to a random general fact)\n"
    '  Success body:      {"x402_content": {"data": "<JSON string>", ...},\n'
    '                      "x402_meta":    {"seller": ..., "generated_at": ...}}\n'
    "                     `x402_content.data` is a JSON string — parse it to\n"
    '                     read `{"topic": ..., "fact": ...}`.\n'
    "  Price per call:    $0.01 USDC.\n"
    "\n"
    "RULES\n"
    "  1. One `http_request` GET per topic the user asks about.\n"
    "     If the user asks for two topics, make two calls.\n"
    "  2. If the user's topic is not in the supported list, pick the closest\n"
    "     supported topic rather than letting the seller fall back silently —\n"
    "     e.g. 'volcanoes' → 'space', 'whales' → 'oceans'.\n"
    "  3. Parse `x402_content.data` to get the `fact` and answer concisely,\n"
    "     citing each fact verbatim.\n"
    "  4. End every response with the total amount spent in USD — $0.01 per\n"
    "     successful call.\n"
)


def _ensure_vended_log_delivery(manager_arn: str, region: str) -> None:
    """Idempotently wire CloudWatch Logs vended delivery for a PaymentManager.

    Three control-plane ops, each a no-op on re-run:

      1. ``CreateLogGroup`` — destination Log Group, if missing.
      2. ``PutDeliverySource`` — Payments → logs pipe.
      3. ``PutDeliveryDestination`` — target the Log Group.
      4. ``CreateDelivery`` — bind source to destination.

    Authorization for the Manager to vend logs is granted by the IAM
    permissions
    ``bedrock-agentcore:PaymentsAllowVendedLogDeliveryForResource`` and
    ``bedrock-agentcore:AllowVendedLogDeliveryForResource`` on the
    calling principal (already attached to the agent runtime's
    execution role in the CDK stack — CloudWatch checks both as a
    product-level + service-level gate). There is no SDK call to "arm"
    vended delivery; CloudWatch authorizes both implicitly when
    ``put_delivery_source`` runs against a Payment Manager ARN.
    See ``docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AWS-logs-infrastructure-V2-service-specific.html``.

    Any ``ConflictException`` / already-exists shape is swallowed so this
    can run on every Manager the agent sees without side effects.
    """
    if not ENABLE_VENDED_LOG_DELIVERY or not manager_arn:
        return
    if manager_arn in _VENDED_LOG_DELIVERY_CONFIGURED:
        return

    # Derive a stable, Manager-scoped log group name from the Manager ID so
    # re-runs of the same Manager hit the same log group instead of creating
    # duplicates. The Manager ID is the last path segment of the ARN.
    manager_id = manager_arn.rsplit("/", 1)[-1]
    log_group_name = f"/bedrock-agentcore/payments/{manager_id}"
    source_name = f"pay-for-api-payments-src-{manager_id}"
    destination_name = f"pay-for-api-payments-dest-{manager_id}"

    logs_client = boto3.client("logs", region_name=region)

    # STS account lookup so we can construct the destination ARN below.
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    destination_arn = f"arn:aws:logs:{region}:{account_id}:delivery-destination:{destination_name}"
    log_group_arn = f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}"

    def _swallow(code_set: set[str], fn, **kwargs):
        """Call fn(**kwargs); swallow the given error codes."""
        try:
            return fn(**kwargs)
        except botocore.exceptions.ClientError as exc:
            err_code = exc.response["Error"].get("Code", "")
            if err_code in code_set:
                return None
            raise

    # 1. Ensure the log group exists before we point a delivery at it.
    _swallow(
        {"ResourceAlreadyExistsException"},
        logs_client.create_log_group,
        logGroupName=log_group_name,
    )

    # 2. Delivery source — Payments resource emits APPLICATION_LOGS.
    # CloudWatch validates the caller's
    # bedrock-agentcore:PaymentsAllowVendedLogDeliveryForResource and
    # bedrock-agentcore:AllowVendedLogDeliveryForResource permissions
    # against the resourceArn at this point. Without either, this call
    # returns AccessDeniedException.
    _swallow(
        {"ConflictException", "ResourceAlreadyExistsException"},
        logs_client.put_delivery_source,
        name=source_name,
        resourceArn=manager_arn,
        logType="APPLICATION_LOGS",
    )

    # 3. Delivery destination — Log Group we just ensured.
    _swallow(
        {"ConflictException", "ResourceAlreadyExistsException"},
        logs_client.put_delivery_destination,
        name=destination_name,
        deliveryDestinationConfiguration={
            "destinationResourceArn": log_group_arn,
        },
    )

    # 4. Bind source to destination. CreateDelivery is idempotent on the
    # (source, destination) pair — returns ConflictException on re-runs.
    _swallow(
        {"ConflictException", "ResourceAlreadyExistsException"},
        logs_client.create_delivery,
        deliverySourceName=source_name,
        deliveryDestinationArn=destination_arn,
    )

    _VENDED_LOG_DELIVERY_CONFIGURED.add(manager_arn)
    logger.info(
        "Vended log delivery ensured for Manager %s → %s",
        manager_id,
        log_group_name,
    )


def _build_agent(payment_config: dict | None):
    """Construct a Strands Agent with one http_request tool and — if payment
    context is provided — the AgentCorePaymentsPlugin for automatic x402
    handling.

    ``payment_config`` keys:
      - manager_arn, instrument_id, session_id, payment_user_id, region
    """
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from strands_tools import http_request

    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=AWS_REGION,
        temperature=0.7,
    )

    # ── AgentCoreMemorySessionManager ──
    # Memory is keyed on (memory_id, actor_id, session_id). We use the
    # vendor-assigned paymentUserId as actor so all of a user's
    # invocations roll up under one actor regardless of which notebook
    # kernel or process is driving the runtime. If memory is unavailable
    # (bad SDK version, missing resource, etc.) we log and continue
    # without — the plugin still works.
    session_manager = None
    actor_id = (payment_config or {}).get("payment_user_id") or ""
    if MEMORY_ID and actor_id:
        try:
            import uuid as _uuid

            from bedrock_agentcore.memory.integrations.strands.config import (
                AgentCoreMemoryConfig,
            )
            from bedrock_agentcore.memory.integrations.strands.session_manager import (
                AgentCoreMemorySessionManager,
            )

            session_id = f"{actor_id}-{_uuid.uuid4().hex[:8]}"
            memory_config = AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id=actor_id,
            )
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=memory_config,
                region_name=AWS_REGION,
            )
            logger.info(
                "AgentCoreMemorySessionManager attached memory=%s actor=%s session=%s",
                MEMORY_ID,
                actor_id,
                session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Memory session manager unavailable, continuing without: %s",
                exc,
            )

    plugins: list = []
    if ENABLE_PAYMENTS_PLUGIN and payment_config:
        missing = [
            k for k in ("manager_arn", "instrument_id", "session_id", "payment_user_id") if not payment_config.get(k)
        ]
        if missing:
            logger.info(
                "AgentCorePaymentsPlugin skipped — missing fields on this invocation: %s",
                missing,
            )
        else:
            try:
                from bedrock_agentcore.payments.integrations.config import (
                    AgentCorePaymentsPluginConfig,
                )
                from bedrock_agentcore.payments.integrations.strands.plugin import (
                    AgentCorePaymentsPlugin,
                )

                plugin_cfg = AgentCorePaymentsPluginConfig(
                    payment_manager_arn=payment_config["manager_arn"],
                    user_id=payment_config["payment_user_id"],
                    payment_instrument_id=payment_config["instrument_id"],
                    payment_session_id=payment_config["session_id"],
                    region=payment_config.get("region") or AWS_REGION,
                    agent_name="pay-for-api-agent",
                    network_preferences_config=payment_config.get("network_preferences"),
                )
                plugins.append(AgentCorePaymentsPlugin(config=plugin_cfg))
                logger.info(
                    "AgentCorePaymentsPlugin attached — manager=%s instrument=%s session=%s user=%s",
                    payment_config["manager_arn"],
                    payment_config["instrument_id"],
                    payment_config["session_id"],
                    payment_config["payment_user_id"],
                )
            except Exception as exc:  # noqa: BLE001 — plugin optional at edit time
                logger.warning(
                    "AgentCorePaymentsPlugin init failed, continuing without: %s",
                    exc,
                )

    kwargs: dict = {
        "model": model,
        "tools": [http_request],
        "system_prompt": SYSTEM_PROMPT,
    }
    if plugins:
        kwargs["plugins"] = plugins
    if session_manager is not None:
        kwargs["session_manager"] = session_manager

    # Wrap agent construction in a try/retry so a corrupt memory session
    # doesn't break the invocation. On failure we drop memory and retry
    # with a fresh agent — the plugin still pays.
    try:
        return Agent(**kwargs)
    except Exception as exc:  # noqa: BLE001
        if session_manager is None:
            raise
        logger.warning(
            "Agent init with memory failed (%s) — retrying without memory",
            exc,
        )
        kwargs.pop("session_manager", None)
        return Agent(**kwargs)


# ── FastAPI app ──

app = FastAPI(title="Pay for API — Buyer Agent", version="1.0.0")


@app.get("/ping")
async def ping():
    return JSONResponse(content={"status": "ok"}, status_code=200)


# Maximum prompt length the /invocations endpoint accepts. Bounds the
# Bedrock token bill on each invoke and prevents a flood of multi-MB
# prompts from filling the runtime memory. Tune up if your use case
# legitimately needs longer prompts; this is a defensive cap, not a
# product constraint.
MAX_PROMPT_LEN = 5000


@app.post("/invocations")
async def invocations(request: fastapi.Request):
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Invalid JSON body on /invocations: %s", exc)
        return JSONResponse(
            content={"error": "Invalid JSON body"},
            status_code=400,
        )

    prompt = data.get("prompt") or data.get("text") or data.get("message") or ""
    seller_url = data.get("sellerUrl") or data.get("seller_url")
    if not prompt:
        return JSONResponse(content={"error": "prompt is required"}, status_code=400)
    if not seller_url:
        return JSONResponse(content={"error": "sellerUrl is required"}, status_code=400)

    # ── Defensive input validation ──
    # The prompt is forwarded to Bedrock and the seller URL is fetched
    # by the http_request tool. Bounding both keeps the runtime
    # behaving on hostile input even though AgentCore Runtime fronts
    # this endpoint with its own auth + payload validation.
    if len(prompt) > MAX_PROMPT_LEN:
        return JSONResponse(
            content={"error": f"prompt exceeds {MAX_PROMPT_LEN} characters"},
            status_code=400,
        )
    if not (seller_url.startswith("https://") or seller_url.startswith("http://")):
        return JSONResponse(
            content={"error": "sellerUrl must be an http(s) URL"},
            status_code=400,
        )

    payment_config = {
        "manager_arn": data.get("managerArn") or data.get("manager_arn", ""),
        "instrument_id": data.get("instrumentId") or data.get("instrument_id", ""),
        "session_id": data.get("sessionId") or data.get("session_id", ""),
        "payment_user_id": data.get("paymentUserId") or data.get("payment_user_id", ""),
        "region": data.get("region", AWS_REGION),
        "network_preferences": (data.get("networkPreferences") or data.get("network_preferences")),
    }

    # Wire up vended log delivery the first time we see a Manager — no-op
    # thereafter for the same Manager in the same process. Any errors are
    # logged but do not fail the invocation, since observability is a
    # best-effort add-on.
    try:
        _ensure_vended_log_delivery(
            manager_arn=payment_config["manager_arn"],
            region=payment_config["region"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vended log delivery setup failed, continuing: %s", exc)

    # We prepend the seller URL to the user prompt so the agent knows the
    # exact URL to GET. Keeping it out of the system prompt lets the
    # notebook point the same agent at different sellers without rebuild.
    enriched_prompt = f"Seller URL: {seller_url.rstrip('/')}/facts\n\n{prompt}"

    try:
        agent = _build_agent(payment_config=payment_config)
        result = agent(enriched_prompt)
        return JSONResponse(content={"response": str(result)})
    except Exception as exc:  # noqa: BLE001
        logger.error("Invocation error: %s", exc, exc_info=True)
        return JSONResponse(
            content={"error": "Agent invocation failed. See runtime logs for details."},
            status_code=500,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    # AgentCore Runtime routes traffic into the container on all
    # interfaces, so bind to 0.0.0.0 inside the container by default.
    # Override with HOST=127.0.0.1 when running the container directly on
    # a developer machine.
    host = os.environ.get("HOST", "0.0.0.0")  # nosec B104 — required by AgentCore Runtime
    logger.info("Starting pay-for-api agent on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
