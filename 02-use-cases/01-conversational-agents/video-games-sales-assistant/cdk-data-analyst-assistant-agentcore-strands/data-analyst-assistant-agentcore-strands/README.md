# Data Analyst Assistant - Strands Agent

A video games sales data analyst assistant built with the **[Strands Agents SDK](https://strandsagents.com/)** and powered by **[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)**.

## Overview

This agent provides an intelligent data analyst assistant specialized in video game sales analysis. It leverages Amazon Bedrock Claude models for natural language processing, Aurora Serverless PostgreSQL for data storage, and AgentCore Memory for conversation context management.

## Features

- Natural language to SQL query conversion
- Video game sales data analysis and insights
- Conversation memory and context awareness via AgentCore Memory
- Real-time streaming responses
- Comprehensive error handling and logging

## Agent Tools

| Tool | Type | Description |
|------|------|-------------|
| `get_tables_information` | Custom | Retrieves metadata about database tables, including structure, columns, and relationships |
| `execute_sql_query` | Custom | Executes SQL queries against the PostgreSQL database based on natural language questions |
| `current_time` | Native (Strands) | Provides current date and time information based on user's timezone |

## Project Structure

```
data-analyst-assistant-agentcore-strands/
├── app.py                 # Main application entry point
├── Dockerfile             # Container configuration for AgentCore Runtime
├── instructions.txt       # Agent system prompt and behavior configuration
├── requirements.txt       # Python dependencies
├── src/
│   ├── tools/             # Custom tool implementations
│   └── utils/             # Utility functions and helpers
└── resources/             # Additional resources
```

## Configuration

The agent uses the following environment variables:

| Variable | Description |
|----------|-------------|
| `PROJECT_ID` | Project identifier for SSM parameter retrieval |
| `MEMORY_ID` | AgentCore Memory ID for conversation context |
| `BEDROCK_MODEL_ID` | Bedrock model ID (default: `global.anthropic.claude-haiku-4-5-20251001-v1:0`) |

## Model Provider

- **Amazon Bedrock** with Claude Haiku 4.5 (default: `global.anthropic.claude-haiku-4-5-20251001-v1:0`)

## License

This project is licensed under the Apache-2.0 License.
