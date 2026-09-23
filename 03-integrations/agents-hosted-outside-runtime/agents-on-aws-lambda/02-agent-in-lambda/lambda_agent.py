"""
Strands agent hosted inside AWS Lambda with AgentCore Gen AI observability.

Required Lambda configuration:
  - Attach the ADOT managed layer (AWSOpenTelemetryDistroPython)
  - Enable active X-Ray tracing
  - Set environment variables:
      AGENT_OBSERVABILITY_ENABLED=true
      AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument
      OTEL_METRICS_EXPORTER=none
      OTEL_AWS_APPLICATION_SIGNALS_ENABLED=true
      OTEL_RESOURCE_ATTRIBUTES=service.name=<function-name>,service.version=1.0

The ADOT layer exports spans to X-Ray and CloudWatch Application Signals.
Additionally, _CWJsonSpanExporter (below) writes each completed span as a
single compact JSON line to stdout so that AgentCore batch evaluation can read
session data from the Lambda CloudWatch log group.
"""

import json
import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from strands import Agent

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ── CloudWatch compact-JSON span exporter ──────────────────────────────────────


class _CWJsonSpanExporter(SpanExporter):
    """Emit each span as a single compact JSON line to stdout.

    One stdout line = one CloudWatch Logs log event = one span record.
    This lets AgentCore batch evaluation read Gen AI spans directly from the
    Lambda log group (/aws/lambda/<function>) without requiring a separate
    /aws/spans sink.
    """

    def export(self, spans):
        for span in spans:
            ctx = span.context
            record = {
                "name": span.name,
                "traceId": format(ctx.trace_id, "032x"),
                "spanId": format(ctx.span_id, "016x"),
                "parentSpanId": (format(span.parent.span_id, "016x") if span.parent else None),
                "startTimeUnixNano": span.start_time,
                "endTimeUnixNano": span.end_time,
                "attributes": dict(span.attributes or {}),
                "events": [
                    {
                        "name": e.name,
                        "timeUnixNano": e.timestamp,
                        "attributes": dict(e.attributes or {}),
                    }
                    for e in (span.events or [])
                ],
                "resource": dict(span.resource.attributes or {}),
                "status": {"code": span.status.status_code.name},
            }
            print(json.dumps(record, default=str), flush=True)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def _register_cw_span_exporter() -> None:
    """Attach _CWJsonSpanExporter to the current global TracerProvider.

    Called at module load time (after the ADOT exec-wrapper has already
    initialised the global TracerProvider) so every Strands span is captured.
    Uses SimpleSpanProcessor so spans flush synchronously before Lambda freezes.
    """
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            provider.add_span_processor(SimpleSpanProcessor(_CWJsonSpanExporter()))
            logger.info("CW compact-JSON span exporter registered")
        else:
            logger.warning("TracerProvider has no add_span_processor — skipping CW exporter")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not register CW span exporter: %s", exc)


if os.environ.get("AGENT_OBSERVABILITY_ENABLED") == "true":
    _register_cw_span_exporter()


# ── Agent (initialised once per container) ─────────────────────────────────────

agent = Agent()


# ── Handler ────────────────────────────────────────────────────────────────────


def handler(event, context=None):
    """Lambda entry point — forwards the incoming prompt to the Strands agent."""
    prompt = event.get("prompt", "Hello! How can I help you today?")
    logger.info("Received prompt: %s", prompt)
    result = agent(prompt)
    return {"result": result.message}


if __name__ == "__main__":
    print(handler({"prompt": "How far is the Moon from Earth?"}))
