"""Analysis sub-agent with data analysis and source comparison tools."""

from __future__ import annotations

import json
from typing import Annotated

from strands import Agent, tool

_INPUT_SUMMARY_MAX_LEN = 200


@tool
def analyze_data(
    data: Annotated[str, "The data or text to analyze"],
    analysis_type: Annotated[
        str,
        "Type of analysis: 'trend', 'statistical', 'qualitative', or 'comparative'",
    ] = "qualitative",
) -> str:
    """Analyze data and return structured insights.

    Performs the specified type of analysis on the provided data and returns
    findings with confidence scores.

    Note:
        This is a functional stub. Replace with real analysis logic
        (e.g., pandas pipelines, statistical libraries) for production use.
    """
    # Stub: return analysis results based on type
    analysis = {
        "analysis_type": analysis_type,
        "input_summary": data[:_INPUT_SUMMARY_MAX_LEN] if len(data) > _INPUT_SUMMARY_MAX_LEN else data,
        "findings": [
            {
                "insight": f"Primary {analysis_type} insight from the provided data",
                "confidence": 0.85,
                "evidence": "Derived from pattern analysis of input data",
            },
            {
                "insight": f"Secondary {analysis_type} observation",
                "confidence": 0.72,
                "evidence": "Inferred from contextual analysis",
            },
        ],
        "methodology": f"{analysis_type} analysis applied to input data",
    }
    return json.dumps(analysis)


@tool
def compare_sources(
    sources: Annotated[str, "JSON string of source texts or data points to compare"],  # noqa: ARG001
    comparison_axis: Annotated[
        str,
        "The dimension to compare along: 'agreement', 'timeline', 'methodology', or 'scope'",
    ] = "agreement",
) -> str:
    """Compare multiple sources along a specified axis.

    Evaluates agreement/disagreement, temporal patterns, or methodological
    differences across the provided sources.

    Note:
        This is a functional stub. Replace with real comparison logic
        for production use.
    """
    # Stub: return comparison results
    comparison = {
        "comparison_axis": comparison_axis,
        "source_count": 2,
        "agreement_level": "moderate",
        "key_differences": [
            f"Sources differ on methodology when compared by {comparison_axis}",
            "Scope of analysis varies between sources",
        ],
        "consensus_points": [
            "Sources agree on primary conclusions",
            "Consistent data trends observed across sources",
        ],
        "recommendation": f"Further investigation recommended along {comparison_axis} axis",
    }
    return json.dumps(comparison)


def get_tools() -> list:
    """Return the list of analysis tools."""
    return [analyze_data, compare_sources]


SYSTEM_PROMPT = """\
You are an analysis specialist agent. Your job is to analyze data and compare sources
to produce structured analytical insights.

IMPORTANT: Structure your final response with these sections:

TRENDS:
- List identified trends and patterns

COMPARISONS:
- List key comparisons between data points or sources

INSIGHTS:
- List actionable insights derived from the analysis

CONFIDENCE:
- State your overall confidence level (high/medium/low) with justification

Always use the analyze_data tool to process information, and compare_sources when
multiple data points are available. Be analytical and evidence-based.
"""


def create_analysis_agent() -> Agent:
    """Create and return a configured analysis agent.

    Returns:
        A Strands Agent configured with analysis tools and system prompt.
    """
    return Agent(
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        tools=get_tools(),
        system_prompt=SYSTEM_PROMPT,
        name="analysis-agent",
        description="Analyzes data and compares sources to produce structured insights",
    )
