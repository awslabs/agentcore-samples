# Additional Security Considerations

## Encryption at Rest

This sample uses AWS-managed and customer-managed KMS encryption:

**Customer-Managed KMS Key (Included):**

- Amazon CloudWatch Logs (Agent and Session Binding services)
- Amazon S3 sessions bucket

**AWS-Managed Encryption Keys:**

- Application Load Balancer access logs S3 bucket (ALB only supports SSE-S3)
- Amazon ECR repositories (auto-created by CDK with default encryption AWS-managed KMS)

- **Amazon Bedrock AgentCore Identity** token vault: If customer-managed KMS is required, use [`set-token-vault-cmk`](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore-control/set-token-vault-cmk.html)

- **AWS Secrets Manager** (SSO credentials): If customer-managed KMS is required, add `--kms-key-id` parameter when creating the secret

### Network and Monitoring

- **VPC Flow Logs**: VPC Flow Logs are not enabled in this sample to reduce costs. For production deployments, enable VPC Flow Logs for network traffic monitoring and security analysis.

- **VPC Endpoints**: This sample does not use VPC endpoints for AWS services (Bedrock, S3, Secrets Manager). For production deployments, consider adding VPC endpoints to avoid routing traffic through NAT gateways and internet gateways, which can reduce costs.

- **WAF and CloudFront**: The ALB is publicly accessible (0.0.0.0/0 ingress on port 443) and protected by OIDC authentication. For production deployments, consider adding AWS WAF for protection against common web exploits (SQL injection, XSS, DDoS) and CloudFront for content delivery, caching, and additional DDoS protection at the edge.

- **CloudWatch Alarms**: This sample does not include CloudWatch alarms for monitoring. For production deployments, implement CloudWatch alarms on exceptional resource usage and metrics (CPU, memory, error rates, API throttling) to detect and respond to operational and security issues.

### Access Control

- **Amazon S3 Bucket Policies**: S3 bucket policies can be used to further restrict access with IAM condition keys for fine-grained access control based on user identity, IP address, or request attributes.

- **KMS Key Administration**: The KMS key policy allows the root account full permissions (`kms:*`). For production deployments, consider restricting key administrative permissions to specific IAM principals or roles in your account to follow the principle of least privilege.

- **Amazon Bedrock Guardrails**: This sample does not configure Bedrock Guardrails. For production deployments, consider implementing guardrails to filter harmful content, PII, and inappropriate inputs/outputs from the agent based on your requirements.
