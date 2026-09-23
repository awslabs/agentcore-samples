"""
Deploy — Public AgentCore Browser + Private VPC AgentCore Runtime.

Deploys a CloudFormation stack that demonstrates the hybrid architecture:
  - AgentCore Browser (PUBLIC mode)  → internet access for web browsing
  - AgentCore Runtime (VPC mode)     → isolated private subnet for agent logic

The Runtime agent uses the Browser tool to fetch live web data while running
inside a private subnet with no direct internet egress.

Architecture:
  Internet ←→ AgentCore Browser (public subnet)
                      ↑
  Private subnet: AgentCore Runtime → invokes Browser via internal channels

Usage:
    python deploy.py [--region REGION] [--stack-name NAME] [--cleanup]

Prerequisites:
    pip install boto3
    AWS credentials configured (aws sts get-caller-identity)

IAM permissions required:
    cloudformation:CreateStack / DescribeStacks / DeleteStack
    iam:CreateRole / PassRole / AttachRolePolicy / ...
    ec2:* (VPC, subnet, security group creation)
    bedrock-agentcore:CreateBrowser / CreateAgentRuntime / ...
    ssm:StartSession (to connect to the development EC2 instance)
"""

import argparse

import boto3

TEMPLATE_FILE = "cfn-browser.yaml"
DEFAULT_STACK = "agentcore-public-browser-private-vpc"


def deploy_stack(region: str, stack_name: str) -> dict:
    """Create the CloudFormation stack and wait for completion."""
    cf = boto3.client("cloudformation", region_name=region)

    with open(TEMPLATE_FILE, "r") as f:
        template_body = f.read()

    print(f"Deploying stack '{stack_name}' in {region} ...")
    print("This takes approximately 10 minutes.")

    try:
        response = cf.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
        print(f"Stack ID: {response['StackId']}")
    except cf.exceptions.AlreadyExistsException:
        print(f"Stack '{stack_name}' already exists — describing outputs.")

    waiter = cf.get_waiter("stack_create_complete")
    waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
    print(f"Stack '{stack_name}' CREATE_COMPLETE")

    stacks = cf.describe_stacks(StackName=stack_name)["Stacks"]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    return outputs


def print_testing_instructions(outputs: dict, region: str) -> None:
    """Print step-by-step instructions for testing the deployed stack."""
    agent_runtime_arn = outputs.get("AgentRuntimeArn", "<AgentRuntimeArn>")
    agent_runtime_id = outputs.get("AgentRuntimeId", "<AgentRuntimeId>")
    dev_instance = outputs.get("DevelopmentInstanceId", "<InstanceId>")

    print("\n" + "=" * 70)
    print("TESTING INSTRUCTIONS")
    print("=" * 70)
    print(f"""
Step 1 — Connect to the EC2 development instance (inside the private VPC):
  AWS Console → Systems Manager → Session Manager → {dev_instance}
  or:
  aws ssm start-session --target {dev_instance} --region {region}

Step 2 — Set up the environment on the EC2 instance:

  sudo dnf install git -y
  curl -LsSf https://astral.sh/uv/install.sh | sh
  source ~/.bashrc
  uv init vpc-browser --python 3.13 && cd vpc-browser
  uv venv --python 3.13 && source .venv/bin/activate
  uv pip install boto3

Step 3 — Create and run the test script on the EC2 instance:

  cat > call-agent.py << 'SCRIPT'
  import boto3, json
  client = boto3.client('bedrock-agentcore', region_name='{region}')
  payload = json.dumps({{"prompt": "What is the weather in Richmond VA today?"}})
  response = client.invoke_agent_runtime(
      agentRuntimeArn="{agent_runtime_arn}",
      runtimeSessionId='dfmeoagmreaklgmrkleafremoigrmtesogmtrskhmtkrl',
      payload=payload,
      qualifier="DEFAULT"
  )
  data = json.loads(response['response'].read())
  print("Agent Response:", data)
  SCRIPT

  python call-agent.py

Step 4 — Monitor execution in CloudWatch:
  Log group: /aws/bedrock-agentcore/runtimes/{agent_runtime_id}

Step 5 — View the live browser session:
  AWS Console → Amazon Bedrock AgentCore → Built-in Tools
  → Browser Tools → (your browser) → View live session
""")


def delete_stack(region: str, stack_name: str) -> None:
    cf = boto3.client("cloudformation", region_name=region)
    print(f"Deleting stack '{stack_name}'...")
    cf.delete_stack(StackName=stack_name)
    waiter = cf.get_waiter("stack_delete_complete")
    waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40})
    print(f"Stack '{stack_name}' deleted.")


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy public AgentCore Browser + private VPC Runtime stack")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack-name", default=DEFAULT_STACK)
    parser.add_argument("--cleanup", action="store_true", help="Delete the stack instead of deploying")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.cleanup:
        delete_stack(args.region, args.stack_name)
        return

    outputs = deploy_stack(args.region, args.stack_name)
    print_testing_instructions(outputs, args.region)

    print("\nTo clean up:")
    print(f"  python deploy.py --cleanup --stack-name {args.stack_name} --region {args.region}")


if __name__ == "__main__":
    main()
