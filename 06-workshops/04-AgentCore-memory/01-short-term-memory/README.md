# AgentCore Memory: Short-Term Memory

## Overview

Short-term memory in Amazon Bedrock AgentCore provides immediate conversation context and session-based information management. It enables AI agents to maintain continuity within a single interaction or closely related sessions, ensuring coherent and contextually aware responses throughout a conversation.

## What is Short-Term Memory?

Short-term memory focuses on:

- **Session Continuity**: Maintaining context within a single conversation session
- **Immediate Context**: Preserving recent conversation history for coherent responses
- **Temporary State**: Managing transient information that's relevant for the current interaction
- **Conversation Flow**: Ensuring smooth transitions between topics within a session

## How Short-Term Memory Works in AgentCore

### Event Storage

AgentCore Memory stores complete conversation events in raw form, providing immediate access to:

- Last `k` User messages and agent responses
- Conversation metadata (timestamps, session IDs, actor IDs)
- Branching conversation paths for complex interactions

### Session Management

Short-term memory operates at the session level:

- Each conversation session maintains its own context
- Related sessions can share context through session grouping
- Automatic cleanup of expired session data (based on the configured TTL)

### Real-Time Access

Unlike long-term memory strategies that process in the background, short-term memory provides:

- Immediate retrieval of recent conversation history
- Conversation Continuation when a session discontinues or the agent fails.
- Real-time context updates as conversations progress
- Low-latency access to session-specific information

## Best Practices

1. **Context Window Management**: Monitor context usage to prevent overflow
2. **Session Boundaries**: Clearly define when sessions begin and end
3. **Memory Cleanup**: Implement appropriate cleanup policies for expired sessions
4. **Error Handling**: Handle memory retrieval failures gracefully
5. **Performance Optimization**: Use efficient querying patterns (e.g. via Summary Strategy in long term) for large conversation histories

## Integration with Frameworks

Short-term memory integrates seamlessly with popular agentic frameworks:

- **Strands Agent**: Native integration with conversation hooks
- **LangGraph**: State management integration
- **Custom Frameworks**: Direct API access for flexible implementation

## Available Sample Notebooks

Explore these hands-on examples to learn short-term memory implementation:

| Framework     | Use Case        | Description                                                                                            | Notebook                                                                                                                   | Architecture                                                           |
| ------------- | --------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Strands Agent | Personal Agent  | AI assistant that maintains conversation context and remembers user interactions within a session      | [personal-agent.ipynb](./01-single-agent/with-strands-agent/personal-agent.ipynb)                                          | [View](./01-single-agent/with-strands-agent/architecture.png)          |
| LangGraph     | Fitness Coach   | Personal fitness coach that tracks workout progress and maintains context throughout training sessions | [personal-fitness-coach.ipynb](./01-single-agent/with-langgraph-agent/personal-fitness-coach.ipynb)                        | [View](./01-single-agent/with-langgraph-agent/images/architecture.png) |
| LangGraph     | Support Agent   | Customer support agent with human-in-the-loop capabilities for complex issue resolution                | [support-agent-human-in-the-loop.ipynb](./01-single-agent/with-langgraph-agent/support-agent-human-in-the-loop.ipynb)      | [View](./01-single-agent/with-langgraph-agent/images/architecture.png) |
| LangGraph     | Math Agent      | Mathematical problem-solving agent that uses LangGraph checkpointing backed by AgentCore memory        | [math-agent-with-checkpointing.ipynb](./01-single-agent/with-langgraph-agent/math-agent-with-checkpointing.ipynb)          | [View](./01-single-agent/with-langgraph-agent/images/architecture.png) |
| Strands Agent | Travel Planning | Collaborative agents that share context while planning complex travel itineraries                      | [travel-planning-agent.ipynb](./02-multi-agent/with-strands-agent/travel-planning-agent.ipynb)                             | [View](./02-multi-agent/with-strands-agent/architecture.png)           |

## Getting Started

1. Choose a sample that matches your use case
2. Navigate to the sample folder
3. Install requirements: `pip install -r requirements.txt`
4. Open the Jupyter notebook and follow the step-by-step implementation

## Next Steps

Once you're comfortable with short-term memory, explore [Long-Term Memory](../02-long-term-memory/) to learn about persistent memory strategies that work across multiple conversations and sessions.
