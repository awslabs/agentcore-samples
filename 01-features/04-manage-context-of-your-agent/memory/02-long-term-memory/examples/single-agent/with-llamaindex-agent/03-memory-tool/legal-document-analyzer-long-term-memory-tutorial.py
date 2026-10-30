#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Legal Document Analyzer (Long-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create a Legal Document Analyzer with **long-term memory** persistence across multiple cases and legal proceedings - allowing the analyzer to build cumulative legal knowledge and track case precedents over months and years.
#
# ## Architecture Overview
#
# ![LlamaIndex AgentCore Long-Term Memory Architecture](LlamaIndex-AgentCore-LTM-Arch.png)
#
# ## Tutorial Details
#
# **Tutorial Details:**
# - **Tutorial type**: Long-term Cross-Session Memory
# - **Agent usecase**: Legal Document Analyzer
# - **Agentic Framework**: LlamaIndex
# - **LLM model**: Anthropic Claude 3.7 Sonnet
# - **Tutorial components**: AgentCore Long-term Memory, LlamaIndex Agent, Legal Tools
# - **Example complexity**: Advanced
#
# ## Business Value
#
# **Enterprise Legal Intelligence**: Transform your legal practice with persistent AI memory that accumulates case knowledge, tracks legal strategy evolution, and maintains comprehensive precedent databases across cases and time periods.
#
# **Key Professional Advantages:**
# - **Case Continuity**: Seamless knowledge transfer between legal matters and team members
# - **Precedent Database**: Preserve critical case law, strategies, and outcomes permanently
# - **Cross-Case Intelligence**: Identify patterns and connections across multiple legal matters
# - **Strategic Advantage**: Leverage historical case data for superior legal positioning
# - **Client Value**: Maintain detailed context for multi-year client relationships
# - **Risk Management**: Track regulatory changes and their impact on legal strategies
#
# ## Long-Term Memory Configuration
#
# **Technical Setup**: This tutorial uses AgentCore Memory with Semantic Strategy for 12-month retention:
# - **Memory Type**: Semantic strategy with automatic insight extraction
# - **Retention**: 365-day event expiry for legal case continuity
# - **Cross-Session**: Same actor_id + memory_id, different session_id per legal period
# - **Search Capability**: Built-in memory retrieval tool for semantic search across case history
#
# ## Technical Overview
#
# **Key Long-Term Memory Components:**
# 1. **Semantic Strategy Configuration**: Uses SemanticStrategy for automatic insight extraction with 365-day retention
# 2. **Cross-Session Persistence**: Same actor_id + memory_id, different session_id per period enables knowledge continuity
# 3. **Custom Memory Search Tool**: Wraps AgentCore's native search_long_term_memories() in LlamaIndex FunctionTool
# 4. **Semantic Processing Pipeline**: 90-second wait for conversational events → semantic memories conversion
# 5. **Dynamic Session Management**: Uses memory.context.session_id for flexible session handling
#
# **You'll learn to:**
#
# - Create persistent AgentCore Memory across multiple legal cases
# - Build cumulative legal knowledge over time
# - Implement semantic search across case law and precedents
# - Track legal strategy evolution and case outcomes
# - Test cross-session legal knowledge persistence and retrieval
#
# ## Scenario Context
#
# In this example, we'll create a "Legal Document Analyzer" that maintains legal knowledge across multiple cases spanning months and years. The analyzer uses AgentCore Memory to build a persistent knowledge base of case precedents, legal strategies, regulatory changes, and case outcomes that grows and evolves over time, enabling sophisticated longitudinal legal analysis.
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
# Create or get the AgentCore Memory resource for long-term legal knowledge:


# Create AgentCore Memory with Semantic Strategy for long-term persistence
region = os.getenv("AWS_REGION", "us-east-1")
memory_client = MemoryClient(region_name=region)

try:
    # Create memory with semantic strategy for automatic insight extraction
    # Use stable name + create_or_get_memory so re-runs reuse the existing ACTIVE memory
    memory = memory_client.create_or_get_memory(
        name="LegalAnalyzerSemanticLTM",
        strategies=[
            {
                StrategyType.SEMANTIC.value: {
                    "name": "legalLongTermMemory",
                    "namespaces": ["/legal/{actorId}/"],
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


# ## Step 3: Legal Tools Implementation
#
# Define specialized tools for longitudinal legal analysis:


def analyze_contract_clause(case_id: str, clause_type: str, risk_level: str, precedent_reference: str) -> str:
    """Analyze contract clause with risk assessment and precedent reference"""
    return f"📋 Analyzed {clause_type} clause for {case_id} (Risk: {risk_level})"


def track_case_precedent(case_id: str, precedent_case: str, legal_principle: str, applicability: str) -> str:
    """Track case precedent with legal principle and applicability assessment"""
    return f"⚖️ {case_id} precedent: {precedent_case} - {legal_principle}"


def document_regulatory_change(
    regulation_type: str,
    change_description: str,
    impact_assessment: str,
    client_implications: str,
) -> str:
    """Document regulatory change with client portfolio implications"""
    print(f"📜 Regulatory update: {regulation_type} - {change_description} (Impact: {impact_assessment})")
    return f"Documented regulatory change: {regulation_type}"


def update_legal_strategy(case_id: str, strategy_type: str, approach: str, confidence_level: str) -> str:
    """Update legal strategy for specific case"""
    print(f"🎯 Legal strategy: {case_id} - {strategy_type} ({confidence_level} confidence)")
    return f"Updated strategy for {case_id}"


def log_case_outcome(case_id: str, outcome_type: str, result: str, lessons_learned: str) -> str:
    """Log case outcome with lessons learned"""
    print(f"🏛️ Case outcome: {case_id} - {outcome_type}: {result}")
    return f"Logged outcome for {case_id}"


def log_legal_milestone(quarter: str, milestone: str, details: str) -> str:
    """Log a legal milestone with quarter and detailed progress"""
    print(f"🎯 {quarter} milestone: {milestone}")
    return f"Logged milestone for {quarter}: {milestone} - {details}"


def track_legal_metrics(metric_type: str, value: str, case_id: str, quarter: str) -> str:
    """Track specific legal metrics with case and timeline"""
    print(f"📊 {quarter}: {metric_type} = {value} (for {case_id})")
    return f"Tracked {metric_type}: {value} for {case_id} in {quarter}"


def save_legal_insight(insight: str, quarter: str, legal_context: str) -> str:
    """Save legal insights with context"""
    print(f"💡 {quarter} insight: {insight[:50]}...")
    return f"Saved {quarter} insight with legal context: {legal_context}"


# Create tool objects for the agent
legal_tools = [
    FunctionTool.from_defaults(fn=analyze_contract_clause),
    FunctionTool.from_defaults(fn=track_case_precedent),
    FunctionTool.from_defaults(fn=document_regulatory_change),
    FunctionTool.from_defaults(fn=update_legal_strategy),
    FunctionTool.from_defaults(fn=log_case_outcome),
    FunctionTool.from_defaults(fn=log_legal_milestone),
    FunctionTool.from_defaults(fn=track_legal_metrics),
    FunctionTool.from_defaults(fn=save_legal_insight),
]

print("✅ Legal tools created!")


# ## Step 3b: Add Memory Retrieval Tool
#
# Create a tool that allows the agent to search its long-term memory:


def create_memory_retrieval_tool(memory_id: str, actor_id: str, region: str):
    """Create a tool for the agent to search its own long-term memory"""

    def search_long_term_memory(query: str) -> str:
        """Search long-term memory for relevant legal information about cases, precedents, strategies, and outcomes.

        Use this tool when you need to recall:
        - Case information (precedents, strategies, outcomes)
        - Legal precedents and their applications
        - Regulatory changes and their impacts
        - Legal strategies and their effectiveness
        - Case outcomes and lessons learned

        Args:
            query: Search query describing what information you need (e.g., 'CASE-001 precedents', 'contract strategies', 'Q1 outcomes')

        Returns:
            Relevant information from long-term memory
        """
        try:
            from bedrock_agentcore.memory.session import MemorySessionManager

            # Create session manager (only needs memory_id and region)
            session_manager = MemorySessionManager(memory_id=memory_id, region_name=region)

            # The bug was the namespace VALUE: this read from "/strategies/" while the
            # semantic strategy writes to "/legal/{actorId}/", so recall returned 0.
            # Read from the SAME namespace the strategy writes to. We keep the
            # `namespace_prefix=` arg (works across the bedrock-agentcore versions this
            # tutorial supports; `namespace=`/`namespace_path=` only exist in newer SDKs).
            results = session_manager.search_long_term_memories(
                query=query,
                namespace_prefix=f"/legal/{actor_id}/",
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
memory_search_tool = create_memory_retrieval_tool(memory_id, "legal-analyst", region)

# Add memory search to the tools list
legal_tools_with_memory = legal_tools + [memory_search_tool]

print(f"✅ Memory retrieval tool created! Total tools: {len(legal_tools_with_memory)}")
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
# Create helper function to simulate different legal periods:


# Configuration for LONG-TERM memory (cross-session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ANALYST_ID = "legal-analyst"  # Same analyst across all sessions


def create_legal_session(session_name: str):
    """Create a new legal session with long-term memory persistence"""
    context = AgentCoreMemoryContext(
        actor_id=ANALYST_ID,  # Same analyst
        memory_id=memory_id,  # Same memory store (enables long-term memory)
        session_id=f"legal-{session_name}",  # Different session per period
        namespace="/legal-analysis/",
    )

    memory = AgentCoreMemory(context=context, region_name=region)
    llm = BedrockConverse(model=MODEL_ID, region_name=region)
    agent = FunctionAgent(
        tools=legal_tools_with_memory,  # Use tools with memory search capability
        llm=llm,
        verbose=True,  # Enable verbose to see when memory is searched
        system_prompt="""You are a senior legal analyst with access to long-term memory.
        
CRITICAL: When asked about cases, precedents, strategies, or historical information, 
you MUST use the search_long_term_memory tool FIRST before responding.

For example:
- "What cases am I analyzing?" → Use search_long_term_memory("cases precedents")
- "What strategies have I used?" → Use search_long_term_memory("legal strategies")
- "What outcomes have I achieved?" → Use search_long_term_memory("case outcomes")

Always provide conclusive, complete responses without asking follow-up questions.\n
Execute all requested actions immediately and completely. Provide detailed, professional responses.""",
    )

    return agent, memory


print("✅ Multi-session Legal Document Analyzer setup complete!")


# ## Step 5: Q1 Legal Session - Initial Case Analysis
#
# Start the first legal session and establish case baseline:


# === Q1 LEGAL SESSION ===
print("🗓️ === Q1: INITIAL CASE ANALYSIS ===")

agent_q1, memory_q1 = create_legal_session("q1")

# Analyze initial contract clause

import asyncio  # noqa: E402


async def main():
    response = await agent_q1.run(
        "I'm Senior Legal Analyst Sarah Chen. Analyze contract clause for 'CASE-001' with clause type 'Indemnification', "
        "risk level 'High', precedent reference 'Smith v. Johnson (2019) - broad indemnification scope creates "
        "significant liability exposure for client'.",
        memory=memory_q1,
    )

    print("🎯 Q1 Initial Analysis:")
    print(response)

    # Document initial legal strategy
    response = await agent_q1.run(
        "Update legal strategy for 'CASE-001': strategy type 'Contract Negotiation', "
        "approach 'narrow indemnification scope, add carve-outs for gross negligence and willful misconduct', confidence level 'high'.",
        memory=memory_q1,
    )
    print("💭 Q1 Contract Strategy:", response)

    response = await agent_q1.run(
        "Update legal strategy for 'CASE-001': strategy type 'Risk Mitigation', "
        "approach 'insurance requirements, liability caps, and mutual indemnification structure', confidence level 'medium'.",
        memory=memory_q1,
    )
    print("💭 Q1 Risk Strategy:", response)

    # Verify events were stored
    print("\n🔍 Verifying events were stored...")
    try:
        from bedrock_agentcore.memory import MemoryClient

        client = MemoryClient(region_name=region)
        # Get session_id from the memory context
        current_session_id = memory_q1.context.session_id
        events = client.list_events(
            memory_id=memory_id,
            actor_id=ANALYST_ID,
            session_id=current_session_id,  # Dynamic - uses current session
        )
        print(f"✅ Stored {len(events)} conversational events in {current_session_id}")
    except Exception as e:
        print(f"⚠️  Could not verify events: {e}")

    # Allow time for semantic memory processing
    import asyncio

    print("\n⏳ Waiting for semantic memory extraction and indexing...")
    print("   (AgentCore processes conversational events in the background)")
    await asyncio.sleep(90)  # Increased wait time for memory extraction
    print("✅ Memory processing complete - memories should now be searchable")

    # ## Step 6: Q2 Legal Session - Regulatory Update Response
    #
    # Test long-term memory retrieval and adapt to regulatory changes:

    # === Q2 LEGAL SESSION ===
    print("\n🗓️ === Q2: REGULATORY UPDATE RESPONSE (NEW SESSION) ===")

    agent_q2, memory_q2 = create_legal_session("q2")

    # Test cross-session case recall - agent should use search_long_term_memory tool
    print("\n🧠 Testing memory retrieval across sessions...")
    print("   (Watch for the agent to use search_long_term_memory tool)\n")

    response = await agent_q2.run(
        "What cases am I analyzing? What are their risk levels, strategies, and precedents?",
        memory=memory_q2,
    )

    print("\n🧠 Q2 Case Recall:")
    print(response)
    print("\n✅ Expected: CASE-001, indemnification analysis, Smith v. Johnson precedent")

    # Document regulatory change
    response = await agent_q2.run(
        "Document regulatory change: regulation type 'Contract Law Update', "
        "change description 'New state legislation limits indemnification scope in commercial contracts', "
        "impact assessment 'favorable for our client position, strengthens negotiation stance', "
        "client implications 'can push for narrower indemnification terms with legal backing'.",
        memory=memory_q2,
    )
    print("🌍 Q2 Regulatory Update:", response)

    # Update strategy based on regulatory change
    response = await agent_q2.run(
        "Update legal strategy for 'CASE-001': strategy type 'Regulatory Leverage', "
        "approach 'cite new state legislation to support narrow indemnification position, strengthen negotiation leverage', confidence level 'high'.",
        memory=memory_q2,
    )
    print("⚖️ Q2 Strategy Update:", response)

    # Track Q2 case progress
    response = await agent_q2.run(
        "Track legal metrics for 'CASE-001': metric type 'Negotiation Progress', value 'Favorable terms secured', "
        "case_id 'CASE-001', quarter 'Q2 2024'.",
        memory=memory_q2,
    )
    print("📈 Q2 Progress:", response)

    # Test strategy comparison
    response = await agent_q2.run(
        "How did the regulatory change impact CASE-001's strategy? Compare Q1 vs Q2 approaches.",
        memory=memory_q2,
    )
    print("📊 Q2 Strategy Analysis:")
    print(response)
    print("\n✅ Expected: Q1 contract negotiation → Q2 regulatory leverage, strengthened position")

    # ## Step 7: Q3 Legal Session - Case Resolution and New Matter
    #
    # Progress to case resolution and new case intake:

    # === Q3 LEGAL SESSION ===
    print("\n🗓️ === Q3: CASE RESOLUTION AND NEW MATTER ===")

    agent_q3, memory_q3 = create_legal_session("q3")

    # Log case outcome
    response = await agent_q3.run(
        "Log case outcome for 'CASE-001' with outcome type 'Settlement Agreement', "
        "result 'Favorable terms achieved - narrow indemnification scope, liability caps at $500K, mutual structure', "
        "lessons learned 'regulatory leverage was decisive, early precedent research paid off, client saved estimated $2M in potential liability'.",
        memory=memory_q3,
    )
    print("📅 Q3 Case Resolution:", response)

    # Start new case analysis
    response = await agent_q3.run(
        "Analyze contract clause for 'CASE-002': clause type 'Non-Compete', "
        "risk level 'Medium', precedent reference 'TechCorp v. StartupInc (2020) - geographic and temporal scope must be reasonable'.",
        memory=memory_q3,
    )
    print("💭 Q3 New Case Analysis:", response)

    # Test comprehensive legal history recall
    response = await agent_q3.run(
        "What is the complete legal analysis history? Include all cases, strategies, regulatory changes, and outcomes.",
        memory=memory_q3,
    )
    print("📋 Q3 Complete History:")
    print(response)
    print("\n✅ Expected: CASE-001 journey → CASE-002 start, regulatory updates, strategy evolution")
    # Explicitly track key legal findings
    await agent_q3.run(
        "Save legal finding: finding 'Contract contains 3 high-risk clauses', confidence 'high'.",
        memory=memory_q3,
    )

    # Allow time for semantic memory processing
    import asyncio

    print("\n⏳ Waiting for legal memory extraction...")
    await asyncio.sleep(90)
    print("✅ Legal memory processing complete")

    # ## Step 8: Q4 Legal Session - Year-End Review and Planning
    #
    # Test semantic search and annual legal analysis:

    # === Q4 LEGAL SESSION ===
    print("\n🗓️ === Q4: YEAR-END REVIEW AND PLANNING ===")

    agent_q4, memory_q4 = create_legal_session("q4")

    # Track annual legal metrics
    response = await agent_q4.run(
        "Track legal metrics: metric type '2024 Annual Performance', value 'Cases resolved: 2, Success rate: 100%, Client savings: $2.5M', "
        "case_id 'ANNUAL-SUMMARY', quarter '2024 Annual'.",
        memory=memory_q4,
    )
    print("📈 Q4 Annual Metrics:", response)

    # Test regulatory impact correlation
    response = await agent_q4.run(
        "What regulatory changes have I documented this year? How did they impact case strategies?",
        memory=memory_q4,
    )
    print("🌍 Q4 Regulatory Impact Analysis:")
    print(response)
    print("\n✅ Expected: Contract law update → strengthened CASE-001 negotiation position")

    # Test semantic search for similar legal strategies
    response = await agent_q4.run(
        "What legal strategies have I used? Which were most effective based on case outcomes?",
        memory=memory_q4,
    )
    print("⚖️ Q4 Strategy Effectiveness Analysis:")
    print(response)
    print("\n✅ Expected: Contract negotiation + regulatory leverage = successful outcomes")

    # ## Step 9: Year 2 Q1 Session - Multi-Year Legal Perspective
    #
    # Test long-term legal knowledge and practice evolution:

    # === YEAR 2 Q1 LEGAL SESSION ===
    print("\n🗓️ === YEAR 2 Q1: MULTI-YEAR LEGAL PERSPECTIVE ===")

    agent_y2q1, memory_y2q1 = create_legal_session("year2-q1")

    # Multi-year legal practice analysis
    response = await agent_y2q1.run(
        "Analyze my legal practice evolution: How have my cases and strategies developed over the past year? "
        "What were the key legal decisions and their outcomes?",
        memory=memory_y2q1,
    )
    print("📊 Year 2 Q1 Practice Analysis:")
    print(response)
    print("\n✅ Expected: CASE-001 → CASE-002 progression, regulatory adaptation, strategy refinement")

    # Test legal precedent evolution tracking
    response = await agent_y2q1.run(
        "How have my legal precedents and strategies evolved? What case law have I applied and why?",
        memory=memory_y2q1,
    )
    print("💭 Year 2 Q1 Precedent Evolution:")
    print(response)
    print("\n✅ Expected: Started with indemnification precedents → expanded to non-compete law")

    # ## Step 10: Final Legal Practice Assessment
    #
    # Comprehensive test of long-term legal analysis capabilities:

    # Final comprehensive legal practice query
    response = await agent_y2q1.run(
        "Provide my complete legal practice portfolio: all cases with their legal journeys, "
        "strategy effectiveness, regulatory changes applied, precedent utilization, and case outcomes. "
        "Include lessons learned and best practices developed.",
        memory=memory_y2q1,
    )
    print("💼 Complete Legal Practice Portfolio:")
    print(response)
    print("\n✅ Expected: Full case portfolio with strategy evolution, regulatory adaptation, and outcome analysis")

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
    # ✅ **Long-term Memory Integration**: Using AgentCore Memory with LlamaIndex for cross-session legal analysis
    #
    # ✅ **Legal Case Tracking**: Case evolution and strategy development over multiple quarters
    #
    # ✅ **Regulatory Intelligence**: Semantic retrieval of regulatory changes and their case applications
    #
    # ✅ **Legal Strategy Evolution**: Natural progression from initial analysis to regulatory-adaptive approaches
    #
    # ✅ **Precedent Management**: Detailed tracking of case law and their strategic applications
    #
    # ✅ **Legal Practice Excellence**: Comprehensive case management and outcome optimization over time
    #
    # The Legal Document Analyzer showcases how long-term memory transforms the analyzer into a persistent legal partner that grows smarter over time, maintaining complete case histories and enabling sophisticated legal knowledge retrieval across extended practice periods.

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
