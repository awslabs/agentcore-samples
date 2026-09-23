# Okta Integration with Amazon Bedrock AgentCore

This repository contains comprehensive notebooks demonstrating how to integrate Okta with Amazon Bedrock AgentCore for various authentication and authorization scenarios.

## What is Okta?

Okta is a cloud-based identity and access management service that provides secure identity solutions for enterprises, enabling seamless authentication and authorization across applications and services.

### Key Features:
- **Single Sign-On (SSO)** - Users authenticate once to access multiple applications
- **Multi-Factor Authentication (MFA)** - Enhanced security through additional verification methods  
- **Adaptive Authentication** - Risk-based authentication policies based on user behavior and context
- **Universal Directory** - Centralized user management and profile synchronization
- **API Access Management** - OAuth 2.0 and OpenID Connect support for API security

### Integration with AgentCore

Okta can be used as an identity provider with AgentCore Identity to:
- Authenticate users before they can invoke agents (inbound authentication)
- Authorize agents to access protected resources on behalf of users (outbound authentication)
- Secure AgentCore Gateway endpoints with JWT-based authorization

## Example Notebooks Overview

This learning path includes practical notebooks that demonstrate different integration patterns:

### 1. Step by Step Okta for Inbound Auth.ipynb

**Purpose**: Shows how to use Okta for **inbound authentication** to protect AgentCore Runtime agent endpoints, ensuring only authenticated users can invoke agents.

**What you'll learn**:
- Setting up Okta tenant and application configuration
- Creating AgentCore OAuth2 credential providers
- Implementing OAuth 2.0 flows for user authentication and delegation
- Building and deploying agents on AgentCore Runtime with Okta integration
- Managing user sessions

**Key Integration Pattern**:
- Users must authenticate with Okta before accessing AgentCore Runtime agents endpoints
- Bearer tokens validate user identity on each request
- Agents remain protected behind authentication layer

## Support and Documentation

- [Okta Developer Documentation](https://developer.okta.com/)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [OAuth 2.0 and OpenID Connect](https://developer.okta.com/docs/concepts/oauth-openid/)

## Note

Okta is not an AWS service. Please refer to Okta documentation for costs and licensing related to Okta usage.
