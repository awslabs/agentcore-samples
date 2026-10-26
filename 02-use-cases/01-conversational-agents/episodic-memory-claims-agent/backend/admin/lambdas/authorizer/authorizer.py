import base64
import json


def handler(event, context):
    token = event.get("authorizationToken", "").replace("Bearer ", "")
    method_arn = event["methodArn"]

    try:
        payload = _decode_jwt_payload(token)
        groups = _parse_groups(payload.get("cognito:groups"))

        if "admin" in groups:
            return _build_policy(payload["sub"], "Allow", method_arn)

        return _build_policy(payload.get("sub", "unauthorized"), "Deny", method_arn)
    except Exception:  # noqa: BLE001 - fail closed: deny on any token error
        return _build_policy("unauthorized", "Deny", method_arn)


def _decode_jwt_payload(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT")
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _parse_groups(groups_claim):
    if isinstance(groups_claim, list):
        return groups_claim
    if isinstance(groups_claim, str):
        return [g.strip() for g in groups_claim.split(",") if g.strip()]
    return []


def _build_policy(principal_id, effect, method_arn):
    arn_parts = method_arn.split(":")
    region = arn_parts[3]
    account_id = arn_parts[4]
    api_gw_parts = arn_parts[5].split("/")
    api_id = api_gw_parts[0]
    stage = api_gw_parts[1]

    resource_arn = f"arn:aws:execute-api:{region}:{account_id}:{api_id}/{stage}/*"

    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource_arn,
                }
            ],
        },
    }
