import os
from pathlib import Path
from urllib.parse import urlsplit

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


def export_config():
    endpoint = os.environ.get("OTEL_ENDPOINT", "").rstrip("/")
    try:
        url = urlsplit(endpoint)
        valid = url.scheme == "https" and url.hostname and url.username is None and not (url.query or url.fragment)
        valid = valid and not any(c.isspace() or c == "\\" for c in endpoint)
        _ = url.port  # Reject malformed ports before constructing either exporter.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Set OTEL_ENDPOINT to your trusted HTTPS OTLP base URL (no credentials, query or fragment).")

    try:
        token = Path("/etc/secrets/dynatrace_otel").read_text().strip()
    except FileNotFoundError:
        token = os.environ.get("DT_TOKEN", "").strip()
    if not token or not token.isascii() or not token.isprintable() or " " in token:
        raise ValueError("Supply a valid Dynatrace ingest token via the secret file or DT_TOKEN.")
    return endpoint, {"Authorization": f"Api-Token {token}"}


def init():
    endpoint, headers = export_config()
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "delta"
    resource = Resource.create({"service.name": "agent-core-samples"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers))
    )
    trace.set_tracer_provider(provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers))
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader], resource=resource))
