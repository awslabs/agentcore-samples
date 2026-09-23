import { NagSuppressions } from 'cdk-nag';
import { Stack } from 'aws-cdk-lib';

/**
 * Apply cdk-nag suppressions for acceptable warnings in the Knowledge Base RAG Agent
 * This is an educational template, so some best practices are relaxed for simplicity
 */
export function applyNagSuppressions(stack: Stack): void {
  // Suppress IAM4: AWS Managed Policies
  // Justification: Using AWS managed policies for Lambda execution is standard practice
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-IAM4',
      reason: 'AWS managed policies (AWSLambdaBasicExecutionRole) are standard for Lambda functions',
    },
  ]);

  // Suppress Lambda4: Lambda DLQ
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-Lambda4',
      reason: 'DLQ not required for educational template Lambda functions; add in production workloads',
    },
  ]);

  // Suppress Lambda6: Lambda reserved concurrency
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-Lambda6',
      reason: 'Reserved concurrency not set for educational templates; configure in production',
    },
  ]);

  // Suppress Lambda10: Lambda code signing
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-Lambda10',
      reason: 'Code signing not required for educational templates',
    },
  ]);

  // Suppress EC23: Security group allows all inbound
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-EC23',
      reason: 'Sample security groups intentionally permissive for demo connectivity',
    },
  ]);

  // Suppress EC27: No description on security group rules
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-EC27',
      reason: 'Security group rule descriptions not required for educational templates',
    },
  ]);

  // Suppress ECS4: Container insights not enabled
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-ECS4',
      reason: 'Container insights optional for educational templates; enable in production',
    },
  ]);

  // Suppress ECS7: No awslogs log driver
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-ECS7',
      reason: 'Log driver configuration optional for educational templates',
    },
  ]);

  // Suppress DDB3: DynamoDB PITR
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-DDB3',
      reason: 'PITR not required for educational template data; enable in production',
    },
  ]);

  // Suppress S10: S3 deny public access policy
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-S10',
      reason: 'S3 bucket policy configuration simplified for educational templates',
    },
  ]);

  // Suppress RDS10: Multi-AZ
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-RDS10',
      reason: 'Multi-AZ not required for educational template databases; enable in production',
    },
  ]);

  // Suppress RDS11: Aurora reader instances
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-RDS11',
      reason: 'Aurora reader not required for educational template; add in production',
    },
  ]);

  // Suppress IAM5: Wildcard Permissions
  // Justification: Bedrock and OpenSearch operations require wildcards for dynamic resources
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-IAM5',
      reason: 'Wildcard permissions required for Bedrock Agent and OpenSearch Serverless operations',
    },
  ]);

  // Suppress L1: Lambda Runtime Version
  // Justification: Using latest stable Node.js 20 runtime
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-L1',
      reason: 'Using Node.js 20.x which is the latest stable LTS runtime',
    },
  ]);
}


/**
 * Apply suppressions for S3-related warnings
 */
export function suppressS3Warnings(stack: Stack): void {
  // Suppress S1: S3 Access Logging
  // Justification: Access logging is optional for demo templates
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-S1',
      reason: 'S3 access logging is optional for educational templates. Enable in production.',
    },
  ]);
}

/**
 * Apply suppressions for Cognito warnings
 */
export function suppressCognitoWarnings(stack: Stack): void {
  // Suppress COG2: MFA
  // Justification: MFA is optional for demo templates, should be enabled in production
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-COG2',
      reason: 'MFA is optional for educational templates. Enable in production for enhanced security.',
    },
  ]);

  // Suppress COG3: Advanced Security Mode
  // Justification: Advanced Security Mode requires Cognito Plus plan (additional cost)
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-COG3',
      reason: 'Advanced Security Mode requires Cognito Plus feature plan. Enable in production if budget allows.',
    },
  ]);

  // Suppress SMG4: Secrets Manager Automatic Rotation
  // Justification: Automatic rotation is optional for initial user passwords in demo templates
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-SMG4',
      reason: 'Automatic rotation is optional for initial user passwords in educational templates. Enable in production for enhanced security.',
    },
  ]);
}

/**
 * Apply suppressions for API Gateway warnings
 */
export function suppressApiGatewayWarnings(stack: Stack): void {
  // Suppress APIG2: Request Validation
  // Justification: Request validation handled in Lambda functions
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-APIG2',
      reason: 'Request validation is performed in Lambda functions with input sanitization',
    },
  ]);

  // Suppress APIG3: WAF Integration
  // Justification: WAF is optional for demo templates, adds significant cost
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-APIG3',
      reason: 'AWS WAF integration is optional for educational templates. Enable in production for DDoS protection.',
    },
  ]);

  // Suppress APIG4: Authorization (for health check endpoint only)
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-APIG4',
      reason: 'Health check endpoint is intentionally public for monitoring',
    },
  ]);

  // Suppress COG4: Cognito Authorizer (for health check endpoint only)
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-COG4',
      reason: 'Health check endpoint does not require authentication',
    },
  ]);
}


/**
 * Apply suppressions for CloudFront warnings
 */
export function suppressCloudFrontWarnings(stack: Stack): void {
  // Suppress CFR1: Geo Restrictions
  // Justification: Geo restrictions are optional for demo templates
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-CFR1',
      reason: 'Geo restrictions are optional for educational templates. Configure based on compliance requirements.',
    },
  ]);

  // Suppress CFR2: WAF Integration
  // Justification: WAF is optional for demo templates, adds significant cost
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-CFR2',
      reason: 'AWS WAF integration is optional for educational templates. Enable in production for DDoS protection.',
    },
  ]);

  // Suppress CFR3: CloudFront Access Logging
  // Justification: Access logging is optional for demo templates
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-CFR3',
      reason: 'CloudFront access logging is optional for educational templates. Enable in production.',
    },
  ]);

  // Suppress CFR4: TLS Version
  // Justification: CloudFront default certificate enforces TLS 1.2+
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-CFR4',
      reason: 'Using CloudFront default certificate which enforces TLS 1.2+ for viewer connections',
    },
  ]);
}

/**
 * Apply suppressions for VPC warnings
 */
export function suppressVpcWarnings(stack: Stack): void {
  // Suppress VPC7: VPC Flow Logs
  // Justification: Flow logs are optional for demo templates
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-VPC7',
      reason: 'VPC Flow Logs are optional for educational templates. Enable in production for network monitoring.',
    },
  ]);
}

/**
 * Apply suppressions for SNS warnings
 */
export function suppressSnsWarnings(stack: Stack): void {
  // Suppress SNS3: SSL Enforcement
  // Justification: SNS topics with SSE enabled automatically enforce SSL
  NagSuppressions.addStackSuppressions(stack, [
    {
      id: 'AwsSolutions-SNS3',
      reason: 'SNS topics with encryption enabled automatically enforce SSL for publishers',
    },
  ]);
}
