#!/usr/bin/env python3
"""Create, update or delete the chat online evaluation config.

The chat Runtime is scored live by two managed third-party evaluators:

  ThirdParty.DeepEval.ConversationCompleteness   self-service resolution
  ThirdParty.DeepEval.KnowledgeRetention         diagnostic: remembering earlier turns

The AgentCore API accepts these evaluator ids, but the CloudFormation resource schema for
online evaluation configs does not yet, so this one config is managed here rather than in
the stack. The stack creates its execution role (output ChatOnlineEvalRoleArn). deploy.sh
runs `apply` after the stack deploys; destroy.sh runs `delete` before the stack is removed.

Usage:
    python3 scripts/chat_online_eval.py apply  [--region us-west-2]
    python3 scripts/chat_online_eval.py delete [--region us-west-2]
"""

import argparse
import time

import boto3

CONFIG_NAME = "ReceiptsAgent_ChatLive"
EVALUATORS = ["ThirdParty.DeepEval.ConversationCompleteness", "ThirdParty.DeepEval.KnowledgeRetention"]
SERVICE_NAME = "ReceiptsAgent_receiptschat.DEFAULT"


def outputs(region: str, stack: str) -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=region)
    outs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outs}


def existing(control) -> dict | None:
    token = None
    while True:
        page = control.list_online_evaluation_configs(**({"nextToken": token} if token else {}))
        for config in page.get("onlineEvaluationConfigs", []):
            if config["onlineEvaluationConfigName"] == CONFIG_NAME:
                return config
        token = page.get("nextToken")
        if not token:
            return None


def apply(region: str, stack: str) -> None:
    outs = outputs(region, stack)
    chat_runtime_id = outs["ChatRuntimeArn"].rsplit("/", 1)[-1]
    settings = {
        "description": "Chat, live: self-service resolution and knowledge retention per conversation",
        "rule": {"samplingConfig": {"samplingPercentage": 100.0}, "sessionConfig": {"sessionTimeoutMinutes": 5}},
        "dataSourceConfig": {
            "cloudWatchLogs": {
                "logGroupNames": [f"/aws/bedrock-agentcore/runtimes/{chat_runtime_id}-DEFAULT"],
                "serviceNames": [SERVICE_NAME],
            }
        },
        "evaluators": [{"evaluatorId": e} for e in EVALUATORS],
        "evaluationExecutionRoleArn": outs["ChatOnlineEvalRoleArn"],
    }
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    current = existing(control)
    if current:
        control.update_online_evaluation_config(
            onlineEvaluationConfigId=current["onlineEvaluationConfigId"], executionStatus="ENABLED", **settings
        )
        print(f"Updated {CONFIG_NAME} ({current['onlineEvaluationConfigId']})")
    else:
        created = control.create_online_evaluation_config(
            onlineEvaluationConfigName=CONFIG_NAME, enableOnCreate=True, **settings
        )
        print(f"Created {CONFIG_NAME} ({created['onlineEvaluationConfigId']})")


def delete(region: str) -> None:
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    current = existing(control)
    if not current:
        print(f"{CONFIG_NAME} not present")
        return
    control.delete_online_evaluation_config(onlineEvaluationConfigId=current["onlineEvaluationConfigId"])
    # Wait for it to go, so the stack can then delete the execution role it uses.
    for _ in range(30):
        if not existing(control):
            break
        time.sleep(5)
    print(f"Deleted {CONFIG_NAME}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["apply", "delete"])
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--stack", default="AgentCore-ReceiptsAgent-dev")
    args = parser.parse_args()
    if args.action == "apply":
        apply(args.region, args.stack)
    else:
        delete(args.region)


if __name__ == "__main__":
    main()
