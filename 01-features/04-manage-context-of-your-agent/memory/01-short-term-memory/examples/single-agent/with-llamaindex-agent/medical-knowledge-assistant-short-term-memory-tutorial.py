#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Medical Knowledge Assistant (Short-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create a Medical Knowledge Assistant. We'll focus on **short-term memory** persistence within a single patient consultation session - allowing the assistant to remember patient symptoms, medical history, drug interactions, and diagnostic reasoning throughout a medical consultation.
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
# | Agent usecase       | Medical Knowledge Assistant                                                      |
# | Agentic Framework   | LlamaIndex                                                                       |
# | LLM model           | Anthropic Claude 3.7 Sonnet                                                       |
# | Tutorial components | AgentCore Short-term Memory, LlamaIndex Agent, Medical Analysis Tools           |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create AgentCore Memory for medical consultation data
# - Use LlamaIndex native memory integration for medical workflows
# - Build medical-specific tools for patient analysis
# - Maintain medical context within a single consultation session
# - Test memory boundaries and session isolation
#
# ## Scenario Context
#
# In this example, we'll create a "Medical Knowledge Assistant" that helps healthcare providers analyze patient cases, check drug interactions, and retrieve clinical guidelines within a single consultation session. The assistant uses AgentCore Memory to maintain context about patient symptoms, medical history, medications, and diagnostic reasoning throughout the consultation.
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
# Create or get the AgentCore Memory resource for our medical assistant:


# Create AgentCore Memory resource
region = os.getenv("AWS_REGION", "us-east-1")
client = MemoryClient(region_name=region)

try:
    response = client.create_memory_and_wait(
        name=f"MedicalAssistantShortTerm_{int(datetime.now().timestamp())}",
        description="Medical knowledge assistant short-term memory for single consultation context",
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


# ## Step 3: Medical Analysis Tools Implementation
#
# Define specialized tools for medical consultation tasks:


def record_patient_symptoms(symptoms: str, severity: str, duration: str) -> str:
    """Record patient symptoms with severity and duration"""
    print(f"🩺 Recorded symptoms: {symptoms} ({severity} severity, {duration} duration)")
    return f"Recorded patient symptoms: {symptoms}"


def check_drug_interaction(medication1: str, medication2: str, interaction_level: str) -> str:
    """Check drug interaction between medications"""
    print(f"💊 Drug interaction check: {medication1} + {medication2} ({interaction_level} risk)")
    return f"Drug interaction assessed: {medication1} and {medication2}"


def save_vital_signs(temperature: str, blood_pressure: str, heart_rate: str, notes: str) -> str:
    """Save patient vital signs with notes"""
    print(
        f"📊 Vital signs: Temp {temperature}, BP {blood_pressure}, HR {heart_rate}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    return "Saved vital signs for patient"


def retrieve_clinical_guideline(condition: str, guideline_type: str, evidence_level: str) -> str:
    """Retrieve clinical guideline for medical condition"""
    print(f"📋 Retrieved {guideline_type} guideline for {condition} (Evidence: {evidence_level})")
    return f"Retrieved clinical guideline for {condition}"


def document_differential_diagnosis(primary_diagnosis: str, alternatives: str, confidence: str) -> str:
    """Document differential diagnosis with confidence level"""
    print(f"🔍 Differential diagnosis: {primary_diagnosis} ({confidence} confidence)")
    return f"Documented differential diagnosis: {primary_diagnosis}"


# Create tool objects for the agent
medical_tools = [
    FunctionTool.from_defaults(fn=record_patient_symptoms),
    FunctionTool.from_defaults(fn=check_drug_interaction),
    FunctionTool.from_defaults(fn=save_vital_signs),
    FunctionTool.from_defaults(fn=retrieve_clinical_guideline),
    FunctionTool.from_defaults(fn=document_differential_diagnosis),
]


# ## Step 4: LlamaIndex Agent Implementation
#
# Create the medical assistant agent with short-term memory context:


# Configuration for SHORT-TERM memory (single session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Create memory context for single session
context = AgentCoreMemoryContext(
    actor_id="medical-provider",
    memory_id=memory_id,
    session_id="consultation-session-today",  # Same session throughout
    namespace="/medical-consultation/",
)

# Initialize AgentCore Memory and LLM
agentcore_memory = AgentCoreMemory(context=context, region_name=region)
llm = BedrockConverse(model=MODEL_ID, region_name=region)

# Create the medical assistant agent
medical_agent = FunctionAgent(tools=medical_tools, llm=llm, verbose=True)

print("✅ Medical Knowledge Assistant with short-term memory is ready!")


# ## Step 5: Testing Short-Term Memory Capabilities
#
# Let's test our medical assistant's short-term memory through a comprehensive patient consultation session.

# ### Test 1: Patient Intake and Initial Assessment


# Initialize consultation session with patient details

import asyncio  # noqa: E402


async def main():
    response = await medical_agent.run(
        "I'm Dr. Emily Chen conducting a consultation for patient John Smith, 45-year-old male. "
        "Record symptoms: 'chest pain, shortness of breath, fatigue' with 'severe' severity and '3 days' duration. "
        "Patient has history of hypertension and diabetes.",
        memory=agentcore_memory,
    )

    print("🎯 Patient Intake:")
    print(response)

    # ### Test 2: Vital Signs Documentation

    # Document vital signs with clinical context
    response = await medical_agent.run(
        "Save vital signs: temperature '99.2°F', blood pressure '165/95 mmHg', heart rate '110 bpm' "
        "with notes 'elevated BP and tachycardia, patient appears diaphoretic and anxious'.",
        memory=agentcore_memory,
    )

    print("📊 Vital Signs Documentation:")
    print(response)

    # ### Test 3: Drug Interaction Analysis

    # Check drug interactions for current medications
    response = await medical_agent.run(
        "Check drug interaction between 'Lisinopril 10mg' and 'Metformin 500mg' with 'low' interaction level. "
        "Patient is currently taking both for hypertension and diabetes management.",
        memory=agentcore_memory,
    )

    print("💊 Drug Interaction Check:")
    print(response)

    # Check potential new medication interaction
    response = await medical_agent.run(
        "Check drug interaction between 'Lisinopril 10mg' and 'Nitroglycerin sublingual' with 'moderate' interaction level. "
        "Considering nitroglycerin for chest pain management.",
        memory=agentcore_memory,
    )

    print("💊 Additional Drug Check:")
    print(response)

    # ### Test 4: Patient Context Recall

    # Test patient information and vital signs recall
    response = await medical_agent.run(
        "What patient am I consulting with? What are their presenting symptoms, vital signs, and current medications?",
        memory=agentcore_memory,
    )

    print("🧠 Patient Context Recall:")
    print(response)
    print("\n✅ Expected: John Smith, 45M, chest pain/SOB/fatigue, elevated BP 165/95, Lisinopril/Metformin")

    # ### Test 5: Clinical Guideline Retrieval

    # Retrieve clinical guidelines based on symptoms
    response = await medical_agent.run(
        "Retrieve clinical guideline for 'acute chest pain' with 'diagnostic protocol' type and 'Level A' evidence level. "
        "Need to evaluate this patient's chest pain systematically.",
        memory=agentcore_memory,
    )

    print("📋 Clinical Guideline Retrieval:")
    print(response)

    # ### Test 6: Differential Diagnosis Documentation

    # Document differential diagnosis with reasoning
    response = await medical_agent.run(
        "Document differential diagnosis: primary 'Acute Coronary Syndrome' with alternatives "
        "'pulmonary embolism, aortic dissection, anxiety disorder' and 'high' confidence level. "
        "Based on chest pain, elevated vitals, and cardiac risk factors.",
        memory=agentcore_memory,
    )

    print("🔍 Differential Diagnosis:")
    print(response)

    # ### Test 7: Comprehensive Clinical Reasoning

    # Test comprehensive clinical reasoning
    response = await medical_agent.run(
        "Based on John's symptoms, vital signs, and medical history, why did I consider Acute Coronary Syndrome? "
        "What specific clinical indicators support this diagnosis?",
        memory=agentcore_memory,
    )

    print("🤔 Clinical Reasoning Test:")
    print(response)
    print("\n✅ Expected: Chest pain + SOB + elevated BP/HR + diabetes/HTN history = ACS risk factors")

    # ### Test 8: Drug Interaction Recall

    # Test drug interaction memory
    response = await medical_agent.run(
        "What drug interactions have I checked for this patient? Which combination had moderate risk and why?",
        memory=agentcore_memory,
    )

    print("💊 Drug Interaction Recall:")
    print(response)
    print("\n✅ Expected: Lisinopril+Metformin (low risk), Lisinopril+Nitroglycerin (moderate risk)")

    # ### Test 9: Treatment Planning Integration

    # Test integrated treatment planning
    response = await medical_agent.run(
        "Based on my differential diagnosis and drug interaction checks, what treatment considerations "
        "should I keep in mind for John? Include medication interactions and clinical guidelines.",
        memory=agentcore_memory,
    )

    print("🏥 Treatment Planning:")
    print(response)
    print("\n✅ Expected: ACS protocol, monitor Lisinopril+Nitroglycerin interaction, consider cardiac workup")

    # Comprehensive case summary
    response = await medical_agent.run(
        "Provide a complete case summary: patient demographics, presenting symptoms, vital signs, "
        "current medications, drug interactions checked, differential diagnosis, and clinical guidelines retrieved.",
        memory=agentcore_memory,
    )

    print("📋 Complete Case Summary:")
    print(response)
    print("\n✅ Expected: Full consultation details with all recorded information")

    # ## Step 6: Testing Session Boundaries
    #
    # Let's test the boundaries of short-term memory by creating a different session:

    # Create a different session context
    new_session_context = AgentCoreMemoryContext(
        actor_id="medical-provider",
        memory_id=memory_id,
        session_id="different-consultation-session",  # Different session ID
        namespace="/medical-consultation/",
    )

    new_session_memory = AgentCoreMemory(context=new_session_context, region_name=region)

    # Test memory isolation
    response = await medical_agent.run(
        "What patients am I consulting with today? What symptoms and vital signs have I recorded?",
        memory=new_session_memory,
    )

    print("🚧 Session Boundary Test (Different Session):")
    print(response)
    print("\n✅ Expected: Limited or no recall from previous session (short-term memory boundary)")

    # Return to original session to verify persistence
    response = await medical_agent.run(
        "Back in my original consultation - what were John Smith's exact vital signs and primary diagnosis?",
        memory=agentcore_memory,  # Original session memory
    )

    print("🔄 Original Session Return:")
    print(response)
    print("\n✅ Expected: Full recall of BP 165/95, HR 110, ACS diagnosis")

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
    response1 = await medical_agent.run("What have we discussed so far in this session?", memory=agentcore_memory)
    print(f"Response 1 length: {len(str(response1))} chars")

    # Test 2: Session memory - does the agent maintain context?
    response2 = await medical_agent.run("What did we talk about earlier?", memory=agentcore_memory)
    print(f"Response 2 length: {len(str(response2))} chars")

    # Test 3: Cross-reference capability - can it connect to previous context?
    response3 = await medical_agent.run("How does this relate to what we discussed before?", memory=agentcore_memory)
    print(f"Response 3 length: {len(str(response3))} chars")

    # ### Test 10: Comprehensive Case Summary

    # ## Summary
    #
    # In this notebook, we've demonstrated:
    #
    # ✅ **Short-term Memory Integration**: Using AgentCore Memory with LlamaIndex for session-scoped medical consultations
    #
    # ✅ **Medical-Specific Tools**: Patient symptom tracking, drug interaction checking, and clinical guideline retrieval
    #
    # ✅ **Clinical Reasoning**: Assistant remembers patient details, vital signs, and diagnostic reasoning
    #
    # ✅ **Drug Safety Management**: Comprehensive medication interaction tracking and assessment
    #
    # ✅ **Session Boundaries**: Memory isolation between different patient consultation sessions
    #
    # ✅ **Evidence-Based Medicine**: Clinical guideline integration and differential diagnosis documentation
    #
    # The Medical Knowledge Assistant showcases how short-term memory enables comprehensive patient care within a single consultation session while maintaining clear boundaries between different patient encounters.

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
