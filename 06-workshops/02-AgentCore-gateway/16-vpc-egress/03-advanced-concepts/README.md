<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Advanced Concepts

This section covers private DNS, private certificates, static IP egress, and the patterns needed to make them work with AgentCore Gateway VPC egress.


## Disabled VPC DNS: Routing Domain

> **Use `routingDomain` only when DNS is not enabled in your VPC.** If your VPC has DNS enabled (the default), AgentCore Gateway VPC egress reaches private endpoints via Private DNS automatically — no `routingDomain` needed.

Amazon VPC Lattice requires that the domain used in a resource configuration be resolvable. If your VPC does not have DNS enabled and your private endpoint uses a domain that is only resolvable within your VPC (for example, a Route 53 private hosted zone), use the `routingDomain` field as a fallback.

![arch](./images/private-domain.png)

### How it works

When using a routing domain:

1. The **target URL** uses the actual private DNS name of your resource (the name resolvable within your VPC)
2. The **`routingDomain`** is a separate, publicly resolvable domain that AgentCore uses only to set up the VPC Lattice resource configuration
3. At invocation time, AgentCore routes traffic through the routing domain but sends requests with the private DNS name as the **TLS SNI hostname**, so your resource receives requests addressed to its actual private domain


### Common routing domain options

The routing domain can be any publicly resolvable domain that routes to your private resource within the VPC:

| Option | Routing Domain | Target URL |
|--------|---------------|------------|
| **Internal ALB** | `internal-<name>-<id>.us-west-2.elb.amazonaws.com` | Private DNS name of the resource behind the ALB |
| **Internal NLB** | `internal-<name>-<id>.us-west-2.elb.amazonaws.com` | Private DNS name of the resource behind the NLB |
| **VPC Endpoint (VPCE)** | `<vpce-id>.execute-api.<region>.vpce.amazonaws.com` | Private API Gateway hostname (e.g., `https://<api-id>.execute-api.<region>.amazonaws.com`) |

## Private Certificates: ALB Workaround

VPC egress requires your target endpoint to have a **publicly trusted TLS certificate**. If your private resource uses a certificate issued by a private certificate authority (CA), the recommended workaround is to place an internal Application Load Balancer (ALB) in front of your resource.

![privateCA](./images/private-ca.png)

### How it works

```
AgentCore Gateway
  → VPC Lattice (routingDomain: ALB DNS)
    → Resource Gateway ENIs
      → Internal ALB (public cert, TLS termination + host header transform)
        → Your resource (private cert, HTTPS)
```

1. The **target URL** uses a domain that matches your public ACM certificate (e.g., `https://my-server.my-company.com`)
2. The **`routingDomain`** is the internal ALB DNS name
3. VPC Lattice routes traffic to the ALB via the routing domain. The TLS SNI is set to `my-server.my-company.com`, which matches the ALB's public ACM certificate, so the TLS handshake succeeds
4. The ALB **terminates TLS** and applies a **host header transform** to rewrite the Host header from the public domain to the private resource's domain (e.g., `my-server.my-company.internal`)
5. The ALB forwards the request to your backend resource over HTTPS using the private certificate. All traffic stays inside your VPC


For domain and certificate setup guides, see the [Prerequisites](../00-prerequisites/) folder.

## Static Gateway IP

If your external MCP server requires IP-based allowlisting, you can route AgentCore Gateway traffic through a **NAT Gateway with an Elastic IP** in your VPC. This gives all outbound traffic a static, known source IP that the MCP server operator can allowlist.

![arch](./images/gateway-static-ip.png)

### How it works

1. Use **VPC egress** (managed VPC resource) to route AgentCore Gateway traffic into your VPC through a Resource Gateway
2. Place the Resource Gateway ENIs in a **private subnet** that routes outbound traffic (0.0.0.0/0) through a NAT Gateway
3. The NAT Gateway has an **Elastic IP** — a static, public IP address
4. All traffic to external MCP servers exits through this Elastic IP
5. The MCP server allowlists the Elastic IP — only traffic from your AgentCore Gateway is accepted

For high availability, deploy one NAT Gateway per Availability Zone. Each NAT Gateway has its own Elastic IP, so provide all EIPs to the MCP server for allowlisting.

## Labs

| Notebook | Description |
|----------|-------------|
| [01-private-domain.ipynb](./01-private-domain.ipynb) | Connect AgentCore Gateway to a privately resolvable endpoint |
| [02-private-certificate-authority.ipynb](./02-private-certificate-authority.ipynb) | Use the ALB workaround for APIs with AWS Private CA certificates. |
| [03-self-signed-certificate.ipynb](./03-self-signed-certificate.ipynb) | Use the ALB workaround for APIs with self-signed certificates (no Private CA cost). |
| [04-static-gateway-ip.ipynb](./04-static-gateway-ip.ipynb) | Route AgentCore Gateway traffic through a NAT Gateway with a static Elastic IP for allowlisting. |

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
