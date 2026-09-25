#!/usr/bin/env bash
# Launch the Strands agent under ADOT auto-instrumentation.
#
# opentelemetry-instrument wraps the target process so every framework we
# depend on (strands, boto3, urllib3, http.server) is auto-instrumented with
# no code changes. The AWS distro (activated via OTEL_PYTHON_DISTRO=aws_distro
# and OTEL_PYTHON_CONFIGURATOR=aws_configurator) points the OTLP exporter at
# the CloudWatch OTLP endpoint and SigV4-signs each request with the
# execution role's credentials (picked up from the standard AWS SDK chain).
set -euo pipefail

# python3.11 is installed explicitly by the Dockerfile because Strands
# requires >= 3.10 and AL2023's default `python3` is 3.9.
exec opentelemetry-instrument python3.11 -u /app/agent.py
