"""
Deploy ANS sync poller as a Lambda function with EventBridge 5-min schedule.

Creates:
  1. IAM role for the Lambda (bedrock-agentcore + CloudWatch Logs)
  2. Lambda function with the sync logic inline
  3. EventBridge rule triggering every 5 minutes
  4. Permission for EventBridge to invoke the Lambda

Usage:
    python deploy_poller.py --registry-id <your-registry-id> --region us-east-1
"""

import argparse
import json
import time
import zipfile
import io
import boto3

FUNCTION_NAME = "ans-registry-sync-poller"
ROLE_NAME = "ans-registry-sync-poller-role"
RULE_NAME = "ans-registry-sync-every-5min"
REGION = "us-east-1"


def get_account_id(region):
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def create_lambda_role(iam, account_id):
    """Create IAM role for the Lambda function."""
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    perms = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock-agentcore-control:*",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Lambda role for ANS registry sync poller",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  Created role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{account_id}:role/{ROLE_NAME}"
        print(f"  Role exists: {role_arn}")
        iam.update_assume_role_policy(
            RoleName=ROLE_NAME,
            PolicyDocument=json.dumps(trust),
        )

    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="ans-sync-permissions",
        PolicyDocument=json.dumps(perms),
    )
    print("  Attached permissions policy")

    # Wait for role propagation
    print("  Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return role_arn


def build_lambda_zip(registry_id, region):
    """Build a zip with the Lambda handler code inline."""
    handler_code = f'''
import json
import hashlib
import socket
import ssl
import re
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request

import boto3

REGISTRY_ID = "{registry_id}"
REGION = "{region}"
ANS_EXTENSION_URI = "https://ans-protocol.org/ext/ans-identity/v1"


def parse_ans_name(ans_name):
    stripped = re.sub(r"^ans://", "", ans_name)
    m = re.match(r"v(\\d+\\.\\d+\\.\\d+)\\.(.*)", stripped)
    if m:
        return m.group(1), m.group(2)
    return "0.0.0", stripped


def fetch_badge(badge_url):
    req = Request(badge_url, headers={{"Accept": "application/json"}})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def dns_txt_lookup(qname):
    """Minimal DNS TXT lookup using socket (no dnspython in Lambda)."""
    import subprocess
    try:
        result = subprocess.run(
            ["dig", "+short", "TXT", qname],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().strip('"')
    except Exception:
        pass
    return ""


def fetch_ans_metadata(ans_name):
    version, host = parse_ans_name(ans_name)
    result = {{
        "ansName": ans_name, "host": host, "version": version,
        "status": "UNKNOWN", "domainValidation": "", "registeredAt": "",
        "badgeUrl": "", "identityCert": {{"type":"","fingerprint":""}},
        "serverCert": {{"type":"","fingerprint":""}},
        "trustVector": {{"integrity":0,"identity":0,"solvency":0,"behavior":0,"safety":0}},
        "trustComposite": 0.0, "trustProfile": "UNTRUSTED",
        "syncedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }}

    # Get badge URL from DNS
    badge_txt = dns_txt_lookup(f"_ans-badge.{{host}}")
    badge_url = ""
    if badge_txt:
        for part in badge_txt.split(";"):
            p = part.strip()
            if p.startswith("url="):
                badge_url = p[4:]
                break
    result["badgeUrl"] = badge_url

    # Fetch TL badge
    if badge_url:
        try:
            tl = fetch_badge(badge_url)
            event = tl.get("payload", {{}}).get("producer", {{}}).get("event", {{}})
            att = event.get("attestations", {{}})
            result["status"] = tl.get("status", "UNKNOWN")
            result["domainValidation"] = att.get("domainValidation", "")
            result["registeredAt"] = event.get("issuedAt", "")
            id_cert = att.get("identityCert", {{}})
            result["identityCert"] = {{"type": id_cert.get("type",""), "fingerprint": id_cert.get("fingerprint","")}}
            srv_cert = att.get("serverCert", {{}})
            result["serverCert"] = {{"type": srv_cert.get("type",""), "fingerprint": srv_cert.get("fingerprint","")}}
        except Exception as e:
            print(f"TL badge fetch error: {{e}}")

    # Compute trust (simplified)
    has_badge = result["status"] not in ("UNKNOWN", "")
    integrity = 45 + (15 if has_badge else 0) + 10  # assume agent card exists + PKI valid
    identity = 30
    id_type = result["identityCert"]["type"]
    if "EV" in id_type: identity += 35
    elif "OV" in id_type: identity += 20
    vector = {{"integrity": min(integrity,100), "identity": min(identity,100), "solvency":0, "behavior":50, "safety":40}}
    avg = sum(vector.values()) / 5
    profile = "FIDUCIARY" if avg>=90 else "TRANSACTIONAL" if avg>=70 else "READ_ONLY" if avg>=50 else "UNTRUSTED"
    result["trustVector"] = vector
    result["trustComposite"] = round(avg, 1)
    result["trustProfile"] = profile
    return result


def check_changed(stored, fresh):
    if stored.get("status") != fresh.get("status"): return True
    if stored.get("serverCert",{{}}).get("fingerprint") != fresh.get("serverCert",{{}}).get("fingerprint"): return True
    if stored.get("identityCert",{{}}).get("fingerprint") != fresh.get("identityCert",{{}}).get("fingerprint"): return True
    stv = stored.get("trustVector", {{}})
    ftv = fresh.get("trustVector", {{}})
    for d in ("integrity","identity","solvency","behavior","safety"):
        if stv.get(d) != ftv.get(d): return True
    return False


def handler(event, context):
    print(f"ANS sync poller triggered at {{datetime.now(timezone.utc).isoformat()}}")
    cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
    records = cp.list_registry_records(registryId=REGISTRY_ID).get("registryRecords", [])
    print(f"Found {{len(records)}} records")

    updated_count = 0
    for rec_summary in records:
        record_id = rec_summary.get("recordId", "")
        name = rec_summary.get("name", "")

        try:
            full = cp.get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
        except Exception as e:
            print(f"Error getting {{name}}: {{e}}")
            continue

        # Extract ANS extension
        try:
            raw = full.get("descriptors",{{}}).get("a2a",{{}}).get("agentCard",{{}}).get("inlineContent","")
            if not raw:
                raw = full.get("descriptors",{{}}).get("custom",{{}}).get("inlineContent","")
            if not raw: continue
            card = json.loads(raw)
            stored_ans = None
            for ext in card.get("capabilities",{{}}).get("extensions",[]):
                if ext.get("uri") == ANS_EXTENSION_URI:
                    stored_ans = ext.get("params", {{}})
                    break
            if not stored_ans: continue
        except Exception:
            continue

        ans_name = stored_ans.get("ansName", "")
        if not ans_name: continue

        print(f"Checking {{name}} ({{ans_name}})...")
        fresh = fetch_ans_metadata(ans_name)
        if not check_changed(stored_ans, fresh):
            print(f"  No changes for {{name}}")
            continue

        # Update the extension
        import copy
        updated_card = copy.deepcopy(card)
        fresh["syncedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for ext in updated_card.get("capabilities",{{}}).get("extensions",[]):
            if ext.get("uri") == ANS_EXTENSION_URI:
                ext["params"] = fresh
                break

        desc_key = "a2a" if "a2a" in full.get("descriptors",{{}}) else "custom"
        if desc_key == "a2a":
            descriptors = {{"a2a": {{"agentCard": {{"schemaVersion": "0.3", "inlineContent": json.dumps(updated_card)}}}}}}
        else:
            descriptors = {{"custom": {{"inlineContent": json.dumps(updated_card)}}}}

        cp.update_registry_record(registryId=REGISTRY_ID, recordId=record_id, descriptors=descriptors)
        updated_count += 1
        print(f"  Updated {{name}}")

    print(f"Sync complete: {{updated_count}} records updated out of {{len(records)}}")
    return {{"updated": updated_count, "total": len(records)}}
'''

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", handler_code)
    return buf.getvalue()


def deploy(registry_id, region):
    account_id = get_account_id(region)
    iam = boto3.client("iam", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    events = boto3.client("events", region_name=region)

    print("\n1. Creating IAM role...")
    role_arn = create_lambda_role(iam, account_id)

    print("\n2. Building Lambda zip...")
    zip_bytes = build_lambda_zip(registry_id, region)
    print(f"  Zip size: {len(zip_bytes)} bytes")

    print("\n3. Creating/updating Lambda function...")
    try:
        lam.create_function(
            FunctionName=FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.handler",
            Code={"ZipFile": zip_bytes},
            Timeout=120,
            MemorySize=256,
            Environment={"Variables": {"REGISTRY_ID": registry_id, "REGION": region}},
        )
        print(f"  Created Lambda: {FUNCTION_NAME}")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        print(f"  Updated Lambda code: {FUNCTION_NAME}")
        time.sleep(3)
        lam.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Timeout=120,
            MemorySize=256,
            Environment={"Variables": {"REGISTRY_ID": registry_id, "REGION": region}},
        )
        print("  Updated Lambda config")

    lambda_arn = f"arn:aws:lambda:{region}:{account_id}:function:{FUNCTION_NAME}"

    print("\n4. Creating EventBridge rule (every 5 minutes)...")
    events.put_rule(
        Name=RULE_NAME,
        ScheduleExpression="rate(5 minutes)",
        State="ENABLED",
        Description="Trigger ANS sync poller every 5 minutes",
    )
    print(f"  Rule: {RULE_NAME}")

    print("\n5. Adding Lambda as target...")
    events.put_targets(
        Rule=RULE_NAME,
        Targets=[{"Id": "ans-sync-lambda", "Arn": lambda_arn}],
    )

    print("\n6. Adding invoke permission...")
    try:
        lam.add_permission(
            FunctionName=FUNCTION_NAME,
            StatementId="eventbridge-invoke",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=f"arn:aws:events:{region}:{account_id}:rule/{RULE_NAME}",
        )
        print("  Permission added")
    except lam.exceptions.ResourceConflictException:
        print("  Permission already exists")

    print("\n✅ Deployed!")
    print(f"  Lambda: {lambda_arn}")
    print(f"  Rule: {RULE_NAME} (every 5 minutes)")
    print(f"  Registry: {registry_id}")
    print(f"  Logs: CloudWatch /aws/lambda/{FUNCTION_NAME}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ANS sync poller Lambda")
    parser.add_argument("--registry-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    deploy(args.registry_id, args.region)
