#!/bin/bash
# Build a Lambda-compatible deployment ZIP.
#
# Bundles strands-agents + aws-opentelemetry-distro.
# aws-opentelemetry-distro ensures the opentelemetry-sdk version in /var/task
# is compatible with the ADOT managed layer's startup code (/opt/otel-instrument).
# The ADOT layer is still attached separately — it provides the exec wrapper,
# X-Ray exporter, and Application Signals integration.
#
# Usage (replace 'finch' with 'docker' if using Docker):
#   chmod +x build.sh
#   ./build.sh
#
# Output: package.zip  (upload this to your Lambda function)

set -e

echo "Building Lambda deployment package..."

# Build inside the SAM container to ensure native deps compile for the Lambda runtime.
# Replace 'finch' with 'docker' if you are using Docker.
finch run --rm -v "$PWD":/var/task public.ecr.aws/sam/build-python3.13:latest-x86_64 /bin/sh -c "
  rm -rf package package.zip
  mkdir -p package
  pip install --quiet -r requirements.txt -t /var/task/package
  cd /var/task/package
  zip -r9q /var/task/package.zip .
  cd /var/task
  zip -g package.zip lambda_agent.py
"

echo "Build complete → package.zip"
