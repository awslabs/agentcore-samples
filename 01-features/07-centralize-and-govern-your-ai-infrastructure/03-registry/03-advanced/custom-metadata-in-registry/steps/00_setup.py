"""Step 0 (base credentials): create what the other steps need.

- Three IAM roles, one per persona (admin, publisher, consumer). Each step assumes one of them,
  so you can see which persona is allowed to do what.
- The approval function: an AWS Lambda function (lambda/approval_check.py) and its IAM role. It
  approves or rejects each submitted record from its custom metadata, so nobody approves by hand.
  Its role can read records and set their status, but can't edit their metadata.
- An Amazon EventBridge rule that matches AWS Agent Registry's "Registry Record State changed to
  Pending Approval" event and invokes the approval function.
- A model check: one 1-token Amazon Bedrock call to MODEL_ID, which the consumer's agent in step 5
  uses. If it fails, the script prints how to fix it and carries on, because steps 1-4 don't need
  the model.

Rerunning updates the roles, the function code and the rule in place.
"""

import io
import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from botocore.exceptions import ClientError
from common import (
    BASE_PROFILE,
    FUNCTION_NAME,
    FUNCTION_ROLE,
    LAMBDA_DIR,
    LOG_GROUP,
    MODEL_ID,
    PERSONAS,
    REGION,
    REGISTRY_NAME,
    RULE_NAME,
    account_id,
    base_session,
    role_name,
    save_state,
    session_for,
)

ACTIONS = {
    "admin": ["CreateRegistry", "GetRegistry", "UpdateRegistry", "DeleteRegistry", "ListRegistries"],
    "publisher": [
        "CreateRegistryRecord",
        "UpdateRegistryRecord",
        "GetRegistryRecord",
        "ListRegistryRecords",
        "SubmitRegistryRecordForApproval",
        "GetRegistry",
        "DeleteRegistryRecord",
    ],
    "consumer": [
        "SearchDiscoverableRegistryRecords",
        "ListDiscoverableRegistryRecords",
        "GetDiscoverableRegistryRecord",
        "InvokeRegistryMcp",
    ],
}
BEDROCK_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
FUNCTION_ACTIONS = ["GetRegistry", "GetRegistryRecord", "UpdateRegistryRecordStatus"]
PATTERN = {"source": ["aws.agent-registry"], "detail-type": ["Registry Record State changed to Pending Approval"]}


def policy_for(persona, acct):
    reg = f"arn:aws:agent-registry:{REGION}:{acct}:registry/*"
    unscoped = {"CreateRegistry", "ListRegistries"}
    scoped = [f"agent-registry:{a}" for a in ACTIONS[persona] if a not in unscoped]
    statements = [{"Effect": "Allow", "Action": scoped, "Resource": [reg, reg + "/record/*"]}]
    if persona == "consumer":
        # A us. cross-Region inference profile invokes the foundation model in several US Regions,
        # so the foundation-model ARN can't be scoped to one Region.
        statements.append(
            {
                "Effect": "Allow",
                "Action": BEDROCK_ACTIONS,
                "Resource": [
                    f"arn:aws:bedrock:{REGION}:{acct}:inference-profile/*",
                    "arn:aws:bedrock:*::foundation-model/*",
                ],
            }
        )
    if persona == "admin":
        # CreateRegistry/DeleteRegistry make the service create and delete a workload identity and
        # (first time only) a service-linked role as the caller. Without these the registry ends in
        # CREATE_FAILED "Unable to create workload identity because access was denied".
        wid = f"arn:aws:bedrock-agentcore:{REGION}:{acct}:workload-identity-directory/default"
        statements += [
            {"Effect": "Allow", "Action": [f"agent-registry:{a}" for a in sorted(unscoped)], "Resource": "*"},
            {
                "Effect": "Allow",
                "Resource": [wid, wid + "/*"],
                "Action": [
                    "bedrock-agentcore:CreateWorkloadIdentity",
                    "bedrock-agentcore:GetWorkloadIdentity",
                    "bedrock-agentcore:DeleteWorkloadIdentity",
                ],
            },
            {
                "Effect": "Allow",
                "Action": "iam:CreateServiceLinkedRole",
                "Resource": f"arn:aws:iam::{acct}:role/aws-service-role/agent-registry.amazonaws.com/*",
                "Condition": {"StringEquals": {"iam:AWSServiceName": "agent-registry.amazonaws.com"}},
            },
        ]
    return {"Version": "2012-10-17", "Statement": statements}


def function_policy(acct):
    reg = f"arn:aws:agent-registry:{REGION}:{acct}:registry/*"
    log_group = f"arn:aws:logs:{REGION}:{acct}:log-group:{LOG_GROUP}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [f"agent-registry:{a}" for a in FUNCTION_ACTIONS],
                "Resource": [reg, reg + "/record/*"],
            },
            {"Effect": "Allow", "Action": "logs:CreateLogGroup", "Resource": log_group},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": log_group + ":*"},
        ],
    }


def put_role(role, principal, policy):
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": principal, "Action": "sts:AssumeRole"}],
        }
    )
    try:
        arn = iam.create_role(
            RoleName=role, AssumeRolePolicyDocument=trust, Tags=[{"Key": "project", "Value": "cm-demo"}]
        )["Role"]["Arn"]
        print(f"created role {role}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=role, PolicyDocument=trust)
        arn = iam.get_role(RoleName=role)["Role"]["Arn"]
        print(f"updated role {role}")
    iam.put_role_policy(RoleName=role, PolicyName=f"{role}-policy", PolicyDocument=json.dumps(policy))
    return arn


def build_zip():
    with tempfile.TemporaryDirectory() as tmp:
        # The Lambda Python runtime's built-in boto3 has no agent-registry-control client yet, so
        # the function ships its own.
        pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile"]
        subprocess.run([*pip, "--target", tmp, "boto3>=1.43.113"], check=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(LAMBDA_DIR / "approval_check.py", "approval_check.py")
            for path in Path(tmp).rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(tmp))
        return buf.getvalue()


def deploy_function(role_arn, code):
    deadline = time.time() + 60
    while True:
        try:
            arn = lam.create_function(
                FunctionName=FUNCTION_NAME,
                Runtime="python3.14",
                Role=role_arn,
                Handler="approval_check.handler",
                Code={"ZipFile": code},
                Timeout=30,
                MemorySize=128,
                Environment={"Variables": {"REGISTRY_NAME": REGISTRY_NAME}},
            )["FunctionArn"]
            lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION_NAME)
            print(f"created function {FUNCTION_NAME}")
            return arn
        except lam.exceptions.ResourceConflictException:
            arn = lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=code)["FunctionArn"]
            lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION_NAME)
            print(f"updated function {FUNCTION_NAME}")
            return arn
        except ClientError as e:
            # A new role takes a few seconds before Lambda can assume it.
            if e.response["Error"]["Code"] != "InvalidParameterValueException" or time.time() > deadline:
                raise
            time.sleep(5)


print(f">> acting as base credentials (profile {BASE_PROFILE})")
session, acct = base_session(), account_id()
iam, lam, events = session.client("iam"), session.client("lambda"), session.client("events")

for persona in PERSONAS:
    put_role(role_name(persona), {"AWS": f"arn:aws:iam::{acct}:root"}, policy_for(persona, acct))
function_role_arn = put_role(FUNCTION_ROLE, {"Service": "lambda.amazonaws.com"}, function_policy(acct))

print("waiting until every role can be assumed")
for persona in PERSONAS:
    session_for(persona)

print(f"packaging lambda/approval_check.py with boto3 for {FUNCTION_NAME}")
function_arn = deploy_function(function_role_arn, build_zip())

rule_arn = events.put_rule(Name=RULE_NAME, EventPattern=json.dumps(PATTERN), State="ENABLED")["RuleArn"]
events.put_targets(Rule=RULE_NAME, Targets=[{"Id": "approval-function", "Arn": function_arn}])
try:
    lam.add_permission(
        FunctionName=FUNCTION_NAME,
        StatementId="eventbridge-invoke",
        Action="lambda:InvokeFunction",
        Principal="events.amazonaws.com",
        SourceArn=rule_arn,
    )
except lam.exceptions.ResourceConflictException:
    pass  # already granted by an earlier run
print(f"EventBridge rule {RULE_NAME} -> AWS Lambda {FUNCTION_NAME}")

save_state(function_arn=function_arn, rule_arn=rule_arn)

# Base credentials, so a missing model access grant isn't confused with a new role's IAM delay.
try:
    session.client("bedrock-runtime").converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": "ping"}]}],
        inferenceConfig={"maxTokens": 1},
    )
    print(f"Amazon Bedrock model {MODEL_ID}: invocable")
except ClientError as e:
    code = e.response["Error"]["Code"]
    print(
        f"Amazon Bedrock can't invoke {MODEL_ID} ({code}). "
        "Enable access to it in the Amazon Bedrock console before step 05, or set MODEL_ID."
    )

print("\npersonas")
for persona in PERSONAS:
    extra = BEDROCK_ACTIONS if persona == "consumer" else []
    print(f"  {role_name(persona):28} {' '.join(ACTIONS[persona] + extra)}")
print(f"  {FUNCTION_ROLE:28} {' '.join(FUNCTION_ACTIONS)}  (approval function)")
