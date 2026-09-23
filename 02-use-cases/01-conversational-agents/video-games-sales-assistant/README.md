# Deploying a Conversational Data Analyst Assistant Solution with Amazon Bedrock AgentCore

> [!IMPORTANT]
> **🚀 Ready-to-Deploy Agent Web Application**: Use this reference solution to build agent-powered web applications across different industries. Adapt it to your business domain by adding custom agent tools for specific workflows. To accelerate development, use **[Kiro](https://kiro.dev/)** with its **[Powers](https://kiro.dev/powers/)** for Strands Agents SDK, Amazon Bedrock AgentCore, and AWS Amplify, along with the **[AWS CDK MCP Server](https://awslabs.github.io/mcp/servers/cdk-mcp-server)** for infrastructure guidance — so you can extend this solution **without starting from scratch**.

This solution provides a Generative AI application reference that allows users to interact with data through a natural language interface. The solution leverages **[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)**, a managed service that enables you to deploy, run, and scale custom agent applications, along with the **[Strands Agents SDK](https://strandsagents.com/)** to build an agent that connects to a PostgreSQL database, providing data analysis capabilities through a Next.js web application built with **[AWS Amplify Gen 2](https://docs.amplify.aws/)**.

<div align="center">
<img src="./images/data-analyst-assistant-agentcore-strands-agents-sdk.gif" alt="Conversational Data Analyst Assistant Solution with Amazon Bedrock AgentCore">
</div>

🤖 A Data Analyst Assistant offers an approach to data analysis that enables enterprises to interact with their structured data through natural language conversations rather than complex SQL queries. This kind of assistant provides an intuitive question-answering for data analysis conversations and can be improved by offering data visualizations to enhance the user experience.

✨ This solution enables users to:

- Ask questions about video game sales data in natural language
- Receive AI-generated responses based on SQL queries to a PostgreSQL database
- View query results in tabular format
- Explore data through automatically generated visualizations
- Get insights and analysis from the AI assistant

🚀 This reference solution can help you explore use cases like:

- Empower analysts with real-time business intelligence
- Provide quick answers to C-level executives for common business questions
- Unlock new revenue streams through data monetization (consumer behavior, audience segmentation)
- Optimize infrastructure through performance insights

## Solution Overview

The following architecture diagram illustrates a reference solution for a generative AI data analyst assistant that is built using Strands Agents SDK and powered by Amazon Bedrock. This assistant enables users to access structured data that is stored in a PostgreSQL database through a question-answering interface.

![Video Games Sales Assistant](./images/gen-ai-assistant-diagram.png)

> [!IMPORTANT]
> This sample application is meant for demo purposes and is not production ready. Please make sure to validate the code with your organizations security best practices.

### CDK Infrastructure Deployment

The AWS CDK stack deploys and configures the following managed services:

**Amazon Bedrock AgentCore Resources:**
- **[AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)**: Provides the managed execution environment with invocation endpoints (`/invocations`) and health monitoring (`/ping`) for your agent instances
- **[AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)**: A fully managed service that gives AI agents the ability to remember, learn, and evolve through interactions. Configured with:
  - **Short-term memory (STM)**: Event-based conversation history scoped per user and session, providing immediate conversational context within a session
  - **Long-term memory (LTM)**: A semantic "Facts" strategy that asynchronously extracts knowledge from conversations and stores it in per-user namespaces (`/facts/{actorId}`). Facts persist across sessions and are retrieved via vector similarity search, enabling the agent to recall insights from previous conversations
- **Observability**: Runtime application logs and memory extraction logs delivered to CloudWatch Logs, plus runtime traces to AWS X-Ray — all with 14-day retention

The AgentCore infrastructure handles all storage complexity and provides efficient retrieval without requiring developers to manage underlying infrastructure, ensuring continuity and traceability across agent interactions.

**Data Source and VPC Infrastructure:**
- **VPC with Public and Private Subnets**: Network isolation and security for database resources
- **Amazon Aurora Serverless v2 PostgreSQL**: Stores the video game sales data with RDS Data API integration
- **Amazon DynamoDB**: Stores raw query results for data analysis audit trails
- **AWS Secrets Manager**: Secure storage for database credentials (admin and read-only)
- **Amazon S3**: Import bucket for loading data into Aurora PostgreSQL

All configuration values (database ARNs, secret ARNs, model ID, etc.) are passed directly as environment variables to the AgentCore Runtime — no SSM Parameter Store required.

### Amplify Deployment for the Front-End Application

- **Next.js Web Application (Amplify Gen 2)**: Delivers the user interface for the assistant
    - Uses Amazon Cognito (via Amplify Gen 2) for user authentication and IAM permissions — no manual IAM configuration needed. Each authenticated user's Cognito `sub` is used as the `actorId` for memory, ensuring isolated per-user memory namespaces
    - The application invokes Amazon Bedrock AgentCore for interacting with the assistant (client-side streaming)
    - For chart generation, the application directly invokes the Claude Haiku 4.5 model (client-side)
    - DynamoDB query results are fetched through a Next.js API route (server-side)
    - A Memory Facts panel lets users view the long-term knowledge extracted from their conversations by AgentCore Memory

### Strands Agent Features

| Feature | Description |
|----------|----------|
| Native Tools   | current_time - A built-in Strands tool that provides the current date and time information based on user's timezone. |
| Custom Tools | get_tables_information - A custom tool that retrieves metadata about the database tables, including their structure, columns, and relationships, to help the agent understand the database schema.<br>execute_sql_query - A custom tool that allows the agent to run SQL queries against the PostgreSQL database based on the user's natural language questions, retrieving the requested data for analysis. |
| Model Provider | Amazon Bedrock |

> [!NOTE]
> The Next.js Web Application uses Amazon Cognito (deployed by Amplify Gen 2) for user authentication and permissions management, providing secure access to Amazon Bedrock AgentCore and Amazon DynamoDB services through authenticated user roles.

> [!TIP]
> You can also change the data source to connect to your preferred database engine by adapting the Agent's instructions and tool implementations.

> [!IMPORTANT] 
> Enhance AI safety and compliance by implementing **[Amazon Bedrock Guardrails](https://aws.amazon.com/bedrock/guardrails/)** for your AI applications with the seamless integration offered by **[Strands Agents SDK](https://strandsagents.com/latest/user-guide/safety-security/guardrails/)**.

The **user interaction workflow** operates as follows:

- The web application sends user business questions to the AgentCore Invoke (via client-side streaming)
- The Strands Agent (powered by Claude Haiku 4.5) processes natural language and determines when to execute database queries
- The agent's built-in tools execute SQL queries against the Aurora PostgreSQL database and formulate an answer to the question
- AgentCore Memory manages conversation context through the `AgentCoreMemorySessionManager` integration. STM provides conversation continuity within a session (scoped by `sessionId`), while LTM retrieves relevant facts from the `/facts/{actorId}` namespace across all past sessions for that user. LTM extraction is asynchronous (20-40 seconds after events are saved)
- After the agent's streaming response completes, the raw data query results are fetched from DynamoDB through a Next.js API route to display both the answer and the corresponding records
- For chart generation, the application invokes a model (powered by Claude Haiku 4.5) to analyze the agent's answer and raw data query results to generate the necessary data to render an appropriate chart visualization

## Deployment Instructions

The deployment consists of two main steps:

1. **Back-End Deployment - [Amazon Bedrock AgentCore and Data Source Deployment with CDK](./cdk-data-analyst-assistant-agentcore-strands/)**
2. **Front-End Implementation - [Integrating AgentCore with a Ready-to-Use Data Analyst Assistant Application (Next.js)](./amplify-video-games-sales-assistant-agentcore-strands/)**

> [!NOTE]
> *It is recommended to use the Oregon (us-west-2) or N. Virginia (us-east-1) regions to deploy the application.*

> [!IMPORTANT] 
> Remember to clean up resources after testing to avoid unnecessary costs by following the clean-up steps provided.

## Application Features

The following images showcase a conversational experience analysis that includes: natural language answers, the reasoning process used by the LLM to generate SQL queries, the database records retrieved from those queries, and the resulting chart visualizations.

- **AgentCore data analyst assistant welcome with Memory Facts access**

![Welcome screen with AgentCore branding](./images/preview.png)

- **Long-term Memory Facts from AgentCore Memory**

![Memory Facts panel with extracted knowledge](./images/preview1.png)

- **Conversational agent with tool use and reasoning**

![Agent conversation with SQL query execution](./images/preview2.png)

- **Raw query results in tabular format**

![Query results displayed as data table](./images/preview3.png)

- **Auto-generated chart from answer and data**

![Chart visualization from query results](./images/preview4.png)

- **Conversation summary and data analysis conclusion**

![Summary and conclusion of analysis conversation](./images/preview5.png)

## Thank You

## License

This project is licensed under the Apache-2.0 License.