# Connect to gateway Privately

> [!IMPORTANT]
> This lab is currently in **read-only mode**, we are actively working on the runnable code and scripts. The documentation below explains the concepts and setup steps.

Access your Amazon Bedrock AgentCore gateway over a private endpoint using AWS PrivateLink, keeping all traffic off the public internet. Instances in your VPC don't need public IP addresses to reach the gateway.

![architecture](./images/architecture.png)

## How it works

You create an **interface VPC endpoint** powered by AWS PrivateLink. This provisions endpoint network interfaces (ENIs) in your subnets that serve as the entry point for traffic destined to AgentCore gateway. With private DNS enabled, you can use the same gateway URL — DNS resolves to the private ENI IPs instead of public addresses.

Clients on-premises can reach the gateway via AWS Direct Connect or VPN through the VPC endpoint.

## AgentCore PrivateLink Endpoints

AgentCore provides three PrivateLink service endpoints:

| Endpoint | Service name | Use case |
| :--- | :--- | :--- |
| **Data plane** | `com.amazonaws.{region}.bedrock-agentcore` | runtime invocations, memory, identity, Built-in Tools |
| **Control plane** | `com.amazonaws.{region}.bedrock-agentcore-control` | gateway/runtime/memory management APIs |
| **gateway** | `com.amazonaws.{region}.bedrock-agentcore.gateway` | MCP tool calls to AgentCore gateway |

### Which endpoint do I need?

- **MCP clients calling the gateway** → use the **gateway** endpoint (`com.amazonaws.{region}.bedrock-agentcore.gateway`)
- **Agents calling AgentCore runtime** → use the **Data plane** endpoint
- **Admin scripts calling control plane APIs** → use the **Control plane** endpoint

## Setup

### Step 1: Create the VPC endpoint

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-0123456789abcdef0 \
  --service-name com.amazonaws.us-west-2.bedrock-agentcore.gateway \
  --vpc-endpoint-type Interface \
  --subnet-ids subnet-aaa subnet-bbb \
  --security-group-ids sg-123 \
  --private-dns-enabled
```

> [!NOTE]
> Enable **private DNS** so your gateway URL (`https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`) resolves to the VPC endpoint's private IPs automatically.

### Step 2: Configure security group

Allow inbound HTTPS (port 443) from your MCP clients:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-123 \
  --protocol tcp \
  --port 443 \
  --cidr 10.0.0.0/16
```

### Step 3: Use the gateway normally

With private DNS enabled, your existing gateway URL works unchanged — traffic routes through the VPC endpoint instead of the public internet:

```bash
curl -s -X POST https://<gateway-id>.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

## Endpoint Policies

By default, the VPC endpoint allows full access. You can restrict access with an endpoint policy:

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "bedrock-agentcore:*",
      "Resource": "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/*"
    }
  ]
}
```

> [!IMPORTANT]
> If your gateway uses **OAuth** (not SigV4) for inbound auth, set `Principal` to `*` in the endpoint policy. VPC endpoint policies can only restrict based on IAM principals — OAuth tokens are not evaluated by the endpoint policy.

## Considerations

- With private DNS enabled, **all** AgentCore API calls from the VPC route through the endpoint (not just gateway calls). If you need to reach other AgentCore services publicly, create separate endpoints or disable private DNS and use endpoint-specific DNS names.
- Security groups on the endpoint ENIs control which resources in the VPC can reach the gateway.
- For on-premises access via Direct Connect, ensure your on-premises DNS resolves the gateway domain to the VPC endpoint's private IPs (via Route 53 Resolver inbound endpoints or DNS forwarding).

## Documentation

- [Interface VPC Endpoints for AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-interface-endpoints.html)
- [AWS PrivateLink](https://docs.aws.amazon.com/vpc/latest/privatelink/what-is-privatelink.html)
- [Create an Interface Endpoint](https://docs.aws.amazon.com/vpc/latest/privatelink/create-interface-endpoint.html)
- [Endpoint Policies](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-access.html)
