"""
Human-in-the-loop review infrastructure for Automated Policy Management.

Creates an SNS topic to notify human reviewers of generated Cedar policies,
and an SQS queue so that programmatic approval responses can be received.

In the demo notebook the human review is completed by the user running an
interactive approval cell. The SNS email notification is the "paper trail"
that a real governance team would receive.
"""

import json
import logging
import time
import boto3

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Infrastructure setup
# ---------------------------------------------------------------------------


def setup_review_infrastructure(region: str) -> dict:
    """
    Create the SNS topic and SQS queue for human review notifications.

    Returns a dict with:
        sns_topic_arn, sqs_queue_url, sqs_queue_arn
    """
    sns = boto3.client("sns", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    suffix = int(time.time())

    # SNS topic
    topic_name = f"AgentCorePolicy-HumanReview-{suffix}"
    topic_resp = sns.create_topic(
        Name=topic_name,
        Tags=[
            {"Key": "Purpose", "Value": "AgentCorePolicy-HumanReview"},
        ],
    )
    topic_arn = topic_resp["TopicArn"]
    print(f"   SNS topic created: {topic_arn}")

    # SQS queue for programmatic responses
    queue_name = f"AgentCorePolicy-ApprovalQueue-{suffix}"
    queue_resp = sqs.create_queue(
        QueueName=queue_name,
        Attributes={
            "MessageRetentionPeriod": "3600",  # 1 hour - demo only
            "VisibilityTimeout": "300",
        },
        tags={"Purpose": "AgentCorePolicy-HumanReview"},
    )
    queue_url = queue_resp["QueueUrl"]

    queue_attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )
    queue_arn = queue_attrs["Attributes"]["QueueArn"]
    print(f"   SQS queue created: {queue_url}")

    # Allow SNS to send messages to SQS
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowSNSPublish",
                "Effect": "Allow",
                "Principal": {"Service": "sns.amazonaws.com"},
                "Action": "sqs:SendMessage",
                "Resource": queue_arn,
                "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
            }
        ],
    }
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={"Policy": json.dumps(policy)},
    )

    # Subscribe SQS to SNS
    sns.subscribe(
        TopicArn=topic_arn,
        Protocol="sqs",
        Endpoint=queue_arn,
        Attributes={"RawMessageDelivery": "true"},
    )
    print("   SQS subscribed to SNS topic")

    return {
        "sns_topic_arn": topic_arn,
        "sqs_queue_url": queue_url,
        "sqs_queue_arn": queue_arn,
    }


def subscribe_email_to_topic(
    sns_client: boto3.client,
    topic_arn: str,
    email: str,
) -> None:
    """Subscribe a reviewer email address to the SNS topic."""
    sns_client.subscribe(
        TopicArn=topic_arn,
        Protocol="email",
        Endpoint=email,
    )
    print(
        f"   Email subscription created for {email}. "
        "The reviewer must confirm via the AWS SNS confirmation email."
    )


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


def build_review_notification(
    tool_action_key: str,
    nl_statement: str,
    cedar_statement: str,
    manifest_entry: dict,
) -> str:
    """
    Build a human-readable notification message for the reviewer.
    """
    rbac = manifest_entry.get("rbac", {})
    lines = [
        "=" * 70,
        "CEDAR POLICY REVIEW REQUEST",
        "AgentCore Automated Policy Management",
        "=" * 70,
        "",
        f"Tool Action : {tool_action_key}",
        f"Description : {manifest_entry.get('description', 'N/A')}",
        "",
        "RBAC Requirements from Tool Registration:",
        f"  Allowed Roles    : {', '.join(rbac.get('allowed_roles', ['any']))}",
        f"  Classification   : {rbac.get('data_classification', 'N/A')}",
        f"  Requires Approval: {rbac.get('requires_approval', False)}",
    ]

    constraints = rbac.get("constraints", {})
    if constraints:
        lines.append("  Parameter Constraints:")
        for param, c in constraints.items():
            if "enum" in c:
                lines.append(f"    {param}: must be one of {c['enum']}")
            elif "max" in c:
                lines.append(f"    {param}: maximum value {c['max']}")

    lines += [
        "",
        "Natural Language Policy Statement:",
        f"  {nl_statement}",
        "",
        "Generated Cedar Policy:",
        "-" * 70,
        cedar_statement,
        "-" * 70,
        "",
        "Please review and APPROVE or REJECT this policy.",
        "=" * 70,
    ]
    return "\n".join(lines)


def send_policy_for_review(
    region: str,
    topic_arn: str,
    tool_action_key: str,
    nl_statement: str,
    cedar_statement: str,
    manifest_entry: dict,
) -> None:
    """
    Publish the generated Cedar policy to the SNS topic for human review.
    """
    sns = boto3.client("sns", region_name=region)
    message = build_review_notification(
        tool_action_key, nl_statement, cedar_statement, manifest_entry
    )

    sns.publish(
        TopicArn=topic_arn,
        Subject=f"[Policy Review Required] {tool_action_key}",
        Message=message,
    )
    print(f"   Review notification sent for: {tool_action_key}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def cleanup_review_infrastructure(
    region: str,
    sns_topic_arn: str,
    sqs_queue_url: str,
) -> None:
    """Delete SNS topic and SQS queue created for the demo."""
    sns = boto3.client("sns", region_name=region)
    sqs = boto3.client("sqs", region_name=region)

    try:
        # Delete all subscriptions first
        subs = sns.list_subscriptions_by_topic(TopicArn=sns_topic_arn)
        for sub in subs.get("Subscriptions", []):
            if sub.get("SubscriptionArn", "").startswith("arn:"):
                sns.unsubscribe(SubscriptionArn=sub["SubscriptionArn"])
        sns.delete_topic(TopicArn=sns_topic_arn)
        print(f"   SNS topic deleted: {sns_topic_arn}")
    except Exception as e:
        print(f"   Warning: could not delete SNS topic: {e}")

    try:
        sqs.delete_queue(QueueUrl=sqs_queue_url)
        print(f"   SQS queue deleted: {sqs_queue_url}")
    except Exception as e:
        print(f"   Warning: could not delete SQS queue: {e}")
