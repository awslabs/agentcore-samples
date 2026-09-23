# Static Gateway IP: Allowlist AgentCore Gateway Traffic

Give Amazon Bedrock AgentCore Gateway a **static, known IP address** so that external services can allowlist it. By default, AgentCore Gateway traffic originates from dynamic AWS-managed IPs. This lab routes traffic through a NAT Gateway with an Elastic IP for a fixed egress address.

## The Problem

- **WAF rules**: Your API gateways are behind a WAF, and AgentCore Gateway requests are blocked because the source IPs are dynamic
- **MCP server allowlisting**: Your MCP server provider requires a static source IP for allowlisting

## Solution

Route AgentCore Gateway traffic through your VPC using VPC egress, and exit through a **NAT Gateway with an Elastic IP**.

![Static Gateway IP Architecture](images/gateway-static-ip.png)

### How it works

1. AgentCore Gateway uses **managed VPC resource** to route traffic into your VPC through a Resource Gateway
2. Resource Gateway ENIs are placed in a **private subnet** that routes outbound traffic (0.0.0.0/0) through a NAT Gateway
3. The NAT Gateway has an **Elastic IP** — a static public IP address
4. All traffic to the external MCP server exits through this single Elastic IP
5. The MCP server allowlists this IP

> **Resilience vs simplicity:** This lab uses a single NAT Gateway for a single static IP. In production, deploy one NAT Gateway per Availability Zone. Each has its own Elastic IP — provide all IPs to the allowlist.

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root**. Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Deploy the static IP infrastructure (CDK) — from project root

This stack creates a NAT Gateway with an Elastic IP in the VPC's public subnet, and updates the private subnet route table to route outbound traffic through it.

```bash
cdk deploy StaticGatewayIp --require-approval never --outputs-file static-ip-outputs.json
```

```bash
export ELASTIC_IP=$(python3 -c "import json; print(json.load(open('static-ip-outputs.json'))['StaticGatewayIp']['ElasticIp'])")
export NAT_GW_ID=$(python3 -c "import json; print(json.load(open('static-ip-outputs.json'))['StaticGatewayIp']['NatGatewayId'])")

echo "Elastic IP (static egress): $ELASTIC_IP"
echo "NAT Gateway ID:             $NAT_GW_ID"
```

### Step 2: Create the Gateway Target — from `vpcegress/`

Point the target at any external MCP server or API. The traffic exits through the Elastic IP regardless of the endpoint.

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name static-ip-target \
  --endpoint https://your-external-mcp-server.com/mcp \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS
```

### Step 3: Verify the static IP

From the external MCP server's logs or a test endpoint, confirm that requests arrive from `$ELASTIC_IP`.

```bash
echo "All requests from AgentCore Gateway will originate from: $ELASTIC_IP"
echo "Provide this IP to the MCP server operator for allowlisting."
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to invoke tools and verify the source IP.

Follow the same invocation pattern as [lab 01 (Private Domain)](./01-private-domain.md#demo). The external service should see all requests coming from `$ELASTIC_IP`.

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name static-ip-target
```

Destroy the CDK stack (from project root):

```bash
cdk destroy StaticGatewayIp --force
```

> [!NOTE]
> The Elastic IP is released when the CDK stack is destroyed. No ongoing charges after cleanup.

## Documentation

- [NAT Gateway](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-nat-gateway.html)
- [Elastic IP Addresses](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
