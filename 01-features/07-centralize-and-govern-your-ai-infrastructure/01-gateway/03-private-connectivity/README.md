# Private Connectivity

Configure private network connectivity for your Amazon Bedrock AgentCore gateway — both outbound (gateway to private resources) and inbound (clients to gateway over private network).

## Connect gateway to Private Resources (VPC Egress)

Route AgentCore gateway traffic to MCP servers, APIs, and tools running inside your VPC — without exposing them to the public internet. Uses Amazon VPC Lattice under the hood.

![VPC Egress Architecture](connect-gateway-to-private-resources/images/architecture.png)

### Which targets need `privateEndpoint`?

Not all targets require VPC egress configuration. Traffic to AgentCore-managed services stays on the AWS backbone automatically.

| Target type | Needs `privateEndpoint`? | How traffic flows |
| :--- | :--- | :--- |
| MCP Server (self-hosted in VPC) | Yes | Resource gateway ENIs → your MCP server |
| MCP Server (AgentCore runtime) | No | AWS backbone (never leaves AWS network) |
| OpenAPI (private endpoint in VPC) | Yes | Resource gateway ENIs → your API |
| API gateway (regional, public) | No | AWS backbone via VPC Link |
| API gateway (private) | Yes (use OpenAPI target + `routingDomain`) | Resource gateway ENIs → VPCE → Private API gateway |
| Lambda | No | Lambda handles VPC access natively |
| Smithy | Not currently supported | — |
| Private identity providers | Yes | Resource gateway ENIs → your IdP (Keycloak, PingFederate, etc.) |

### Two modes for VPC egress

| Mode | You provide | AgentCore manages |
| :--- | :--- | :--- |
| **Managed VPC resource** | VPC ID, subnet IDs, security groups | Resource gateway + Resource Configuration |
| **Self-managed Lattice** | Resource Configuration ARN | Nothing — you manage the Lattice resources |

### Key requirements

- **TLS certificate**: Targets must present a publicly trusted certificate (ACM public cert). Private CA or self-signed certs require the [ALB proxy workaround](connect-gateway-to-private-resources/03-advanced-concepts/02-private-certificate-authority.md).
- **Inbound auth required**: Targets with `privateEndpoint` cannot use `NO_AUTH` as the gateway authorizer (unless an interceptor Lambda is configured).
- **DNS resolution**: With Private DNS enabled (VPC default), the Resource gateway resolves domains via your VPC's DNS resolver. Use `routingDomain` only when DNS is not enabled.

## Connect to gateway Privately (Private Ingress)

Access AgentCore gateway over a private network path using AWS PrivateLink. MCP clients in your VPC or on-premises (via Direct Connect) reach the gateway without traversing the public internet.

![Private Ingress Architecture](connect-to-gateway-privately/images/architecture.png)

## Tutorials

| Section | Description |
| :--- | :--- |
| [connect-gateway-to-private-resources](connect-gateway-to-private-resources/) | Route gateway traffic to resources inside your VPC (managed or self-managed VPC Lattice, cross-account, ECS, EKS) |
| [connect-to-gateway-privately](connect-to-gateway-privately/) | Access the gateway over a private endpoint using AWS PrivateLink |

## Documentation

- [AgentCore gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
- [Connect to private resources using VPC Lattice](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-egress-private-endpoints.html)
- [Connect to private identity providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-private-idp.html)
- [AgentCore gateway Private Endpoints](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-private-endpoints.html)
- [Amazon VPC Lattice](https://docs.aws.amazon.com/vpc-lattice/latest/ug/what-is-vpc-lattice.html)
- [AWS PrivateLink](https://docs.aws.amazon.com/vpc/latest/privatelink/what-is-privatelink.html)
