<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# EKS Deployment


Deploy MCP servers and REST APIs on Amazon EKS and connect them to AgentCore gateway using VPC egress.

## Scope

The labs in this section use a **private hosted zone** with a **public certificate**. The endpoint domain resolves inside the VPC to an alias for the internal NLB; the Resource Gateway uses private DNS. For other domain and certificate combinations, see [Advanced Concepts](../03-advanced-concepts/).

## Architecture

![arch](./images/eks-mcp.png)

An [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/) runs behind a single internal Network Load Balancer (NLB). The NLB provides:

- **Static IPs**: one per AZ, useful for allowlisting
- **TLS termination**: terminates TLS with an ACM public certificate and forwards plain HTTP to NGINX

NGINX performs **path-based routing** to multiple backend services, so a single NLB can serve multiple MCP servers (e.g., `/mcp-server/mcp` and `/stock-mcp/mcp`).

```text
AgentCore gateway
  → VPC Lattice (private DNS alias to the NLB)
    → Resource gateway ENIs
      → Internal NLB (TLS :443, public cert)
        → NGINX Ingress (HTTP :80, path-based routing)
          → EKS Pods (HTTP :8000 or :8080)
```

## Prerequisites

- Completed [Lab 0: Prerequisites](../00-prerequisites/) (VPC + AgentCore gateway deployed)
- An ACM public certificate: see [Create an ACM Public Certificate](../00-prerequisites/concepts/create-acm-public-certificate.md)
- A parent domain for the private hosted zone: see [Public Certificate + Private Domain](../00-prerequisites/concepts/public-certificate-private-domain.md)

> **Cost Warning:** Running an EKS cluster incurs ongoing charges ($0.10/hr for the control plane + EC2 node group instances). An internal NLB adds additional cost. Make sure to run the **Cleanup** section in each notebook after completing the lab, and destroy the `SharedEksCluster` stack when you're done with all EKS labs to avoid unnecessary charges. See [Amazon EKS pricing](https://aws.amazon.com/eks/pricing/) and [Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/) for details.

## Labs

| Lab | Description |
|-----|-------------|
| [mcp-server-gateway-managed](./mcp-server-gateway-managed.md) | Deploy FastMCP servers on EKS behind an NGINX Ingress Controller (single NLB, path-based routing) with a private hosted zone. Uses managed VPC resource. |
| [api-server-gateway-managed](./api-server-gateway-managed.md) | Deploy a REST API (FastAPI) on EKS behind an internal NLB, connected to AgentCore gateway with an OpenAPI schema. |

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
