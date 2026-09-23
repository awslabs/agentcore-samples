<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Create a Public DNS Record

> **Note:** The guidance in this document is intended for **workshop and learning purposes only**. For production deployments, please adhere to your organization's security policies and DNS management practices.

Create a DNS record in your **public hosted zone** pointing your domain to the load balancer deployed by the CDK stack.

## Prerequisites

- A Route 53 public hosted zone for your domain (can be in a different account)
- The load balancer DNS name from the CDK stack outputs (`AlbDnsName` or `NlbDnsName`)

## Create the record

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id <public-hosted-zone-id> \
  --profile <profile-with-dns-access> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "your-domain.com",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [{"Value": "<load-balancer-dns-from-stack-outputs>"}]
      }
    }]
  }'
```

> The load balancer DNS name is in the CDK stack outputs (e.g., `AlbDnsName` or `NlbDnsName`).

## Verify

```bash
dig @8.8.8.8 your-domain.com A +short
# Should return private IPs (the load balancer is internal)
```

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
