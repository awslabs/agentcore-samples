# AWS re:Invent 2025 AIML301: Build End-to-End SRE Usecase with Bedrock AgentCore

## Overview

This workshop demonstrates how Site Reliability Engineers (SREs) can leverage Amazon Bedrock AgentCore to automate incident response, from diagnostics through remediation to prevention.

**Workshop Scenario:** A CRM application deployed on AWS (EC2 + NGINX + DynamoDB) experiences faults. You will build a multi-agent system to diagnose issues, remediate them safely with approval workflows, and prevent recurrence through research and best practices.

## Learning Objectives

By completing this workshop, you will:

1. **Lab 1** - Verify prerequisites and set up a realistic CRM application stack with fault injection capabilities
2. **Lab 2** - Build a diagnostics agent that analyzes CloudWatch logs and metrics
3. **Lab 3a** - Create a remediation agent with approval workflows and code interpreter
4. **Lab 3b** - Implement fine-grained access control with custom Lambda interceptor
5. **Lab 4** - Implement a prevention agent using AgentCore Browser for research
6. **Lab 5** - Orchestrate all agents using a supervisor pattern with AgentCore Gateway and interactive Streamlit UI

## Quick Start

### Recommended Lab Flow

```
Lab-01 (Prerequisites & Infrastructure)
   ↓
Lab-02 (Diagnostics Agent)
   ↓
Lab-03a (Remediation Agent)
   ↓
Lab-03b (Fine-Grained Access Control)
   ↓
Lab-04 (Prevention Agent)
   ↓
Lab-05 (Multi-Agent Orchestration + Streamlit UI)
```

### Getting Started

1. **Download the workshop** to your local machine
2. **Open Jupyter Notebook/Lab** in the workshop directory
3. **Start with `Lab-01-prerequisites-infra.ipynb`** and run all sections
4. **Follow labs sequentially** through Lab-05
5. **Clean up resources** when done using the cleanup cells in Lab-05

**⏱️ Estimated Time:**
- Complete workshop (Labs 1-5): **2 hours**

**✨ Everything happens within the notebook - no terminal commands needed!**

## How It Works

### Everything Runs in Notebooks

- Open a notebook, run it from top to bottom
- All setup, configuration, and provisioning happens automatically
- No terminal commands needed
- Each notebook is self-contained
- Notebooks import helpers and utilities as needed

**Example of what happens inside a notebook:**
1. Install required Python packages via `pip install`
2. Configure AWS credentials and environment
3. Verify prerequisites
4. Provision AWS resources (EC2, DynamoDB, Lambda, etc.)
5. Implement and test agents
6. Inject faults for testing
7. Run diagnostics, remediation, or prevention workflows
8. Monitor results via CloudWatch
9. Clean up resources when done

## Architecture

The workshop implements a multi-agent system for automated incident response:

![Architecture Diagram](architecture/architecture.png)

**Key Components:**

1. **CRM Application Stack**
   - EC2 instances running NGINX web servers
   - DynamoDB for data persistence
   - CloudWatch for logs and metrics

2. **Agent System**
   - **Diagnostics Agent**: Analyzes CloudWatch logs and metrics to identify issues
   - **Remediation Agent**: Executes fixes using Code Interpreter with approval workflows
   - **Prevention Agent**: Researches best practices using Browser tool
   - **Supervisor Agent**: Orchestrates all agents and manages workflow

3. **AgentCore Platform**
   - **Runtime**: Serverless deployment for agents
   - **Gateway**: MCP protocol for tool orchestration with JWT authentication
   - **Code Interpreter**: Safe execution environment for remediation scripts
   - **Browser**: Web research capabilities for prevention
   - **Memory**: Context persistence across interactions

4. **Security & Access Control**
   - Cognito for user authentication
   - OAuth2 M2M for agent-to-agent communication
   - Lambda interceptor for fine-grained RBAC
   - JWT-based authorization

5. **User Interface**
   - Streamlit web app for interactive agent interaction
   - Real-time streaming responses
   - Approval workflow integration

## Demo Video

Watch the complete workshop walkthrough:

![Workshop Demo](demo/aim301-multi-agent-mcp-agentcore-gateway.gif)

The demo shows:
- Setting up the CRM application infrastructure
- Injecting faults to simulate real incidents
- Running diagnostics to identify issues
- Executing remediation with approval workflows
- Researching prevention strategies
- Orchestrating all agents through the Streamlit UI

## Workshop Structure

```
├── Lab-01-prerequisites-infra.ipynb             # Lab 1: Prerequisites & Infrastructure Setup
├── Lab-02-diagnostics-agent.ipynb               # Lab 2: Diagnostics Agent
├── Lab-03a-remediation-agent.ipynb              # Lab 3a: Remediation Agent + Approval
├── Lab-03b-remediation-agent-fgac.ipynb         # Lab 3b: Fine-Grained Access Control
├── Lab-04-prevention-agent.ipynb                # Lab 4: Prevention Agent
├── Lab-05-multi-agent-orchestration.ipynb       # Lab 5: Multi-Agent Orchestration + Streamlit
│
├── lab_helpers/                        # Helper modules imported by notebooks
│   ├── lab_01/                        # Lab 1 specific helpers
│   ├── lab_02/                        # Lab 2 specific helpers
│   ├── lab_03/                        # Lab 3 specific helpers
│   ├── lab_04/                        # Lab 4 specific helpers
│   ├── lab_05/                        # Lab 5 specific helpers (includes streamlit_app.py)
│   ├── constants.py                   # Configuration constants
│   ├── parameter_store.py             # AWS Parameter Store utilities
│   └── ...                            # Other shared utilities
├── requirements.txt                    # Python dependencies
└── README.md                           # This file
```

## Prerequisites

Before starting, ensure you have:

- Python 3.10 or higher
- Jupyter Notebook or JupyterLab installed
- AWS Account with permissions for EC2, DynamoDB, Lambda, CloudWatch, Bedrock
- AWS credentials configured locally (or will be set up in Lab 1)

The `Lab-01-prerequisites-infra.ipynb` notebook will verify all these and install any missing dependencies.

## Lab Overview

**Lab 1: Prerequisites & Infrastructure Setup**
- Verify Python version, AWS credentials, and dependencies
- Install workshop requirements and verify Bedrock access
- Deploy CRM application (EC2 + NGINX + DynamoDB)
- Set up Cognito for authentication
- Set up CloudWatch monitoring
- Create fault injection utilities

**Lab 2: Diagnostics Agent**
- Build Strands agent to analyze CloudWatch logs
- Deploy Lambda function with diagnostic tools
- Create AgentCore Gateway with MCP protocol
- Test agent against real application logs

**Lab 3a: Remediation Agent with Code Interpreter**
- Deploy agent to AgentCore Runtime
- Integrate AgentCore Code Interpreter for safe execution
- Implement OAuth2 M2M authentication
- Test remediation workflows

**Lab 3b: Fine-Grained Access Control**
- Create Lambda interceptor for request authorization
- Implement role-based access control (RBAC)
- Configure Cognito groups (Approvers vs SRE)
- Test access control with different user roles

**Lab 4: Prevention Agent with Browser**
- Deploy Runtime agent with AgentCore Browser tool
- Research AWS documentation and best practices
- Generate prevention playbooks
- OAuth2 M2M authentication

**Lab 5: Multi-Agent Orchestration with Streamlit**
- Create supervisor agent to coordinate all three agents
- Set up central AgentCore Gateway with JWT authentication
- Reuse Lab 3b interceptor for RBAC
- Deploy multi-agent system
- Launch interactive Streamlit chat interface with real-time streaming
- Test end-to-end incident response workflow

## Key Technologies

- **Amazon Bedrock** - Foundation models (Claude 3.7 Sonnet)
- **AgentCore** - Serverless agent platform
  - Runtime (deployment)
  - Memory (context persistence)
  - Gateway (tool orchestration with JWT authentication)
  - Code Interpreter (remediation execution)
  - Browser (research and documentation)
  - Observability (monitoring and tracing)
- **Strands Framework** - Agent framework for tool-use patterns with streaming support
- **Streamlit** - Interactive web UI for real-time agent interaction
- **AWS Services** - EC2, DynamoDB, CloudWatch, Lambda, IAM, Cognito, Bedrock
- **Jupyter Notebooks** - Interactive learning environment

## Project Files

### Lab Helpers (`lab_helpers/`)
Python modules that notebooks import for cleaner code:
- `lab_01/` - Infrastructure deployment and fault injection
- `lab_02/` - Lambda deployment, MCP client, gateway setup
- `lab_03/` - Runtime deployment, OAuth2 setup, interceptor
- `lab_04/` - Runtime deployment, gateway setup, logging
- `lab_05/` - Supervisor agent code, Streamlit app, IAM setup
- `constants.py` - Configuration constants and parameter paths
- `parameter_store.py` - AWS Parameter Store utilities
- `config.py` - Workshop configuration
- `cognito_setup.py` - Cognito user pool and client setup
- `short_term_memory.py` - AgentCore Memory integration

## Troubleshooting

**If something goes wrong:**
1. Check the notebook output for error messages
2. Verify AWS credentials in the error output
3. Ensure you're in the correct AWS region
4. Review CloudWatch logs directly from the notebook
5. Run prerequisite verification again

**Common issues:**
- Missing AWS credentials → Run `Lab-01-prerequisites-infra.ipynb` again
- Bedrock model not accessible → Ensure Bedrock is enabled in your region
- Lambda timeout → Check CloudWatch logs in notebook
- Resource already exists → Run cleanup notebook and retry

## After the Workshop

To apply what you've learned:

1. **In your own environment:**
   - Adapt agents to your monitoring systems
   - Integrate with your deployment pipeline
   - Connect to your incident management platform

2. **For production use:**
   - Deploy agents to AgentCore Runtime
   - Set up persistent memory for incident history
   - Enable observability and alerting
   - Establish team approval workflows

3. **Advanced capabilities:**
   - Multi-team orchestration
   - Cross-account incident response
   - Custom tool development
   - Third-party integrations

## Resources

- [Amazon Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [AgentCore Documentation](https://docs.aws.amazon.com/agentcore/)
- [Strands Framework GitHub](https://github.com/aws-samples/strands-agents)
- [AWS re:Invent 2025](https://reinvent.awsevents.com/)

## License

This workshop is provided as-is under the MIT License.