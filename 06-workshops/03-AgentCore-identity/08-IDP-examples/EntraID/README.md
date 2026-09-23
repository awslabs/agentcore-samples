# Microsoft Entra ID Integration with Amazon Bedrock AgentCore

This repository contains three comprehensive notebooks demonstrating how to integrate Microsoft Entra ID (formerly Azure Active Directory) with Amazon Bedrock AgentCore for various authentication and authorization scenarios.

## What is Microsoft Entra ID?

Microsoft Entra ID is Microsoft's cloud-based identity and access management service that serves as the central identity provider for Microsoft 365, Azure, and other SaaS applications.

### Key Features:
- **Single Sign-On (SSO)** - Users authenticate once to access multiple applications
- **Multi-Factor Authentication (MFA)** - Enhanced security through additional verification methods  
- **Conditional Access** - Policy-based access control based on user, device, location, and risk
- **Application Integration** - Supports modern authentication protocols like OAuth 2.0, OpenID Connect, and SAML

### Integration with AgentCore


Microsoft Entra ID can be used as an identity provider with AgentCore Identity to:
- Authenticate users before they can invoke agents (inbound authentication)
- Authorize agents to access protected resources on behalf of users (outbound authentication)
- Secure AgentCore Gateway endpoints with JWT-based authorization

## Example Notebooks Overview

This learning path includes three practical notebooks that demonstrate different integration patterns:

### 1. Step By Step MS EntraID and 3LO Outbound for Tools.ipynb

**Purpose**: Demonstrates how to use Entra ID for **outbound authentication** where AgentCore Runtime deployed agents access external resources (Microsoft OneNote) on behalf of authenticated users.

**What you'll learn**:
- Setting up Entra ID tenant and application registration
- Creating AgentCore OAuth2 credential providers
- Implementing 3-legged OAuth (3LO) flow for user delegation
- Building agents and deploying on AgentCore Runtime to create and manage OneNote notebooks

**Key Integration Pattern**: 
- User authenticates with Entra ID
- AgentCore Runtime receives delegated permissions to access OneNote API
- AgentCore Runtime agent tools performs actions on user's behalf


**Tools Created**:
- `create_notebook` - Creates new OneNote notebooks
- `create_notebook_section` - Adds sections to notebooks  
- `add_content_to_notebook_section` - Creates pages with content

### 2. Step by Step Entra ID for Inbound Auth.ipynb

**Purpose**: Shows how to use Entra ID for **inbound authentication** to protect AgentCore Runtime agent endpoints, ensuring only authenticated users can invoke agents.

**What you'll learn**:
- Configuring custom JWT authorizers with Entra ID
- Using MSAL (Microsoft Authentication Library) for device code flow
- Protecting AgentCore Runtime endpoints with bearer tokens
- Managing session-based conversations with authenticated users

**Key Integration Pattern**:
- Users must authenticate with Entra ID before accessing AgentCore Runtime agents endpoints
- Bearer tokens validate user identity on each request
- Agents remain protected behind authentication layer


### 3. Step by Step Entra ID with AgentCore Gateway.ipynb

**Purpose**: Demonstrates using Entra ID to secure **AgentCore Gateway** endpoints with machine-to-machine (M2M) authentication using client credentials flow.

**What you'll learn**:
- Setting up Entra ID app roles for API protection
- Configuring AgentCore Gateway with custom JWT authorization
- Creating Lambda functions as MCP (Model Context Protocol) tools
- Using client credentials flow for service-to-service authentication

**Key Integration Pattern**:
- Applications authenticate using client credentials (no user interaction)
- Gateway validates JWT tokens against Entra ID
- Lambda functions exposed as standardized MCP tools



## Support and Documentation

- [Microsoft Entra ID Documentation](https://learn.microsoft.com/en-us/entra/)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [OAuth 2.0 Specification](https://oauth.net/2/)

## Note

Microsoft Entra ID is not an AWS service. Please refer to Microsoft Entra ID documentation for costs and licensing related to Entra ID usage.