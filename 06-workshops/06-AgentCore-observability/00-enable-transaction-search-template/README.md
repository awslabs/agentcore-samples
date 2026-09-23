# Enable Transaction Search for Amazon Bedrock AgentCore Observability

This tutorial demonstrates how to enable Amazon CloudWatch Transaction Search for AgentCore observability. Transaction Search provides an interactive analytics experience for complete visibility of your application transaction spans and traces across distributed systems.

## Getting Started

The Project folder has the following:

- A Jupyter notebook demonstrating how to enable Transaction Search using CloudFormation
- A CloudFormation template (transaction_search.yml) for automated deployment
- Sample images showing before and after Transaction Search enablement

## Cleanup

After completing the tutorial:

1. Delete the CloudFormation stack: `transaction-search`
2. This removes the resource policy and disables Transaction Search
3. Existing traces and logs are retained according to retention policies
