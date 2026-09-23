"""
Deploy — VPC AgentCore Browser + VPC AgentCore Runtime (fully private).

Deploys a CloudFormation stack that keeps both the AgentCore Browser and
the AgentCore Runtime inside a private VPC. The browser is in VPC mode with
no public internet egress — it can only reach internal web servers inside the
same VPC. This pattern is suitable for:

  - Accessing internal corporate portals / intranet pages
  - Processing sensitive data that must never leave the VPC
  - Air-gapped or compliance-regulated environments

The stack includes a sample internal web server (EC2) running on port 8080
that serves a simple holiday calendar page. The Runtime agent uses the VPC
Browser to fetch data from this internal server.

Architecture:
  ┌──────────────────────────────────────────────────────────┐
  │  VPC                                                     │
  │                                                          │
  │  Private Subnet A          Private Subnet B              │
  │  ┌──────────────────┐      ┌──────────────────────────┐  │
  │  │ AgentCore Runtime│─────▶│ AgentCore Browser (VPC)  │  │
  │  │ (VPC mode)       │      │  ↕ (only VPC traffic)    │  │
  │  │                  │      └──────────────────────────┘  │
  │  │ Dev EC2 (client) │              │                      │
  │  └──────────────────┘              ▼                      │
  │                           Internal Web Server :8080       │
  └──────────────────────────────────────────────────────────┘

Usage:
    python deploy.py [--region REGION] [--stack-name NAME] [--cleanup]

Prerequisites:
    pip install boto3
    AWS credentials configured (aws sts get-caller-identity)
"""

import argparse

import boto3

TEMPLATE_FILE = "cfn-vpc-browser.yaml"
DEFAULT_STACK = "agentcore-vpc-browser-from-vpc"


def deploy_stack(region: str, stack_name: str) -> dict:
    """Create the CloudFormation stack and wait for completion (~13 minutes)."""
    cf = boto3.client("cloudformation", region_name=region)

    with open(TEMPLATE_FILE, "r") as f:
        template_body = f.read()

    print(f"Deploying stack '{stack_name}' in {region} ...")
    print("This takes approximately 13 minutes.")

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
    agent_runtime_arn = outputs.get("AgentRuntimeArn", "<AgentRuntimeArn>")
    agent_runtime_id = outputs.get("AgentRuntimeId", "<AgentRuntimeId>")
    dev_instance = outputs.get("DevelopmentInstanceId", "<InstanceId>")
    web_server_ip = outputs.get("WebServerPrivateIp", "<WebServerPrivateIp>")

    print("\n" + "=" * 70)
    print("TESTING INSTRUCTIONS")
    print("=" * 70)
    print(f"""
Step 1 — Connect to the EC2 development instance (inside the VPC):
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
  payload = json.dumps({{
      "prompt": "Access {web_server_ip} over http at port 8080 to check what are holidays in November"
  }})
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
    parser = argparse.ArgumentParser(description="Deploy fully-private VPC AgentCore Browser + Runtime stack")
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
