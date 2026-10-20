"""Drain pools.

Default: stop every AVAILABLE session (optionally filtered by --arn and/or --endpoint) and
delete its item. The pool manager refills on the next run. You rarely need
this: the pool manager already retires sessions from an older runtime version.

Each session is stopped against its own stored `arn` - the table can hold sessions for several
registered agents at once, so a single --runtime-arn flag would stop the wrong agent's sessions
for every item that isn't the one you meant.

--all: stop every session in the table, including IN_USE ones
(used by './deploy.sh destroy' after the pool manager is paused).
"""
import argparse

import boto3

ap = argparse.ArgumentParser()
ap.add_argument("--table", required=True)
ap.add_argument("--arn", help="only this agent's pool(s)")
ap.add_argument("--endpoint", help="only this endpoint's pool")
ap.add_argument("--all", action="store_true")
a = ap.parse_args()

table = boto3.resource("dynamodb").Table(a.table)
agentcore = boto3.client("bedrock-agentcore")


def items():
    names = {"#sid": "sessionId", "#e": "endpoint", "#a": "arn"}
    filters, values = [], {}
    if not a.all:
        names["#p"] = "pool"   # only declared when used - DynamoDB rejects unused expression names
        filters.append("attribute_exists(#p)")
    if a.arn:
        filters.append("#a = :a")
        values[":a"] = a.arn
    if a.endpoint:
        filters.append("#e = :e")
        values[":e"] = a.endpoint
    kwargs = {"ProjectionExpression": "#sid, #e, #a", "ExpressionAttributeNames": names}
    if filters:
        kwargs["FilterExpression"] = " AND ".join(filters)
    if values:
        kwargs["ExpressionAttributeValues"] = values
    while True:
        resp = table.scan(**kwargs)
        yield from resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


count = 0
for it in list(items()):
    sid, endpoint, arn = it["sessionId"], it.get("endpoint", "DEFAULT"), it.get("arn")
    if sid.startswith("#lock") or not arn:
        continue
    if not a.all:
        try:   # leave the pool first so nobody claims it while it stops
            table.update_item(Key={"sessionId": sid}, UpdateExpression="REMOVE #p",
                              ConditionExpression="attribute_exists(#p)",
                              ExpressionAttributeNames={"#p": "pool"})
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            continue
    try:
        agentcore.stop_runtime_session(agentRuntimeArn=arn, runtimeSessionId=sid, qualifier=endpoint)
    except agentcore.exceptions.ResourceNotFoundException:
        pass
    table.delete_item(Key={"sessionId": sid})
    count += 1
print(f"drained {count} session(s)")
