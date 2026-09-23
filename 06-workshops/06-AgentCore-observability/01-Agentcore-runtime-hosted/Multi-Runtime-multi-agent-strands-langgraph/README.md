# Multi-Agent Systems with Observability

## Overview

This tutorial demonstrates how to build **multi-agent systems** with full observability using Amazon Bedrock AgentCore Runtime and Observability. You'll learn two patterns for coordinating multiple agents while maintaining end-to-end tracing through CloudWatch GenAI Observability.

###  Patterns

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         MULTI-AGENT Patterns                              │
├─────────────────────────────────┬─────────────────────────────────────────┤
│     PART 1: SINGLE RUNTIME      │      PART 2: MULTI-RUNTIME              │
│                                 │                                         │
│  ┌───────────────────────────┐  │  ┌───────────────────────────────────┐  │
│  │   AgentCore Runtime       │  │  │      ORCHESTRATOR (Strands)       │  │
│  │                           │  │  │      AgentCore Runtime #1         │  │
│  │  ┌─────────────────────┐  │  │  └──────────────┬────────────────────┘  │
│  │  │    ORCHESTRATOR     │  │  │                 │                       │
│  │  │      (Strands)      │  │  │         ┌──────┴──────┐                 │
│  │  │         │           │  │  │         ▼             ▼                 │
│  │  │    ┌────┴────┐      │  │  │  ┌────────────┐ ┌────────────┐          │
│  │  │    ▼         ▼      │  │  │  │  TRAVEL    │ │  WEATHER   │          │
│  │  │ TRAVEL    WEATHER   │  │  │  │  (Strands) │ │ (LangGraph)│          │
│  │  │(Strands)  (Strands) │  │  │  │ Runtime #2 │ │ Runtime #3 │          │
│  │  └─────────────────────┘  │  │  └────────────┘ └────────────┘          │
│  └───────────────────────────┘  │                                         │
│                                 │                                         │
│  - Single unified trace         │  - Linked traces via session ID         │
│  - Simple deployment            │  - Mix frameworks (Strands + LangGraph  │
└─────────────────────────────────┴─────────────────────────────────────────┘
```


## Prerequisites

1. AWS CLI configured (`aws configure`) with required permissions 
2. Amazon Bedrock model access enabled for `global.anthropic.claude-haiku-4-5-20251001-v1:0`
3. CloudWatch Transaction Search enabled ([Setup Guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html))

## Project Structure

```
03-multi-runtimes-with-observability/
├── multi_agent_observability.ipynb   # Main tutorial notebook
├── utils.py                          # Helper functions
├── requirements.txt                  # Dependencies
│
├── single_runtime/                   # Part 1: All agents in one runtime
│   ├── multi_agent.py               # Orchestrator + Travel + Weather agents
│   └── requirements.txt
│
├── travel_agent/                     # Part 2: Strands-based travel agent
│   ├── main.py                      # Web search capabilities
│   └── requirements.txt
│
├── weather_agent/                    # Part 2: LangGraph-based weather agent
│   ├── main.py                      # Weather lookup capabilities
│   └── requirements.txt
│
└── orchestrator_agent/               # Part 2: Coordinator agent
    ├── main.py                      # Routes queries to sub-agents
    └── requirements.txt
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Launch the tutorial notebook
jupyter notebook multi_agent_observability.ipynb
```

## Part 1: Single Runtime Architecture

All agents run in a single AgentCore Runtime with direct function calls between them.

```
┌─────────────────────────────────────────────────────────────┐
│                   AgentCore Runtime                          │
│                                                             │
│    User Query ──► ORCHESTRATOR                              │
│                        │                                    │
│                   ┌────┴────┐                               │
│                   ▼         ▼                               │
│              TRAVEL      WEATHER                            │
│              AGENT       AGENT                              │
│                │           │                                │
│                ▼           ▼                                │
│           web_search   get_weather                          │
│                                                             │
│    Telemetry:  CloudWatch GenAI Observability Dashboard     │
└─────────────────────────────────────────────────────────────┘
```

**Key Points:**
- Single deployment, single IAM role
- Unified trace tree in CloudWatch
- Best for: tightly coupled agents, single team ownership

## Part 2: Multi-Runtime Architecture

Each agent runs in its own AgentCore Runtime, communicating via direct invocation with session ID propagation.

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                              │
│                 AgentCore Runtime #1                         │
│                   (Strands Agent)                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
            invoke_agent_runtime() + session_id
                           │
           ┌───────────────┴───────────────┐
           ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│     TRAVEL AGENT        │     │     WEATHER AGENT       │
│  AgentCore Runtime #2   │     │  AgentCore Runtime #3   │
│     (Strands)           │     │     (LangGraph)         │
│                         │     │                         │
│  Tool: web_search       │     │  Tool: get_weather      │
│  (DuckDuckGo)           │     │  (Mock data)            │
└─────────────────────────┘     └─────────────────────────┘
           │                               │
           └───────────────┬───────────────┘
                           ▼
              CloudWatch GenAI Observability
                   (Linked Traces)
```

**Key Points:**
- Different frameworks per agent (Strands + LangGraph)
- Traces linked


## View Traces

After running the agents, view Traces, Sessions, logs, vended logs, and metrics in CloudWatch Dashboards and monitor the logs and metrics on Cloudwatch.


## Next Steps

After completing this tutorial, explore [Multi runtimes with A2A](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/02-use-cases/A2A-multi-agent-incident-response)


