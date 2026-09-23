# Long-term memory — Strands self-managed strategy

A culinary assistant demonstrating AgentCore's **self-managed memory strategy** — you replace AgentCore's built-in extraction with your own pipeline. Trigger conditions (message count, idle timeout, token count) publish to SNS → SQS → your Lambda, which downloads the conversation payload from S3, runs custom extraction, and writes records back via `BatchCreateMemoryRecords`.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Culinary Assistant |
| Framework | Strands Agents |
| LLM model | Anthropic Claude (Bedrock) |
| Strategies | Self-managed (custom extraction pipeline) |
| Components | SNS, SQS, Lambda, S3, `BatchCreateMemoryRecords` |
| Complexity | Advanced |

## What it does

- [`agentcore_self_managed_memory_demo.py`](./agentcore_self_managed_memory_demo.py) — provisions the infrastructure (S3, SNS, SQS, Lambda, IAM), creates the self-managed memory, fires test events through the pipeline, and shows an agent retrieving the stored records.
- [`lambda_function.py`](./lambda_function.py) — the extraction handler: downloads the S3 payload, extracts memories, and stores them.
- [`aws_utils.py`](./aws_utils.py) — helpers for creating and tearing down the AWS infrastructure.

## Prerequisites

- Python 3.11+
- AWS credentials with access to Lambda, S3, SNS, SQS, and Bedrock
- Amazon Bedrock model access (Claude)

## How to run

```bash
pip install -r requirements.txt
python agentcore_self_managed_memory_demo.py
```

For the variant that adds citation/data-lineage metadata, see [`../culinary-assistant-self-managed-strategy-with-citations/`](../culinary-assistant-self-managed-strategy-with-citations/). See the [Strands single-agent README](../../README.md) for context.
