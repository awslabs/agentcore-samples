#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Investment Portfolio Advisor (Short-term Memory)
#
# ## Introduction
#
# This notebook demonstrates how to integrate Amazon Bedrock AgentCore Memory capabilities with LlamaIndex to create an Investment Portfolio Advisor. We'll focus on **short-term memory** persistence within a single client consultation session - allowing the advisor to remember client profiles, portfolio holdings, market analysis, and investment recommendations throughout a financial advisory session.
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
# | Agent usecase       | Investment Portfolio Advisor                                                     |
# | Agentic Framework   | LlamaIndex                                                                       |
# | LLM model           | Anthropic Claude 3.7 Sonnet                                                       |
# | Tutorial components | AgentCore Short-term Memory, LlamaIndex Agent, Financial Analysis Tools         |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create AgentCore Memory for financial advisory data
# - Use LlamaIndex native memory integration for investment workflows
# - Build finance-specific tools for portfolio analysis
# - Maintain financial context within a single advisory session
# - Test memory boundaries and session isolation
#
# ## Scenario Context
#
# In this example, we'll create an "Investment Portfolio Advisor" that helps financial advisors analyze client portfolios, assess risk metrics, and provide investment recommendations within a single advisory session. The advisor uses AgentCore Memory to maintain context about client profiles, portfolio holdings, market research, and investment analysis throughout the consultation.
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
# Create or get the AgentCore Memory resource for our investment advisor:


# Create AgentCore Memory resource
region = os.getenv("AWS_REGION", "us-east-1")
client = MemoryClient(region_name=region)

try:
    response = client.create_memory_and_wait(
        name=f"InvestmentAdvisorShortTerm_{int(datetime.now().timestamp())}",
        description="Investment portfolio advisor short-term memory for single session context",
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


# ## Step 3: Financial Analysis Tools Implementation
#
# Define specialized tools for investment advisory tasks:


def profile_client_risk(client_name: str, risk_tolerance: str, time_horizon: str, investment_goals: str) -> str:
    """Profile client risk tolerance and investment objectives"""
    print(f"👤 Client profile: {client_name} ({risk_tolerance} risk, {time_horizon} horizon)")
    return f"Profiled client: {client_name}"


def analyze_portfolio_holdings(portfolio_value: str, asset_allocation: str, top_holdings: str) -> str:
    """Analyze current portfolio holdings and allocation"""
    print(f"📊 Portfolio analysis: ${portfolio_value} total value, allocation: {asset_allocation}")
    return f"Analyzed portfolio worth ${portfolio_value}"


def calculate_risk_metrics(var_95: str, sharpe_ratio: str, beta: str, volatility: str) -> str:
    """Calculate portfolio risk metrics and performance indicators"""
    print(f"📈 Risk metrics: VaR 95% {var_95}, Sharpe {sharpe_ratio}, Beta {beta}, Vol {volatility}")
    return "Calculated risk metrics for portfolio"


def research_market_sector(sector: str, outlook: str, key_drivers: str, recommendation: str) -> str:
    """Research market sector with outlook and investment recommendation"""
    print(f"🔍 Sector research: {sector} - {outlook} outlook ({recommendation})")
    return f"Researched {sector} sector"


def generate_investment_recommendation(security: str, action: str, rationale: str, target_allocation: str) -> str:
    """Generate investment recommendation with rationale"""
    print(f"💡 Investment rec: {action} {security} (target: {target_allocation})")
    return f"Generated recommendation: {action} {security}"


def check_regulatory_compliance(rule_type: str, compliance_status: str, notes: str) -> str:
    """Check regulatory compliance for investment recommendations"""
    print(f"⚖️ Compliance check: {rule_type} - {compliance_status}")
    return f"Checked compliance: {rule_type}"


# Create tool objects for the agent
financial_tools = [
    FunctionTool.from_defaults(fn=profile_client_risk),
    FunctionTool.from_defaults(fn=analyze_portfolio_holdings),
    FunctionTool.from_defaults(fn=calculate_risk_metrics),
    FunctionTool.from_defaults(fn=research_market_sector),
    FunctionTool.from_defaults(fn=generate_investment_recommendation),
    FunctionTool.from_defaults(fn=check_regulatory_compliance),
]


# ## Step 4: LlamaIndex Agent Implementation
#
# Create the investment advisor agent with short-term memory context:


# Configuration for SHORT-TERM memory (single session)
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Create memory context for single session
context = AgentCoreMemoryContext(
    actor_id="financial-advisor",
    memory_id=memory_id,
    session_id="advisory-session-today",  # Same session throughout
    namespace="/investment-advisory/",
)

# Initialize AgentCore Memory and LLM
agentcore_memory = AgentCoreMemory(context=context, region_name=region)
llm = BedrockConverse(model=MODEL_ID, region_name=region)

# Create the investment advisor agent
investment_agent = FunctionAgent(tools=financial_tools, llm=llm, verbose=True)

print("✅ Investment Portfolio Advisor with short-term memory is ready!")


# ## Step 5: Testing Short-Term Memory Capabilities
#
# Let's test our investment advisor's short-term memory through a comprehensive client advisory session.

# ### Test 1: Client Onboarding and Risk Profiling


# Initialize advisory session with client details

import asyncio  # noqa: E402


async def main():
    response = await investment_agent.run(
        "I'm Financial Advisor Michael Chen meeting with client 'Robert Johnson'. "
        "Profile client risk: 'Robert Johnson' with 'moderate' risk tolerance, '15 years' time horizon, "
        "and investment goals 'retirement planning, wealth preservation, moderate growth'.",
        memory=agentcore_memory,
    )

    print("🎯 Client Onboarding:")
    print(response)

    # ### Test 2: Portfolio Holdings Analysis

    # Analyze current portfolio composition
    response = await investment_agent.run(
        "Analyze portfolio holdings: portfolio value '$2,500,000', asset allocation '60% stocks, 35% bonds, 5% cash', "
        "top holdings 'AAPL 8%, MSFT 7%, SPY 12%, BND 15%, VTIAX 10%'.",
        memory=agentcore_memory,
    )

    print("📊 Portfolio Analysis:")
    print(response)

    # ### Test 3: Risk Metrics Calculation

    # Calculate comprehensive risk metrics
    response = await investment_agent.run(
        "Calculate risk metrics: VaR 95% '-$125,000', Sharpe ratio '1.15', Beta '0.85', volatility '12.3%'. "
        "Portfolio shows moderate risk profile with good risk-adjusted returns.",
        memory=agentcore_memory,
    )

    print("📈 Risk Metrics:")
    print(response)

    # ### Test 4: Client Profile Recall

    # Test client information and portfolio recall
    response = await investment_agent.run(
        "What client am I advising? What are their risk tolerance, investment goals, and current portfolio value?",
        memory=agentcore_memory,
    )

    print("🧠 Client Profile Recall:")
    print(response)
    print("\n✅ Expected: Robert Johnson, moderate risk, 15yr horizon, $2.5M portfolio, retirement planning")

    # ### Test 5: Market Sector Research

    # Research technology sector for potential opportunities
    response = await investment_agent.run(
        "Research market sector: 'Technology' with 'positive' outlook, key drivers 'AI adoption, cloud growth, digital transformation', "
        "recommendation 'overweight - increase allocation by 5%'.",
        memory=agentcore_memory,
    )

    print("🔍 Technology Sector Research:")
    print(response)

    # Research healthcare sector for diversification
    response = await investment_agent.run(
        "Research market sector: 'Healthcare' with 'neutral' outlook, key drivers 'aging demographics, drug innovation, regulatory changes', "
        "recommendation 'maintain - current allocation appropriate'.",
        memory=agentcore_memory,
    )

    print("🔍 Healthcare Sector Research:")
    print(response)

    # ### Test 6: Investment Recommendations

    # Generate specific investment recommendations
    response = await investment_agent.run(
        "Generate investment recommendation: security 'QQQ (Nasdaq ETF)', action 'BUY', "
        "rationale 'increase tech exposure per sector research, aligns with moderate risk profile', target allocation '8%'.",
        memory=agentcore_memory,
    )

    print("💡 QQQ Investment Recommendation:")
    print(response)

    response = await investment_agent.run(
        "Generate investment recommendation: security 'VGIT (Intermediate Treasury ETF)', action 'REDUCE', "
        "rationale 'rebalance to fund tech allocation, maintain duration risk management', target allocation '12%'.",
        memory=agentcore_memory,
    )

    print("💡 VGIT Rebalancing Recommendation:")
    print(response)

    # ### Test 7: Risk Metrics Recall and Analysis

    # Test risk metrics memory and interpretation
    response = await investment_agent.run(
        "What were Robert's current risk metrics? How does the Sharpe ratio and Beta align with his moderate risk tolerance?",
        memory=agentcore_memory,
    )

    print("📊 Risk Metrics Analysis:")
    print(response)
    print("\n✅ Expected: VaR -$125K, Sharpe 1.15, Beta 0.85, Vol 12.3% - good for moderate risk")

    # ### Test 8: Regulatory Compliance Check

    # Check regulatory compliance for recommendations
    response = await investment_agent.run(
        "Check regulatory compliance: rule type 'Fiduciary Duty - Best Interest', compliance status 'COMPLIANT', "
        "notes 'recommendations align with client risk profile and investment objectives'.",
        memory=agentcore_memory,
    )

    print("⚖️ Fiduciary Compliance:")
    print(response)

    response = await investment_agent.run(
        "Check regulatory compliance: rule type 'Portfolio Concentration Limits', compliance status 'COMPLIANT', "
        "notes 'no single position exceeds 15%, sector allocation within guidelines'.",
        memory=agentcore_memory,
    )

    print("⚖️ Concentration Compliance:")
    print(response)

    # ### Test 9: Investment Rationale Integration

    # Test integrated investment reasoning
    response = await investment_agent.run(
        "Based on my sector research and Robert's profile, why did I recommend increasing QQQ allocation? "
        "How does this align with his risk tolerance and investment goals?",
        memory=agentcore_memory,
    )

    print("🤔 Investment Rationale:")
    print(response)
    print("\n✅ Expected: Tech sector positive outlook + moderate risk tolerance + 15yr horizon = QQQ increase")

    # Comprehensive advisory session summary
    response = await investment_agent.run(
        "Provide a complete advisory summary: client profile, current portfolio metrics, sector research findings, "
        "investment recommendations, and compliance status. Include rationale for all recommendations.",
        memory=agentcore_memory,
    )

    print("📋 Complete Advisory Summary:")
    print(response)
    print(
        "\n✅ Expected: Full session details with Robert's profile, $2.5M portfolio, tech/healthcare research, QQQ/VGIT recs"
    )

    # ## Step 6: Testing Session Boundaries
    #
    # Let's test the boundaries of short-term memory by creating a different session:

    # Create a different session context
    new_session_context = AgentCoreMemoryContext(
        actor_id="financial-advisor",
        memory_id=memory_id,
        session_id="different-advisory-session",  # Different session ID
        namespace="/investment-advisory/",
    )

    new_session_memory = AgentCoreMemory(context=new_session_context, region_name=region)

    # Test memory isolation
    response = await investment_agent.run(
        "What clients am I advising today? What portfolio values and investment recommendations have I made?",
        memory=new_session_memory,
    )

    print("🚧 Session Boundary Test (Different Session):")
    print(response)
    print("\n✅ Expected: Limited or no recall from previous session (short-term memory boundary)")

    # Return to original session to verify persistence
    response = await investment_agent.run(
        "Back in my original session - what were Robert Johnson's exact risk metrics and my QQQ recommendation?",
        memory=agentcore_memory,  # Original session memory
    )

    print("🔄 Original Session Return:")
    print(response)
    print("\n✅ Expected: Full recall of Sharpe 1.15, Beta 0.85, QQQ BUY 8% allocation")

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
    response1 = await investment_agent.run("What have we discussed so far in this session?", memory=agentcore_memory)
    print(f"Response 1 length: {len(str(response1))} chars")

    # Test 2: Session memory - does the agent maintain context?
    response2 = await investment_agent.run("What did we talk about earlier?", memory=agentcore_memory)
    print(f"Response 2 length: {len(str(response2))} chars")

    # Test 3: Cross-reference capability - can it connect to previous context?
    response3 = await investment_agent.run("How does this relate to what we discussed before?", memory=agentcore_memory)
    print(f"Response 3 length: {len(str(response3))} chars")

    # ### Test 10: Comprehensive Advisory Summary

    # ## Summary
    #
    # In this notebook, we've demonstrated:
    #
    # ✅ **Short-term Memory Integration**: Using AgentCore Memory with LlamaIndex for session-scoped investment advisory
    #
    # ✅ **Financial-Specific Tools**: Client profiling, portfolio analysis, risk metrics, and investment recommendations
    #
    # ✅ **Investment Reasoning**: Advisor remembers client profiles, market research, and recommendation rationale
    #
    # ✅ **Risk Management**: Comprehensive risk metric tracking and regulatory compliance checking
    #
    # ✅ **Session Boundaries**: Memory isolation between different client advisory sessions
    #
    # ✅ **Regulatory Compliance**: Fiduciary duty and investment guideline adherence
    #
    # The Investment Portfolio Advisor showcases how short-term memory enables comprehensive financial advisory within a single client session while maintaining clear boundaries between different client consultations.

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
