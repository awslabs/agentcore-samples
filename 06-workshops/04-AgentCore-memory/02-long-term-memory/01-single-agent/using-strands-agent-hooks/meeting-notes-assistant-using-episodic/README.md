# AgentCore Memory: Episodic Memory Strategy

| Information         | Details                                                      |
|:--------------------|:-------------------------------------------------------------|
| Tutorial type       | Long term Episodic                                           |
| Agent type          | Meeting Notes Assistant                                      |
| Agentic Framework   | Strands Agents                                               |
| LLM model           | Anthropic Claude Haiku 4.5                                   |
| Tutorial components | AgentCore Episodic Memory with Reflections, Hooks            |
| Example complexity  | Intermediate                                                 |

## Overview

Episodic memory captures meaningful slices of user and system interactions so applications can recall context in a way that feels focused and relevant. Instead of storing every raw event, it identifies important moments, summarizes them into compact records, and organizes them so the system can retrieve what matters without noise.

**Reflections** build on episodic records by analyzing past episodes to surface insights, patterns, and higher-level conclusions. They turn raw experience into guidance the application can use immediately.

## What is Episodic Memory?

Episodic memory provides:

- **Episode Detection**: Automatically identifies when meaningful interaction sequences complete
- **Structured Capture**: Records situation, intent, assessment, justification, and episode-level reflection
- **Cross-Episode Learning**: Generates reflections that identify patterns across multiple episodes
- **Contextual Retrieval**: Enables agents to learn from past experiences and avoid repeating mistakes

## How Episodic Memory Differs from Other Strategies

| Strategy | Focus | Best For |
|----------|-------|----------|
| **Semantic** | Facts and knowledge | Static information retrieval |
| **User Preference** | User settings and preferences | Personalization |
| **Summary** | Conversation condensation | Long conversation context |
| **Episodic** | Interaction sequences + reflections | Learning from experience |

Episodic memory is unique because it:
1. Captures the **sequence** of actions, not just facts
2. Generates **reflections** that identify patterns across episodes
3. Helps agents understand **why** certain approaches worked or failed

## When to Use Episodic Memory

Ideal use cases include:

- **Meeting assistants**: Track decisions, action items, and follow-ups across meetings
- **Customer support conversations**: Learn from successful resolution patterns
- **Agent-driven workflows**: Remember which tool combinations work best
- **Personal productivity tools**: Adapt to user working patterns over time
- **Project management**: Identify recurring blockers and successful strategies

## Strategy Steps

The episodic memory strategy includes three steps:

1. **Extraction**: Analyzes in-progress episode and determines if complete
2. **Consolidation**: Combines extractions into a single episode when complete
3. **Reflection**: Generates insights across multiple episodes

## Namespace Organization

Episodes and reflections are stored in configurable namespaces:

```python
# Store episodes at actor level (recommended for most use cases)
"namespaceTemplates": ["meetings/actor/{actorId}/episodes"]

# Reflections must be same as or prefix of episodic namespace
"reflectionConfiguration": {
    "namespaceTemplates": ["meetings/actor/{actorId}"]  # Prefix of episodes namespace
}
```

**Important**: The reflection namespace must be the same as or a prefix of the episodic namespace. For example, if episodes are at `meetings/actor/{actorId}/episodes`, reflections should be at `meetings/actor/{actorId}` (prefix).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Meeting Notes Assistant                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐     ┌──────────────────────────────────────────────────┐  │
│  │  Meeting     │     │              Strands Agent                        │  │
│  │ Participant  │────▶│  ┌─────────────────────────────────────────────┐  │  │
│  │              │     │  │           System Prompt                     │  │  │
│  │  "Let's      │     │  │  "You are a meeting assistant that tracks   │  │  │
│  │  discuss     │     │  │   decisions and action items..."            │  │  │
│  │  Q3 goals"   │     │  └─────────────────────────────────────────────┘  │  │
│  └──────────────┘     │                      │                            │  │
│                       │                      ▼                            │  │
│                       │  ┌─────────────────────────────────────────────┐  │  │
│                       │  │         EpisodicMemoryHooks                 │  │  │
│                       │  │  ┌───────────────┐  ┌───────────────────┐   │  │  │
│                       │  │  │ MessageAdded  │  │ AfterInvocation   │   │  │  │
│                       │  │  │    Hook       │  │      Hook         │   │  │  │
│                       │  │  │ (retrieve)    │  │ (save events)     │   │  │  │
│                       │  │  └───────┬───────┘  └─────────┬─────────┘   │  │  │
│                       │  └──────────┼────────────────────┼─────────────┘  │  │
│                       │             │                    │                │  │
│                       │  ┌──────────┴────────────────────┴─────────────┐  │  │
│                       │  │              Tools                          │  │  │
│                       │  │  capture_action | identify_decision |       │  │  │
│                       │  │  summarize_discussion | track_followup      │  │  │
│                       │  └─────────────────────────────────────────────┘  │  │
│                       └──────────────────────────────────────────────────┘  │
│                                          │                                   │
│                                          ▼                                   │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    AgentCore Memory Service                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                   Episodic Strategy                              │  │  │
│  │  │                                                                  │  │  │
│  │  │   ┌──────────────┐   ┌───────────────┐   ┌─────────────────┐   │  │  │
│  │  │   │  Extraction  │──▶│ Consolidation │──▶│   Reflection    │   │  │  │
│  │  │   │              │   │               │   │                 │   │  │  │
│  │  │   │ Detect when  │   │ Combine into  │   │ Generate cross- │   │  │  │
│  │  │   │ meeting ends │   │ single record │   │ meeting insights│   │  │  │
│  │  │   └──────────────┘   └───────────────┘   └─────────────────┘   │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                        │  │
│  │  ┌─────────────────────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │        Episodes             │  │         Reflections             │ │  │
│  │  │ /meetings/actor/{id}/episodes│  │/meetings/actor/{id}/reflections │ │  │
│  │  │                             │  │                                 │ │  │
│  │  │  • Meeting purpose          │  │  • Effective meeting patterns   │ │  │
│  │  │  • Key decisions made       │  │  • Action item completion rate  │ │  │
│  │  │  • Action items assigned    │  │  • Participant preferences      │ │  │
│  │  │  • Follow-up status         │  │  • Common blockers              │ │  │
│  │  └─────────────────────────────┘  └─────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Data Flow:
1. Meeting participant discusses topics
2. MessageAdded hook retrieves relevant past meeting episodes & reflections
3. Agent processes discussion with historical context
4. Agent uses tools (capture_action, identify_decision, summarize_discussion, track_followup)
5. AfterInvocation hook saves interaction as event
6. AgentCore extracts episodes when meeting completes (~1 min)
7. Reflections generated across multiple meetings (background)
```

## Available Sample Notebooks

| Framework | Use Case | Description | Notebook |
|-----------|----------|-------------|----------|
| Strands Agent | Meeting Notes | Meeting assistant that tracks decisions, action items, and learns from past meetings | [meeting-notes-assistant.ipynb](./meeting-notes-assistant.ipynb) |

## Getting Started

1. Navigate to this folder
2. Install requirements: `pip install -r requirements.txt`
3. Open the Jupyter notebook and follow the step-by-step implementation

## Sample Prompts

Try these meeting scenarios to test episodic memory learning:

### 1. Follow-up on Previous Decision
**Prompt**: "Let's revisit the Q3 marketing budget we discussed last week"

**Expected Behavior**: Agent recalls past episode with budget discussion, retrieves previous decisions, and references context from that meeting.

### 2. Action Item Check
**Prompt**: "Did we assign someone to handle the website redesign?"

**Expected Behavior**: Agent retrieves past episodes where website redesign was discussed, identifies assigned action items and owner.

### 3. Recurring Meeting Pattern
**Prompt**: "We need to plan the weekly sprint review meeting"

**Expected Behavior**: Agent applies learned patterns from past sprint reviews (e.g., "Team prefers 30-min format" or "Always include demo time").

### 4. New Meeting with Context
**Prompt**: "Let's have a quick sync about the product launch timeline. We need to finalize dates."

**Expected Behavior**: Multi-step meeting facilitation using tools to capture decisions, identify action items, and track follow-ups.

### 5. Participant Preference Recognition
**Prompt**: "Sarah wants to discuss the technical architecture for the new feature"

**Expected Behavior**: Agent recognizes Sarah's preferences from past meetings (e.g., "Sarah prefers detailed diagrams" or "Technical meetings with Sarah typically need 1 hour").

### 6. New Topic
**Prompt**: "We need to discuss the company's sustainability initiative for the first time"

**Expected Behavior**: Agent acknowledges this is a new topic with no past episodes, provides general meeting structure, captures decisions and action items for future reference.

## Key Concepts

### Episodes vs Reflections

**Episodes** capture individual interaction sequences:
- A project planning meeting where decisions were made
- A sprint retrospective with action items assigned
- A budget review discussion with specific outcomes

**Reflections** analyze patterns across episodes:
- Which meeting formats work best for different teams
- Common blockers that repeatedly surface
- Action item completion rates by team member
- Participant communication preferences

### Retrieval Best Practices

1. **Query by intent**: Episodes are indexed by "intent", reflections by "use case"
2. **Include tool results**: When creating events, include `TOOL` results for optimal extraction
3. **Use reflections proactively**: Query reflections at task start to avoid known pitfalls
4. **Linearize successful episodes**: Feed successful episode turns to focus the agent

## Next Steps

After mastering episodic memory:
- Combine with semantic memory for comprehensive agent experiences
- Implement cross-agent reflection sharing for team learning
- Build feedback loops to improve episode detection

## Troubleshooting

### Episodes Not Appearing
**Issue**: No episodes found after running tests

**Solution**: Episode extraction takes approximately 1 minute after a conversation completes. Wait and retry retrieval. Episodes are extracted asynchronously in the background.

### Permission Errors
**Issue**: `AccessDeniedException` when creating memory or saving events

**Solution**: Ensure your AWS credentials have the necessary permissions:
- Policy: `BedrockAgentCoreFullAccess` (managed policy)
- Or custom policy with `bedrock-agentcore:*` permissions

### Model Access Errors
**Issue**: Cannot access Claude Haiku 4.5 model

**Solution**: Enable model access in the AWS Bedrock console:
1. Navigate to AWS Console → Bedrock → Model access
2. Request access for "Anthropic Claude Haiku 4.5"
3. Wait for approval (usually instant for standard models)

### Empty Reflection Results
**Issue**: Reflections namespace returns no results

**Solution**: Reflections are generated after multiple episodes are collected. Run additional meeting sessions with varied scenarios to accumulate episodes. Reflection generation happens in the background and may take several minutes.

### Memory Creation Fails with "Already Exists"
**Issue**: Memory resource with same name already exists

**Solution**: The code handles this automatically by reusing the existing memory. If you want to start fresh, delete the old memory first using `client.delete_memory_and_wait(memory_id=memory_id)`.

## Clean Up

After completing the tutorial, delete the memory resource to avoid ongoing charges:

```python
try:
    client.delete_memory_and_wait(memory_id=memory_id)
    print(f"✅ Deleted memory resource: {memory_id}")
except Exception as e:
    print(f"❌ Error deleting memory: {e}")
```

**Note**: This permanently deletes all episodes and reflections stored for this memory resource. Make sure to export any data you want to keep before deletion.

**Cost Considerations**: AgentCore Memory pricing is based on storage and retrieval. Regular cleanup of development/test memory resources helps control costs.
