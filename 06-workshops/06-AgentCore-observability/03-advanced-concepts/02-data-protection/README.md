# Amazon Bedrock AgentCore Observability: Data Protection

## Overview

In this tutorial, we will learn how to implement comprehensive data protection in agentic AI applications using Amazon Bedrock Guardrails and Amazon CloudWatch Logs Data Protection policies. This tutorial demonstrates how to protect sensitive data throughout the agent's lifecycle, from input processing to output generation and logging.

We will focus on creating a defense-in-depth strategy for securing your AI applications by combining multiple layers of protection that work in tandem to safeguard personally identifiable information (PII), financial data, health records, and other confidential information.

### Tutorial Details

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Observability & Security                                                         |
| Agent type          | Single                                                                           |
| Agentic Framework   | Strands Agents                                                                   |
| LLM model           | Anthropic Claude Haiku 4.5                                                     |
| Tutorial components | Data Protection, Bedrock Guardrails, CloudWatch Logs Data Protection           |
| Tutorial vertical   | Cross-vertical                                                                   |
| Example complexity  | Advanced                                                                         |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                                    |

### Tutorial Architecture

In this tutorial, we will demonstrate how to implement data protection mechanisms for agents deployed on AgentCore runtime. We'll use a customer support agent that processes sensitive information and show how to protect this data using multiple layers of security.

The example includes:
- A Strands Agent with customer support capabilities
- Amazon Bedrock Guardrails for content filtering
- CloudWatch Logs Data Protection for log masking
- Sensitive information detection and handling

### Tutorial Key Features

* **Multi-layered Data Protection**: Implementing Bedrock Guardrails and CloudWatch Logs Data Protection
* **Sensitive Information Detection**: Automatically detecting PII, financial data, and other confidential information
* **Agent Security**: Protecting sensitive data in agent interactions and traces
* **Compliance Support**: Meeting privacy regulations (GDPR, HIPAA, CCPA) requirements
* **Defense-in-Depth Strategy**: Creating comprehensive security for agentic AI applications

## What You'll Learn

In this hands-on tutorial, you'll explore:

- How to detect sensitive information in Agent interactions and CloudWatch Logs and Traces
- Amazon Bedrock Guardrails: How to configure sensitive information filters to prevent AI agents from processing or generating sensitive content
- CloudWatch Logs Data Protection: How to automatically detect and mask sensitive data in application logs
- AgentCore Integration: How to implement these protective measures within agentic workflows

## Why This Matters

Without proper safeguards, agentic AI systems can:

- Inadvertently expose sensitive customer data in responses or logs
- Process or retain information that violates privacy regulations
- Generate outputs containing PII that shouldn't be shared
- Create compliance and security vulnerabilities in your application infrastructure

## Files in this Tutorial

- `data_protection.ipynb` - Main tutorial notebook with step-by-step instructions
- `requirements.txt` - Python dependencies required for the tutorial
- `data/` - Sample data files including customer support conversation examples
- `images/` - Architecture diagrams and visual aids for the tutorial
