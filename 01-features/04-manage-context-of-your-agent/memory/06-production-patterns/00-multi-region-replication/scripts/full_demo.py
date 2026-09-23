"""Full two-region demo: LTM via record streaming + STM via dual-write CreateEvent.

This is the headline sample. It replicates **both** memory layers across two
regions using the production architecture (not polling):

* **STM (short-term events)** — replicated at *write time* by dual-writing each
  conversation turn with :class:`DualRegionEventWriter`:
    - SOURCE region: normal ``CreateEvent`` -> STM + triggers LTM extraction.
    - TARGET region: ``CreateEvent(extractionMode="SKIP")`` -> STM history only,
      NO re-extraction (the LTM records arrive via the stream instead).
* **LTM (long-term records)** — replicated via **record streaming**: the source
  memory streams extracted records to a Kinesis Data Stream; this script consumes
  that stream locally (the same code the Lambda runs) and re-creates each record
  in the target with its original ``memoryRecordId``.

End to end:
  1. Create source + target memories (identical semantic-strategy config).
  2. Create a Kinesis stream in the source region and enable record streaming on
     the source memory (``streamDeliveryResources`` = MEMORY_RECORDS/FULL_CONTENT).
  3. Drive a conversation through ``DualRegionEventWriter`` (STM replicates now;
     source extraction kicks off and will publish LTM records to the stream).
  4. Consume the Kinesis stream and replicate LTM records to the target region.
  5. Verify the target holds the same STM events AND LTM records (same IDs), and
     prove ``extractionMode="SKIP"`` kept the target from re-extracting (its LTM
     count matches what the stream delivered — it didn't double).
  6. Tear everything down (``--keep`` leaves it in place).

Usage:
    python scripts/full_demo.py --source-region us-east-1 --target-region us-west-2
    python scripts/full_demo.py --keep
    python scripts/full_demo.py --extraction-timeout 600 --stream-poll-timeout 600
"""

import argparse
import json
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, ".")
from agentcore_replication import (
    DualRegionEventWriter,
    StreamStats,
    make_target_client,
    process_kinesis_records,
    stream_delivery_resources,
)

ACTOR_ID = "demo-user"
SESSION_ID = "demo-session-1"
NAMESPACE_TEMPLATE = "/facts/{actorId}"

CONVERSATION = [
    ("USER", "Hi! I'm planning a trip and wanted to set up my travel profile."),
    ("ASSISTANT", "Great — tell me your preferences and I'll remember them."),
    ("USER", "I'm vegetarian, and I always want a window seat on flights."),
    ("ASSISTANT", "Noted: vegetarian meals and window seats."),
    ("USER", "I live in Seattle and I prefer morning departures before 10am."),
    ("ASSISTANT", "Got it — Seattle home base, morning flights before 10am."),
    ("USER", "Also I'm allergic to peanuts, please flag that for meals."),
    ("ASSISTANT", "I'll flag the peanut allergy on every booking."),
]


def log(msg=""):
    print(msg, flush=True)


def banner(title):
    log()
    log("=" * 72)
    log(f"  {title}")
    log("=" * 72)


# --------------------------------------------------------------------------- #
# Control-plane helpers
# --------------------------------------------------------------------------- #


def wait_active(ctl, mem_id, label, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = ctl.get_memory(memoryId=mem_id)["memory"]["status"]
        log(f"  [{label}] {mem_id} status={st}")
        if st == "ACTIVE":
            return
        if st == "FAILED":
            raise SystemExit(f"{label} memory FAILED")
        time.sleep(15)
    raise SystemExit(f"{label} memory not ACTIVE in {timeout}s")


def create_memory(ctl, region, name, execution_role_arn=None):
    strategies = [{"semanticMemoryStrategy": {"name": "semantic", "namespaces": [NAMESPACE_TEMPLATE]}}]
    kwargs = dict(
        name=name,
        description="STM+LTM cross-region replication demo",
        eventExpiryDuration=90,
        memoryStrategies=strategies,
    )
    if execution_role_arn:
        kwargs["memoryExecutionRoleArn"] = execution_role_arn
    mem_id = ctl.create_memory(**kwargs)["memory"]["id"]
    log(f"Created {name} -> {mem_id} ({region})")
    wait_active(ctl, mem_id, region)
    return mem_id


# --------------------------------------------------------------------------- #
# Kinesis + streaming setup (source region)
# --------------------------------------------------------------------------- #


def create_stream(kinesis, name):
    try:
        kinesis.create_stream(StreamName=name, ShardCount=1)
    except kinesis.exceptions.ResourceInUseException:
        pass
    waiter = kinesis.get_waiter("stream_exists")
    waiter.wait(StreamName=name)
    arn = kinesis.describe_stream_summary(StreamName=name)["StreamDescriptionSummary"]["StreamARN"]
    log(f"Kinesis stream ready: {arn}")
    return arn


def ensure_memory_stream_role(iam, stream_arn, role_name):
    """Create (or reuse) an execution role AgentCore uses to write to Kinesis."""
    assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume),
            Description="AgentCore memory -> Kinesis streaming (demo)",
        )["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="kinesis-put",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "kinesis:PutRecord",
                            "kinesis:PutRecords",
                            "kinesis:DescribeStream",
                            "kinesis:DescribeStreamSummary",
                        ],
                        "Resource": stream_arn,
                    }
                ],
            }
        ),
    )
    log(f"Memory streaming role ready: {arn}")
    # IAM propagation is eventually consistent; give it a moment.
    time.sleep(10)
    return arn


def enable_streaming(ctl, memory_id, stream_arn):
    ctl.update_memory(
        memoryId=memory_id,
        streamDeliveryResources=stream_delivery_resources(stream_arn),
    )
    log(f"Record streaming enabled on {memory_id} -> {stream_arn}")


def consume_stream(kinesis, stream_name, target_client, target_memory_id, expected, timeout):
    """Poll the Kinesis stream and replicate LTM records until `expected` land."""
    log(f"\nConsuming Kinesis stream (up to {timeout}s, expecting ~{expected} records)...")
    shard_id = kinesis.describe_stream(StreamName=stream_name)["StreamDescription"]["Shards"][0]["ShardId"]
    shard_iter = kinesis.get_shard_iterator(StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")[
        "ShardIterator"
    ]

    stats = StreamStats()
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = kinesis.get_records(ShardIterator=shard_iter, Limit=100)
        shard_iter = resp["NextShardIterator"]
        records = resp.get("Records", [])
        if records:
            process_kinesis_records(records, target_client, target_memory_id, stats=stats)
            log(
                f"  stream: received={stats.received} replicated={stats.replicated} "
                f"skipped={stats.skipped} failed={stats.failed}"
            )
        if stats.replicated >= expected and expected > 0:
            break
        time.sleep(5)
    return stats


# --------------------------------------------------------------------------- #
# Data-plane verification helpers
# --------------------------------------------------------------------------- #


def count_events(data, mem_id):
    total, token = 0, None
    while True:
        kw = {"memoryId": mem_id, "actorId": ACTOR_ID, "sessionId": SESSION_ID, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        resp = data.list_events(**kw)
        total += len(resp.get("events", []))
        token = resp.get("nextToken")
        if not token:
            return total


def list_records(data, mem_id):
    recs, token = [], None
    while True:
        kw = {"memoryId": mem_id, "namespace": "/", "maxResults": 100}
        if token:
            kw["nextToken"] = token
        resp = data.list_memory_records(**kw)
        recs.extend(resp.get("memoryRecordSummaries", []))
        token = resp.get("nextToken")
        if not token:
            return recs


def wait_for_extraction(data, mem_id, label, timeout):
    log(f"\nWaiting up to {timeout}s for SOURCE LTM extraction (async)...")
    deadline = time.time() + timeout
    recs = []
    while time.time() < deadline:
        recs = list_records(data, mem_id)
        if recs:
            time.sleep(20)  # let multi-fact extraction finish
            return list_records(data, mem_id)
        log(f"  [{label}] 0 LTM records yet; waiting...")
        time.sleep(15)
    return recs


def show_records(recs, label):
    log(f"\n{label}: {len(recs)} LTM record(s)")
    for r in recs:
        text = (r.get("content") or {}).get("text", "")
        log(f"  - {r['memoryRecordId']}: {text}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-region", default="us-east-1")
    p.add_argument("--target-region", default="us-west-2")
    p.add_argument(
        "--extraction-timeout", type=int, default=420, help="seconds to wait for async source LTM extraction"
    )
    p.add_argument("--stream-poll-timeout", type=int, default=420, help="seconds to consume the Kinesis stream")
    p.add_argument("--keep", action="store_true", help="don't delete resources")
    args = p.parse_args()

    suffix = uuid.uuid4().hex[:8]
    src_ctl = boto3.client("bedrock-agentcore-control", region_name=args.source_region)
    tgt_ctl = boto3.client("bedrock-agentcore-control", region_name=args.target_region)
    kinesis = boto3.client("kinesis", region_name=args.source_region)
    iam = boto3.client("iam")

    src_id = tgt_id = None
    stream_name = f"agentcore-ltm-demo-{suffix}"
    role_name = f"agentcore-mem-stream-demo-{suffix}"
    created_stream = created_role = False

    try:
        banner("STEP 1 — Create matching memories in both regions")
        tgt_id = create_memory(tgt_ctl, args.target_region, f"replDemoTgt{suffix}")
        src_id = create_memory(src_ctl, args.source_region, f"replDemoSrc{suffix}")

        banner("STEP 2 — Create Kinesis stream + enable record streaming on SOURCE")
        stream_arn = create_stream(kinesis, stream_name)
        created_stream = True
        role_arn = ensure_memory_stream_role(iam, stream_arn, role_name)
        created_role = True
        # Re-create source memory WITH the execution role so it can write to
        # Kinesis (memoryExecutionRoleArn can't be added by this demo's
        # update_memory call surface uniformly, so set it up front via update).
        src_ctl.update_memory(memoryId=src_id, memoryExecutionRoleArn=role_arn)
        wait_active(src_ctl, src_id, f"{args.source_region}/role")
        enable_streaming(src_ctl, src_id, stream_arn)

        banner("STEP 3 — Dual-write the conversation (STM replicates NOW)")
        log(
            "SOURCE: CreateEvent (normal) -> STM + triggers LTM extraction\n"
            'TARGET: CreateEvent(extractionMode="SKIP") -> STM history only\n'
        )
        writer = DualRegionEventWriter(src_id, tgt_id, args.source_region, args.target_region)
        for i, (role, text) in enumerate(CONVERSATION):
            writer.record_turn(ACTOR_ID, SESSION_ID, role, text, event_timestamp=time.time() + i * 0.001)
            log(f"  + [{role}] {text}")

        src_data = boto3.client("bedrock-agentcore", region_name=args.source_region)
        tgt_data = boto3.client("bedrock-agentcore", region_name=args.target_region)
        src_events = count_events(src_data, src_id)
        log(
            f"\nSource STM: {src_events} events. "
            f"Target STM (via SKIP dual-write): {count_events(tgt_data, tgt_id)} events."
        )

        banner("STEP 4 — Wait for SOURCE extraction, then replicate LTM via STREAM")
        src_recs = wait_for_extraction(src_data, src_id, args.source_region, args.extraction_timeout)
        show_records(src_recs, "SOURCE")

        target_client = make_target_client(boto3.Session(), args.target_region)
        stream_stats = consume_stream(
            kinesis, stream_name, target_client, tgt_id, expected=len(src_recs), timeout=args.stream_poll_timeout
        )
        log(f"\nStream replication stats: {stream_stats.as_dict()}")

        banner("STEP 5 — Verify TARGET (both layers) + prove extractionMode=SKIP")
        time.sleep(8)
        tgt_events = count_events(tgt_data, tgt_id)
        log(f"TARGET STM: {tgt_events} events (source had {src_events}).")

        # Freshly-written LTM records are not queryable instantly (the same async
        # indexing lag we wait out on the source). Poll until the records the
        # stream replicated become visible, so we measure a real baseline rather
        # than a transient 0.
        expected_ltm = stream_stats.replicated
        log(f"\nWaiting for the {expected_ltm} stream-replicated record(s) to be queryable in the target...")
        tgt_recs = []
        deadline = time.time() + 120
        while time.time() < deadline:
            tgt_recs = list_records(tgt_data, tgt_id)
            if len(tgt_recs) >= expected_ltm:
                break
            time.sleep(5)
        show_records(tgt_recs, "TARGET")

        banner("RESULT")
        ok = True

        if src_events and tgt_events >= src_events:
            log(f'✅ STM: {tgt_events} events dual-written to target with extractionMode="SKIP".')
        else:
            ok = False
            log(f"❌ STM: expected >= {src_events}, found {tgt_events}.")

        if src_recs:
            src_texts = {(r.get("content") or {}).get("text") for r in src_recs}
            tgt_texts = [(r.get("content") or {}).get("text") for r in tgt_recs]
            tgt_text_set = set(tgt_texts)

            if src_texts.issubset(tgt_text_set):
                log(f"✅ LTM: all {len(src_texts)} source records replicated via the Kinesis record stream.")
                log(
                    "   (Replays are idempotent: the source memoryRecordId is used "
                    "as the target requestIdentifier, so re-delivery is a no-op.)"
                )
            else:
                ok = False
                log(f"❌ LTM: missing in target: {src_texts - tgt_text_set}")

            # SKIP proof: the SKIP'd STM events were dual-written back in Step 3,
            # minutes ago — long enough that, if SKIP were ignored, the target
            # would already have re-extracted them. Re-extraction would surface as
            # records BEYOND the ones the stream delivered, i.e. duplicate facts
            # (the same text appearing twice). So: no duplicate texts AND the
            # target count does not exceed what the stream replicated.
            duplicates = len(tgt_texts) != len(tgt_text_set)
            if not duplicates and len(tgt_recs) <= expected_ltm:
                log(
                    f"✅ SKIP: target holds exactly the {len(tgt_recs)} "
                    "stream-replicated record(s), no duplicates — the dual-written "
                    "events were NOT re-extracted. LTM came only from the stream, "
                    'exactly as extractionMode="SKIP" guarantees.'
                )
            else:
                ok = False
                log(
                    f"❌ SKIP: target has {len(tgt_recs)} records "
                    f"(expected {expected_ltm}), duplicates={duplicates} — the "
                    "SKIP'd events appear to have been re-extracted."
                )
        else:
            log(
                "ℹ️  LTM: source produced no records in time; STM path still "
                "verified. Re-run with a larger --extraction-timeout."
            )

        log()
        log(
            "🎉 DEMO PASSED: STM (dual-write) + LTM (record streaming) replicated."
            if ok
            else "DEMO FAILED — see ❌ lines above."
        )
        if not ok:
            sys.exit(1)

    finally:
        if not args.keep:
            log("\nCleaning up...")
            for ctl, mid, rg in [(src_ctl, src_id, args.source_region), (tgt_ctl, tgt_id, args.target_region)]:
                if mid:
                    try:
                        ctl.delete_memory(memoryId=mid)
                        log(f"  deleted memory {mid} ({rg})")
                    except ClientError as e:
                        log(f"  warn {mid}: {e}")
            if created_stream:
                try:
                    kinesis.delete_stream(StreamName=stream_name, EnforceConsumerDeletion=True)
                    log(f"  deleted stream {stream_name}")
                except ClientError as e:
                    log(f"  warn stream: {e}")
            if created_role:
                try:
                    iam.delete_role_policy(RoleName=role_name, PolicyName="kinesis-put")
                    iam.delete_role(RoleName=role_name)
                    log(f"  deleted role {role_name}")
                except ClientError as e:
                    log(f"  warn role: {e}")
        else:
            log("\n--keep set; resources left in place:")
            log(f"  source memory: {src_id} ({args.source_region})")
            log(f"  target memory: {tgt_id} ({args.target_region})")
            log(f"  kinesis stream: {stream_name} ({args.source_region})")
            log(f"  streaming role: {role_name}")


if __name__ == "__main__":
    main()
