#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Academic Research Assistant (Short-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create an Academic Research Assistant. We'll focus on **short-term memory** persistence within a single research session - allowing the assistant to remember papers, findings, and research context throughout a conversation.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short-term Conversational Memory                                                |
# | Agent usecase       | Academic Research Assistant                                                      |
# | Agentic Framework   | LlamaIndex                                                                       |
# | LLM model           | Anthropic Claude 3.7 Sonnet                                                  |
# | Tutorial components | AgentCore Short-term Memory, LlamaIndex Agent, Research Tools                   |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Create AgentCore Memory for research data persistence
# - Use LlamaIndex native memory integration
# - Build research-specific tools for paper analysis
# - Maintain research context within a single session
# - Test memory boundaries and session isolation
#
# ## Scenario Context
#
# In this example, we'll create an "Academic Research Assistant" that helps researchers track papers, findings, and research topics within a single research session. The assistant uses AgentCore Memory to maintain context about papers reviewed, key findings discovered, and research progress throughout the conversation.
#
# ## Architecture Overview
#
# ![LlamaIndex AgentCore Short-Term Memory Architecture](LlamaIndex-AgentCore-STM-Arch.png)
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS IAM role with AgentCore Memory permissions:
#   - `bedrock-agentcore:CreateMemory`
#   - `bedrock-agentcore:CreateEvent`
#   - `bedrock-agentcore:ListEvents`
#   - `bedrock-agentcore:RetrieveMemories`
# - Access to Amazon Bedrock models

# ## Step 1: Install Dependencies and Setup


# Install necessary libraries


# Import required components
from bedrock_agentcore.memory import MemoryClient
from llama_index.memory.bedrock_agentcore import AgentCoreMemory, AgentCoreMemoryContext
from llama_index.llms.bedrock_converse import BedrockConverse as _BedrockConverseBase
from llama_index.core.base.llms.types import MessageRole
import asyncio as _asyncio
from typing import List
from llama_index.core.base.llms.types import ChatMessage


class BedrockConverse(_BedrockConverseBase):
    """Sync wrapper to avoid aiobotocore Python 3.13 credential loading issue."""

    async def achat(self, messages, **kwargs):
        return await _asyncio.to_thread(self.chat, messages, **kwargs)

    async def astream_chat(self, messages, **kwargs):
        async def _gen():
            resp = await _asyncio.to_thread(self.chat, messages, **kwargs)
            yield resp

        return _gen()

    async def astream_chat_with_tools(
        self,
        tools,
        user_msg=None,
        chat_history=None,
        verbose=False,
        allow_parallel_tool_calls=False,
        tool_required=False,
        **kwargs,
    ):
        chat_kwargs = self._prepare_chat_with_tools_compat(
            tools,
            user_msg=user_msg,
            chat_history=chat_history,
            verbose=verbose,
            allow_parallel_tool_calls=allow_parallel_tool_calls,
            tool_required=tool_required,
            **kwargs,
        )

        async def _gen():
            resp = await _asyncio.to_thread(self.chat, **chat_kwargs)
            yield resp

        return _gen()


# Patch: only store user + final-assistant text messages to avoid tool-call reconstruction errors
_original_aput_messages = AgentCoreMemory.aput_messages


async def _filtered_aput_messages(self, messages: List[ChatMessage]) -> None:
    text_messages = [
        m
        for m in messages
        if m.role in (MessageRole.USER, MessageRole.ASSISTANT) and m.content  # skip empty tool-call assistant messages
    ]
    if text_messages:
        await _original_aput_messages(self, text_messages)


AgentCoreMemory.aput_messages = _filtered_aput_messages
from llama_index.core.agent.workflow import FunctionAgent  # noqa: E402
from llama_index.core.tools import FunctionTool  # noqa: E402
from datetime import datetime  # noqa: E402
import os  # noqa: E402


# ## Step 2: AgentCore Memory Configuration
#
# Create or get the AgentCore Memory resource for our research assistant:


# Create AgentCore Memory resource
region = os.getenv("AWS_REGION", "us-east-1")
client = MemoryClient(region_name=region)

try:
    response = client.create_memory_and_wait(
        name=f"AcademicResearchShortTerm_{int(datetime.now().timestamp())}",
        description="Academic research assistant short-term memory for single session context",
        strategies=[],
        event_expiry_days=7,
        max_wait=300,
        poll_interval=10,
    )
    memory_id = response["id"]
    print(f"✅ Created AgentCore Memory: {memory_id}")
    import time

    time.sleep(5)  # brief propagation delay before first CreateEvent
except Exception as e:
    print(f"❌ Error creating memory: {e}")
    raise


# ## Step 3: Research Tools Implementation
#
# Define specialized tools for academic research tasks:


def save_paper_summary(title: str, authors: str, key_findings: str) -> str:
    """Save a research paper summary with title, authors, and key findings"""
    print(f"📄 Saved paper: {title} by {authors}")
    return f"Successfully saved paper summary for '{title}'"


def track_research_topic(topic: str, status: str) -> str:
    """Track research topic progress with current status"""
    print(f"🔬 Tracking research topic: {topic} (Status: {status})")
    return f"Now tracking research topic: {topic} with status {status}"


def save_research_finding(finding: str, confidence: str) -> str:
    """Save a research finding with confidence level"""
    print(f"💡 Research finding saved with {confidence} confidence")
    return f"Saved research finding with {confidence} confidence level"


# Create tool objects for the agent
research_tools = [
    FunctionTool.from_defaults(fn=save_paper_summary),
    FunctionTool.from_defaults(fn=track_research_topic),
    FunctionTool.from_defaults(fn=save_research_finding),
]


# ## Step 4: LlamaIndex Agent Implementation
#
# Create the research assistant agent with short-term memory context:


# Configuration for SHORT-TERM memory (single session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Create memory context for single session
context = AgentCoreMemoryContext(
    actor_id="academic-researcher",
    memory_id=memory_id,
    session_id="research-session-today",  # Same session throughout
    namespace="/academic-research/",
)

# Initialize AgentCore Memory and LLM
agentcore_memory = AgentCoreMemory(context=context, region_name=region)
llm = BedrockConverse(model=MODEL_ID, region_name=region)

# Create the research assistant agent
research_agent = FunctionAgent(tools=research_tools, llm=llm, verbose=True)

print("✅ Academic Research Assistant with short-term memory is ready!")


# ## Step 5: Testing Short-Term Memory Capabilities
#
# Let's test our research assistant's short-term memory through a comprehensive research session.

# ### Test 1: Session Initialization


# Initialize research session with detailed context

import asyncio  # noqa: E402


async def main():
    response = await research_agent.run(
        "I'm Dr. Sarah Smith from MIT's Computer Science Department, starting research on 'Machine Learning in Healthcare Applications'. "
        "Track this topic with status 'Literature Review'.",
        memory=agentcore_memory,
    )

    print("🎯 Session Initialization:")
    print(response)

    # ### Test 2: Adding Research Papers

    # Add first paper with detailed metrics
    response = await research_agent.run(
        "Save paper: 'Deep Learning for Medical Image Analysis' by Zhang et al. "
        "Key findings: CNNs achieve 95.2% accuracy in chest X-ray diagnosis, 12% improvement over radiologists, "
        "trained on 100,000 images with 0.03 false positive rate.",
        memory=agentcore_memory,
    )

    print("📄 Paper 1 Added:")
    print(response)

    # Add second paper with contrasting findings
    response = await research_agent.run(
        "Save paper: 'Transformers in Medical NLP' by Johnson et al. "
        "Key findings: BERT models achieve 89.1% F1-score in clinical note classification, "
        "struggle with rare diseases (<70% accuracy), excel at symptom extraction (94% precision).",
        memory=agentcore_memory,
    )

    print("📄 Paper 2 Added:")
    print(response)

    # ### Test 3: Identity and Context Recall

    # Test identity and research context recall
    response = await research_agent.run(
        "What's my name, institution, and current research focus?",
        memory=agentcore_memory,
    )

    print("🧠 Identity Recall Test:")
    print(response)
    print("\n✅ Expected: Dr. Sarah Smith, MIT, Machine Learning in Healthcare")

    # ### Test 4: Detailed Metrics Recall

    # Test specific metric recall
    response = await research_agent.run(
        "What were the exact accuracy percentages mentioned in the papers I reviewed? "
        "Which authors wrote about CNNs vs Transformers?",
        memory=agentcore_memory,
    )

    print("📊 Detailed Metrics Recall:")
    print(response)
    print("\n✅ Expected: Zhang et al - CNNs 95.2%, Johnson et al - BERT 89.1%")

    # ### Test 5: Contextual Reasoning

    # Test contextual understanding and reasoning
    response = await research_agent.run(
        "Based on the papers I've reviewed, which approach would be better for analyzing "
        "chest X-rays vs clinical notes? Explain your reasoning.",
        memory=agentcore_memory,
    )

    print("🤔 Contextual Reasoning Test:")
    print(response)
    print("\n✅ Expected: CNNs for X-rays (Zhang paper), Transformers for clinical notes (Johnson paper)")

    # ### Test 6: Research Finding Synthesis

    # Add synthesized research finding
    response = await research_agent.run(
        "Based on Zhang's CNN results (95.2% accuracy) and Johnson's Transformer results (89.1% F1-score), "
        "I conclude that deep learning models consistently achieve >85% accuracy in healthcare tasks. "
        "This finding has high confidence. Save it.",
        memory=agentcore_memory,
    )

    print("🔬 Research Finding Synthesis:")
    print(response)

    # ### Test 7: Cross-Reference Capability

    # Test cross-referencing between findings and papers
    response = await research_agent.run(
        "How does my research finding about >85% accuracy relate to the specific results "
        "from Zhang and Johnson? What evidence supports this conclusion?",
        memory=agentcore_memory,
    )

    print("🔗 Cross-Reference Test:")
    print(response)
    print("\n✅ Expected: Reference to Zhang 95.2% and Johnson 89.1% as supporting evidence")

    # ### Test 8: Practical Application Scenario

    # Test practical application of accumulated knowledge
    response = await research_agent.run(
        "I'm writing a grant proposal for healthcare AI research. What evidence can I cite "
        "about deep learning effectiveness? Include specific numbers and authors.",
        memory=agentcore_memory,
    )

    print("📝 Grant Proposal Support:")
    print(response)
    print("\n✅ Expected: Comprehensive summary with Zhang 95.2%, Johnson 89.1%, synthesis finding")

    # ## Step 6: Testing Session Boundaries
    #
    # Let's test the boundaries of short-term memory by creating a different session:

    # Create a different session context
    new_session_context = AgentCoreMemoryContext(
        actor_id="academic-researcher",
        memory_id=memory_id,
        session_id="different-research-session",  # Different session ID
        namespace="/academic-research/",
    )

    new_session_memory = AgentCoreMemory(context=new_session_context, region_name=region)

    # Test memory isolation
    response = await research_agent.run(
        "What research have I been working on? What specific accuracy numbers did I find?",
        memory=new_session_memory,
    )

    print("🚧 Session Boundary Test (Different Session):")
    print(response)
    print("\n✅ Expected: Limited or no recall from previous session (short-term memory boundary)")

    # Return to original session to verify persistence
    response = await research_agent.run(
        "Now back in my original session - what were the accuracy numbers from Zhang and Johnson again?",
        memory=agentcore_memory,  # Original session memory
    )

    print("🔄 Original Session Return:")
    print(response)
    print("\n✅ Expected: Full recall of Zhang 95.2%, Johnson 89.1%")

    # Run all validation tests

    # Test 1: Memory recall - can the agent recall what was discussed?
    response1 = await research_agent.run("What have we discussed so far in this session?", memory=agentcore_memory)
    print(f"🧠 Memory Recall Test response length: {len(str(response1))} chars")
    print(str(response1))

    # Test 2: Session memory - does the agent maintain context?
    response2 = await research_agent.run("What did we talk about earlier?", memory=agentcore_memory)
    print(f"💾 Session Memory Test response length: {len(str(response2))} chars")
    print(str(response2))

    # Test 3: Cross-reference capability - can it connect to previous context?
    response3 = await research_agent.run("How does this relate to what we discussed before?", memory=agentcore_memory)
    print(f"🔗 Cross-Reference Test response length: {len(str(response3))} chars")
    print(str(response3))

    # ## Clean Up
    #
    # Let's delete the memory to clean up the resources used in this notebook:

    # ## Summary
    #
    # You've seen how `AgentCoreMemoryContext` gives a LlamaIndex `FunctionAgent` session-scoped memory: papers and findings added during a session are recalled, cross-referenced, and synthesized; a fresh `session_id` isolates the context.
    #
    # For cross-session persistence, add a strategy (e.g. `SemanticStrategy`) — see the [long-term counterpart](../../../../02-long-term-memory/examples/single-agent/with-llamaindex-agent/03-memory-tool/academic-research-assistant-long-term-memory-tutorial.py). Full API reference: [AgentCore Memory docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html).


if __name__ == "__main__":
    asyncio.run(main())
