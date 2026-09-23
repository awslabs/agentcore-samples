# Event-Driven Claims Agent — Documentation

The Event-Driven Claims Agent is an insurance claims processor built on Amazon Bedrock AgentCore. It demonstrates a dual-agent architecture where a Claims Processor evaluates submissions, a Validation Agent reviews decisions, and the system routes outcomes to auto-approval or human review based on confidence scoring.

## Where to Start

| Your goal | Start here |
|-----------|-----------|
| Deploy and try it out | [README Quick Start](../README.md#quick-start) |
| Understand how it works | [Architecture](ARCHITECTURE.md) |
| Modify it for your use case | [Tutorial: Make It Your Own](tutorial.md) |
| Look up a specific setting | [Configuration Reference](CONFIGURATION.md) |
| Validate auth & behavior after deploy | [`scripts/test_auth.py`](../scripts/test_auth.py) + [Deployment → Verify](deployment.md#9-verify-deployment) |
| Deploy step-by-step (manual) | [Deployment Guide](deployment.md) |

## Prerequisites

- AWS Account with Bedrock model access (Claude Sonnet 4)
- AgentCore CLI 0.30.0 (`npm install -g @aws/agentcore@0.30.0`; verify with `agentcore --version`)
- Node.js 20+ (required by the AgentCore CLI; CDK itself accepts 18+)
- Docker or Finch (for container builds)
- Python 3.12+ with uv
- AWS CLI v2 configured

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture](ARCHITECTURE.md) | System components, data flows, dual-agent pipeline, and Mermaid diagrams |
| [Tutorial](tutorial.md) | Guided walkthrough: change thresholds, add policies, add tools, adapt the domain |
| [Deployment](deployment.md) | One-command deploy, manual step-by-step, local dev, verification, and teardown |
| [Configuration](CONFIGURATION.md) | Every env var, Cedar policy, Cognito setting, model config, and memory parameter |
