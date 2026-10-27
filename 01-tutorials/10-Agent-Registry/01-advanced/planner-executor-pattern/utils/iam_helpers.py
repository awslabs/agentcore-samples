import json
import time


def attach_dynamodb_policy_and_wait(agentcore_client, iam_client, agent_id, agent_name,
                                     aws_region, account_id, table_prefix):
    """Attach DynamoDB access policy to an agent's execution role and wait for READY status."""
    resp = agentcore_client.get_agent_runtime(agentRuntimeId=agent_id)
    role_arn = (resp.get("executionRoleArn") or resp.get("roleArn") or resp.get("agentRuntimeRoleArn"))
    role_name = role_arn.split("/")[-1]

    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="DynamoDBToolsPolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                    "dynamodb:Scan", "dynamodb:Query"
                ],
                "Resource": f"arn:aws:dynamodb:{aws_region}:{account_id}:table/{table_prefix}*"
            }]
        })
    )
    print(f"DynamoDB policy attached: {role_name}")

    while True:
        status = agentcore_client.get_agent_runtime(agentRuntimeId=agent_id).get("status")
        if status == "READY":
            print(f"{agent_name} READY")
            break
        print(f"  status: {status}...")
        time.sleep(15)
