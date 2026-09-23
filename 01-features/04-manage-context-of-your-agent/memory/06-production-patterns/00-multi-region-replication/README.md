# Cross-Region Replication for Amazon Bedrock AgentCore Memory

A deployable sample for **customer-driven, cross-region replication** of AgentCore
Memory. It replicates **both** memory layers from a primary region to a replica
region, so you can fail over to the replica with conversation history *and*
extracted knowledge intact.

| Layer | What it holds | How it replicates |
| --- | --- | --- |
| **LTM** (long-term records) | extracted facts / knowledge | **record streaming** — source memory → Kinesis Data Stream → consumer Lambda → `BatchCreateMemoryRecords` in the target |
| **STM** (short-term events) | raw conversation turns | **dual-write at write time** — `CreateEvent` to the source (normal) and to the target with `extractionMode="SKIP"` |

## How it works

```
 Source Region (us-east-1)                      Target Region (us-west-2)
┌────────────────────────────────┐            ┌──────────────────────────────┐
│ Memory (primary)               │            │ Memory (replica)             │
│                                │            │                              │
│ CreateEvent (normal) ──────────┼── STM ─────┼─▶ CreateEvent(extractionMode │
│   │  dual-write at write time  │  dual-write│     ="SKIP")  history only,  │
│   ▼                            │            │     NO re-extraction         │
│ LTM extraction                 │            │                              │
│   │                            │            │ BatchCreateMemoryRecords     │
│   ▼                            │            │   (requestIdentifier =       │
│ Kinesis record stream ─────────┼── LTM ─────┼─▶  source memoryRecordId) ◀── │
│ (MEMORY_RECORDS/FULL_CONTENT)  │  streaming │     consumer Lambda          │
└────────────────────────────────┘            └──────────────────────────────┘
```

Two AgentCore building blocks make this work:

* **`extractionMode="SKIP"` on `CreateEvent`** — copy events into the target for
  history / failover replay **without** re-triggering LTM extraction there. This
  is the key to keeping the two paths from colliding: the source's extracted
  records already arrive over the stream, so the target must *not* re-extract the
  replicated events into duplicate records.
* **Record streaming (`streamDeliveryResources`)** — the source memory publishes
  every extracted record to a Kinesis Data Stream, which the consumer replays into
  the target. Using the source `memoryRecordId` as the target `requestIdentifier`
  makes replays idempotent (re-delivery is a conditional no-op, not a duplicate).

## Repository layout

```
.
├── agentcore_replication/      # reusable Python package
│   ├── stream_consumer.py      # LTM: consume Kinesis record stream -> target
│   └── dual_writer.py          # STM: dual-write CreateEvent (SKIP on target)
├── lambda/
│   └── stream_handler.py       # Kinesis-triggered LTM consumer (production)
├── infra/
│   └── streaming-stack.yaml    # Kinesis + consumer Lambda + ESM + IAM + DLQ
├── scripts/
│   ├── create_memories.py      # create matching source + target memories
│   ├── enable_streaming.py     # turn record streaming on/off (UpdateMemory)
│   ├── full_demo.py            # end-to-end STM+LTM demo (the headline sample)
│   └── deploy_streaming.sh     # deploy the LTM streaming path
└── requirements.txt
```

## Prerequisites

* AWS CLI v2, configured for an account with AgentCore access in **both** regions.
* Python 3.10+ and `pip install -r requirements.txt` (`boto3 >= 1.43.36`).
* IAM permissions to create CloudFormation stacks, Lambda functions, IAM roles,
  Kinesis streams, and S3 buckets in the source region.

## Run the full demo first

The fastest way to *see* both layers replicate is the self-contained demo. With
**real AWS resources** it creates two throwaway memories, enables record
streaming on the source, dual-writes a conversation, consumes the Kinesis stream
locally (the same code the Lambda runs), verifies both layers landed in the
target, and proves `extractionMode="SKIP"` prevented re-extraction. It tears
everything down afterward (`--keep` leaves it in place).

```bash
pip install -r requirements.txt

python scripts/full_demo.py \
  --source-region us-east-1 \
  --target-region us-west-2

# leave the memories/stream/role in place to inspect:
python scripts/full_demo.py --keep

# extraction + streaming are async; allow more time on a cold account:
python scripts/full_demo.py --extraction-timeout 600 --stream-poll-timeout 600
```

## Build it into your own pipeline

### 1. Create matching memories in both regions

```bash
python scripts/create_memories.py \
  --name my_agent_memory \
  --source-region us-east-1 \
  --target-region us-west-2
# writes memories.json with both IDs
```

This writes the source and target memory IDs to `memories.json`. Use those two
IDs in place of the `mem-...` placeholders in steps 2 and 3.

### 2. STM — dual-write events from your agent

Replace your single `create_event` call with `DualRegionEventWriter`, which writes
to both regions (target with `extractionMode="SKIP"`):

```python
from agentcore_replication import DualRegionEventWriter

writer = DualRegionEventWriter(
    source_memory_id="mem-aaaaaaaaaa",
    target_memory_id="mem-bbbbbbbbbb",
    source_region="us-east-1",
    target_region="us-west-2",
)
writer.record_turn(actor_id="user-1", session_id="sess-1",
                   role="USER", text="I'm vegetarian")
```

### 3. LTM — deploy the streaming consumer

```bash
scripts/deploy_streaming.sh \
  --source-memory-id mem-aaaaaaaaaa \
  --target-memory-id mem-bbbbbbbbbb \
  --source-region us-east-1 \
  --target-region us-west-2
```

This creates the Kinesis stream + consumer Lambda (`infra/streaming-stack.yaml`)
in the **source** region, wires the Event Source Mapping with a DLQ and CloudWatch
alarms, attaches a streaming execution role to the source memory, and enables
record streaming. From then on, extracted LTM records flow source → Kinesis →
Lambda → `BatchCreateMemoryRecords` in the target.

> **Note:** record streaming only carries records created *after* you enable it.
> Enable streaming before your agents start writing, or backfill pre-existing data
> with `ListMemoryRecords` → `BatchCreateMemoryRecords` (the same idempotent
> `requestIdentifier` path the consumer uses).

## Failover

This is active-passive. On primary-region failure, point your agents at the replica
memory in the target region. To replicate the other direction afterward, swap
source/target: enable streaming on the new primary (`enable_streaming.py`), point
your dual-writer the other way, and deploy a consumer in the new source region.
Because LTM writes are idempotent, the first reverse pass safely lands only what's
missing.

> **Pause replication** during failover with
> `scripts/enable_streaming.py --memory-id <SRC> --region <SRC_REGION> --disable`.

## Cost

* **Kinesis** — one shard in the source region (~$11/mo) plus PUT payload units.
* **Lambda** — invoked per stream batch; pennies at typical memory write rates.
* **AgentCore Memory** storage in the second region.
* **STM dual-write** adds one extra `CreateEvent` per turn — no standing infra.

## Limitations

* **Deletes** are not replicated (`MemoryRecordDeleted` events are skipped).
  AgentCore consolidation handles stale records in the target; call
  `DeleteMemoryRecord` explicitly if you need exact parity.
* **STM latency**: STM is replicated synchronously at write time, so a target
  outage surfaces on the write path — wrap `record_turn` to tolerate target
  failures (the source write is what your agent depends on).
* **LTM latency**: RPO ≈ extraction time + Kinesis/Lambda lag (seconds).
* **Single AWS account** in this sample. Cross-account requires resource policies /
  assumed roles on the target memory and stream.
* **Strategy IDs**: both memories are created with identical strategy config so
  namespaces align. The source `memoryStrategyId` is not forwarded (it is
  per-memory); AgentCore associates replicated records by namespace. If your
  regions differ, map source strategy IDs to target IDs in the consumer.
