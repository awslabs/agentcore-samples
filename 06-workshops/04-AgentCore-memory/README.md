# Amazon Bedrock AgentCore Memory

## Overview

Memory is a critical component of Agent intelligence. Large Language Models (LLMs) lack persistent memory across conversations. Amazon Bedrock AgentCore Memory addresses this by providing a managed service that enables AI agents to maintain relevant context across sessions, deliver personalized experiences and help the agent to learn over time.

## Key Capabilities

- **Core Infrastructure**: Serverless setup with built-in encryption and observability
- **Event Storage**: Raw event storage (conversation history/checkpointing) with branching support
- **Strategy Management**: Configurable extraction strategies (SEMANTIC, SUMMARY, USER_PREFERENCES, EPISODIC, SELF_MANAGED)
- **Memory Records Extraction**: Automatic extraction of facts, preferences, and summaries based on configured strategies
- **Semantic Search**: Vector-based retrieval of relevant memories using natural language queries

## How AgentCore Memory Works

![high_level_workflow](./images/high_level_memory.png)

AgentCore Memory operates on two levels:

### Short-Term Memory

Immediate conversation context and session-based information that provides continuity within a single interaction or closely related sessions.

### Long-Term Memory

Persistent information extracted and stored across multiple conversations, including facts, preferences, and summaries that enable personalized experiences over time.

## Memory Architecture

1. **Conversation Storage**: Complete conversations are saved in raw form for immediate access
2. **Strategy Processing**: Configured strategies automatically analyze conversations in the background
3. **Information Extraction**: Important data is extracted based on strategy types (typically takes ~1 minute)
4. **Organized Storage**: Extracted information is stored in structured namespaces for efficient retrieval
5. **Semantic Retrieval**: Natural language queries can retrieve relevant memories using vector similarity

## Memory Strategy Types

AgentCore Memory supports five strategy types:

- **Semantic Memory**: Stores factual information using vector embeddings for similarity search
- **Summary Memory**: Creates and maintains conversation summaries for context preservation
- **User Preference Memory**: Tracks user-specific preferences and settings
- **Episodic Memory**: Captures meaningful interaction sequences with automatic episode detection, consolidation, and reflection generation
- **Self-managed Memory**: Allows customization of extraction and consolidation logic

## Folder Structure

```
04-AgentCore-memory/
├── 01-short-term-memory/          # Session-based memory and context management
│   ├── 01-single-agent/
│   │   ├── with-strands-agent/    # Strands SDK examples + checkpointing
│   │   ├── with-langgraph-agent/  # LangGraph examples + checkpointing + human-in-the-loop
│   │   └── with-llamaindex-agent/ # LlamaIndex examples across multiple domains
│   └── 02-multi-agent/
│       └── with-strands-agent/    # Multi-agent travel planning
├── 02-long-term-memory/           # Persistent memory across conversations
│   ├── 01-single-agent/
│   │   ├── using-strands-agent-hooks/         # Strands lifecycle hooks integration
│   │   ├── using-strands-agent-memory-tool/   # Strands memory tool integration
│   │   ├── using-langgraph-agent-hooks/       # LangGraph hooks integration
│   │   └── using-llamaindex-agent-memory-tool/ # LlamaIndex memory tool integration
│   └── 02-multi-agent/
│       └── with-strands-agent/    # Multi-agent travel booking + healthcare
├── 03-advanced-patterns/          # Advanced integrations and tooling
│   ├── 01-guardrails-integration/ # Memory with Amazon Bedrock Guardrails
│   ├── 02-memory-runtime-integration/          # Memory + AgentCore Runtime
│   ├── 03-memory-identity-runtime-integration/ # Memory + Identity + Runtime
│   ├── 04-memory-browser/         # Web UI for browsing memory stores
│   └── 05-memory-streaming/       # Streaming memory record extraction
├── 04-memory-branching/           # Conversation branching and parallel execution
└── 05-memory-security-patterns/   # IAM policies and Cognito identity integration
    ├── 01-memory-iam-policies/
    └── 02-memory-iam-cognito-identities/
```

## Sample Notebooks

### Short-Term Memory

| Framework  | Agent Type | Use Case                          | Notebook                                                                                                                                                         |
| ---------- | ---------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Strands    | Single     | Personal Agent                    | [personal-agent.ipynb](./01-short-term-memory/01-single-agent/with-strands-agent/personal-agent.ipynb)                                                           |
| Strands    | Single     | Personal Agent (Memory Manager)   | [personal-agent-memory-manager.ipynb](./01-short-term-memory/01-single-agent/with-strands-agent/personal-agent-memory-manager.ipynb)                             |
| LangGraph  | Single     | Personal Fitness Coach            | [personal-fitness-coach.ipynb](./01-short-term-memory/01-single-agent/with-langgraph-agent/personal-fitness-coach.ipynb)                                         |
| LangGraph  | Single     | Math Agent with Checkpointing     | [math-agent-with-checkpointing.ipynb](./01-short-term-memory/01-single-agent/with-langgraph-agent/math-agent-with-checkpointing.ipynb)                           |
| LangGraph  | Single     | Support Agent (Human-in-the-Loop) | [support-agent-human-in-the-loop.ipynb](./01-short-term-memory/01-single-agent/with-langgraph-agent/support-agent-human-in-the-loop.ipynb)                       |
| LlamaIndex | Single     | Academic Research Assistant       | [academic-research-assistant.ipynb](./01-short-term-memory/01-single-agent/with-llamaindex-agent/academic-research-assistant-short-term-memory-tutorial.ipynb)   |
| LlamaIndex | Single     | Investment Portfolio Advisor      | [investment-portfolio-advisor.ipynb](./01-short-term-memory/01-single-agent/with-llamaindex-agent/investment-portfolio-advisor-short-term-memory-tutorial.ipynb) |
| LlamaIndex | Single     | Legal Document Analyzer           | [legal-document-analyzer.ipynb](./01-short-term-memory/01-single-agent/with-llamaindex-agent/legal-document-analyzer-short-term-memory-tutorial.ipynb)           |
| LlamaIndex | Single     | Medical Knowledge Assistant       | [medical-knowledge-assistant.ipynb](./01-short-term-memory/01-single-agent/with-llamaindex-agent/medical-knowledge-assistant-short-term-memory-tutorial.ipynb)   |
| Strands    | Multi      | Travel Planning Agent             | [travel-planning-agent.ipynb](./01-short-term-memory/02-multi-agent/with-strands-agent/travel-planning-agent.ipynb)                                              |
| Strands    | Multi      | Travel Planning (Memory Manager)  | [travel-planning-agent-memory-manager.ipynb](./01-short-term-memory/02-multi-agent/with-strands-agent/travel-planning-agent-memory-manager.ipynb)                |

### Long-Term Memory

| Framework  | Agent Type | Integration | Use Case                                      | Notebook                                                                                                                                                                                                     |
| ---------- | ---------- | ----------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Strands    | Single     | Hooks       | Customer Support (Built-in Strategy)          | [customer-support-inbuilt-strategy.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/customer-support/customer-support-inbuilt-strategy.ipynb)                                          |
| Strands    | Single     | Hooks       | Customer Support (Override Strategy)          | [customer-support-override-strategy.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/customer-support/customer-support-override-strategy.ipynb)                                        |
| Strands    | Single     | Hooks       | Math Assistant                                | [math-assistant.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/simple-math-assistant/math-assistant.ipynb)                                                                           |
| Strands    | Single     | Hooks       | Meeting Notes (Episodic)                      | [meeting-notes-assistant.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/meeting-notes-assistant-using-episodic/meeting-notes-assistant.ipynb)                                        |
| Strands    | Single     | Hooks       | Culinary Assistant (Self-Managed)             | [agentcore_self_managed_memory_demo.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/culinary-assistant-self-managed-strategy/agentcore_self_managed_memory_demo.ipynb)                |
| Strands    | Single     | Hooks       | Culinary Assistant (Self-Managed + Citations) | [agentcore_self_managed_memory_demo.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-hooks/culinary-assistant-self-managed-strategy-with-citations/agentcore_self_managed_memory_demo.ipynb) |
| Strands    | Single     | Memory Tool | Culinary Assistant                            | [culinary-assistant.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-memory-tool/culinary-assistant.ipynb)                                                                                   |
| Strands    | Single     | Memory Tool | Debugging Assistant (Episodic)                | [debugging_assistant_episodic_memory.ipynb](./02-long-term-memory/01-single-agent/using-strands-agent-memory-tool/debugging-agent/debugging_assistant_episodic_memory.ipynb)                                 |
| LangGraph  | Single     | Hooks       | Nutrition Assistant (User Preferences)        | [nutrition-assistant-with-user-preference-saving.ipynb](./02-long-term-memory/01-single-agent/using-langgraph-agent-hooks/custom-user-preferences/nutrition-assistant-with-user-preference-saving.ipynb)     |
| LangGraph  | Single     | Hooks       | Nutrition Assistant (Episodic)                | [nutrition-assistant-with-episodic-memory.ipynb](./02-long-term-memory/01-single-agent/using-langgraph-agent-hooks/episodic-memory/nutrition-assistant-with-episodic-memory.ipynb)                           |
| LlamaIndex | Single     | Memory Tool | Academic Research Assistant                   | [academic-research-assistant.ipynb](./02-long-term-memory/01-single-agent/using-llamaindex-agent-memory-tool/academic-research-assistant-long-term-memory-tutorial.ipynb)                                    |
| LlamaIndex | Single     | Memory Tool | Investment Portfolio Advisor                  | [investment-portfolio-advisor.ipynb](./02-long-term-memory/01-single-agent/using-llamaindex-agent-memory-tool/investment-portfolio-advisor-long-term-memory-tutorial.ipynb)                                  |
| LlamaIndex | Single     | Memory Tool | Legal Document Analyzer                       | [legal-document-analyzer.ipynb](./02-long-term-memory/01-single-agent/using-llamaindex-agent-memory-tool/legal-document-analyzer-long-term-memory-tutorial.ipynb)                                            |
| LlamaIndex | Single     | Memory Tool | Medical Knowledge Assistant                   | [medical-knowledge-assistant.ipynb](./02-long-term-memory/01-single-agent/using-llamaindex-agent-memory-tool/medical-knowledge-assistant-long-term-memory-tutorial.ipynb)                                    |
| Strands    | Multi      | Hooks       | Travel Booking Assistant                      | [travel-booking-assistant.ipynb](./02-long-term-memory/02-multi-agent/with-strands-agent/travel-booking-agent/travel-booking-assistant.ipynb)                                                                |
| Strands    | Multi      | Hooks       | Healthcare Data Assistant (Episodic)          | [healthcare-data-assistant.ipynb](./02-long-term-memory/02-multi-agent/with-strands-agent/healthcare-assistant-using-episodic/healthcare-data-assistant.ipynb)                                               |

### Advanced Patterns

| Pattern                     | Description                                        | Notebook                                                                                                                                             |
| --------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guardrails Integration      | Combine memory with Amazon Bedrock Guardrails      | [guardrails-memory.ipynb](./03-advanced-patterns/01-guardrails-integration/guardrails-memory.ipynb)                                                  |
| Memory + Runtime            | Integrate memory with AgentCore Runtime            | [runtime_memory_integration.ipynb](./03-advanced-patterns/02-memory-runtime-integration/runtime_memory_integration.ipynb)                            |
| Memory + Identity + Runtime | Integrate memory, identity resolution, and runtime | [runtime_memory_identity_integration.ipynb](./03-advanced-patterns/03-memory-identity-runtime-integration/runtime_memory_identity_integration.ipynb) |
| Memory Browser              | Web UI for exploring and managing memory stores    | [README](./03-advanced-patterns/04-memory-browser/README.md)                                                                                         |
| Memory Streaming            | Stream memory record extraction results            | [memory_record_streaming.ipynb](./03-advanced-patterns/05-memory-streaming/memory_record_streaming.ipynb)                                            |

### Memory Branching

| Use Case                                      | Notebook                                                                                                                                       |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Travel Planning with Memory Branching         | [travel-planning-agent-with-memory-branching.ipynb](./04-memory-branching/travel-planning-agent-with-memory-branching.ipynb)                   |
| Multi-Agent Parallel Execution with Branching | [multi-agent-parallel-execution-with-memory-branching.ipynb](./04-memory-branching/multi-agent-parallel-execution-with-memory-branching.ipynb) |

### Memory Security Patterns

| Pattern                                | Notebook                                                                                                                                                                  |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| IAM Policies for Memory Access Control | [runtime_memory_identity_integration.ipynb](./05-memory-security-patterns/01-memory-iam-policies/runtime_memory_identity_integration.ipynb)                               |
| IAM + Cognito Federated Identities     | [runtime_memory_federated_identity_integration.ipynb](./05-memory-security-patterns/02-memory-iam-cognito-identities/runtime_memory_federated_identity_integration.ipynb) |

## Resources

- [Amazon Bedrock AgentCore Memory Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
- [Deep Dive Video](https://www.youtube.com/live/-N4v6-kJgwA)

## Prerequisites

- Python 3.10 or higher
- AWS account with Amazon Bedrock access
- Jupyter Notebook environment
- Required Python packages (see individual sample `requirements.txt` files)
