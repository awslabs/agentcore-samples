"""Pool manager: keeps every registered (arn, endpoint) pool at its minPoolSize.

Three ways in:
  1. broker.claim(), on a hit, fire-and-forget invokes {"replenish_one": {"arn", "endpoint"}} -
     no lease, no sweep: warm exactly one replacement for the one just claimed, as fast as possible.
  2. broker.claim(), on a miss, and the table stream (claims/releases/retirements) both invoke with
     no special payload, which runs the full lease-protected sweep below.
  3. Every 5 minutes, the schedule invokes the same full sweep - the only trigger that can catch an
     AVAILABLE session aging out with zero claim/release traffic (nothing mutates the table, so the
     stream has nothing to fire on), and the only thing that generally revisits a stale runtime
     version if a promotion isn't followed by any claim.

Only one full sweep works at a time. A lease item in the table acts as the lock: a run
that finds it held marks it "pending" and returns, and the holder runs another
pass before releasing, so no trigger is lost and the pool never overshoots. The
replenish_one fast path intentionally skips this lock - it only ever adds exactly
one session per claim, so there's no overshoot for the lease to prevent, and taking
it would only add back the latency this path exists to avoid.
"""
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from boto3.dynamodb.conditions import Key
from common import (
    agent_pools,
    agentcore,
    get_global_config,
    get_pool,
    pool_key,
    stop_session,
    table,
)

control = boto3.client("bedrock-agentcore-control")


def endpoint_state(arn, endpoint):
    """(liveVersion, status) of the endpoint, or (None, None) if the lookup fails."""
    runtime_id = arn.rsplit("/", 1)[-1]
    try:
        ep = control.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=endpoint)
        return ep.get("liveVersion"), ep.get("status")
    except Exception as exc:  # noqa: BLE001 - don't block refills on a control-plane hiccup
        print(json.dumps({"endpoint_lookup_error": f"{arn}#{endpoint}", "error": str(exc)}))
        return None, None


def available_items(arn, endpoint):
    items, kwargs = [], {}
    while True:
        resp = table.query(IndexName="available",
                           KeyConditionExpression=Key("pool").eq(pool_key(arn, endpoint)), **kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def take_out(session_id, status):
    """Remove a session from the pool before stopping it, so no one claims it mid-retire."""
    try:
        table.update_item(Key={"sessionId": session_id},
                          UpdateExpression="SET #s = :r REMOVE #p",
                          ConditionExpression="attribute_exists(#p)",
                          ExpressionAttributeNames={"#s": "status", "#p": "pool"},
                          ExpressionAttributeValues={":r": status})
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False   # claimed in the meantime


def retire(arn, endpoint, items, live_version, now, retire_after_s):
    """items come from the KEYS_ONLY "available" index: only sessionId, pool, createdAt are
    present. retireAt is derived from createdAt (it's always createdAt + this pool's retireAfterS)
    rather than read from the item, which would silently default to 0 and retire every item every
    pass. version isn't derivable the same way, so it costs one GetItem, skipped once age already
    decided the item's fate.
    """
    kept, by_age, stale = [], 0, 0
    for it in items:
        is_old = int(it["createdAt"]) + retire_after_s <= now
        is_stale = False
        if live_version and not is_old:
            version = table.get_item(Key={"sessionId": it["sessionId"]},
                                     ProjectionExpression="version").get("Item", {}).get("version")
            is_stale = bool(version and version != live_version)
        if not (is_stale or is_old):
            kept.append(it)
            continue
        if not take_out(it["sessionId"], "STALE" if is_stale else "RETIRING"):
            continue
        stop_session(it["sessionId"], endpoint, arn)
        table.delete_item(Key={"sessionId": it["sessionId"]})
        stale += is_stale
        by_age += not is_stale
    return kept, by_age, stale


def warm(arn, endpoint):
    """Start one session on the endpoint. Runs in a worker thread (boto3 clients are thread-safe)."""
    session_id = f"pool-{uuid.uuid4().hex}"   # session IDs need at least 33 characters
    started = time.time()
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=arn, runtimeSessionId=session_id, qualifier=endpoint,
        payload=json.dumps({"action": "warmup"}).encode())
    resp["response"].read()
    return session_id, round((time.time() - started) * 1000)


def put_available(arn, endpoint, session_id, version, retire_after_s, max_lifetime_s):
    created = int(time.time())
    item = {"sessionId": session_id, "status": "AVAILABLE", "pool": pool_key(arn, endpoint),
            "arn": arn, "endpoint": endpoint, "createdAt": created,
            "retireAt": created + retire_after_s,
            "expiresAt": created + max_lifetime_s}   # TTL backstop for abandoned sessions
    if version:
        item["version"] = version
    table.put_item(Item=item)


def replenish_one(arn, endpoint):
    """Fast path triggered directly by broker.claim() on a hit. No lease: see module docstring."""
    pool = get_pool(arn, endpoint)
    if not pool:   # unregistered between the claim and this async invoke landing - rare, skip
        result = {"replenish_one_skipped": f"{arn}#{endpoint}", "reason": "not registered"}
        print(json.dumps(result))
        return result
    live_version, _ = endpoint_state(arn, endpoint)
    try:
        session_id, ms = warm(arn, endpoint)
    except Exception as exc:  # noqa: BLE001 - one failed fast-path replenish, logged, not fatal
        result = {"replenish_one_error": f"{arn}#{endpoint}", "error": str(exc)}
        print(json.dumps(result))
        return result
    put_available(arn, endpoint, session_id, live_version, pool["retireAfterS"], pool["maxLifetimeS"])
    result = {"replenished": f"{arn}#{endpoint}", "warmup_ms": ms}
    print(json.dumps(result))
    return result


def run_once():
    now = int(time.time())
    cfg = get_global_config()
    summary, plan = {}, []
    for row in agent_pools():
        arn, endpoint, target = row["arn"], row["endpoint"], row["minPoolSize"]
        key = pool_key(arn, endpoint)
        live_version, status = endpoint_state(arn, endpoint)
        items, by_age, stale = retire(arn, endpoint, available_items(arn, endpoint), live_version,
                                      now, row["retireAfterS"])
        missing = max(0, target - len(items))
        if status in ("CREATING", "UPDATING"):
            missing = 0   # wait for the endpoint to settle, the next run refills it
        summary[key] = {"live_version": live_version, "status": status, "available": len(items),
                        "retired_age": by_age, "retired_stale": stale, "missing": missing,
                        "added": 0, "failed": 0, "warmup_ms": []}
        plan.append([arn, endpoint, live_version, missing, row["retireAfterS"], row["maxLifetimeS"]])

    # interleave pools so one large pool can't starve the others within the per-run cap
    queue = []
    while len(queue) < cfg["maxAddsPerRun"] and any(p[3] > 0 for p in plan):
        for p in plan:
            if p[3] > 0 and len(queue) < cfg["maxAddsPerRun"]:
                queue.append((p[0], p[1], p[2], p[4], p[5]))
                p[3] -= 1

    with ThreadPoolExecutor(max_workers=max(1, cfg["refillConcurrency"])) as ex:
        futures = {ex.submit(warm, arn, ep): (arn, ep, ver, ra, ml)
                   for arn, ep, ver, ra, ml in queue}
        for fut in as_completed(futures):
            arn, endpoint, version, retire_after_s, max_lifetime_s = futures[fut]
            s = summary[pool_key(arn, endpoint)]
            try:
                session_id, ms = fut.result()
            except Exception as exc:  # noqa: BLE001 - failed warm-up: the next run tries again
                s["failed"] += 1
                print(json.dumps({"warmup_error": f"{arn}#{endpoint}", "error": str(exc)}))
                continue
            put_available(arn, endpoint, session_id, version, retire_after_s, max_lifetime_s)
            s["added"] += 1
            s["warmup_ms"].append(ms)

    print(json.dumps(summary))
    return summary


# ---------- single-writer lease ----------

LOCK_KEY = {"sessionId": "#lock#pool-manager"}   # no "pool" attribute, so it never shows in the index
LEASE_S = 330                                    # a little over the 300 s Lambda timeout
MAX_PASSES = 5
_cond_failed = table.meta.client.exceptions.ConditionalCheckFailedException


def acquire(owner):
    now = int(time.time())
    try:
        table.put_item(Item={**LOCK_KEY, "owner": owner, "leaseUntil": now + LEASE_S,
                             "expiresAt": now + 86400},
                       ConditionExpression="attribute_not_exists(sessionId) OR leaseUntil < :now",
                       ExpressionAttributeValues={":now": now})
        return True
    except _cond_failed:
        pass
    try:   # someone else holds it: ask them for another pass
        table.update_item(Key=LOCK_KEY, UpdateExpression="SET pending = :t",
                          ConditionExpression="attribute_exists(sessionId) AND leaseUntil >= :now",
                          ExpressionAttributeValues={":t": True, ":now": now})
        return False
    except _cond_failed:
        return acquire(owner)   # the lock was released or expired in between: try again


def release_or_continue(owner):
    """Release the lock unless another trigger arrived meanwhile. Returns True when released."""
    try:
        table.delete_item(Key=LOCK_KEY,
                          ConditionExpression="#o = :o AND attribute_not_exists(pending)",
                          ExpressionAttributeNames={"#o": "owner"}, ExpressionAttributeValues={":o": owner})
        return True
    except _cond_failed:
        table.update_item(Key=LOCK_KEY, UpdateExpression="REMOVE pending SET leaseUntil = :l",
                          ConditionExpression="#o = :o", ExpressionAttributeNames={"#o": "owner"},
                          ExpressionAttributeValues={":o": owner, ":l": int(time.time()) + LEASE_S})
        return False


def handler(event, context):
    if "replenish_one" in event:
        target = event["replenish_one"]
        return replenish_one(target["arn"], target["endpoint"])

    owner = uuid.uuid4().hex
    if not acquire(owner):
        result = {"skipped": "another run is in progress; it will run one more pass"}
        print(json.dumps(result))
        return result
    passes = []
    try:
        for _ in range(MAX_PASSES):
            passes.append(run_once())
            if release_or_continue(owner):
                break
            if context and context.get_remaining_time_in_millis() < 120_000:
                break   # leave the rest to the next trigger; the lease expires on its own
        else:
            release_or_continue(owner)
    except Exception:
        try:   # free the lock on failure so the next trigger isn't blocked for the whole lease
            table.delete_item(Key=LOCK_KEY, ConditionExpression="#o = :o",
                              ExpressionAttributeNames={"#o": "owner"}, ExpressionAttributeValues={":o": owner})
        except Exception as unlock_exc:  # noqa: BLE001 - lock still expires on its own via the lease TTL
            print(json.dumps({"lock_release_error": str(unlock_exc)}))
        raise
    return passes[-1] if len(passes) == 1 else {"passes": passes}
