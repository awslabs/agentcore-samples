"""Writer/synthesizer sub-agent with report drafting and formatting tools."""

from __future__ import annotations

import json
from typing import Annotated

from strands import Agent, tool


@tool
def draft_section(
    section_title: Annotated[str, "Title of the report section"],
    content: Annotated[str, "Raw content and notes to draft into a polished section"],
) -> str:
    """Draft a polished report section from raw content.

    Takes rough notes and content, then produces a well-structured section
    suitable for a professional report.

    Note:
        This is a functional stub. The agent's LLM capabilities handle
        the actual writing; this tool provides structure.
    """
    section = {
        "title": section_title,
        "drafted_content": content,
        "word_count": len(content.split()),
        "status": "drafted",
    }
    return json.dumps(section)


@tool
def format_report(
    sections: Annotated[str, "JSON string of report sections to compile"],  # noqa: ARG001
    report_title: Annotated[str, "Title for the final report"],
) -> str:
    """Compile drafted sections into a formatted final report.

    Assembles individual sections into a cohesive report with proper
    structure, transitions, and formatting.

    Note:
        This is a functional stub. In production, this could generate
        PDF, Markdown, or HTML output.
    """
    report = {
        "title": report_title,
        "format": "markdown",
        "sections_compiled": True,
        "metadata": {
            "generated_by": "writer-agent",
            "format_version": "1.0",
        },
    }
    return json.dumps(report)


def get_tools() -> list:
    """Return the list of writer tools."""
    return [draft_section, format_report]


SYSTEM_PROMPT = """\
You are a professional writer and report synthesizer. Your job is to take research
findings and analytical insights from other agents and produce a clear, well-structured
final report.

IMPORTANT: Structure your final report with these sections:

## Executive Summary
A brief overview of the key findings and conclusions.

## Key Findings
The most important discoveries from the research and analysis.

## Analysis
Detailed examination of the findings with supporting evidence.

## Conclusions
Final conclusions and any recommended next steps.

Use the draft_section tool to draft each section, then format_report to compile
the final output. Write in a professional, clear tone. Synthesize information
from all available sources — do not simply repeat them verbatim.
"""


def create_writer_agent() -> Agent:
    """Create and return a configured writer/synthesizer agent.

    Returns:
        A Strands Agent configured with writing tools and system prompt.
        Uses a higher-quality model for better writing output.
    """
    return Agent(
        model="us.anthropic.claude-sonnet-4-6",
        tools=get_tools(),
        system_prompt=SYSTEM_PROMPT,
        name="writer-agent",
        description="Synthesizes research and analysis into polished reports",
    )
