"""
observability.py — OpenTelemetry setup for the Text-to-Python IDE.

Initializes Strands Agents telemetry with X-Ray-compatible trace export to
CloudWatch via the OTLP protocol. Traces appear under:
  CloudWatch → X-Ray → Traces (and in the Bedrock AgentCore Observability view
  when the runtime is deployed).

For local development without a collector, set OTEL_TRACES_EXPORTER=console
in .env to print spans to stdout instead.
"""

import logging
import os

logger = logging.getLogger(__name__)

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
OTEL_EXPORTER = os.getenv("OTEL_TRACES_EXPORTER", "none")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "text-to-python-ide")


def setup_telemetry():
    """Initialize OpenTelemetry tracing for Strands Agents.

    Call this once at app startup (before agents are created).
    Strands automatically instruments agent invocations, model calls,
    and tool executions with spans once a TracerProvider is active.
    """
    if not OTEL_ENABLED:
        logger.info("Observability disabled (OTEL_ENABLED=false)")
        return

    try:
        from opentelemetry import trace as trace_api
        from strands.telemetry.config import StrandsTelemetry, get_otel_resource, SDKTracerProvider

        # Create a TracerProvider with X-Ray ID generator for CloudWatch correlation
        resource = get_otel_resource()
        provider = _create_xray_provider(resource)

        # Set as global provider BEFORE passing to StrandsTelemetry
        trace_api.set_tracer_provider(provider)

        # Pass the pre-configured provider to StrandsTelemetry
        telemetry = StrandsTelemetry(tracer_provider=provider)

        if OTEL_EXPORTER == "console":
            telemetry.setup_console_exporter()
            telemetry.setup_meter(enable_console_exporter=True)
            logger.info("✅ Observability: Console span exporter enabled")
        elif OTEL_EXPORTER == "otlp":
            kwargs = {}
            if OTEL_ENDPOINT:
                kwargs["endpoint"] = OTEL_ENDPOINT
            telemetry.setup_otlp_exporter(**kwargs)
            telemetry.setup_meter(enable_otlp_exporter=True)
            logger.info("✅ Observability: OTLP exporter enabled (endpoint=%s)", OTEL_ENDPOINT or "default")
        else:
            # "none" — tracer provider is set (spans are created) but not exported
            logger.info(
                "✅ Observability: Tracer initialized (no exporter — set OTEL_TRACES_EXPORTER=console or otlp to export)"
            )

        logger.info("✅ Observability: Strands telemetry initialized (service=%s)", SERVICE_NAME)

    except ImportError as e:
        logger.warning("⚠️  Observability setup skipped — missing package: %s", e)
    except Exception as e:
        logger.warning("⚠️  Observability setup failed: %s", e)


def _create_xray_provider(resource):
    """Create a TracerProvider with X-Ray-compatible ID generation."""
    from opentelemetry.sdk.trace import TracerProvider as SDKProvider

    try:
        from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator

        provider = SDKProvider(resource=resource, id_generator=AwsXRayIdGenerator())
        logger.info("✅ Observability: X-Ray ID generator configured")
    except ImportError:
        provider = SDKProvider(resource=resource)
        logger.info("ℹ️  X-Ray ID generator not available — using default IDs")

    return provider
