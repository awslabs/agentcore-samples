<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Create an ACM Public Certificate

> **Note:** The guidance in this document is intended for **workshop and learning purposes only**. For production deployments, please adhere to your organization's security policies and certificate lifecycle requirements.

## Prerequisites

- A domain you own with a Route 53 hosted zone (or DNS access to add CNAME records)
- AWS CLI configured

## Step 1: Request the certificate

In the account where your load balancer will be deployed:

```bash
aws acm request-certificate \
  --domain-name "*.your-domain.com" \
  --validation-method DNS \
  --region us-west-2 \
  --profile default
```

> Use a wildcard cert (`*.your-domain.com`) to cover all subdomains, or request a specific domain. The domain must match what you'll point to the load balancer.

Note the `CertificateArn` from the response.

## Step 2: Get the DNS validation record

```bash
aws acm describe-certificate \
  --certificate-arn <certificate-arn> \
  --region us-west-2 \
  --profile default \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Returns a CNAME record:

```json
{
  "Name": "_abc123.your-domain.com.",
  "Type": "CNAME",
  "Value": "_def456.acm-validations.aws."
}
```

## Step 3: Add the validation CNAME

Add this CNAME in the **public hosted zone** for your domain.

**Console:** Route 53 → Hosted zones → your domain → Create record → CNAME → paste Name and Value.

**CLI:**

```bash
# Get the hosted zone ID
aws route53 list-hosted-zones-by-name \
  --dns-name your-domain.com \
  --query 'HostedZones[0].Id' \
  --output text \
  --profile <profile-with-dns-access>

# Add the validation record
aws route53 change-resource-record-sets \
  --hosted-zone-id <hosted-zone-id> \
  --profile <profile-with-dns-access> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "<Name from Step 2>",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [{"Value": "<Value from Step 2>"}]
      }
    }]
  }'
```

> If your domain is in a different AWS account, add the CNAME in that account's Route 53 hosted zone.

## Step 4: Wait for validation

```bash
aws acm describe-certificate \
  --certificate-arn <certificate-arn> \
  --region us-west-2 \
  --profile default \
  --query 'Certificate.Status'
```

Wait until it returns `"ISSUED"`. Usually takes a few minutes.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](../LICENSE.txt) file for details.
