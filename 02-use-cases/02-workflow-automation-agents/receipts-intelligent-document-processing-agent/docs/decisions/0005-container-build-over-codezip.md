# ADR-0005: Container Build Over CodeZip

**Status:** Accepted
**Date:** 2026-06-24

## Context

An AgentCore Runtime can be built as a zipped code artifact (`CodeZip`) or as a `Container` image. The receipts agent is a multi-step pipeline (Textract OCR, a deterministic table parser, a dual-agent extraction, MCP tool calls) with a non-trivial dependency set (`strands-agents`, `bedrock-agentcore`, `aws-opentelemetry-distro`, `mcp`, `botocore[crt]`).

## Decision

Build the Runtime as a `Container` (`build: "Container"` in `agentcore.json`). (Carries the claims sample's ADR-0005.)

## Reasoning

A container handles a larger, multi-dependency pipeline cleanly and gives reproducible builds. The image is built in the cloud (CodeBuild + ECR, synthesized by the CDK) — a local container engine (Docker or Finch) is only needed to assemble the build context, not to run anything. The fast inner loop (`agentcore dev --no-browser`) runs the agent directly with no container at all.

## Alternatives Considered

`CodeZip`: lighter for a tiny single-file agent, but awkward once the dependency set and the multi-step pipeline grow. Rejected for this workload.

## Consequences

A deploy includes a container build (the slowest stage, a few minutes). The build runs in the cloud, so contributors need a container engine present only for `deploy`. `deploy.sh` auto-detects Docker or Finch (`CDK_DOCKER=finch`).
