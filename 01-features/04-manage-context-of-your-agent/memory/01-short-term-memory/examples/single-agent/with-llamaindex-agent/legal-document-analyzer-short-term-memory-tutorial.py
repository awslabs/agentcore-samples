#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Legal Document Analyzer (Short-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create a Legal Document Analyzer. We'll focus on **short-term memory** persistence within a single legal analysis session - allowing the analyzer to remember contract clauses, precedents, and compliance issues throughout a legal review.
#
# ## Architecture Overview
#
# ![LlamaIndex AgentCore Short-Term Memory Architecture](LlamaIndex-AgentCore-STM-Arch.png)
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short-term Conversational Memory                                                |
# | Agent usecase       | Legal Document Analyzer                                                          |
# | Agentic Framework   | LlamaIndex                                                                       |
# | LLM model           | Anthropic Claude 3.7 Sonnet                                                  |
# | Tutorial components | AgentCore Short-term Memory, LlamaIndex Agent, Legal Analysis Tools             |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Create AgentCore Memory for legal document analysis
# - Use LlamaIndex native memory integration for legal workflows
# - Build legal-specific tools for contract analysis
# - Maintain legal context within a single analysis session
# - Test memory boundaries and session isolation
#
# ## Scenario Context
#
# In this example, we'll create a "Legal Document Analyzer" that helps attorneys analyze contracts, track legal issues, and manage compliance requirements within a single legal review session. The analyzer uses AgentCore Memory to maintain context about contract clauses, risk assessments, precedents, and compliance issues throughout the analysis.
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
# Create or get the AgentCore Memory resource for our legal analyzer:


# Create AgentCore Memory resource
region = os.getenv("AWS_REGION", "us-east-1")
client = MemoryClient(region_name=region)

try:
    response = client.create_memory_and_wait(
        name=f"LegalAnalyzerShortTerm_{int(datetime.now().timestamp())}",
        description="Legal document analyzer short-term memory for single session context",
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


# ## Step 3: Legal Analysis Tools Implementation
#
# Define specialized tools for legal document analysis:


def analyze_contract_clause(clause_text: str, clause_type: str, risk_level: str) -> str:
    """Analyze a contract clause and assess its risk level"""
    print(f"⚖️ Analyzed {clause_type} clause (Risk: {risk_level})")
    return f"Analyzed {clause_type} clause with {risk_level} risk assessment"


def track_legal_issue(issue: str, priority: str, status: str) -> str:
    """Track legal issue with priority and status"""
    print(f"📋 Tracking legal issue: {issue} ({priority} priority, {status})")
    return f"Now tracking legal issue: {issue}"


def save_legal_precedent(case_name: str, jurisdiction: str, relevance: str) -> str:
    """Save legal precedent with jurisdiction and relevance"""
    print(f"📚 Saved legal precedent: {case_name} ({jurisdiction})")
    return f"Saved legal precedent: {case_name}"


def flag_compliance_issue(regulation: str, violation_type: str, severity: str) -> str:
    """Flag compliance issue with regulation and severity"""
    print(f"🚨 Flagged {severity} compliance issue: {regulation}")
    return f"Flagged compliance issue: {regulation}"


# Create tool objects for the agent
legal_tools = [
    FunctionTool.from_defaults(fn=analyze_contract_clause),
    FunctionTool.from_defaults(fn=track_legal_issue),
    FunctionTool.from_defaults(fn=save_legal_precedent),
    FunctionTool.from_defaults(fn=flag_compliance_issue),
]


# ## Step 4: LlamaIndex Agent Implementation
#
# Create the legal analyzer agent with short-term memory context:


# Configuration for SHORT-TERM memory (single session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Create memory context for single session
context = AgentCoreMemoryContext(
    actor_id="legal-analyst",
    memory_id=memory_id,
    session_id="legal-analysis-session-today",  # Same session throughout
    namespace="/legal-analysis/",
)

# Initialize AgentCore Memory and LLM
agentcore_memory = AgentCoreMemory(context=context, region_name=region)
llm = BedrockConverse(model=MODEL_ID, region_name=region)

# Create the legal analyzer agent
legal_agent = FunctionAgent(tools=legal_tools, llm=llm, verbose=True)

print("✅ Legal Document Analyzer with short-term memory is ready!")


# ## Step 5: Testing Short-Term Memory Capabilities
#
# Let's test our legal analyzer's short-term memory through a comprehensive contract analysis session.

# ### Test 1: Case Setup and Initialization


# Initialize legal analysis session with detailed context

import asyncio  # noqa: E402


async def main():
    response = await legal_agent.run(
        "I'm Attorney Maria Johnson from Johnson & Associates, analyzing a $5M software licensing agreement "
        "between TechCorp (licensor) and DataSoft Inc (licensee). Track this as 'Software License Review' "
        "with critical priority and active status. Contract value: $5M over 3 years.",
        memory=agentcore_memory,
    )

    print("🎯 Case Setup:")
    print(response)

    # ### Test 2: Contract Clause Analysis

    # Analyze liability clause with specific terms
    response = await legal_agent.run(
        "Analyze this liability clause: 'TechCorp's total liability shall not exceed $50,000 for any direct damages "
        "and excludes all indirect, consequential, or punitive damages.' This is a 'Liability Limitation' clause "
        "with 'High' risk level due to low cap vs contract value.",
        memory=agentcore_memory,
    )

    print("⚖️ Liability Clause Analysis:")
    print(response)

    # Analyze termination clause with notice periods
    response = await legal_agent.run(
        "Analyze termination clause: 'Either party may terminate with 90 days written notice. "
        "TechCorp may terminate immediately for material breach or non-payment exceeding 30 days.' "
        "This is a 'Termination' clause with 'Medium' risk level.",
        memory=agentcore_memory,
    )

    print("📋 Termination Clause Analysis:")
    print(response)

    # ### Test 3: Contract Context Recall

    # Test contract context and risk assessment recall
    response = await legal_agent.run(
        "What contract am I analyzing? Who are the parties, what's the value, and what's my assessment of the liability cap?",
        memory=agentcore_memory,
    )

    print("🧠 Contract Context Recall:")
    print(response)
    print("\n✅ Expected: TechCorp/DataSoft, $5M contract, $50K liability cap (high risk)")

    # ### Test 4: Detailed Clause Recall

    # Test specific clause details recall
    response = await legal_agent.run(
        "What are the exact termination notice periods I found? What triggers immediate termination?",
        memory=agentcore_memory,
    )

    print("📋 Termination Details Recall:")
    print(response)
    print("\n✅ Expected: 90 days notice, immediate for breach or 30+ day non-payment")

    # ### Test 5: Legal Precedent Integration

    # Save relevant precedent with case details
    response = await legal_agent.run(
        "Save legal precedent: 'TechSoft Inc. v. MegaCorp' from 'Delaware Superior Court' with 'Critical' relevance. "
        "This case established that liability caps below 1% of contract value are unconscionable in software licensing.",
        memory=agentcore_memory,
    )

    print("📚 Legal Precedent Saved:")
    print(response)

    # ### Test 6: Risk Assessment Reasoning

    # Test risk assessment reasoning
    response = await legal_agent.run(
        "Why did I assess the liability clause as high risk? What's the mathematical relationship between the cap and contract value?",
        memory=agentcore_memory,
    )

    print("🤔 Risk Assessment Reasoning:")
    print(response)
    print("\n✅ Expected: $50K cap vs $5M contract = 1% ratio, high risk due to low percentage")

    # ### Test 7: Compliance Issue Flagging

    # Flag compliance issue with regulatory details
    response = await legal_agent.run(
        "Flag compliance issue: 'GDPR Article 82 Data Protection' violation type 'Inadequate Liability Coverage' "
        "with 'Critical' severity. The $50K cap is insufficient for potential GDPR fines up to 4% of annual revenue.",
        memory=agentcore_memory,
    )

    print("🚨 GDPR Compliance Issue:")
    print(response)

    # ### Test 8: Precedent Application

    # Test precedent application to current case
    response = await legal_agent.run(
        "How does the TechSoft v. MegaCorp precedent apply to my current contract analysis? "
        "What does it suggest about the liability clause?",
        memory=agentcore_memory,
    )

    print("⚖️ Precedent Application:")
    print(response)
    print("\n✅ Expected: Both have ~1% liability caps, precedent suggests unconscionability")

    # ### Test 9: Comprehensive Risk Assessment

    # Comprehensive risk assessment query
    response = await legal_agent.run(
        "Provide a comprehensive risk assessment for DataSoft: What are all the risks I've identified, "
        "their severity levels, and supporting precedents?",
        memory=agentcore_memory,
    )

    print("📊 Comprehensive Risk Assessment:")
    print(response)
    print("\n✅ Expected: High risk liability cap, GDPR compliance issues, TechSoft precedent support")

    # ## Step 6: Testing Session Boundaries
    #
    # Let's test the boundaries of short-term memory by creating a different session:

    # Create a different session context
    new_session_context = AgentCoreMemoryContext(
        actor_id="legal-analyst",
        memory_id=memory_id,
        session_id="different-legal-session",  # Different session ID
        namespace="/legal-analysis/",
    )

    new_session_memory = AgentCoreMemory(context=new_session_context, region_name=region)

    # Test memory isolation
    response = await legal_agent.run(
        "What contracts am I analyzing? What liability caps and compliance issues have I found?",
        memory=new_session_memory,
    )

    print("🚧 Session Boundary Test (Different Session):")
    print(response)
    print("\n✅ Expected: Limited or no recall from previous session (short-term memory boundary)")

    # Return to original session to verify persistence
    response = await legal_agent.run(
        "Back in my original session - what was the exact liability cap amount and GDPR compliance issue I identified?",
        memory=agentcore_memory,  # Original session memory
    )

    print("🔄 Original Session Return:")
    print(response)
    print("\n✅ Expected: Full recall of $50K cap, GDPR Article 82 issue")

    # ## 🧪 Automated Test Validation
    # Run these cells to validate that memory integration is working correctly:

    # Define validation functions inline
    class TestValidator:
        def __init__(self):
            self.results = {}

        def validate_memory_recall(self, response):
            """Check if agent can recall information from earlier in the session"""
            # Check for substantive response (not just "I don't know")
            has_content = len(response) > 50
            # Check for memory indicators
            has_memory_indicators = any(
                word in response.lower()
                for word in [
                    "earlier",
                    "mentioned",
                    "discussed",
                    "previously",
                    "you",
                    "we",
                    "our",
                ]
            )
            return "✅ PASS" if (has_content and has_memory_indicators) else "❌ FAIL"

        def validate_session_memory(self, response):
            """Check if agent maintains context within session"""
            has_memory_content = len(response) > 100 and any(
                word in response.lower()
                for word in [
                    "previous",
                    "earlier",
                    "mentioned",
                    "discussed",
                    "before",
                    "already",
                ]
            )
            return "✅ PASS" if has_memory_content else "❌ FAIL"

        def validate_cross_reference(self, response):
            """Check if agent can connect current query to previous context"""
            # Look for connecting language
            connecting_words = [
                "relate",
                "connection",
                "previous",
                "earlier",
                "discussed",
                "mentioned",
                "context",
                "based on",
                "as we",
                "as i",
            ]
            has_connection = any(word in response.lower() for word in connecting_words)
            has_substance = len(response) > 80
            return "✅ PASS" if (has_connection and has_substance) else "❌ FAIL"

        def run_validation_summary(self, test_results):
            print("🧪 COMPREHENSIVE TEST VALIDATION SUMMARY")
            print("=" * 60)

            total_tests = len(test_results)
            passed_tests = sum(1 for result in test_results.values() if "PASS" in result)
            pass_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0

            for test_name, result in test_results.items():
                print(f"{test_name}: {result}")

            print("=" * 60)
            print(f"📊 Overall Pass Rate: {passed_tests}/{total_tests} ({pass_rate:.1f}%)")

            if pass_rate >= 80:
                print("✅ EXCELLENT: Memory integration working correctly!")
            elif pass_rate >= 60:
                print("⚠️  GOOD: Most memory features working, some issues to investigate")
            else:
                print("❌ NEEDS ATTENTION: Memory integration has significant issues")

            return pass_rate

    validator = TestValidator()  # noqa: F841
    print("✅ Validation functions loaded!")

    # Run all validation tests

    # Test 1: Memory recall - can the agent recall what was discussed?
    response1 = await legal_agent.run("What have we discussed so far in this session?", memory=agentcore_memory)
    print(f"Response 1 length: {len(str(response1))} chars")

    # Test 2: Session memory - does the agent maintain context?
    response2 = await legal_agent.run("What did we talk about earlier?", memory=agentcore_memory)
    print(f"Response 2 length: {len(str(response2))} chars")

    # Test 3: Cross-reference capability - can it connect to previous context?
    response3 = await legal_agent.run("How does this relate to what we discussed before?", memory=agentcore_memory)
    print(f"Response 3 length: {len(str(response3))} chars")

    # ## Summary
    #
    # In this notebook, we've demonstrated:
    #
    # ✅ **Short-term Memory Integration**: Using AgentCore Memory with LlamaIndex for session-scoped legal analysis
    #
    # ✅ **Legal-Specific Tools**: Contract clause analysis, precedent management, and compliance tracking
    #
    # ✅ **Contextual Legal Analysis**: Analyzer remembers contract details, risk assessments, and precedents
    #
    # ✅ **Risk Assessment Reasoning**: Connecting liability caps to contract values and legal precedents
    #
    # ✅ **Session Boundaries**: Memory isolation between different legal analysis sessions
    #
    # ✅ **Compliance Management**: Tracking regulatory issues and their severity levels
    #
    # The Legal Document Analyzer showcases how short-term memory enables comprehensive contract analysis within a single session while maintaining clear boundaries between different legal matters.

    # ## Clean Up
    #
    # Let's delete the memory to clean up the resources used in this notebook:

    # Clean up AgentCore Memory resource
    try:
        client.delete_memory(memory_id)
        print(f"✅ Successfully deleted memory: {memory_id}")
    except Exception as e:
        print(f"❌ Error deleting memory: {e}")


if __name__ == "__main__":
    asyncio.run(main())
