<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Create a Private Hosted Zone

> **Note:** The guidance in this document is intended for **workshop and learning purposes only**. For production deployments, please adhere to your organization's security policies and DNS management practices.

Create a Route 53 private hosted zone for your domain, associated with your VPC. Records in this zone only resolve from inside the associated VPCs.

> Some CDK stacks in this workshop create the private hosted zone automatically. Check the lab instructions before creating one manually.

## Prerequisites

- Your VPC ID
- AWS CLI configured

## Step 1: Create the private hosted zone

```bash
aws route53 create-hosted-zone \
  --name your-domain.com \
  --vpc VPCRegion=us-west-2,VPCId=<your-vpc-id> \
  --caller-reference $(date +%s) \
  --hosted-zone-config PrivateZone=true \
  --profile default
```

Note the `HostedZoneId` from the response.

> You can use the same domain name as your public hosted zone. Route 53 resolves private zones first for associated VPCs. The public zone is unaffected.

## Step 2: Add a DNS record

After deploying the CDK stack, add an Alias A record pointing your domain to the load balancer:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id <private-hosted-zone-id> \
  --profile default \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "internal-mcp.your-domain.com",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "<load-balancer-hosted-zone-id>",
          "DNSName": "<load-balancer-dns-from-stack-outputs>",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

> Use an Alias A record (not CNAME) for load balancers: it's free and supports zone apex.

## Verify

From **inside the VPC** (bastion or CloudShell VPC mode):
```bash
dig internal-mcp.your-domain.com A +short
# Returns load balancer private IPs
```

From **outside the VPC**:
```bash
dig @8.8.8.8 internal-mcp.your-domain.com A +short
# Returns nothing (NXDOMAIN)
```

## Cleanup

Delete the DNS record first, then the hosted zone:

```bash
# Delete the record (use the same change batch with Action: DELETE)

# Delete the hosted zone
aws route53 delete-hosted-zone \
  --id <private-hosted-zone-id> \
  --profile default
```

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
