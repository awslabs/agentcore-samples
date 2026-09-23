# Advanced Observability Concepts

This section covers advanced observability patterns and techniques for Amazon Bedrock AgentCore, helping you implement sophisticated customized monitoring and debugging capabilities.

## Available Tutorials

### 01-custom-span-creation/

- **Notebook**: `Custom_Span_Creation.ipynb`
- **Description**: Learn to create custom spans for detailed operation tracing
- **Features**: Manual span creation, custom attributes
- **Use Cases**: Fine-grained monitoring, debugging

### 02-data-protection/

- **Notebook**: `data_protection.ipynb`
- **Description**: Implement comprehensive data protection for sensitive information in agent workflows
- **Features**: Bedrock Guardrails integration, CloudWatch Logs Data Protection, PII detection and masking
- **Use Cases**: Compliance (GDPR, HIPAA, CCPA), sensitive data handling, privacy protection

## What You'll Learn

- **Custom Span Creation**: Add detailed tracing to specific operations
- **Span Attributes**: Enrich traces with custom metadata
- **Nested Spans**: Create hierarchical trace structures
- **Performance Monitoring**: Identify bottlenecks in agent workflows
- **Error Tracking**: Capture and trace exceptions and failures
- **Data Protection**: Implement sensitive data detection and masking in logs and traces
- **Compliance Integration**: Configure Bedrock Guardrails and CloudWatch Data Protection

## Getting Started

1. Navigate to the tutorial directory
2. Copy `.env.example` to `.env` and configure:
   - AWS credentials
   - CloudWatch log group settings
   - OpenTelemetry configuration
3. Enable CloudWatch Transaction Search in your AWS region
4. Install dependencies: `pip install -r requirements.txt`
5. Open and run the Jupyter notebook

## Prerequisites

- Understanding of basic OpenTelemetry concepts
- Familiarity with Amazon CloudWatch
- Experience with agent frameworks (recommended)
- AWS account with appropriate permissions

## Advanced Patterns Covered

- **Manual Instrumentation**: When and how to add custom spans
- **Custom Metrics**: Creating domain-specific measurements
- **Data Protection Policies**: Configuring sensitive information filters
- **Multi-layer Security**: Combining Guardrails with CloudWatch Data Protection

## Cleanup

After completing tutorials:

1. Delete CloudWatch log groups created during examples
2. Remove any test resources
3. Clean up environment configuration files
