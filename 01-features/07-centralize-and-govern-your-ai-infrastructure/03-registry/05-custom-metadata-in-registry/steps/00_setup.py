"""Base credentials: the four persona roles, the approver queue, and the EventBridge rule."""

import json
import time

from common import (
    BASE_PROFILE,
    PERSONAS,
    QUEUE_NAME,
    REGION,
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
    "approver": ["GetRegistryRecord", "ListRegistryRecords", "UpdateRegistryRecordStatus", "GetRegistry"],
    "consumer": [
        "SearchDiscoverableRegistryRecords",
        "ListDiscoverableRegistryRecords",
        "GetDiscoverableRegistryRecord",
        "InvokeRegistryMcp",
    ],
}
PATTERN = {"source": ["aws.agent-registry"], "detail-type": ["Registry Record State changed to Pending Approval"]}


def policy_for(persona, acct, queue_arn):
    reg = f"arn:aws:agent-registry:{REGION}:{acct}:registry/*"
    unscoped = {"CreateRegistry", "ListRegistries"}
    scoped = [f"agent-registry:{a}" for a in ACTIONS[persona] if a not in unscoped]
    statements = [{"Effect": "Allow", "Action": scoped, "Resource": [reg, reg + "/record/*"]}]
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
    if persona == "approver":
        statements.append(
            {
                "Effect": "Allow",
                "Resource": queue_arn,
                "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


print(f">> acting as base credentials (profile {BASE_PROFILE})")
session, acct = base_session(), account_id()
events, sqs, iam = session.client("events"), session.client("sqs"), session.client("iam")

rule_arn = events.put_rule(Name=RULE_NAME, EventPattern=json.dumps(PATTERN), State="ENABLED")["RuleArn"]
try:
    queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
except sqs.exceptions.QueueDeletedRecently:
    print("SQS needs 60 s before a deleted queue name can be reused (you ran 99_cleanup); waiting")
    time.sleep(62)
    queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
sqs.set_queue_attributes(
    QueueUrl=queue_url,
    Attributes={
        "Policy": json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "events.amazonaws.com"},
                        "Action": "sqs:SendMessage",
                        "Resource": queue_arn,
                        "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
                    }
                ],
            }
        )
    },
)
events.put_targets(Rule=RULE_NAME, Targets=[{"Id": "approver-queue", "Arn": queue_arn}])
print(f"EventBridge rule {RULE_NAME} -> SQS queue {QUEUE_NAME}")

trust = json.dumps(
    {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{acct}:root"}, "Action": "sts:AssumeRole"}
        ],
    }
)
for persona in PERSONAS:
    role = role_name(persona)
    try:
        iam.create_role(RoleName=role, AssumeRolePolicyDocument=trust, Tags=[{"Key": "project", "Value": "cm-demo"}])
        print(f"created role {role}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=role, PolicyDocument=trust)
        print(f"updated role {role}")
    iam.put_role_policy(
        RoleName=role, PolicyName=f"{role}-policy", PolicyDocument=json.dumps(policy_for(persona, acct, queue_arn))
    )

print("waiting until every role can be assumed")
for persona in PERSONAS:
    session_for(persona)

save_state(queue_url=queue_url, queue_arn=queue_arn, rule_arn=rule_arn)
print("\npersonas")
for persona in PERSONAS:
    print(f"  {role_name(persona):20} {' '.join(ACTIONS[persona])}")
