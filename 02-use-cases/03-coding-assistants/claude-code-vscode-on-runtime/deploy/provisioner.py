"""The dev box provisioner: POST /api/box, behind API Gateway's JWT authorizer.

The page calls it after the Okta sign-in, with the access token in X-Devbox-Token. API Gateway has already checked
the token's signature, issuer, audience, expiry and the devbox scope; this checks the rest and makes the person's box
the first time they come, one step per call (advance_box in devbox.py, the same code deploy uses):

  200 {"ready": true, "box": {...}}               the box is there: the page opens it
  202 {"ready": false, "step": …, "message": …}   still being made (or another tab is on it): ask again in a few seconds
  403 {"message": …}                              not in DEVBOX_OKTA_GROUP, or not in exactly one tier group
  500 {"message": …}                              an AWS error; the log has the details

It acts only on the person in the verified token, and takes nothing else from the request.
"""

import json
import os
import re
import time

import boto3
import devbox as d
from botocore.config import Config
from botocore.exceptions import ClientError

SETTINGS = d.load_settings(json.loads(os.environ.get("DEVBOX_SETTINGS", "{}")))
PLAN = d.BoxPlan(**json.loads(os.environ.get("DEVBOX_PLAN", "{}"))) if os.environ.get("DEVBOX_PLAN") else None
LOCK_S = 30  # one call holds the person's record at most this long (the function times out at 28 s)
_clients: dict = {}


def clients() -> dict:
    if not _clients:
        cfg = Config(retries={"mode": "standard", "max_attempts": 4}, read_timeout=20, connect_timeout=5)
        for key, service in (("efs", "efs"), ("iam", "iam"), ("acc", "bedrock-agentcore-control"), ("ddb", "dynamodb")):
            _clients[key] = boto3.client(service, region_name=SETTINGS.region, config=cfg)
    return _clients


def groups_claim(value) -> list[str]:
    """API Gateway hands an array claim to Lambda as a string, "[a b c]"; a list, if it ever sends one."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("[") and v.endswith("]"):
            v = v[1:-1]
        return [g for g in re.split(r"[\s,]+", v) if g]
    return []


def answer(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }


def lock(ddb, key: str, now: int) -> bool:
    """Hold the person's record while this call works on it, so two tabs never make the same thing twice."""
    try:
        ddb.update_item(
            TableName=d.BOX_TABLE,
            Key={"key": {"S": key}},
            UpdateExpression="SET lockUntil = :until",
            ConditionExpression="attribute_exists(#k) AND (attribute_not_exists(lockUntil) OR lockUntil < :now)",
            ExpressionAttributeNames={"#k": "key"},
            ExpressionAttributeValues={":until": {"N": str(now + LOCK_S)}, ":now": {"N": str(now)}},
        )
        return True
    except ClientError as e:
        if d.err_code(e) == "ConditionalCheckFailedException":
            return False
        raise


def who(event: dict) -> tuple[dict | None, dict | None]:
    """(the person, or None with the answer to give)."""
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    if not claims:  # the route always has the JWT authorizer, so this is a misconfiguration
        return None, answer(401, {"message": "no verified token"})
    s = SETTINGS
    if (claims.get("cid") or claims.get("client_id")) != s.okta_client_id:
        return None, answer(403, {"message": "this token isn't for the Dev Box app"})
    groups = groups_claim(claims.get("groups"))
    if s.okta_group not in groups:
        return None, answer(403, {"message": f"you're not in the {s.okta_group} group: ask an admin to add you"})
    tier, why = d.tier_for(s, groups)
    if not tier:
        return None, answer(403, {"message": why})
    uid = claims.get("uid") or ""
    if not d.OKTA_UID.match(uid):
        return None, answer(403, {"message": "your token has no Okta uid"})
    return {"uid": uid, "login": claims.get("sub") or "", "tier": tier}, None


def handler(event, context):
    person, refused = who(event)
    if refused:
        return refused
    if PLAN is None or SETTINGS.errors:
        print(f"provisioner not configured: {SETTINGS.errors}")
        return answer(500, {"message": "the provisioner isn't set up yet: an admin must run deploy"})
    cl, key, now = clients(), d.uid_key(person["uid"]), int(time.time())
    try:
        if d.get_box(cl["ddb"], key) is None:
            try:
                d.put_box(cl["ddb"], d.new_box_record(person["uid"], person["login"], person["tier"], now), new=True)
            except ClientError as e:
                if d.err_code(e) != "ConditionalCheckFailedException":
                    raise
        if not lock(cl["ddb"], key, now):
            rec = d.get_box(cl["ddb"], key) or {}
            return answer(
                202,
                {
                    "ready": False,
                    "step": rec.get("step", "busy"),
                    "message": rec.get("message") or "setting up your box",
                },
            )
        rec = d.get_box(cl["ddb"], key)
        rec["tier"] = person["tier"]  # a tier change in Okta reaches the box on the next visit
        if person["login"] and not rec.get("login"):
            rec["login"] = person["login"]
        try:
            res = d.advance_box(cl, SETTINGS, PLAN, rec)
        finally:
            rec["updatedAt"] = int(time.time())
            d.put_box(cl["ddb"], rec)  # also drops the lock
    except ClientError as e:
        print(f"box {key[:12]}…: {d.err_code(e)}: {d.err_text(e)}")
        return answer(
            500,
            {
                "message": f"setting up your box failed ({d.err_code(e)}). An admin can see why in the "
                f"{d.PROVISIONER_LOG_GROUP} log."
            },
        )
    print(f"box {rec.get('name')} ({rec.get('tier')}): {res['state']} at {res['step']}: {res['message']}")
    if res["state"] == "ready":
        return answer(200, {"ready": True, "box": d.browser_box(rec)})
    if res["state"] == "failed":
        return answer(500, {"ready": False, "message": res["message"]})
    return answer(202, {"ready": False, "step": res["step"], "message": res["message"]})
