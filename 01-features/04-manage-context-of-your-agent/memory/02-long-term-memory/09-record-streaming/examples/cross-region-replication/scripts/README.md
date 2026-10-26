# Cross-region replication — scripts

Deployment and runtime scripts backing the [cross-region replication tutorial](../README.md). You don't run these directly — the [notebook](../06-memory-cross-region-replication.py) invokes them — but they're documented here for reference.

| File | What it does |
|---|---|
| [`deploy.sh`](./deploy.sh) | First-time deployment: packages the Lambda, uploads to S3 in both regions, deploys the DynamoDB Global Table and per-region CloudFormation stacks, creates the primary (streaming ON) and secondary (OFF) memories, and seeds the active-region config. Usage: `bash deploy.sh <primary-region> <secondary-region>`. |
| [`toggle-streaming.sh`](./toggle-streaming.sh) | The failover switch — enables or disables streaming on a region's memory via `update-memory`. Usage: `bash toggle-streaming.sh <enable\|disable> <region>`. |
| [`handler.py`](./handler.py) | Lambda consumer. Reads memory-record stream events from Kinesis and replicates them to the remote region via `BatchCreateMemoryRecords`. Skips `replicated/`-prefixed namespaces (loop prevention) and non-replicable events. |
| [`regional-stack.yaml`](./regional-stack.yaml) | Per-region CloudFormation: Kinesis stream, Lambda + event source mapping, SQS DLQ, IAM roles, CloudWatch alarms. |
| [`global-stack.yaml`](./global-stack.yaml) | DynamoDB Global Table tracking which region is active. |

## Prerequisites

- AWS CLI v2 with permissions for CloudFormation, Kinesis, Lambda, IAM, SQS, DynamoDB, S3
- Python 3.10+
- AgentCore access in both target regions

See the [cross-region replication README](../README.md) for the full architecture, RPO/RTO, and failover walkthrough.
