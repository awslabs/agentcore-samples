# Streaming use cases

Each example here composes the **memory record streaming primitive** with another AWS service or analytics pipeline. Read [`../record-streaming.py`](../record-streaming.py) first — it covers how to enable streaming, pick `METADATA_ONLY` vs `FULL_CONTENT`, and consume from Kinesis.

| Example | What it builds |
|---|---|
| [`cross-region-replication/`](./cross-region-replication/) | Replicates memory records from a source region to a destination region via Kinesis and Lambda |
| [`personalised-recommendations.py`](./personalised-recommendations.py) | Feeds streamed records into a recommendations pipeline |
| [`cross-customer-analytics.py`](./cross-customer-analytics.py) | Aggregates streamed records into an analytics store across tenants |

## Running

```bash
python personalised-recommendations.py
python cross-customer-analytics.py
```

The cross-region replication example is a deployable Lambda + Kinesis stack — see its own README for setup.
