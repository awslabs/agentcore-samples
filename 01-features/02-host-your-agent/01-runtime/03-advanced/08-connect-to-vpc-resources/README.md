# Connect to VPC Resources

## Overview

By default, AgentCore runtime deploys agents with `PUBLIC` network mode — the agent runs in AWS-managed infrastructure with internet access. **VPC mode** lets you deploy agents into your own VPC, giving them access to private resources like RDS databases, ElastiCache clusters, internal APIs, or Fargate services that aren't exposed to the internet.

This example uses AWS CDK (TypeScript) to deploy a complete VPC with a Fargate service, then connects an AgentCore runtime agent to it. The agent communicates with the Fargate container over the private network using Cloud Map service discovery.

## When to Use VPC Mode

Use VPC mode when your agent needs to:

- Query a private RDS or Aurora database
- Access an internal API running on ECS/Fargate or EC2
- Connect to ElastiCache (Redis/Memcached) for caching
- Reach services behind a VPN or AWS PrivateLink
- Comply with network isolation requirements

If your agent only needs internet access and AWS service APIs, `PUBLIC` mode is simpler and sufficient.

## How Network Configuration Works

The key difference is the `networkConfiguration` parameter when creating the runtime:

```python
# PUBLIC mode (default) — no VPC needed
control.create_agent_runtime(
    agentRuntimeName="my-agent",
    networkConfiguration={"networkMode": "PUBLIC"},
    # ...
)

# VPC mode — agent runs in your subnets
control.create_agent_runtime(
    agentRuntimeName="my-agent",
    networkConfiguration={
        "networkMode": "VPC",
        "vpcConfiguration": {
            "subnetIds": ["subnet-abc123", "subnet-def456"],
            "securityGroupIds": ["sg-xyz789"],
        }
    },
    # ...
)
```

In VPC mode, AgentCore places the agent's compute into your specified subnets with your security groups. The agent can then reach any resource accessible from those subnets.

### Network Requirements

| Requirement | Details |
|:------------|:--------|
| Subnets | Private subnets with NAT gateway (for pulling dependencies and calling AWS APIs) |
| Security groups | Must allow outbound traffic; inbound rules depend on your architecture |
| DNS | VPC must have DNS resolution enabled for service discovery |

## Architecture

This example creates:

1. **VPC** with public and private subnets across 2 AZs
2. **NAT gateway** for outbound internet access from private subnets
3. **Fargate service** running a simple HTTP echo container in private subnets
4. **Cloud Map** service discovery so the agent can find the Fargate service by DNS name
5. **AgentCore runtime** agent deployed in VPC mode, connected to the same private subnets

The agent calls the Fargate service at `http://echo.agentcore.local:8080/invocations` — a private DNS name resolved through Cloud Map.

## Project Structure

```
08-connect-to-vpc-resources/
├── bin/app.ts                    # CDK app entry point
├── lib/vpc-fargate-stack.ts      # CDK stack — VPC, Fargate, security groups
├── agent/
│   ├── main.py                   # Agent code — calls Fargate via httpx
│   └── requirements.txt          # Agent dependencies
├── resource-code/
│   ├── app.py                    # Fargate container — Flask echo server
│   ├── Dockerfile                # Container image definition
│   └── requirements.txt          # Container dependencies
├── deploy.py                     # Deploy CDK + AgentCore runtime
├── invoke.py                     # Invoke the deployed agent
├── cleanup.py                    # Tear down everything
├── package.json                  # CDK dependencies
└── cdk.json                      # CDK configuration
```

## Deployment

This example uses CDK for infrastructure — not the zip-to-S3 pattern used by other examples. The CDK stack handles VPC creation, Fargate deployment, and ECR image building. The deploy script orchestrates both CDK and AgentCore runtime setup.

### Prerequisites

- Node.js and npm installed
- Docker installed and running (CDK builds the container image)
- AWS CDK bootstrapped: `npx cdk bootstrap`
- AWS CLI configured with appropriate credentials

### Steps

```bash
# Deploy everything — CDK infrastructure + AgentCore runtime in VPC mode
python deploy.py

# Invoke the agent (sends a message through the VPC to the Fargate echo service)
python invoke.py
python invoke.py "hello from the VPC"

# Tear down all resources
python cleanup.py
```

`deploy.py` handles the full workflow: CDK deploy, parsing outputs, uploading agent code to S3, creating the AgentCore runtime with VPC networking, and saving config to `runtime_config.json`.

### Cleanup

```bash
python cleanup.py
```

This deletes the AgentCore runtime, IAM role, S3 artifacts, and all CDK-managed resources (VPC, Fargate service, ECR repository, networking).

## Cost Considerations

VPC mode adds infrastructure costs compared to PUBLIC mode:

- **NAT gateway** — hourly charge + data processing fees
- **Fargate** — vCPU and memory charges while the service runs
- **ECR** — storage for the container image

For development and testing, destroy the stack when not in use.
