<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Public Certificate + Public Domain

> **Note:** The guidance in this document is intended for **workshop and learning purposes only**. For production deployments, please adhere to your organization's security policies, DNS management practices, and certificate lifecycle requirements.

## Overview

| Component | Type | Description |
|-----------|------|-------------|
| **Certificate** | Public (ACM) | Trusted by all clients, including AgentCore Gateway |
| **Domain** | Public (Route 53 public hosted zone) | Resolves globally via public DNS |

## What does this mean?

Your domain (e.g., `mcp.example.com`) is publicly resolvable: anyone can look it up with `dig`. However, it resolves to **private IPs** inside your VPC because the load balancer is internal (not internet-facing).

The domain is discoverable, but the resource is not reachable from the internet.

```
dig @8.8.8.8 mcp.example.com → 10.0.2.52, 10.0.3.60 (private IPs)
```

## When to use this

- Simplest setup: domain resolves globally, no DNS workaround needed
- You're comfortable with the domain name being visible in public DNS
- You have a domain with a public hosted zone in Route 53 (same or different account)

## Traffic flow

```
AgentCore Gateway → VPC Lattice → Resource Gateway ENIs → Internal LB (TLS) → Your resource
```

AgentCore resolves the domain via public DNS, VPC Lattice routes traffic through the Resource Gateway ENIs to the internal load balancer.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
