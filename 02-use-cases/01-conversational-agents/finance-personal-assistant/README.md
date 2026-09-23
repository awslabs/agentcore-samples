# Use agent strategies to streamline complex business tasks

The [workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/57f577e3-9a24-45e2-9937-e48b2cdf6986/en-US) is a hands-on training program designed to empower developers and AI enthusiasts to build sophisticated, context-aware AI agents using cutting-edge technologies like [Amazon Bedrock](https://aws.amazon.com/bedrock), [Strands Agents](https://strandsagents.com/latest/), and [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/).

![architecture](./images/architecture.png)

We're creating a **Multi-Agent Financial Advisory System** that combines specialized expertise areas through intelligent orchestration. The system mimics how professional financial advisory firms operate, with specialists collaborating under a coordinator to provide comprehensive guidance.

Our multi-agent system consists of three core components:

1. **Budget Agent (from [Lab 1](./lab1-develop_a_personal_budget_assistant_strands_agent.ipynb))**

    *Specializes in personal budgeting, spending analysis, and financial discipline*

    | Tool | Description | Example Use Case |
    |------|-------------|------------------|
    | **calculate_budget_breakdown** | 50/30/20 budget calculations for any income level | "Create a budget for my $6000 monthly income" |
    | **analyze_spending_pattern** | Spending pattern analysis with personalized recommendations | "Analyze my $800 dining expenses against $5000 income" |
    | **calculator** | Financial calculations and mathematical operations | "Calculate 20% savings target for my budget" |

2. **Financial Analysis Agent (from [Lab 2](./lab2-build_multi_agent_workflows_with_strands.ipynb))**

    *Focuses on investment research, portfolio management, and market analysis*

    | Tool | Description | Example Use Case |
    |------|-------------|------------------|
    | **get_stock_analysis** | Real-time stock data and comprehensive analysis | "Analyze Apple stock performance and metrics" |
    | **create_diversified_portfolio** | Risk-based portfolio recommendations with allocations | "Create a moderate risk portfolio for $10,000" |
    | **compare_stock_performance** | Multi-stock performance comparison over time periods | "Compare Tesla, Apple, and Google over 6 months" |

3. **Orchestrator Agent (from [Lab 2](./lab2-build_multi_agent_workflows_with_strands.ipynb))**

    *Coordinates specialized agents and synthesizes comprehensive responses*

    | Capability | Description | Example Use Case |
    |------------|-------------|------------------|
    | **Agent Routing** | Intelligently determines which specialist(s) to consult | Routes budget questions to Budget Agent, investment queries to Financial Agent |
    | **Multi-Agent Coordination** | Combines insights from multiple agents for complex queries | "Help me budget and invest" uses both agents together |
    | **Response Synthesis** | Creates coherent responses from multiple agent outputs | Combines budget analysis with investment recommendations |
    | **Context Management** | Maintains conversation flow across agent interactions | Remembers previous advice when making follow-up recommendations |

## Workshop Structure

| Lab | Focus | Duration | What You'll Learn |
|-----|-------|----------|-------------------|
| Prerequisites | Environment Setup | 5 minutes | AWS account setup, SageMaker Studio configuration |
| [Lab 1](./lab1-develop_a_personal_budget_assistant_strands_agent.ipynb) | Personal Finance Assistant | 20 minutes | Build your first Strands agent, understand core concepts, create custom tools |
| [Lab 2](./lab2-build_multi_agent_workflows_with_strands.ipynb)| Multi-Agent Workflows | 20 minutes | Implement multi-agent workflows |
| [Lab 3](./lab3-deploy_agents_on_amazon_bedrock_agentcore.ipynb) | Production Deployment | 15 minutes | Deploy agents using Amazon Bedrock AgentCore, scaling and monitoring |

**Total Duration:** 1 hour

## Prerequisites

Participants should have:

- AWS credentials configured for Amazon Bedrock access
- Python 3.8+ installed on their development machine
- AWS account with Amazon Bedrock access. In an AWS-conducted event, you will have a provisioned account
- Enable [Model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-modify.html) for Claude Haiku 4.5 on Amazon Bedrock.
- Jupyter Notebook or compatible IDE

## Sample Queries

- "I make $6000/month and want to start investing $500/month. Help me create a budget and suggest an investment portfolio."
- "I spend too much on dining out ($800/month) and want to invest the savings. What should I do?"
- "Compare Tesla and Apple stocks, and tell me if I can afford to invest $2000 with my $4000 monthly income."

## Clean-up

Each lab consists of cleanup steps at the end of Jupyter notebooks. By default, these cleanup steps are commented out. Please uncomment them and run them to clean up resources at the end of the workshop.
