#!/usr/bin/env python

# # Debugging Assistant with Episodic Memory
#
# ## Overview
#
# This notebook demonstrates how to build an intelligent **Debugging Assistant** using **AgentCore Episodic Memory** with reflections. The agent learns from past debugging sessions and provides context-aware guidance based on historical experiences.
#
# ### What is Episodic Memory?
#
# **Episodic Memory** captures complete interaction sequences (episodes) with structured context. Unlike semantic memory which stores isolated facts, episodic memory preserves:
# - **Full conversation flows**: Complete debugging sessions from problem statement to resolution
# - **Temporal context**: The sequence and timing of actions taken
# - **Outcomes**: Whether the debugging attempt succeeded or failed
# - **Structured turns**: Individual steps with thoughts, actions, and observations
#
# ![Episodic memory](./episodic_memory.png)
#
# ### What are Reflections?
#
# **Reflections** are synthesized insights automatically extracted from multiple episodes. They provide:
# - **Pattern recognition**: Common issues and their solutions across similar episodes
# - **Best practices**: What strategies worked well in successful debugging sessions
# - **Common pitfalls**: Mistakes to avoid based on failed attempts
# - **Strategic guidance**: High-level advice for approaching similar problems
#
# **Output Structure:**
# - **Episodes**: Stored in `debugging/{actorId}/sessions/{sessionId}` - Full conversation traces
# - **Reflections**: Stored in `debugging/{actorId}` - Synthesized knowledge from multiple episodes
#
# ### When to Use Episodic Memory?
#
# Use episodic memory when:
# 1. **Sequential context matters**: The order of actions and their outcomes is important (e.g., debugging workflows, troubleshooting procedures)
# 2. **Learning from experience**: You want the agent to improve by analyzing past successes and failures
# 3. **Process retrieval**: Users need to recall "how did I solve X last time?" or "show me the exact steps taken"
#
# ### Tutorial Details
#
# | Information | Details |
# |:------------|:--------|
# | Tutorial type | Episodic Memory with Reflections |
# | Agent type | Debugging Assistant |
# | Framework | Strands Agents |
# | LLM model | Claude Haiku 4.5 |
# | Memory strategies | Episodic Memory with Reflection Configuration |
# | Complexity | Intermediate |
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions
# - Access to AgentCore services

# ## Step 1: Install Dependencies and Setup


import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Dict
from pprint import pprint

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("debugging-assistant")

# Import boto3 for control plane and data plane operations
import boto3  # noqa: E402

# Import Strands Agent framework
from strands import Agent, tool  # noqa: E402

logger.info("✅ All dependencies imported successfully")


import os  # noqa: E402

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")
# Session identifiers
ACTOR_ID = "developer"

logger.info(f"Configuration set for region: {REGION}")
logger.info(f"Actor ID: {ACTOR_ID}")


# ## Step 2: Create Memory with Episodic Strategy
#
# We'll create a memory resource configured with **Episodic Memory Strategy** that includes **Reflection Configuration**. This enables:
# - Storing complete debugging session episodes
# - Automatic generation of reflection insights from multiple episodes


# Initialize boto3 client for control and data plane operations
client = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
)
memory_client = boto3.client(
    "bedrock-agentcore-control",
    region_name=REGION,
)


# Define the memory strategy for debugging sessions.
# NOTE: although this tutorial is framed around "episodes" and "reflections", it is
# configured with a SEMANTIC strategy. True episodic memory requires an
# `episodicMemoryStrategy` with a `reflectionConfiguration` (and conversations that
# conclude so the service detects episode completion). See the reference at
# 02-long-term-memory/01-built-in-strategies/episodic.py.
memory_name = "DebugAssistantEpisodic"

episodic_strategy = {
    "semanticMemoryStrategy": {
        "name": "DebuggingEpisodeExtractor",
        "description": "Creates debugging session episodes per actor",
        "namespaces": ["debugging/{actorId}/sessions/{sessionId}"],
    }
}
logger.info(f"Strategy configured: {episodic_strategy['semanticMemoryStrategy']['name']}")
logger.info(f"Episode namespace: {episodic_strategy['semanticMemoryStrategy']['namespaces'][0]}")


# Get or create memory
try:
    # Try to find existing memory first
    list_response = memory_client.list_memories(maxResults=100)
    memory_id = None
    for mem in list_response.get("memories", []):
        detail = memory_client.get_memory(memoryId=mem["id"])
        if detail["memory"].get("name") == memory_name:
            memory_id = mem["id"]
            logger.info(f"✅ Using existing memory: {memory_id}")
            break

    # Create if not found
    if not memory_id:
        logger.info(f"Creating new memory: {memory_name}")
        response = memory_client.create_memory(
            name=memory_name,
            description="Episodic memory for debugging assistant with reflections",
            eventExpiryDuration=90,
            memoryStrategies=[episodic_strategy],
            clientToken=str(uuid.uuid4()),
        )
        memory_id = response["memory"]["id"]
        logger.info(f"✅ Memory created: {memory_id}")

        # Wait for ACTIVE
        import time

        for _ in range(30):
            status = memory_client.get_memory(memoryId=memory_id)["memory"]["status"]
            if status == "ACTIVE":
                logger.info("✅ Memory is ACTIVE")
                break
            time.sleep(10)

except Exception as e:
    logger.error(f"❌ Failed to get/create memory: {e}")
    raise


# ## Step 4: Hydrate Memory with Historical Debugging Sessions
#
# Let's load past debugging sessions into episodic memory. Each session represents a complete debugging workflow.


import os  # noqa: E402
import glob  # noqa: E402

# Load all session data files
data_dir = "./data"
session_files = sorted(glob.glob(f"{data_dir}/*.json"))

logger.info(f"Found {len(session_files)} session files to hydrate")

# Hydrate each session
for session_file in session_files:
    session_name = os.path.basename(session_file).replace(".json", "")
    session_id = f"{session_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    logger.info(f"Hydrating session: {session_name}")

    # Load conversation data
    with open(session_file, "r") as f:
        conversation = json.load(f)

    # Convert to payload format
    payload = []
    for turn in conversation:
        conv_data = turn["conversational"]
        payload.append(
            {
                "conversational": {
                    "content": {"text": conv_data["content"]["text"]},
                    "role": conv_data["role"],
                }
            }
        )

    # Create event using boto3 directly
    event_timestamp = datetime.now(timezone.utc)
    result = client.create_event(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=session_id,
        eventTimestamp=event_timestamp,
        payload=payload,
    )

    logger.info(f"   ✓ Stored {len(payload)} turns - Event ID: {result['event']['eventId']}")

logger.info(f"✅ Successfully hydrated {len(session_files)} debugging sessions")


### list memory records to see if its been extracted to LTM
import time  # noqa: E402
import pprint  # noqa: E402, F811

reflection_namespace = f"debugging/{ACTOR_ID}/"
# time.sleep(60)
# Use boto3 client directly to retrieve memory records
response = client.list_memory_records(memoryId=memory_id, namespace=reflection_namespace, maxResults=20)
memories = response.get("memoryRecordSummaries", [])
logger.info(f"   Found {len(memories)} memories")
if memories:
    text = memories[0]["content"].get("text", "")
    if text:
        try:
            pprint.pp(json.loads(text))
        except json.JSONDecodeError:
            pprint.pp(text)


# check if reflections and episodes have been generated or not.
import pprint  # noqa: E402

# Use boto3 client directly to retrieve memory records
response = client.retrieve_memory_records(
    memoryId=memory_id,
    namespace=f"debugging/{ACTOR_ID}/",
    searchCriteria={
        "searchQuery": "memory leaks",
        "metadataFilters": [
            {
                "left": {"metadataKey": "x-amz-agentcore-memory-recordType"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "REFLECTION"}},
            }
        ],
        "topK": 10,
    },
    maxResults=20,
)

reflections = response.get("memoryRecordSummaries", [])
logger.info(f"   Found {len(reflections)} relevant reflections")
if reflections:
    for reflection in reflections:
        text = reflection["content"].get("text", "")
        if text:
            try:
                reflection_json = json.loads(text)
                pprint.pp(reflection_json)
            except json.JSONDecodeError:
                pprint.pp(text)


# ## Step 5: Create Memory Retrieval Tools
#
# We'll create two specialized tools for the agent:
# 1. **retrieve_process**: Retrieves complete episode traces for detailed step-by-step processes
# 2. **retrieve_reflection_knowledge**: Retrieves synthesized insights and patterns from multiple episodes


def count_chars(text: str) -> int:
    """Character count for a text string (a rough size proxy, not a token count)."""
    return len(text)


def linearize_episodes(episodes: List[Dict], include_steps: bool = True, include_reflections: bool = True) -> str:
    """Linearize episode data into human-readable format."""
    if not episodes:
        return "No relevant episodes found."

    output = []
    for idx, episode in enumerate(episodes, 1):
        content = episode.get("content", {})

        # Parse JSON from text field
        if "text" in content:
            try:
                episode_data = json.loads(content["text"])
            except json.JSONDecodeError:
                output.append(f"Episode {idx}: Unable to parse content\n")
                continue
        else:
            output.append(f"Episode {idx}: No content available\n")
            continue

        output.append(f"{'=' * 80}\nEpisode {idx}\n{'=' * 80}")
        output.append(f"**Situation:** {episode_data.get('situation', 'N/A')}")
        output.append(f"**Intent:** {episode_data.get('intent', 'N/A')}")
        output.append(f"**Assessment:** {episode_data.get('assessment', 'N/A')}\n")

        if include_steps:
            turns = episode_data.get("turns", [])
            if turns:
                output.append("**Debugging Steps:**")
                for turn_idx, turn in enumerate(turns, 1):
                    output.append(f"\nStep {turn_idx}:")
                    output.append(f"  Situation: {turn.get('situation', 'N/A')}")
                    output.append(f"  Action: {turn.get('action', 'N/A')}")
                    output.append(f"  Thought: {turn.get('thought', 'N/A')}")

        if include_reflections:
            reflection = episode_data.get("reflection")
            if reflection:
                output.append(f"\n**Reflection:** {reflection}\n")

    result = "\n".join(output)
    logger.info(f"   Episode size: {count_chars(result)} chars")
    return result


def linearize_reflections(reflections: List[Dict]) -> str:
    """Linearize reflection knowledge into human-readable format."""
    if not reflections:
        return "No reflection knowledge found."

    output = []
    for idx, reflection in enumerate(reflections, 1):
        content = reflection.get("content", {})
        score = reflection.get("score", 0)

        # Parse JSON from text field
        if "text" in content:
            try:
                reflection_data = json.loads(content["text"])
            except json.JSONDecodeError:
                continue
        else:
            continue

        output.append(f"{'=' * 80}\nReflection {idx} (Relevance: {score:.2f})\n{'=' * 80}")
        output.append(f"**Title:** {reflection_data.get('title', 'Untitled')}")
        output.append(f"**Use Cases:** {reflection_data.get('use_cases', 'N/A')}")
        output.append(f"**Hints:** {reflection_data.get('hints', 'N/A')}\n")

    result = "\n".join(output)
    logger.info(f"   Reflection size: {count_chars(result)} chars")
    return result


logger.info("✅ Linearization helper functions created")


# Create memory retrieval tools for the agent


@tool
def retrieve_process(task: str, include_steps: bool = True) -> str:
    """
    Retrieve example processes to help solve the given task. Returns complete debugging
    episodes with configurable detail level.

    Use include_steps parameter to control verbosity:
    - Set include_steps=True when user asks for "exact steps", "full details", "how did we",
      "what steps did we take", or needs complete procedural information
    - Set include_steps=False for pattern/best practice queries where high-level context
      (situation, intent, assessment) is sufficient without step-by-step details

    Args:
        task: The task to solve that requires example processes
        include_steps: Whether to include detailed step-by-step turns (default: True)

    Returns:
        Formatted debugging episodes with optional detailed steps
    """
    logger.info(f"🔍 Retrieving processes for task: {task} (include_steps={include_steps})")

    try:
        # Search across ALL of this actor's sessions. `session_id` is only defined inside
        # the hydration loop (not in this tool's scope), and at query time we want episodes
        # from every past session — so search the actor-level prefix, which `namespace=`
        # matches by prefix (covers each "debugging/{ACTOR_ID}/sessions/<id>/" episode).
        namespace = f"debugging/{ACTOR_ID}/sessions/"

        # Use boto3 client directly to retrieve memory records
        response = client.retrieve_memory_records(
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": task, "topK": 3},
            maxResults=20,
        )

        episodes = response.get("memoryRecordSummaries", [])
        logger.info(f"   Found {len(episodes)} relevant episodes")

        # Linearize with configurable detail level
        return linearize_episodes(episodes, include_steps=include_steps, include_reflections=True)

    except Exception as e:
        logger.error(f"Error retrieving processes: {e}")
        return f"Error retrieving processes: {str(e)}"


@tool
def retrieve_reflection_knowledge(task: str, k: int = 5) -> str:
    """
    Retrieve synthesized reflection knowledge from past agent experiences. Each knowledge
    entry contains: (1) a descriptive title, (2) specific use cases for when to apply it,
    and (3) actionable hints including best practices from successful episodes and common
    pitfalls to avoid from failed episodes. Use this to get strategic guidance and patterns
    for similar tasks.

    Args:
        task: The current task to get strategic guidance for
        k: Number of reflection entries to retrieve (default: 5)

    Returns:
        Synthesized reflection knowledge from past debugging experiences
    """
    logger.info(f"🔍 Retrieving reflection knowledge for task: {task}")

    try:
        # Search in reflection namespace
        namespace = f"debugging/{ACTOR_ID}/"

        # Use boto3 client directly to retrieve memory records
        response = client.retrieve_memory_records(
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={
                # Search by the caller's task, not a hardcoded query, so reflections
                # are ranked for the problem at hand.
                "searchQuery": task,
                "metadataFilters": [
                    {
                        "left": {"metadataKey": "x-amz-agentcore-memory-recordType"},
                        "operator": "EQUALS_TO",
                        "right": {"metadataValue": {"stringValue": "REFLECTION"}},
                    }
                ],
                "topK": k,
            },
            maxResults=20,
        )

        reflections = response.get("memoryRecordSummaries", [])
        logger.info(f"   Found {len(reflections)} relevant reflection insights")

        # Linearize reflections
        return linearize_reflections(reflections)

    except Exception as e:
        logger.error(f"Error retrieving reflections: {e}")
        return f"Error retrieving reflections: {str(e)}"


logger.info("✅ Memory retrieval tools created")


# ## Step 6: Create Debugging Assistant Agent
#
# Now we'll create a Strands agent equipped with our memory retrieval tools.


# Create the debugging assistant agent
debugging_agent = Agent(
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    tools=[retrieve_process, retrieve_reflection_knowledge],
    system_prompt="""You are an expert Debugging Assistant with access to episodic memory.

Your capabilities:
- Retrieve past debugging episodes with complete step-by-step processes
- Access synthesized reflection knowledge showing patterns and best practices
- Provide guidance based on successful debugging experiences
- Warn about common pitfalls observed in past failures

When helping users:
1. Use retrieve_reflection_knowledge for strategic guidance, patterns, and high-level advice
2. Use retrieve_process when users need exact steps or want to recall what was done in a specific session
3. Synthesize insights from memory with your own reasoning
4. Be specific and actionable in your recommendations

Always explain your reasoning and cite relevant past experiences when available.""",
)

logger.info("✅ Debugging assistant agent created")


# ## Step 7: Test the Debugging Assistant
#
# Let's test various scenarios to see how the agent uses episodic memory and reflections.

# ### Test 1: Query for Strategic Guidance (Reflection Knowledge)


# Test 1: Get strategic guidance for memory issues
query1 = "My application is running out of memory when processing large datasets. What should I look for?"

logger.info(f"\n{'=' * 80}")
logger.info("Test 1: Memory Issue Guidance")
logger.info(f"{'=' * 80}")
logger.info(f"Query: {query1}\n")

response1 = debugging_agent(query1)

print("\n" + "=" * 80)
print("AGENT RESPONSE:")
print("=" * 80)
print(response1)


# ### Test 2: Query for Specific Process Details


# Test 2: Get specific debugging process
query2 = "Show me the exact steps for debugging a timeout issue with external API calls."

logger.info(f"\n{'=' * 80}")
logger.info("Test 2: API Timeout Debugging Process")
logger.info(f"{'=' * 80}")
logger.info(f"Query: {query2}\n")

response2 = debugging_agent(query2)

print("\n" + "=" * 80)
print("AGENT RESPONSE:")
print("=" * 80)
print(response2)


# ### Test 3: Query for Pattern Recognition


# Test 3: Get patterns and best practices for concurrency issues
query3 = "What are common patterns and best practices for handling race conditions in multi-threaded applications?"

logger.info(f"\n{'=' * 80}")
logger.info("Test 3: Race Condition Patterns")
logger.info(f"{'=' * 80}")
logger.info(f"Query: {query3}\n")

response3 = debugging_agent(query3)

print("\n" + "=" * 80)
print("AGENT RESPONSE:")
print("=" * 80)
print(response3)


# ### Test 4: Recall Specific Session


# Test 4: Recall what was done in memory leak session
query4 = "What debugging steps did we take when we encountered the memory leak issue? I need the full details."

logger.info(f"\n{'=' * 80}")
logger.info("Test 4: Recall Memory Leak Session")
logger.info(f"{'=' * 80}")
logger.info(f"Query: {query4}\n")

response4 = debugging_agent(query4)

print("\n" + "=" * 80)
print("AGENT RESPONSE:")
print("=" * 80)
print(response4)


# ## Step 8: Direct Memory Inspection
#
# Let's directly inspect what's stored in episodic memory and reflections.


# Inspect episodes directly using boto3
logger.info("" + "=" * 80)
logger.info("Direct Episode Inspection")
logger.info("=" * 80)

# Retrieve episodes using boto3 directly
# Use namespacePath to retrieve episodes across all sessions (hierarchical match)
namespace_path = f"debugging/{ACTOR_ID}/sessions/"
response = client.retrieve_memory_records(
    memoryId=memory_id,
    namespacePath=namespace_path,
    searchCriteria={"searchQuery": "debugging", "topK": 2},
    maxResults=10,
)

episodes = response.get("memoryRecordSummaries", [])

print(f"Found {len(episodes)} episodes in memory:")
for idx, episode in enumerate(episodes, 1):
    print(f"Episode {idx}:")
    pprint.pp(episode, depth=2, width=100)
    print("-" * 80)


import pprint  # noqa: E402

response = client.retrieve_memory_records(
    memoryId=memory_id,
    namespace=reflection_namespace,
    searchCriteria={
        "searchQuery": "memory leaks",
        "metadataFilters": [
            {
                "left": {"metadataKey": "x-amz-agentcore-memory-recordType"},
                "operator": "EQUALS_TO",
                "right": {"metadataValue": {"stringValue": "REFLECTION"}},
            }
        ],
        "topK": 10,
    },
    maxResults=20,
)

reflections = response.get("memoryRecordSummaries", [])
logger.info(f"   Found {len(reflections)} relevant reflections")
if reflections:
    for reflection in reflections:
        text = reflection["content"].get("text", "")
        if text:
            try:
                reflection_json = json.loads(text)
                pprint.pp(reflection_json)
            except json.JSONDecodeError:
                pprint.pp(text)


# ## Summary
#
# ### What We Accomplished
#
# ✅ Created episodic memory with reflection configuration using boto3
#
# ✅ Hydrated memory with historical debugging sessions
#
# ✅ Built specialized retrieval tools for episodes and reflections
#
# ✅ Created an intelligent debugging assistant using Strands framework
#
# ✅ Demonstrated strategic guidance retrieval vs. detailed process retrieval
#
# ### Key Takeaways
#
# 1. **Episodic Memory** preserves complete interaction sequences with temporal context
# 2. **Reflections** automatically synthesize patterns and insights from multiple episodes
# 3. **Linearization** optimizes context by formatting structured data for LLM consumption
# 4. **Tool selection** matters: use reflections for strategy, episodes for detailed steps
# 5. **Boto3 Direct Access** provides full control over Genesis Memory API operations
#
# ### When to Use This Pattern
#
# - **Technical support systems** that learn from past ticket resolutions
# - **Troubleshooting assistants** that recall successful diagnostic procedures
# - **Training systems** that capture expert workflows for knowledge transfer
# - **Process improvement** scenarios where analyzing past outcomes drives better practices

# ## Cleanup (Optional)
#
# Uncomment to delete the memory resource when done.


# Uncomment to delete memory resource using boto3
# try:
#     client.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
#     logger.info(f"✅ Successfully deleted memory: {memory_id}")
# except Exception as e:
#     logger.error(f"Error deleting memory: {e}")
