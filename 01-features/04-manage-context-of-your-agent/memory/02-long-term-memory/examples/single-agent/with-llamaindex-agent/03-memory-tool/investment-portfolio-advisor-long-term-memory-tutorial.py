#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Investment Portfolio Advisor (Long-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create an Investment Portfolio Advisor with **long-term memory** persistence across multiple client meetings and market cycles - allowing the advisor to build cumulative investment knowledge and track portfolio performance over months and years.
#
# ## Architecture Overview
#
# ![LlamaIndex AgentCore Long-Term Memory Architecture](LlamaIndex-AgentCore-LTM-Arch.png)
#
# ## Tutorial Details
#
# **Tutorial Details:**
# - **Tutorial type**: Long-term Cross-Session Memory
# - **Agent usecase**: Investment Portfolio Advisor
# - **Agentic Framework**: LlamaIndex
# - **LLM model**: Anthropic Claude 3.7 Sonnet
# - **Tutorial components**: AgentCore Long-term Memory, LlamaIndex Agent, Financial Tools
# - **Example complexity**: Advanced
#
# ## Business Value
#
# **Enterprise Investment Intelligence**: Transform your wealth management practice with persistent AI memory that accumulates portfolio knowledge, tracks investment evolution, and maintains comprehensive market analysis across quarters and years.
#
# **Key Professional Advantages:**
# - **Portfolio Continuity**: Seamless knowledge transfer between investment periods and team members
# - **Investment Memory**: Preserve critical market insights, strategies, and performance data permanently
# - **Cross-Portfolio Intelligence**: Identify patterns and connections across multiple client portfolios
# - **Strategic Excellence**: Leverage historical performance data for superior investment decisions
# - **Client Relationships**: Maintain detailed context for multi-year wealth management
# - **Risk Management**: Track market cycles and their impact on investment strategies
#
# ## Long-Term Memory Configuration
#
# **Technical Setup**: This tutorial uses AgentCore Memory with Semantic Strategy for 12-month retention:
# - **Memory Type**: Semantic strategy with automatic insight extraction
# - **Retention**: 365-day event expiry for portfolio continuity
# - **Cross-Session**: Same actor_id + memory_id, different session_id per investment period
# - **Search Capability**: Built-in memory retrieval tool for semantic search across portfolio history
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
# - Create persistent AgentCore Memory across multiple client meetings
# - Build cumulative investment knowledge over time
# - Implement semantic search across market research and client history
# - Track portfolio evolution and investment performance
# - Test cross-session financial knowledge persistence and retrieval
#
# ## Scenario Context
#
# In this example, we'll create an "Investment Portfolio Advisor" that maintains client investment knowledge across multiple meetings spanning quarters and years. The advisor uses AgentCore Memory to build a persistent knowledge base of client profiles, portfolio performance, market insights, and investment outcomes that grows and evolves over time, enabling sophisticated longitudinal wealth management.
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
# Create or get the AgentCore Memory resource for long-term investment knowledge:


# Create AgentCore Memory with Semantic Strategy for long-term persistence
region = os.getenv("AWS_REGION", "us-east-1")
memory_client = MemoryClient(region_name=region)

try:
    # Create memory with semantic strategy for automatic insight extraction
    # Use stable name + create_or_get_memory so re-runs reuse the existing ACTIVE memory
    memory = memory_client.create_or_get_memory(
        name="InvestmentAdvisorSemanticLTM",
        strategies=[
            {
                StrategyType.SEMANTIC.value: {
                    "name": "investmentLongTermMemory",
                    "namespaces": ["/investment/{actorId}/"],
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


# ## Step 3: Investment Tools Implementation
#
# Define specialized tools for longitudinal wealth management:


def record_client_meeting(client_id: str, meeting_type: str, portfolio_value: str, key_decisions: str) -> str:
    """Record client meeting with portfolio updates and decisions"""
    return f"📅 Recorded {meeting_type} for {client_id} (${portfolio_value})"


def track_portfolio_performance(
    client_id: str,
    period: str,
    return_pct: str,
    benchmark_return: str,
    attribution: str,
) -> str:
    """Track portfolio performance vs benchmark with attribution analysis"""
    return f"📈 {client_id} {period}: {return_pct} vs {benchmark_return}"


def document_market_insight(
    insight_type: str,
    market_event: str,
    impact_assessment: str,
    client_implications: str,
) -> str:
    """Document market insight with client portfolio implications"""
    print(f"🌍 Market insight: {insight_type} - {market_event} (Impact: {impact_assessment})")
    return f"Documented market insight: {insight_type}"


def update_investment_thesis(client_id: str, asset_class: str, thesis: str, conviction_level: str) -> str:
    """Update investment thesis for specific asset class"""
    print(f"💭 Investment thesis: {client_id} - {asset_class} ({conviction_level} conviction)")
    return f"Updated thesis for {client_id}"


def log_rebalancing_action(client_id: str, action_type: str, securities: str, rationale: str) -> str:
    """Log portfolio rebalancing actions with rationale"""
    print(f"⚖️ Rebalancing: {client_id} - {action_type}: {securities}")
    return f"Logged rebalancing for {client_id}"


def log_advisory_milestone(quarter: str, milestone: str, details: str) -> str:
    """Log an advisory milestone with quarter and detailed progress"""
    print(f"🎯 {quarter} milestone: {milestone}")
    return f"Logged milestone for {quarter}: {milestone} - {details}"


def track_investment_metrics(metric_type: str, value: str, client_id: str, quarter: str) -> str:
    """Track specific investment metrics with client and timeline"""
    print(f"📊 {quarter}: {metric_type} = {value} (for {client_id})")
    return f"Tracked {metric_type}: {value} for {client_id} in {quarter}"


def save_advisory_insight(insight: str, quarter: str, market_context: str) -> str:
    """Save advisory insights with market context"""
    print(f"💡 {quarter} insight: {insight[:50]}...")
    return f"Saved {quarter} insight with market context: {market_context}"


# Create tool objects for the agent
investment_tools = [
    FunctionTool.from_defaults(fn=record_client_meeting),
    FunctionTool.from_defaults(fn=track_portfolio_performance),
    FunctionTool.from_defaults(fn=document_market_insight),
    FunctionTool.from_defaults(fn=update_investment_thesis),
    FunctionTool.from_defaults(fn=log_rebalancing_action),
    FunctionTool.from_defaults(fn=log_advisory_milestone),
    FunctionTool.from_defaults(fn=track_investment_metrics),
    FunctionTool.from_defaults(fn=save_advisory_insight),
]

print("✅ Investment tools created!")


# ## Step 3b: Add Memory Retrieval Tool
#
# Create a tool that allows the agent to search its long-term memory:


def create_memory_retrieval_tool(memory_id: str, actor_id: str, region: str):
    """Create a tool for the agent to search its own long-term memory"""

    def search_long_term_memory(query: str) -> str:
        """Search long-term memory for relevant information about clients, portfolios, past decisions, and market insights.

        Use this tool when you need to recall:
        - Client information (portfolio values, risk profiles, investment goals)
        - Past investment decisions and their outcomes
        - Portfolio performance history
        - Market insights and their applications
        - Investment theses and their evolution

        Args:
            query: Search query describing what information you need (e.g., 'CLIENT-001 portfolio', 'investment theses', 'Q1 performance')

        Returns:
            Relevant information from long-term memory
        """
        try:
            from bedrock_agentcore.memory.session import MemorySessionManager

            # Create session manager (only needs memory_id and region)
            session_manager = MemorySessionManager(memory_id=memory_id, region_name=region)

            # The bug was the namespace VALUE: this read from "/strategies/" while the
            # semantic strategy writes to "/investment/{actorId}/", so recall returned 0.
            # Read from the SAME namespace the strategy writes to. We keep the
            # `namespace_prefix=` arg (works across the bedrock-agentcore versions this
            # tutorial supports; `namespace=`/`namespace_path=` only exist in newer SDKs).
            results = session_manager.search_long_term_memories(
                query=query,
                namespace_prefix=f"/investment/{actor_id}/",
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
memory_search_tool = create_memory_retrieval_tool(memory_id, "financial-advisor", region)

# Add memory search to the tools list
investment_tools_with_memory = investment_tools + [memory_search_tool]

print(f"✅ Memory retrieval tool created! Total tools: {len(investment_tools_with_memory)}")
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


# ## Step 4: Multi-Session Agent Implementation
#
# Create helper function to simulate different advisory periods:


# Configuration for LONG-TERM memory (cross-session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ADVISOR_ID = "financial-advisor"  # Same advisor across all sessions


def create_advisory_session(session_name: str):
    """Create a new advisory session with long-term memory persistence"""
    context = AgentCoreMemoryContext(
        actor_id=ADVISOR_ID,  # Same advisor
        memory_id=memory_id,  # Same memory store (enables long-term memory)
        session_id=f"advisory-{session_name}",  # Different session per period
        namespace="/wealth-management/",
    )

    memory = AgentCoreMemory(context=context, region_name=region)
    llm = BedrockConverse(model=MODEL_ID, region_name=region)
    agent = FunctionAgent(
        tools=investment_tools_with_memory,  # Use tools with memory search capability
        llm=llm,
        verbose=True,  # Enable verbose to see when memory is searched
        system_prompt="""You are a senior investment advisor with access to long-term memory.
        
CRITICAL: When asked about clients, portfolios, past decisions, or historical information, 
you MUST use the search_long_term_memory tool FIRST before responding.

For example:
- "What clients am I managing?" → Use search_long_term_memory("clients portfolio")
- "What was CLIENT-001's performance?" → Use search_long_term_memory("CLIENT-001 performance")
- "What investment theses do I have?" → Use search_long_term_memory("investment thesis")

Always provide conclusive, complete responses without asking follow-up questions.\n
Execute all requested actions immediately and completely. Provide detailed, professional responses.""",
    )

    return agent, memory


print("✅ Multi-session Investment Portfolio Advisor setup complete!")


# ## Step 5: Q1 Advisory Session - Initial Client Onboarding
#
# Start the first advisory session and establish client baseline:


# === Q1 ADVISORY SESSION ===
print("🗓️ === Q1: INITIAL CLIENT ONBOARDING ===")

agent_q1, memory_q1 = create_advisory_session("q1")

# Record initial client meeting

import asyncio  # noqa: E402


async def main():
    response = await agent_q1.run(
        "I'm Senior Advisor Jennifer Walsh. Record client meeting for 'CLIENT-001' with meeting type 'Initial Portfolio Review', "
        "portfolio value '$3,200,000', key decisions 'established moderate-aggressive risk profile, 20-year investment horizon, "
        "target allocation 70% equity/25% fixed income/5% alternatives'.",
        memory=memory_q1,
    )

    print("🎯 Q1 Initial Meeting:")
    print(response)

    # Document initial investment thesis
    response = await agent_q1.run(
        "Update investment thesis for 'CLIENT-001': asset class 'US Large Cap Equity', "
        "thesis 'overweight growth stocks due to technological innovation and earnings momentum', conviction level 'high'.",
        memory=memory_q1,
    )
    print("💭 Q1 Equity Thesis:", response)

    response = await agent_q1.run(
        "Update investment thesis for 'CLIENT-001': asset class 'Fixed Income', "
        "thesis 'short duration bias due to rising rate environment, focus on credit quality', conviction level 'medium'.",
        memory=memory_q1,
    )
    print("💭 Q1 Bond Thesis:", response)

    # Track initial performance baseline
    response = await agent_q1.run(
        "Track portfolio performance for 'CLIENT-001': period 'Q1 2024', return_pct '+8.2%', "
        "benchmark_return '+7.1%', attribution 'tech overweight +0.8%, duration underweight +0.3%'.",
        memory=memory_q1,
    )
    print("📈 Q1 Performance:", response)

    # Verify events were stored
    print("\n🔍 Verifying Q1 events were stored...")
    try:
        client = MemoryClient(region_name=region)  # noqa: F823
        events = client.list_events(
            memory_id=memory_id,
            actor_id=ADVISOR_ID,
            session_id=memory_q1.context.session_id,
        )
        print(f"✅ Stored {len(events)} conversational events in Q1 session")
    except Exception as e:
        print(f"⚠️  Could not verify events: {e}")

    # Allow time for semantic memory processing
    import asyncio

    print("\n⏳ Waiting for semantic memory extraction and indexing...")
    print("   (AgentCore processes conversational events in the background)")
    await asyncio.sleep(90)  # Increased wait time for memory extraction (was 10 seconds)
    print("✅ Memory processing complete - memories should now be searchable")

    # ## Step 6: Q2 Advisory Session - Market Volatility Response
    #
    # Test long-term memory retrieval and adapt to market changes:

    # === Q2 ADVISORY SESSION ===
    print("\n🗓️ === Q2: MARKET VOLATILITY RESPONSE (NEW SESSION) ===")

    agent_q2, memory_q2 = create_advisory_session("q2")

    # Test cross-session client recall - agent should use search_long_term_memory tool
    print("\n🧠 Testing memory retrieval across sessions...")
    print("   (Watch for the agent to use search_long_term_memory tool)\n")

    response = await agent_q2.run(
        "What clients am I managing? What are their portfolio values, risk profiles, and investment theses?",
        memory=memory_q2,
    )

    print("\n🧠 Q2 Client Recall:")
    print(response)
    print("\n✅ Expected: CLIENT-001, $3.2M portfolio, moderate-aggressive, growth equity thesis")

    # Document market volatility insight
    response = await agent_q2.run(
        "Document market insight: insight type 'Geopolitical Risk', market event 'Trade tensions escalation', "
        "impact assessment 'increased volatility, sector rotation from growth to value', "
        "client implications 'review tech overweight, consider defensive positioning'.",
        memory=memory_q2,
    )
    print("🌍 Q2 Market Insight:", response)

    # Log rebalancing response
    response = await agent_q2.run(
        "Log rebalancing action for 'CLIENT-001': action type 'Tactical Adjustment', "
        "securities 'reduced QQQ by 3%, increased VTV (value ETF) by 2%, added VGSH (short treasury) by 1%', "
        "rationale 'defensive positioning due to geopolitical uncertainty, maintain long-term allocation targets'.",
        memory=memory_q2,
    )
    print("⚖️ Q2 Rebalancing:", response)

    # Track Q2 performance impact
    response = await agent_q2.run(
        "Track portfolio performance for 'CLIENT-001': period 'Q2 2024', return_pct '-2.1%', "
        "benchmark_return '-3.8%', attribution 'defensive positioning +1.2%, value tilt +0.5%'.",
        memory=memory_q2,
    )
    print("📈 Q2 Performance:", response)

    # Test performance comparison
    response = await agent_q2.run(
        "How did CLIENT-001's Q2 performance compare to Q1? What was the cumulative return and attribution?",
        memory=memory_q2,
    )
    print("📊 Q2 Performance Analysis:")
    print(response)
    print("\n✅ Expected: Q1 +8.2%, Q2 -2.1%, cumulative ~+5.9%, defensive positioning helped")

    # ## Step 7: Q3 Advisory Session - Recovery and Thesis Update
    #
    # Progress to market recovery and update investment approach:

    # === Q3 ADVISORY SESSION ===
    print("\n🗓️ === Q3: MARKET RECOVERY AND THESIS UPDATE ===")

    agent_q3, memory_q3 = create_advisory_session("q3")

    # Record quarterly review meeting
    response = await agent_q3.run(
        "Record client meeting for 'CLIENT-001' with meeting type 'Quarterly Review', "
        "portfolio value '$3,450,000', key decisions 'market recovery positioning, increase growth allocation, "
        "add international exposure for diversification'.",
        memory=memory_q3,
    )
    print("📅 Q3 Quarterly Review:", response)

    # Update investment thesis based on market evolution
    response = await agent_q3.run(
        "Update investment thesis for 'CLIENT-001': asset class 'International Equity', "
        "thesis 'add developed market exposure via VTIAX, emerging markets recovery potential', conviction level 'medium'.",
        memory=memory_q3,
    )
    print("💭 Q3 International Thesis:", response)

    # Test comprehensive investment history recall
    response = await agent_q3.run(
        "What is the complete investment history for CLIENT-001? Include all meetings, performance periods, "
        "rebalancing actions, and evolution of investment theses.",
        memory=memory_q3,
    )
    print("📋 Q3 Complete History:")
    print(response)
    print("\n✅ Expected: Q1 onboarding → Q2 defensive moves → Q3 recovery positioning, all performance data")

    # ## Step 8: Q4 Advisory Session - Year-End Review and Planning
    #
    # Test semantic search and annual performance analysis:

    # === Q4 ADVISORY SESSION ===
    print("\n🗓️ === Q4: YEAR-END REVIEW AND PLANNING ===")

    agent_q4, memory_q4 = create_advisory_session("q4")

    # Track annual performance
    response = await agent_q4.run(
        "Track portfolio performance for 'CLIENT-001': period '2024 Annual', return_pct '+12.8%', "
        "benchmark_return '+11.2%', attribution 'tactical positioning +1.1%, sector allocation +0.5%'.",
        memory=memory_q4,
    )
    print("📈 Q4 Annual Performance:", response)

    # Test market insight correlation
    response = await agent_q4.run(
        "What market insights have I documented this year? How did they impact CLIENT-001's portfolio decisions?",
        memory=memory_q4,
    )
    print("🌍 Q4 Market Insight Analysis:")
    print(response)
    print("\n✅ Expected: Geopolitical risk insight → defensive positioning → outperformance during volatility")

    # Test semantic search for similar portfolio actions
    response = await agent_q4.run(
        "What rebalancing actions have I taken for CLIENT-001? Which were most effective based on subsequent performance?",
        memory=memory_q4,
    )
    print("⚖️ Q4 Rebalancing Analysis:")
    print(response)
    print("\n✅ Expected: Q2 defensive moves (QQQ reduction, VTV/VGSH adds) helped during volatility")

    # ## Step 9: Year 2 Q1 Session - Multi-Year Perspective
    #
    # Test long-term investment knowledge and client relationship evolution:

    # === YEAR 2 Q1 ADVISORY SESSION ===
    print("\n🗓️ === YEAR 2 Q1: MULTI-YEAR PERSPECTIVE ===")

    agent_y2q1, memory_y2q1 = create_advisory_session("year2-q1")

    # Multi-year portfolio analysis
    response = await agent_y2q1.run(
        "Analyze CLIENT-001's investment journey: How has their portfolio evolved over the past year? "
        "What were the key decisions and their outcomes?",
        memory=memory_y2q1,
    )
    print("📊 Year 2 Q1 Journey Analysis:")
    print(response)
    print("\n✅ Expected: $3.2M → $3.45M growth, defensive positioning success, thesis evolution")

    # Test investment thesis evolution tracking
    response = await agent_y2q1.run(
        "How have my investment theses for CLIENT-001 evolved? What asset classes have I added and why?",
        memory=memory_y2q1,
    )
    print("💭 Year 2 Q1 Thesis Evolution:")
    print(response)
    print(
        "\n✅ Expected: Started with US equity/fixed income → added international exposure → evolved based on market conditions"
    )

    # ## Step 10: Final Wealth Management Portfolio Assessment
    #
    # Comprehensive test of long-term investment advisory capabilities:

    # Final comprehensive wealth management portfolio query
    response = await agent_y2q1.run(
        "Provide my complete wealth management portfolio: all clients with their investment journeys, "
        "performance attribution, market insights applied, rebalancing effectiveness, and thesis evolution. "
        "Include lessons learned and best practices developed.",
        memory=memory_y2q1,
    )
    print("💼 Complete Wealth Management Portfolio:")
    print(response)
    print(
        "\n✅ Expected: Full CLIENT-001 journey with performance attribution, market timing, and investment evolution"
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
    # ✅ **Long-term Memory Integration**: Using AgentCore Memory with LlamaIndex for cross-session wealth management
    #
    # ✅ **Investment Journey Tracking**: Portfolio evolution and performance attribution over multiple quarters
    #
    # ✅ **Market Intelligence**: Semantic retrieval of market insights and their portfolio applications
    #
    # ✅ **Investment Thesis Evolution**: Natural progression from initial positioning to market-adaptive strategies
    #
    # ✅ **Performance Attribution**: Detailed tracking of tactical decisions and their investment outcomes
    #
    # ✅ **Wealth Management Excellence**: Comprehensive client relationship and portfolio optimization over time
    #
    # The Investment Portfolio Advisor showcases how long-term memory transforms the advisor into a persistent wealth management partner that grows smarter over time, maintaining complete investment histories and enabling sophisticated financial knowledge retrieval across extended client relationships.

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
