# !pip install -qr requirements.txt

import logging
import json
from typing import Dict
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("meeting-notes-assistant")

from strands import Agent, tool  # noqa: E402
from strands.hooks import (  # noqa: E402
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Configuration
REGION = "us-west-2"
PARTICIPANT_ID = "participant_001"
SESSION_ID = f"meeting_{datetime.now().strftime('%Y%m%d%H%M%S')}"

from bedrock_agentcore.memory import MemoryClient  # noqa: E402
from bedrock_agentcore.memory.constants import StrategyType  # noqa: E402

client = MemoryClient(region_name=REGION)
memory_name = "MeetingNotesEpisodicMemory"

# NOTE: although this tutorial is framed around "episodes" and "reflections", it is
# configured with a SEMANTIC strategy. True episodic memory requires an
# `episodicMemoryStrategy` with a `reflectionConfiguration` (and conversations that
# conclude so the service detects episode completion). See the reference at
# 02-long-term-memory/01-built-in-strategies/episodic.py.
# Define semantic memory strategy to capture key facts from meeting discussions
strategies = [
    {
        StrategyType.SEMANTIC.value: {
            "name": "MeetingSemanticMemory",
            "description": "Captures key decisions, action items, and facts from meeting discussions",
            "namespaces": ["meetings/actor/{actorId}/episodes"],
        }
    }
]

# Create (or reuse) the memory resource. create_or_get_memory is idempotent: it
# creates the memory (waiting for ACTIVE) or returns the existing one on a re-run,
# replacing the manual create/except/list-and-match.
memory = client.create_or_get_memory(
    name=memory_name,
    strategies=strategies,
    description="Memory for meeting notes assistant",
    event_expiry_days=180,  # TTL for short-term memory events (STM), not for long-term records
)
memory_id = memory["id"]
logger.info(f"✅ Memory ready: {memory_id}")

# Verify episodic strategy is configured
strategies = client.get_memory_strategies(memory_id)
print(json.dumps(strategies, indent=2, default=str))


@tool
def capture_action_item(task: str, owner: str, due_date: str) -> str:
    """Capture an action item from the meeting discussion.

    Args:
        task: Description of the task to be completed
        owner: Person responsible for completing the task
        due_date: When the task should be completed

    Returns:
        Confirmation of action item capture with details
    """
    action_items = {
        "website": "Website redesign - assigned to Sarah, due next Friday",
        "budget": "Review Q3 budget allocation - assigned to Mike, due this week",
        "presentation": "Prepare stakeholder presentation - assigned to Alex, due Monday",
        "testing": "Complete user testing for new feature - assigned to QA team, due end of sprint",
    }

    # Simulate action item storage
    for keyword, stored_item in action_items.items():
        if keyword in task.lower():
            return f"✅ ACTION ITEM CAPTURED:\n{stored_item}\n\nNote: {task}"

    return f"✅ ACTION ITEM CAPTURED:\nTask: {task}\nOwner: {owner}\nDue: {due_date}"


@tool
def identify_decision(decision: str, context: str) -> str:
    """Identify and record a key decision made during the meeting.

    Args:
        decision: The decision that was made
        context: Context or reasoning behind the decision

    Returns:
        Confirmation of decision recording with summary
    """
    decisions = {
        "budget": "Approved Q3 marketing budget increase of 15%",
        "launch": "Product launch date set for November 15th",
        "vendor": "Selected AWS as cloud infrastructure provider",
        "process": "Adopted agile sprint methodology for project management",
    }

    # Simulate decision recording
    for keyword, stored_decision in decisions.items():
        if keyword in decision.lower():
            return f"📌 DECISION RECORDED:\n{stored_decision}\n\nRationale: {context}"

    return f"📌 DECISION RECORDED:\n{decision}\n\nContext: {context}"


@tool
def summarize_discussion(topic: str, key_points: str) -> str:
    """Summarize a discussion topic with key points.

    Args:
        topic: The discussion topic
        key_points: Main points covered in the discussion

    Returns:
        Structured summary of the discussion
    """
    # Simulate discussion summarization
    return f"""📝 DISCUSSION SUMMARY:

Topic: {topic}

Key Points:
{key_points}

Next Steps: Review in next meeting"""


@tool
def track_followup(previous_item: str, status: str) -> str:
    """Track follow-up status of previous action items or decisions.

    Args:
        previous_item: Description of the item to follow up on
        status: Current status (completed, in-progress, blocked, pending)

    Returns:
        Follow-up status with details
    """
    # Simulate follow-up tracking
    statuses = {
        "completed": "✅ COMPLETED",
        "in-progress": "🔄 IN PROGRESS",
        "blocked": "🚫 BLOCKED",
        "pending": "⏳ PENDING",
    }

    status_emoji = statuses.get(status.lower(), "❓ UNKNOWN")

    return f"""{status_emoji}
Item: {previous_item}
Status: {status}
Last Updated: {datetime.now().strftime("%Y-%m-%d")}"""


logger.info("✅ Meeting management tools ready")


def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Get namespace mapping for memory strategies."""
    strategies = mem_client.get_memory_strategies(memory_id)
    result = {}
    for strategy in strategies:
        reflection_config = strategy.get("reflectionConfiguration", {})
        result[strategy["type"]] = {
            "namespaces": strategy.get("namespaces", []),
            "reflectionNamespaces": reflection_config.get("namespaces", []),
        }
    return result


class EpisodicMemoryHooks(HookProvider):
    """Memory hooks for episodic memory with reflections."""

    def __init__(self, memory_id: str, client: MemoryClient):
        self.memory_id = memory_id
        self.client = client
        self.namespaces = get_namespaces(self.client, self.memory_id)

    def retrieve_episodes_and_reflections(self, event: MessageAddedEvent):
        """Retrieve relevant episodes and reflections before processing."""
        messages = event.agent.messages
        if messages[-1]["role"] != "user" or "toolResult" in messages[-1]["content"][0]:
            return

        user_query = messages[-1]["content"][0]["text"]
        actor_id = event.agent.state.get("actor_id")

        if not actor_id:
            logger.warning("Missing actor_id in agent state")
            return

        try:
            all_context = []
            semantic_config = self.namespaces.get("SEMANTIC", {})

            # Retrieve relevant memories from semantic strategy namespaces
            for namespace_template in semantic_config.get("namespaces", []):
                namespace = namespace_template.format(actorId=actor_id)
                memories = self.client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace,
                    query=user_query,
                    top_k=3,
                )

                for memory in memories:
                    if isinstance(memory, dict):
                        content = memory.get("content", {})
                        if isinstance(content, dict):
                            text = content.get("text", "").strip()
                            if text:
                                all_context.append(f"[PAST MEETING] {text}")

            # Inject context into query
            if all_context:
                context_text = "\n".join(all_context)
                original_text = messages[-1]["content"][0]["text"]
                messages[-1]["content"][0]["text"] = (
                    f"Past Experience:\n{context_text}\n\nCurrent Query: {original_text}"
                )
                logger.info(f"Retrieved {len(all_context)} past meeting memories")

        except Exception as e:
            logger.error(f"Failed to retrieve episodes: {e}")

    def save_meeting_interaction(self, event: AfterInvocationEvent):
        """Save meeting interaction for episode extraction."""
        try:
            messages = event.agent.messages
            if len(messages) < 2 or messages[-1]["role"] != "assistant":
                return

            # Collect the full interaction including tool uses
            interaction_messages = []
            for msg in messages:
                role = msg["role"].upper()
                content = msg["content"]

                if isinstance(content, list):
                    for item in content:
                        if "text" in item:
                            interaction_messages.append((item["text"], role))
                        elif "toolUse" in item:
                            # Include tool usage for better episode extraction
                            tool_info = item["toolUse"]
                            tool_text = f"[TOOL: {tool_info.get('name', 'unknown')}]"
                            interaction_messages.append((tool_text, "TOOL"))
                        elif "toolResult" in item:
                            result = item["toolResult"].get("content", [{}])[0].get("text", "")
                            interaction_messages.append((f"[RESULT: {result[:200]}]", "TOOL"))

            if interaction_messages:
                actor_id = event.agent.state.get("actor_id")
                session_id = event.agent.state.get("session_id")

                if not actor_id or not session_id:
                    logger.warning("Missing actor_id or session_id")
                    return

                # Save event - AgentCore will automatically detect episode completion
                self.client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=interaction_messages,
                )
                logger.info("Saved meeting interaction for episode extraction")

        except Exception as e:
            logger.error(f"Failed to save interaction: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register episodic memory hooks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_episodes_and_reflections)
        registry.add_callback(AfterInvocationEvent, self.save_meeting_interaction)
        logger.info("Episodic memory hooks registered")


episodic_hooks = EpisodicMemoryHooks(memory_id, client)

meeting_agent = Agent(
    hooks=[episodic_hooks],
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[
        capture_action_item,
        identify_decision,
        summarize_discussion,
        track_followup,
    ],
    state={"actor_id": PARTICIPANT_ID, "session_id": SESSION_ID},
    system_prompt="""You are an expert meeting assistant with memory of past meetings.

Your role:
- Help facilitate productive meetings by tracking decisions and action items
- Use past meeting episodes to provide relevant context and history
- Apply reflections about what works well for different teams and participants
- Remember participant preferences and communication styles

When you see [PAST EPISODE] context, use it to inform your responses.
When you see [REFLECTION] context, apply those learned patterns.

Always:
1. Listen for key decisions and action items
2. Reference relevant past meetings when helpful
3. Track follow-ups on previous action items
4. Summarize discussions clearly and concisely""",
)

print("✅ Meeting notes agent created with episodic memory")

# Seed with previous meeting sessions
past_sessions = [
    # Session 1: Sprint planning meeting
    ("Let's plan the Q3 sprint. We need to prioritize features.", "USER"),
    ("I'll help capture the key decisions and action items.", "ASSISTANT"),
    (
        "We should focus on the user authentication feature first. It's blocking other work.",
        "USER",
    ),
    ("[TOOL: identify_decision]", "TOOL"),
    (
        "[RESULT: 📌 DECISION RECORDED: Prioritize user authentication feature for Q3 sprint]",
        "TOOL",
    ),
    (
        "Noted. User authentication is the priority. Who will lead this?",
        "ASSISTANT",
    ),
    (
        "Sarah can handle the authentication work. She has experience with OAuth.",
        "USER",
    ),
    ("[TOOL: capture_action_item]", "TOOL"),
    (
        "[RESULT: ✅ ACTION ITEM CAPTURED: Implement user authentication - Sarah - Due: End of sprint]",
        "TOOL",
    ),
    (
        "Perfect! I've captured that Sarah will implement user authentication by end of sprint.",
        "ASSISTANT",
    ),
    # Session 2: Budget review meeting
    (
        "We need to discuss the Q3 marketing budget. Costs are higher than expected.",
        "USER",
    ),
    ("Let me help track this discussion.", "ASSISTANT"),
    ("[TOOL: summarize_discussion]", "TOOL"),
    (
        "[RESULT: 📝 DISCUSSION SUMMARY: Q3 marketing budget - costs exceeding projections]",
        "TOOL",
    ),
    (
        "I propose we increase the budget by 15% to account for the new campaigns.",
        "USER",
    ),
    ("[TOOL: identify_decision]", "TOOL"),
    (
        "[RESULT: 📌 DECISION RECORDED: Approved Q3 marketing budget increase of 15%]",
        "TOOL",
    ),
    (
        "Decision captured. Is there a follow-up needed?",
        "ASSISTANT",
    ),
    ("Yes, Mike needs to update the financial forecast by end of week.", "USER"),
    ("[TOOL: capture_action_item]", "TOOL"),
    (
        "[RESULT: ✅ ACTION ITEM CAPTURED: Update financial forecast - Mike - Due: End of week]",
        "TOOL",
    ),
]

try:
    client.create_event(
        memory_id=memory_id,
        actor_id=PARTICIPANT_ID,
        session_id="seed_session_001",
        messages=past_sessions,
    )
    print("✅ Seeded past meeting episodes")
    print("⏳ Note: Episode extraction happens in background (~1 minute)")
    print("⏳ Note: Reflection extraction takes 10-15 minutes after episodes are stored")
except Exception as e:
    print(f"⚠️ Error seeding history: {e}")

# Test 1: Follow-up on previous decision - should reference past episode
response1 = meeting_agent("Let's revisit the Q3 sprint priorities we discussed last week. What was decided?")
print(f"Agent: {response1}")

# Test 2: Action item check - should retrieve past action items
response2 = meeting_agent("Did we assign someone to handle the user authentication feature?")
print(f"Agent: {response2}")

# Test 3: Budget follow-up - should reference past budget discussion
response3 = meeting_agent("What was the outcome of the Q3 marketing budget discussion?")
print(f"Agent: {response3}")

# Test 4: New meeting with multiple actions
response4 = meeting_agent("""We're having a product launch planning meeting. Key points:
- Launch date: November 15th
- Marketing team needs 2 weeks prep time
- Sarah will coordinate with vendors
- Mike needs to finalize pricing by next Friday

Can you capture the decisions and action items?""")
print(f"Agent: {response4}")

# Test 5: Pattern recognition - agent should remember participant preferences
response5 = meeting_agent("Sarah wants to discuss technical architecture for the new feature. What format works best?")
print(f"Agent: {response5}")

# Test 6: Completely new topic - no past context
response6 = meeting_agent(
    "We need to discuss the company's sustainability initiative for the first time. Let's brainstorm ideas."
)
print(f"Agent: {response6}")

print("\n📚 Semantic Memory Summary:")
print("=" * 50)

semantic_config = get_namespaces(client, memory_id).get("SEMANTIC", {})

# Check stored memories
for namespace_template in semantic_config.get("namespaces", []):
    namespace = namespace_template.format(actorId=PARTICIPANT_ID)

    try:
        memories = client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query="meeting decisions action items",
            top_k=5,
        )

        print(f"\nMEMORIES ({len(memories)} found):")
        for i, memory in enumerate(memories, 1):
            if isinstance(memory, dict):
                content = memory.get("content", {})
                if isinstance(content, dict):
                    text = content.get("text", "")[:200] + "..."
                    print(f"  {i}. {text}")

    except Exception as e:
        print(f"Error retrieving memories: {e}")

print("\n" + "=" * 50)

# Uncomment to delete the memory resource
# try:
#     client.delete_memory_and_wait(memory_id=memory_id)
#     print(f"✅ Deleted memory resource: {memory_id}")
# except Exception as e:
#     print(f"Error deleting memory: {e}")
