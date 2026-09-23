<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Private Certificate + Private Domain

> **Note:** The guidance in this document is intended for **workshop and learning purposes only**. For production deployments, please adhere to your organization's security policies, DNS management practices, and certificate lifecycle requirements.

## Overview

| Component | Type | Description |
|-----------|------|-------------|
| **Certificate** | Private (AWS Private CA) | Trusted only by systems configured to trust your CA |
| **Domain** | Private (Route 53 private hosted zone) | Resolves only inside the VPC |

## What does this mean?

Both the DNS and the certificate are fully private. The domain only resolves inside your VPC, and the certificate is only trusted by clients that have your private CA's root certificate installed. Nothing is exposed to the public internet.

## Limitation with AgentCore Gateway

AgentCore Gateway **does not support private certificates**. It validates TLS certificates against public root CAs only. A fully private setup (private cert + private domain) will fail with a TLS handshake error.

Additionally, a private domain requires `routingDomain` since VPC Lattice needs a publicly resolvable domain for routing.

## Workaround

Place a load balancer with a **public certificate** in front of your resource, and use `routingDomain` for the private domain:

```
AgentCore Gateway
  → VPC Lattice (routes via routingDomain: *.elb.amazonaws.com)
    → Resource Gateway ENIs
      → Load Balancer (public cert, TLS termination)
        → Your resource (private cert or plain HTTP)
```

This effectively converts the setup into the [Public Certificate + Private Domain](./public-certificate-private-domain.md) pattern at the AgentCore layer, while your backend can still use private certificates for internal encryption.

## When would you use this pattern?

- **Zero public exposure**: no public DNS records, no public certificates on backend services
- **Defense in depth**: the load balancer handles AgentCore's public cert requirement, while backend services use your organization's private PKI
- **Regulatory requirements**: environments where all internal communication must use certificates from an approved internal CA

## Setup steps

1. [Create an ACM public certificate](./create-acm-public-certificate.md) for the load balancer (validated against your public parent domain)
2. [Create a private hosted zone](./create-private-hosted-zone.md) associated with your VPC
3. Optionally, set up [AWS Private CA](https://docs.aws.amazon.com/privateca/latest/userguide/PcaWelcome.html) for backend certificates
4. Deploy your CDK stack with the public cert on the load balancer
5. Configure `routingDomain` when creating the AgentCore Gateway target

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
