import os


def read_secret(secret: str):
    try:
        with open(f"/etc/secrets/{secret}", "r") as f:
            return f.read().rstrip()
    except Exception:
        print("No token was provided as secret")
        return os.environ.get("DT_TOKEN", "")


def init():
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "delta"
    token = read_secret("dynatrace_otel")
    headers = {"Authorization": f"Api-Token {token}"}
    OTEL_ENDPOINT = os.environ.get(
        "OTEL_ENDPOINT",
        "https://wkf10640.live.dynatrace.com/api/v2/otlp",  # manually configure your DT tenant here or a OTel collector endpoint
    )
    from opentelemetry import trace, metrics
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create(
        {
            "service.name": "agent-core-samples",
        }
    )

    provider = TracerProvider(resource=resource)
    processor = SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{OTEL_ENDPOINT}/v1/traces", headers=headers))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{OTEL_ENDPOINT}/v1/metrics", headers=headers))
    provider = MeterProvider(
        metric_readers=[reader],
        resource=resource,
    )
    metrics.set_meter_provider(provider)
