"""Session broker: claim and release pooled sessions, one pool per (arn, endpoint).

Invoke it from your backend, for example:
  {"action": "claim",   "userId": "u-123", "arn": "arn:aws:bedrock-agentcore:...", "endpoint": "DEFAULT"}
  {"action": "release", "userId": "u-123", "sessionId": "pool-...", "arn": "arn:...", "endpoint": "DEFAULT"}

Invoke AgentCore Runtime with the returned sessionId AND qualifier=endpoint:
a session belongs to the endpoint (and runtime version) it was started on.

The broker trusts the userId it receives. Put it behind an authenticated
caller (IAM-invoked from your backend, or API Gateway with an authorizer).
"""
import json
import os
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from common import DEFAULT_ENDPOINT, get_pool, pool_key, stop_session, table

POOL_MANAGER_FUNCTION = os.environ["POOL_MANAGER_FUNCTION"]
lam = boto3.client("lambda")


def _kick(payload):
    """Fire-and-forget: wake the pool manager without making the caller wait for it."""
    try:
        lam.invoke(FunctionName=POOL_MANAGER_FUNCTION, InvocationType="Event",
                  Payload=json.dumps(payload).encode())
    except Exception:  # noqa: BLE001, S110 - the schedule and the stream are still there as backstops
        pass


def claim(user_id, arn, endpoint, claim_candidates):
    # claim() only looks at the claim_candidates oldest AVAILABLE items, then gives up and
    # reports a miss if every one of them loses its conditional update to a concurrent caller -
    # even if the pool has far more inventory sitting just past that window. Set this pool's
    # claimCandidates above its expected peak number of claims arriving within the same ~100ms
    # (./deploy.sh register --claim-candidates N), or concurrent callers will collide on the same
    # handful of candidates and get spurious cold-start misses despite a healthy pool depth.
    resp = table.query(
        IndexName="available",
        KeyConditionExpression=Key("pool").eq(pool_key(arn, endpoint)),
        ScanIndexForward=True,          # oldest first
        Limit=claim_candidates)
    for item in resp["Items"]:
        try:
            table.update_item(
                Key={"sessionId": item["sessionId"]},
                UpdateExpression="SET #s = :in_use, userId = :u REMOVE #p",
                ConditionExpression="attribute_exists(#p)",   # still available?
                ExpressionAttributeNames={"#s": "status", "#p": "pool"},
                ExpressionAttributeValues={":in_use": "IN_USE", ":u": user_id})
            # claimed exactly one: exactly one replacement is owed, right now, not on the next
            # stream batch or schedule run.
            _kick({"replenish_one": {"arn": arn, "endpoint": endpoint}})
            return {"sessionId": item["sessionId"], "endpoint": endpoint, "pooled": True}
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            continue  # another caller claimed it first
    # empty pool: hand out a fresh ID, the user gets a normal ~2 s cold start, and nudge a full
    # sweep now instead of waiting for the schedule - this pool is below target right now.
    _kick({"source": "claim-miss", "arn": arn, "endpoint": endpoint})
    return {"sessionId": f"cold-{uuid.uuid4().hex}", "endpoint": endpoint, "pooled": False}


def release(user_id, session_id, endpoint, arn):
    item = table.get_item(Key={"sessionId": session_id}).get("Item")
    if item:
        # check ownership BEFORE stopping, so a caller can't stop someone else's session
        if item.get("userId") != user_id:
            return {"released": False, "reason": "session not owned by caller"}
        endpoint = item.get("endpoint", endpoint)
        arn = item.get("arn", arn)
    # stop first so the memory and files of this user never reach anyone else
    stop_session(session_id, endpoint, arn)
    if item:
        try:
            table.delete_item(Key={"sessionId": session_id},
                              ConditionExpression="userId = :u",
                              ExpressionAttributeValues={":u": user_id})
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # already gone
    return {"released": True}


def handler(event, _context):
    action = event.get("action")
    user_id = event.get("userId")
    arn = event.get("arn")
    endpoint = event.get("endpoint", DEFAULT_ENDPOINT)
    if not user_id:
        raise ValueError("userId is required")
    if not arn:
        raise ValueError("arn is required")
    pool = get_pool(arn, endpoint)
    if not pool:
        raise ValueError(f"(arn={arn!r}, endpoint={endpoint!r}) is not registered; "
                         "run ./deploy.sh register first")
    if action == "claim":
        return claim(user_id, arn, endpoint, pool["claimCandidates"])
    if action == "release":
        return release(user_id, event["sessionId"], endpoint, arn)
    raise ValueError(f"unknown action: {action}")
