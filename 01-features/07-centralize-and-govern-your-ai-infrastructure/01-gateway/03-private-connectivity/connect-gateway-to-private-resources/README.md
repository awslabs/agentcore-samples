<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Connect AgentCore Gateway to a private API

Give an agent access to a private API or MCP server without exposing the backend
to the public internet. These labs connect AgentCore Gateway to resources in a
VPC through VPC Lattice, with separate exercises for managed connectivity,
self-managed resource configurations, certificates and cross-account access.

![Private connectivity architecture](./images/architecture.png)

## Start with one path

For a fresh deployment, start with the **same-account private API** example:

1. Follow [prerequisites](00-prerequisites/README.md) for that path only.
2. Use the nested [`vpcegress/`](vpcegress/README.md) CLI-v1 project for the Gateway.
   The parent CDK project supplies the selected network/backend infrastructure;
   it does not create another Gateway.
3. Follow [connect to a private API](01-managed-vpc-resource/01-getting-started.md),
   including invocation and cleanup. Verify both the Gateway result and the
   backend response; successful synthesis is not private-network connectivity.
4. Remove only the resources owned by that lab and verify their absence.

**Do not deploy all stacks.** Peering, a second account, EKS, Private CA and static
IP egress are separate exercises—not prerequisites for this first path. Use a
fresh project and unique resource names; do not overwrite an existing deployed
v0 checkout or erase its ownership records.

## Choose a connectivity mode

| Mode | What you provide | Ownership |
|---|---|---|
| Managed VPC resource | VPC, subnet and security-group configuration | AgentCore manages the Lattice resource infrastructure for the target |
| Self-managed Lattice | A Resource Configuration ARN or ID | You manage the Resource Gateway and Resource Configuration, including sharing and cleanup |

AgentCore manages the service-network association in both modes. A Resource
Configuration identifies a particular endpoint; it is not blanket access to a VPC.
See the [managed](./images/managed.png) and [self-managed](./images/self-managed.png)
illustrations and the [service guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html).

## Key Terms

- **Resource VPC:** the VPC containing the private backend.
- **Gateway account:** the account owning AgentCore Gateway; it can differ from
  the resource account in the explicitly selected cross-account lab.
- **Resource Gateway:** the VPC Lattice entry point with ENIs in the selected subnets.
- **Resource Configuration:** the endpoint configuration reached through that
  Resource Gateway. Pass this ARN/ID—not the Resource Gateway ARN—to a self-managed target.
- **Service-network association:** the AgentCore-managed connection to the configuration.

## Labs

| Lab | Purpose |
|---|---|
| [Prerequisites](00-prerequisites/) | Prepare only the identity, network and backend resources required by your selected path |
| [Managed VPC resource](01-managed-vpc-resource/) | First private API connection; optional VPC peering exercise |
| [Self-managed Lattice](02-self-managed-lattice/) | Explicit resource configuration ownership; optional cross-account sharing |
| [Advanced concepts](03-advanced-concepts/) | Private domains/certificates and static IP egress |
| [ECS deployment](04-ecs-deployment/) | Two simulated MCP backends behind an internal ALB |
| [EKS deployment](05-eks-deployment/) | Historical Kubernetes examples requiring a separate permissions and lifecycle review |

The labs use Cognito for inbound Gateway authentication. MCP demo backends have
no outbound authentication; private API examples use API-key providers. These
are focused connectivity demonstrations, not production authentication patterns.

## Prerequisites for all labs

- AWS CLI v2, Node.js 22+ and npm, Python 3.12+, and the sample's locally installed CLI.
- A sandbox identity with **project-specific** deployment permissions. Inspect the
  CDK/CloudFormation execution-role chain as well as the caller's permissions;
  a non-admin caller can still delegate to an administrator-level execution role.
- An account/Region already bootstrapped by its owner, or a separately authorized
  bootstrap step. Preserve shared resources and use `--skip-bootstrap` where appropriate.
- Docker only for the selected image-based workloads and their local checks.

Follow the [CLI setup, ownership and tests](vpcegress/README.md) before deployment.
The control SDK and server dependency graphs are hash-locked. Local tests use fake
service responses; they do not prove AWS routing, authorization or cloud cleanup.

## Regions and accounts

The walkthrough uses **`us-west-2`** as its primary region. Verify the availability
of your selected [AgentCore features](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
before deploying elsewhere. An endpoint appearing in an SDK model is not proof
that every private-connectivity mode is available there.

The [peering exercise](01-managed-vpc-resource/02-peering.md) uses a second VPC in
`us-east-1`. The [cross-account exercise](02-self-managed-lattice/02-cross-account.md)
requires a separately authorized resource account. Neither is part of the first
same-account path.

## Domain and certificate requirements

For the selected load-balancer workloads, prepare a domain/Route 53 hosted zone
and the appropriate certificate. See [domain and certificate setup](00-prerequisites/).
Do not create a Private CA or change shared DNS merely to run the initial API lab.

## Cost Warnings

Charges depend on the selected stacks and region. VPC endpoints, NAT gateways,
public IPv4 addresses, load balancers, compute, VPC Lattice and Private CA can
continue accruing charges while idle. Check current service pricing or the
[AWS Pricing Calculator](https://calculator.aws/) rather than treating tutorial
estimates as a budget.

Review cleanup before creating resources. Preserve the CLI and SDK ownership
records through failures; an access error or accepted delete request is not
absence proof. Do not use `cdk deploy --all`, `destroy --all`, broad security-group
cleanup, or shared-bucket pruning. Optional EKS/CA/listener risks are explicitly
listed in the [ownership and safety notes](vpcegress/README.md#historical-optional-infrastructure-risks).

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE.txt).
