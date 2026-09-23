# Overview

Amazon Bedrock AgentCore Evaluations helps you optimize your agent's quality based on real-world interactions.

## Key Features

While AgentCore Observability provides operational insights into agent health, AgentCore Evaluations focuses on agent decision quality and performance outcomes.

It provides built-in and custom evaluators with both on-demand and online evaluation capabilities.

### Built-in and Custom Evaluators

AgentCore Evaluations offers 13 built-in evaluators for critical dimensions like correctness, helpfulness, and safety, plus the ability to create custom evaluators for business-specific requirements.

Test your agents during development and deployment using the on-demand evaluations API, or monitor production agents with the online evaluations API.

### On-demand Evaluations

Run synchronous, on-demand evaluations using built-in and custom metrics on individual traces.

The system uses OpenTelemetry (OTEL) traces to perform scoring and returns a response that includes:
- Score value
- Explanation for the score
- Token usage

Online Evaluations

In production, you need continuous performance monitoring across all interactions without manually evaluating each trace. A statistical sample is often sufficient for generating meaningful performance metrics.

AgentCore Evaluations' online capabilities enable automatic sampling and evaluation:

- Define your sample size and trace selection criteria
- Choose your evaluation metrics (built-in or custom)
- AgentCore Evaluations handles the rest, generating the performance data you need to monitor your agent at scale

## Tutorials overview

In these tutorials we will cover the following functionality:
- [Pre-requisites](00-prereqs): Creating a sample agent to use during the evaluation tutorials
- [Create a custom evaluator](01-creating-custom-evaluators): Learn about built-in and custom metrics, and create a custom metric for evaluating your agents
- [Using on-demand  and online evaluations](02-running-evaluations): Learn how to use on-demand and online evaluations to build, optimize, and monitor your agent at scale
- [Advanced](03-advanced): Explore advanced capabilities including using the boto3 SDK to query Amazon CloudWatch logs for on-demand evaluation, and creating local dashboards to visualize experiments with different agent configuration

