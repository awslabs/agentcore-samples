#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Medical Knowledge Assistant (Long-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create a Medical Knowledge Assistant with **long-term memory** persistence across multiple patient consultations and medical cases - allowing the assistant to build cumulative medical knowledge and track patient care over months and years.
#
# ## Architecture Overview
#
# ![LlamaIndex AgentCore Long-Term Memory Architecture](LlamaIndex-AgentCore-LTM-Arch.png)
#
# ## Tutorial Details
#
# **Tutorial Details:**
# - **Tutorial type**: Long-term Cross-Session Memory
# - **Agent usecase**: Medical Knowledge Assistant
# - **Agentic Framework**: LlamaIndex
# - **LLM model**: Anthropic Claude 3.7 Sonnet
# - **Tutorial components**: AgentCore Long-term Memory, LlamaIndex Agent, Medical Tools
# - **Example complexity**: Advanced
#
# ## Business Value
#
# **Enterprise Medical Intelligence**: Transform your healthcare practice with persistent AI memory that accumulates patient knowledge, tracks treatment evolution, and maintains comprehensive medical histories across cases and time periods.
#
# **Key Professional Advantages:**
# - **Patient Continuity**: Seamless knowledge transfer between medical encounters and care teams
# - **Clinical Memory**: Preserve critical patient histories, treatments, and outcomes permanently
# - **Cross-Patient Intelligence**: Identify patterns and connections across multiple patient cases
# - **Treatment Excellence**: Leverage historical patient data for superior care decisions
# - **Population Health**: Maintain detailed context for longitudinal patient care
# - **Quality Improvement**: Track treatment protocols and their effectiveness over time
#
# ## Long-Term Memory Configuration
#
# **Technical Setup**: This tutorial uses AgentCore Memory with Semantic Strategy for 12-month retention:
# - **Memory Type**: Semantic strategy with automatic insight extraction
# - **Retention**: 365-day event expiry for patient care continuity
# - **Cross-Session**: Same actor_id + memory_id, different session_id per medical period
# - **Search Capability**: Built-in memory retrieval tool for semantic search across patient history
#
# ## Technical Overview
#
# **Key Long-Term Memory Components:**
# 1. **Semantic Strategy Configuration**: Uses SemanticStrategy for automatic insight extraction with 365-day retention
# 2. **Cross-Session Persistence**: Same actor_id + memory_id, different session_id per period enables knowledge continuity
# 3. **Custom Memory Search Tool**: Wraps AgentCore's native search_long_term_memories() in LlamaIndex FunctionTool
# 4. **Semantic Processing Pipeline**: 120-second wait for conversational events → semantic memories conversion
# 5. **Dynamic Session Management**: Uses memory.context.session_id for flexible session handling
#
# **You'll learn to:**
#
# - Create persistent AgentCore Memory across multiple patient consultations
# - Build cumulative medical knowledge over time
# - Implement semantic search across patient histories and treatment outcomes
# - Track treatment effectiveness and medical insights evolution
# - Test cross-session medical knowledge persistence and retrieval
#
# ## Scenario Context
#
# In this example, we'll create a "Medical Knowledge Assistant" that maintains medical knowledge across multiple patient consultations spanning months and years. The assistant uses AgentCore Memory to build a persistent knowledge base of patient histories, treatment protocols, drug interactions, and clinical outcomes that grows and evolves over time, enabling sophisticated longitudinal medical analysis.
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


# Install necessary libraries including semantic strategy toolkit


# Import required components
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
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
import os  # noqa: E402
import boto3  # noqa: E402

print("✅ All dependencies imported successfully!")


# ## Step 2: AgentCore Memory Configuration
#
# Create or get the AgentCore Memory resource for long-term medical knowledge:


# Create AgentCore Memory with Semantic Strategy for long-term persistence
region = os.getenv("AWS_REGION", "us-east-1")
memory_client = MemoryClient(region_name=region)

try:
    # Create memory with semantic strategy for automatic insight extraction
    # Use stable name + create_or_get_memory so re-runs reuse the existing ACTIVE memory
    memory = memory_client.create_or_get_memory(
        name="MedicalAssistantSemanticLTM",
        strategies=[
            {
                StrategyType.SEMANTIC.value: {
                    "name": "medicalLongTermMemory",
                    "namespaces": ["/medical/{actorId}/"],
                }
            }
        ],
        event_expiry_days=365,  # 12-month retention for research records
    )
    memory_id = memory["id"]
    print(f"✅ Created Semantic Memory: {memory_id}")
    print(f"   Status: {memory.get('status')}")
    # Brief wait for data-plane propagation after ACTIVE status
    import time as _time

    _time.sleep(15)
except Exception as e:
    print(f"❌ Error creating memory: {e}")
    raise


# ## Step 3: Medical Tools Implementation
#
# Define specialized tools for longitudinal medical analysis:


def assess_patient_symptoms(patient_id: str, symptoms: str, severity: str, duration: str) -> str:
    """Assess patient symptoms with severity and duration tracking"""
    return f"🩺 Assessed symptoms for {patient_id} (Severity: {severity}, Duration: {duration})"


def check_drug_interactions(patient_id: str, medications: str, interaction_level: str, recommendations: str) -> str:
    """Check drug interactions with safety recommendations"""
    return f"💊 {patient_id} drug interaction check: {interaction_level} - {recommendations}"


def document_treatment_protocol(protocol_type: str, indication: str, effectiveness: str, side_effects: str) -> str:
    """Document treatment protocol with effectiveness and side effects"""
    print(f"📋 Treatment protocol: {protocol_type} for {indication} (Effectiveness: {effectiveness})")
    return f"Documented treatment protocol: {protocol_type}"


def update_clinical_guideline(patient_id: str, guideline_type: str, recommendation: str, evidence_level: str) -> str:
    """Update clinical guideline for specific patient"""
    print(
        f"📖 Clinical guideline: {patient_id} - {guideline_type} ({evidence_level} evidence)"  # codeql[py/clear-text-logging-sensitive-data]
    )
    return f"Updated guideline for {patient_id}"


def log_treatment_outcome(patient_id: str, treatment: str, outcome: str, follow_up_needed: str) -> str:
    """Log treatment outcome with follow-up requirements"""
    print(
        f"🏥 Treatment outcome: {patient_id} - {treatment}: {outcome}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    return f"Logged outcome for {patient_id}"


def log_medical_milestone(quarter: str, milestone: str, details: str) -> str:
    """Log a medical milestone with quarter and detailed progress"""
    print(f"🎯 {quarter} milestone: {milestone}")
    return f"Logged milestone for {quarter}: {milestone} - {details}"


def track_clinical_metrics(metric_type: str, value: str, patient_id: str, quarter: str) -> str:
    """Track specific clinical metrics with patient and timeline"""
    print(f"📊 {quarter}: {metric_type} = {value} (for {patient_id})")  # codeql[py/clear-text-logging-sensitive-data]
    return f"Tracked {metric_type}: {value} for {patient_id} in {quarter}"


def save_medical_insight(insight: str, quarter: str, clinical_context: str) -> str:
    """Save medical insights with clinical context"""
    print(f"💡 {quarter} insight: {insight[:50]}...")
    return f"Saved {quarter} insight with clinical context: {clinical_context}"


# Create tool objects for the agent
medical_tools = [
    FunctionTool.from_defaults(fn=assess_patient_symptoms),
    FunctionTool.from_defaults(fn=check_drug_interactions),
    FunctionTool.from_defaults(fn=document_treatment_protocol),
    FunctionTool.from_defaults(fn=update_clinical_guideline),
    FunctionTool.from_defaults(fn=log_treatment_outcome),
    FunctionTool.from_defaults(fn=log_medical_milestone),
    FunctionTool.from_defaults(fn=track_clinical_metrics),
    FunctionTool.from_defaults(fn=save_medical_insight),
]

print("✅ Medical tools created!")


# ## Step 3b: Add Memory Retrieval Tool
#
# Create a tool that allows the agent to search its long-term memory:


def create_memory_retrieval_tool(memory_id: str, actor_id: str, region: str):
    """Create a tool for the agent to search its own long-term memory"""

    def search_long_term_memory(query: str) -> str:
        """Search long-term memory for relevant medical information about patients, treatments, protocols, and outcomes.

        Use this tool when you need to recall:
        - Patient information (symptoms, treatments, outcomes)
        - Treatment protocols and their effectiveness
        - Drug interactions and safety profiles
        - Clinical guidelines and recommendations
        - Medical insights and lessons learned

        Args:
            query: Search query describing what information you need (e.g., 'PATIENT-001 symptoms', 'diabetes protocols', 'Q1 outcomes')

        Returns:
            Relevant information from long-term memory
        """
        try:
            from bedrock_agentcore.memory.session import MemorySessionManager

            # Create session manager (only needs memory_id and region)
            session_manager = MemorySessionManager(memory_id=memory_id, region_name=region)

            # The bug was the namespace VALUE: this read from "/strategies/" while the
            # semantic strategy writes to "/medical/{actorId}/", so recall returned 0.
            # Read from the SAME namespace the strategy writes to. We keep the
            # `namespace_prefix=` arg (works across the bedrock-agentcore versions this
            # tutorial supports; `namespace=`/`namespace_path=` only exist in newer SDKs).
            results = session_manager.search_long_term_memories(
                query=query,
                namespace_prefix=f"/medical/{actor_id}/",
                top_k=5,
                max_results=10,
            )

            if not results:
                return "No relevant information found in long-term memory. This might be new information or the memory extraction may still be processing."

            # Format results for the agent
            output = "📚 Retrieved from long-term memory:\\n\\n"
            for i, result in enumerate(results, 1):
                # MemoryRecord object - access content attribute
                content = getattr(result, "content", str(result))
                # Truncate very long content
                if len(content) > 300:
                    content = content[:300] + "..."
                output += f"{i}. {content}\\n\\n"

            return output

        except Exception as e:
            return f"⚠️ Error searching memory: {str(e)}. Proceeding without historical context."

    return FunctionTool.from_defaults(fn=search_long_term_memory)


# Create the memory retrieval tool
memory_search_tool = create_memory_retrieval_tool(memory_id, "medical-assistant", region)

# Add memory search to the tools list
medical_tools_with_memory = medical_tools + [memory_search_tool]

print(f"✅ Memory retrieval tool created! Total tools: {len(medical_tools_with_memory)}")
print("   Using namespace: /strategies/ (for semantic strategy compatibility)")


# ## Step 3c: Verify Memory Configuration
#
# Check that semantic strategy is properly configured:


# Check memory configuration
memory_info = (
    boto3.client("bedrock-agentcore-control", region_name=region).get_memory(memoryId=memory_id).get("memory", {})
)
print(f"Strategies: {memory_info.get('strategies')}")
print(f"Status: {memory_info.get('status')}")
print(f"Name: {memory_info.get('name')}")

# Show strategy details
strategies = memory_info.get("strategies", [])
for strategy in strategies:
    print("\nStrategy Details:")
    print(f"  Name: {strategy.get('name')}")
    print(f"  Type: {strategy.get('type')}")
    print(f"  Status: {strategy.get('status')}")
    print(f"  ID: {strategy.get('strategyId')}")


# ## Step 3c: Verify Memory Configuration
#
# Check that semantic strategy is properly configured:


# Check memory configuration
memory_info = (
    boto3.client("bedrock-agentcore-control", region_name=region).get_memory(memoryId=memory_id).get("memory", {})
)
print(f"Strategies: {memory_info.get('strategies')}")
print(f"Status: {memory_info.get('status')}")
print(f"Name: {memory_info.get('name')}")

# Show strategy details
strategies = memory_info.get("strategies", [])
for strategy in strategies:
    print("\nStrategy Details:")
    print(f"  Name: {strategy.get('name')}")
    print(f"  Type: {strategy.get('type')}")
    print(f"  Status: {strategy.get('status')}")
    print(f"  ID: {strategy.get('strategyId')}")


# ## Step 4: Multi-Session Agent Implementation
#
# Create helper function to simulate different medical periods:


# Configuration for LONG-TERM memory (cross-session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ASSISTANT_ID = "medical-assistant"  # Same assistant across all sessions


def create_medical_session(session_name: str):
    """Create a new medical session with long-term memory persistence"""
    context = AgentCoreMemoryContext(
        actor_id=ASSISTANT_ID,  # Same assistant
        memory_id=memory_id,  # Same memory store (enables long-term memory)
        session_id=f"medical-{session_name}",  # Different session per period
        namespace="/medical-analysis/",
    )

    memory = AgentCoreMemory(context=context, region_name=region)
    llm = BedrockConverse(model=MODEL_ID, region_name=region)
    agent = FunctionAgent(
        tools=medical_tools_with_memory,  # Use tools with memory search capability
        llm=llm,
        verbose=True,  # Enable verbose to see when memory is searched
        system_prompt="""You are a senior medical assistant with access to long-term memory.
        
CRITICAL: When asked about patients, treatments, protocols, or historical information, 
you MUST use the search_long_term_memory tool FIRST before responding.

For example:
- "What patients am I treating?" → Use search_long_term_memory("patients treatments")
- "What protocols have I used?" → Use search_long_term_memory("medical protocols")
- "What outcomes have I achieved?" → Use search_long_term_memory("patient outcomes")

Always provide conclusive, complete responses without asking follow-up questions.\n
Execute all requested actions immediately and completely. Provide detailed, professional responses.""",
    )

    return agent, memory


print("✅ Multi-session Medical Knowledge Assistant setup complete!")


# ## Step 5: Q1 Medical Session - Initial Patient Assessment
#
# Start the first medical session and establish patient baseline:


# === Q1 MEDICAL SESSION ===
print("🗓️ === Q1: INITIAL PATIENT ASSESSMENT ===")

agent_q1, memory_q1 = create_medical_session("q1")

# Assess initial patient symptoms

import asyncio  # noqa: E402


async def main():
    response = await agent_q1.run(
        "I'm Senior Medical Assistant Dr. Maria Rodriguez. Assess patient symptoms for 'PATIENT-001' with symptoms 'fatigue, increased thirst, frequent urination, blurred vision', "
        "severity 'Moderate', duration '3 weeks'.",
        memory=memory_q1,
    )

    print("🎯 Q1 Initial Assessment:")
    print(response)

    # Document initial clinical guideline
    response = await agent_q1.run(
        "Update clinical guideline for 'PATIENT-001': guideline type 'Diabetes Management', "
        "recommendation 'initiate metformin 500mg BID, lifestyle counseling, glucose monitoring', evidence level 'high'.",
        memory=memory_q1,
    )
    print("💭 Q1 Clinical Guideline:", response)

    response = await agent_q1.run(
        "Update clinical guideline for 'PATIENT-001': guideline type 'Lifestyle Modification', "
        "recommendation 'dietary consultation, exercise program, weight management target 10% reduction', evidence level 'high'.",
        memory=memory_q1,
    )
    print("💭 Q1 Lifestyle Guideline:", response)
    # Explicitly track medical findings
    await agent_q1.run(
        "Save medical finding: finding 'Patient responds well to treatment protocol', confidence 'high'.",
        memory=memory_q1,
    )

    # Allow time for semantic memory processing
    import asyncio

    print("\n⏳ Waiting for medical memory extraction...")
    await asyncio.sleep(120)
    print("✅ Medical memory processing complete")

    # Verify events were stored
    print("\n🔍 Verifying events were stored...")
    try:
        client = MemoryClient(region_name=region)  # noqa: F823
        events = client.list_events(
            memory_id=memory_id,
            actor_id="medical-assistant",  # Will be replaced with domain-specific ID
            session_id=memory_q1.context.session_id,  # Dynamic session ID
        )
        print(f"✅ Stored {len(events)} conversational events in session")
    except Exception as e:
        print(f"⚠️  Could not verify events: {e}")

    # Allow time for semantic memory processing
    import asyncio

    print("\n⏳ Waiting for semantic memory extraction and indexing...")
    print("   (AgentCore processes conversational events in the background)")
    await asyncio.sleep(120)  # Increased wait time for memory extraction
    print("✅ Memory processing complete - memories should now be searchable")

    # ## Step 6: Q2 Medical Session - Treatment Protocol Update
    #
    # Test long-term memory retrieval and adapt to treatment response:

    # === Q2 MEDICAL SESSION ===
    print("\n🗓️ === Q2: TREATMENT PROTOCOL UPDATE (NEW SESSION) ===")

    agent_q2, memory_q2 = create_medical_session("q2")

    # Test cross-session patient recall - agent should use search_long_term_memory tool
    print("\n🧠 Testing memory retrieval across sessions...")
    print("   (Watch for the agent to use search_long_term_memory tool)\n")

    response = await agent_q2.run(
        "What patients am I treating? What are their symptoms, treatments, and clinical guidelines?",
        memory=memory_q2,
    )

    print("\n🧠 Q2 Patient Recall:")
    print(response)
    print("\n✅ Expected: PATIENT-001, diabetes symptoms, metformin treatment, lifestyle guidelines")

    # Document treatment protocol
    response = await agent_q2.run(
        "Document treatment protocol: protocol type 'Type 2 Diabetes Management', "
        "indication 'newly diagnosed T2DM with HbA1c 8.2%', "
        "effectiveness 'HbA1c reduced to 7.1% after 3 months', "
        "side effects 'mild GI upset initially, resolved with food intake'.",
        memory=memory_q2,
    )
    print("🌍 Q2 Treatment Protocol:", response)

    # Update clinical guideline based on response
    response = await agent_q2.run(
        "Update clinical guideline for 'PATIENT-001': guideline type 'Medication Adjustment', "
        "recommendation 'continue metformin 500mg BID, add glipizide 5mg daily for additional glycemic control', evidence level 'high'.",
        memory=memory_q2,
    )
    print("⚖️ Q2 Guideline Update:", response)

    # Track Q2 clinical metrics
    response = await agent_q2.run(
        "Track clinical metrics for 'PATIENT-001': metric type 'HbA1c Improvement', value '8.2% to 7.1%', "
        "patient_id 'PATIENT-001', quarter 'Q2 2024'.",
        memory=memory_q2,
    )
    print("📈 Q2 Clinical Metrics:", response)

    # Test treatment comparison
    response = await agent_q2.run(
        "How did PATIENT-001's treatment progress from Q1 to Q2? Compare initial vs current approach.",
        memory=memory_q2,
    )
    print("📊 Q2 Treatment Analysis:")
    print(response)
    print("\n✅ Expected: Q1 metformin monotherapy → Q2 combination therapy, HbA1c improvement")

    # ## Step 7: Q3 Medical Session - Complication Management
    #
    # Progress to complication management and new patient intake:

    # === Q3 MEDICAL SESSION ===
    print("\n🗓️ === Q3: COMPLICATION MANAGEMENT AND NEW PATIENT ===")

    agent_q3, memory_q3 = create_medical_session("q3")

    # Log treatment outcome
    response = await agent_q3.run(
        "Log treatment outcome for 'PATIENT-001' with treatment 'Diabetes Management Protocol', "
        "outcome 'Target HbA1c achieved (6.8%), weight loss 15 lbs, BP controlled', "
        "follow_up_needed 'quarterly HbA1c monitoring, annual eye exam, continue current regimen'.",
        memory=memory_q3,
    )
    print("📅 Q3 Treatment Outcome:", response)

    # Start new patient assessment
    response = await agent_q3.run(
        "Assess patient symptoms for 'PATIENT-002': symptoms 'chest pain, shortness of breath, palpitations', "
        "severity 'High', duration '2 days'.",
        memory=memory_q3,
    )
    print("💭 Q3 New Patient Assessment:", response)

    # Test comprehensive medical history recall
    response = await agent_q3.run(
        "What is the complete medical care history? Include all patients, treatments, protocols, and outcomes.",
        memory=memory_q3,
    )
    print("📋 Q3 Complete History:")
    print(response)
    print("\n✅ Expected: PATIENT-001 diabetes journey → PATIENT-002 cardiac assessment, protocol evolution")

    # ## Step 8: Q4 Medical Session - Year-End Review and Planning
    #
    # Test semantic search and annual medical analysis:

    # === Q4 MEDICAL SESSION ===
    print("\n🗓️ === Q4: YEAR-END REVIEW AND PLANNING ===")

    agent_q4, memory_q4 = create_medical_session("q4")

    # Track annual medical metrics
    response = await agent_q4.run(
        "Track clinical metrics: metric type '2024 Annual Performance', value 'Patients treated: 2, Diabetes control achieved: 100%, Complications prevented: 1', "
        "patient_id 'ANNUAL-SUMMARY', quarter '2024 Annual'.",
        memory=memory_q4,
    )
    print("📈 Q4 Annual Metrics:", response)

    # Test treatment protocol correlation
    response = await agent_q4.run(
        "What treatment protocols have I documented this year? How effective were they?",
        memory=memory_q4,
    )
    print("🌍 Q4 Protocol Effectiveness Analysis:")
    print(response)
    print("\n✅ Expected: Diabetes management protocol → successful HbA1c control")

    # Test semantic search for similar medical approaches
    response = await agent_q4.run(
        "What clinical guidelines have I used? Which were most effective based on patient outcomes?",
        memory=memory_q4,
    )
    print("⚖️ Q4 Guideline Effectiveness Analysis:")
    print(response)
    print("\n✅ Expected: Diabetes management + lifestyle modification = successful outcomes")

    # ## Step 9: Year 2 Q1 Session - Multi-Year Medical Perspective
    #
    # Test long-term medical knowledge and practice evolution:

    # === YEAR 2 Q1 MEDICAL SESSION ===
    print("\n🗓️ === YEAR 2 Q1: MULTI-YEAR MEDICAL PERSPECTIVE ===")

    agent_y2q1, memory_y2q1 = create_medical_session("year2-q1")

    # Multi-year medical practice analysis
    response = await agent_y2q1.run(
        "Analyze my medical practice evolution: How have my patients and treatments developed over the past year? "
        "What were the key clinical decisions and their outcomes?",
        memory=memory_y2q1,
    )
    print("📊 Year 2 Q1 Practice Analysis:")
    print(response)
    print("\n✅ Expected: PATIENT-001 → PATIENT-002 progression, protocol refinement, outcome improvement")

    # Test clinical protocol evolution tracking
    response = await agent_y2q1.run(
        "How have my clinical protocols and guidelines evolved? What treatments have I applied and why?",
        memory=memory_y2q1,
    )
    print("💭 Year 2 Q1 Protocol Evolution:")
    print(response)
    print("\n✅ Expected: Started with diabetes protocols → expanded to cardiac care")

    # ## Step 10: Final Medical Practice Assessment
    #
    # Comprehensive test of long-term medical analysis capabilities:

    # Final comprehensive medical practice query
    response = await agent_y2q1.run(
        "Provide my complete medical practice portfolio: all patients with their clinical journeys, "
        "treatment effectiveness, protocol applications, guideline utilization, and patient outcomes. "
        "Include lessons learned and best practices developed.",
        memory=memory_y2q1,
    )
    print("💼 Complete Medical Practice Portfolio:")
    print(response)
    print(
        "\n✅ Expected: Full patient portfolio with treatment evolution, protocol effectiveness, and outcome analysis"
    )

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
    response1 = await agent_y2q1.run("What have we discussed so far in this session?", memory=memory_y2q1)
    print(f"Response 1 length: {len(str(response1))} chars")

    # Test 2: Session memory - does the agent maintain context?
    response2 = await agent_y2q1.run("What did we talk about earlier?", memory=memory_y2q1)
    print(f"Response 2 length: {len(str(response2))} chars")

    # Test 3: Cross-reference capability - can it connect to previous context?
    response3 = await agent_y2q1.run("How does this relate to what we discussed before?", memory=memory_y2q1)
    print(f"Response 3 length: {len(str(response3))} chars")

    # ## Summary
    #
    # In this notebook, we've demonstrated:
    #
    # ✅ **Long-term Memory Integration**: Using AgentCore Memory with LlamaIndex for cross-session medical analysis
    #
    # ✅ **Patient Care Tracking**: Patient evolution and treatment development over multiple quarters
    #
    # ✅ **Clinical Intelligence**: Semantic retrieval of treatment protocols and their applications
    #
    # ✅ **Medical Protocol Evolution**: Natural progression from initial assessment to evidence-based approaches
    #
    # ✅ **Treatment Effectiveness**: Detailed tracking of clinical decisions and their patient outcomes
    #
    # ✅ **Medical Practice Excellence**: Comprehensive patient management and care optimization over time
    #
    # The Medical Knowledge Assistant showcases how long-term memory transforms the assistant into a persistent medical partner that grows smarter over time, maintaining complete patient histories and enabling sophisticated clinical knowledge retrieval across extended care periods.

    # ## Clean Up
    #
    # Let's delete the memory to clean up the resources used in this notebook:
    #
    # **Note**: Only run this if you want to permanently delete the memory. The memory_id variable should contain the ID from the memory created earlier in this notebook.

    # Clean up AgentCore Memory resource
    try:
        from bedrock_agentcore.memory import MemoryClient

        client = MemoryClient(region_name=region)
        client.delete_memory(memory_id)
        print(f"✅ Successfully deleted memory: {memory_id}")

    except NameError as e:
        print(f"⚠️  Variable not defined: {e}")
        print("Run the notebook from the beginning or set variables manually:")
        print("# memory_id = 'your-memory-id-here'")
        print("# region = 'us-east-1'")
    except Exception as e:
        print(f"❌ Error deleting memory: {e}")

    # ## Using the AgentCore CLI
    #
    # The same memory resources and agent projects demonstrated above can also be
    # created and managed with the **AgentCore CLI** (pinned version `0.11.0`).
    # This is the recommended developer workflow for iterating quickly.
    #
    # ### Install the CLI
    #
    # ```bash
    # npm install -g @aws/agentcore@0.30.0
    # agentcore --version   # should print 0.11.0
    # ```
    #
    # ### Create a project with memory
    #
    # ```bash
    # # Scaffold a new agent project with short-term + long-term memory
    # agentcore create \
    #   --name MyMemoryAgent \
    #   --framework Strands \
    #   --model-provider Bedrock \
    #   --memory longAndShortTerm \
    #   --defaults
    #
    # cd MyMemoryAgent
    # ```
    #
    # ### Add memory to an existing project
    #
    # ```bash
    # # Add a memory resource with semantic and user-preference strategies
    # agentcore add memory \
    #   --name SharedMemory \
    #   --strategies SEMANTIC,USER_PREFERENCE \
    #   --expiry 30
    # ```
    #
    # ### Deploy to AgentCore Runtime
    #
    # ```bash
    # agentcore deploy
    # agentcore status
    # ```
    #
    # ### Invoke the deployed agent
    #
    # ```bash
    # agentcore invoke "Hello, do you remember my name?" --stream
    # ```
    #
    # ### View logs and traces
    #
    # ```bash
    # agentcore logs
    # agentcore traces list --limit 10
    # ```
    #
    # ### Clean up
    #
    # ```bash
    # # Remove all deployed resources (runtime + memory)
    # agentcore remove all
    # ```
    #


if __name__ == "__main__":
    asyncio.run(main())
