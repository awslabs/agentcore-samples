#!/usr/bin/env python3
"""
SRE Report Verification Tool

This tool compares SRE investigation reports against ground truth data to identify
hallucinations and verify the accuracy of claims made in the reports.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(Path(__file__).parent / "sre_agent" / ".env")


def _get_anthropic_api_key() -> str:
    """Get Anthropic API key from environment variables."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required for verification"
        )
    return api_key


def _read_file(file_path: str) -> str:
    """Read content from a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        sys.exit(1)


def _create_verification_prompt(report_content: str, ground_truth_content: str) -> str:
    """Create the verification prompt for Claude."""
    return f"""<task>
You are an expert SRE data verification specialist. Your task is to verify the accuracy of an SRE investigation report by comparing it against ground truth data.

<report>
{report_content}
</report>

<ground_truth_data>
{ground_truth_content}
</ground_truth_data>
</task>

<critical_context>
IMPORTANT: The ground truth data contains a comprehensive dataset representing the ENTIRE infrastructure state, including:
- Multiple services (some healthy, some with issues)
- Historical data across different time periods
- Various pod states (running, failed, crashed, etc.)
- Mixed performance metrics (good and bad)
- Different log patterns and error conditions

DO NOT expect every entity in the report to have problems in the ground truth. The ground truth shows the complete picture, so:
- Some services may be healthy while others have issues
- Some pods may be running fine while others are failing
- Performance metrics may show both good and bad patterns
- Only verify that the SPECIFIC claims in the report match what's actually in the data

Focus on accuracy of SPECIFIC claims made in the report, not whether the overall system appears healthy or unhealthy.
</critical_context>

<instructions>
Carefully analyze the SRE investigation report and compare ALL specific claims against the ground truth data. Focus on verifying:

1. **Pod Names** - Any pod names mentioned (e.g., api-service-xyz, database-pod-abc)
2. **Application Names** - Service names referenced
3. **Timestamps** - Specific times mentioned in logs or metrics
4. **Log Entries** - Exact log messages quoted
5. **Metrics Values** - Performance numbers, response times, error rates
6. **Resource Usage** - CPU, memory percentages
7. **Error Counts** - Number of errors or occurrences
8. **Status Information** - Pod states, service health

For each entity mentioned in the report:
- Check if it exists in the ground truth data
- Verify if the details (timestamps, values, status) match exactly
- Identify any fabricated or hallucinated information
- Remember: The absence of problems for a service in the ground truth does NOT invalidate the report unless the report specifically claims that service has issues

<output_format>
If you find hallucinations, respond with:

# ❌ HALLUCINATIONS DETECTED

## Fabricated Claims:
- **[Entity Type]**: [Specific claim]
  - **Report Claims**: [What the report states]
  - **Ground Truth**: [What the data actually shows or "NOT FOUND"]
  - **Verification**: FABRICATED/INACCURATE

## Additional Issues:
[Any other accuracy problems found]

---

If NO hallucinations are found, respond with:

# ✅ REPORT VERIFIED ACCURATE

## Important Entities Found:
- **[Entity Type]**: [Entity name/value]
  - **Ground Truth Reference**: Line [X]: "[exact text from ground truth]"
  - **Report Context**: [How it was used in the report]

## Verification Summary:
All claims in the report have been verified against the ground truth data. No fabricated information detected.
</output_format>

Be extremely thorough and precise. SRE operations require absolute accuracy - even small discrepancies in timestamps, pod names, or metric values are critical to identify.
</instructions>"""


def _verify_report_with_claude(
    report_content: str, ground_truth_content: str, api_key: str
) -> str:
    """Use Claude to verify the report against ground truth data."""
    try:
        client = anthropic.Anthropic(api_key=api_key)

        prompt = _create_verification_prompt(report_content, ground_truth_content)

        logger.info("Sending verification request to Claude 4 Sonnet...")

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            temperature=0.1,  # Low temperature for consistent, accurate analysis
            messages=[{"role": "user", "content": prompt}],
        )

        return response.content[0].text

    except Exception as e:
        logger.error(f"Error calling Claude API: {e}")
        sys.exit(1)


def main():
    """Main function for report verification."""
    parser = argparse.ArgumentParser(
        description="Verify SRE investigation reports against ground truth data"
    )
    parser.add_argument(
        "report_path", help="Path to the SRE investigation report (markdown file)"
    )
    parser.add_argument(
        "--data-path",
        default="backend/data/all_data_dump.txt",
        help="Path to the ground truth data file (default: backend/data/all_data_dump.txt)",
    )
    parser.add_argument(
        "--output", help="Optional output file to save verification results"
    )

    args = parser.parse_args()

    # Validate input files
    if not os.path.exists(args.report_path):
        logger.error(f"Report file not found: {args.report_path}")
        sys.exit(1)

    if not os.path.exists(args.data_path):
        logger.error(f"Ground truth data file not found: {args.data_path}")
        sys.exit(1)

    # Get API key
    try:
        api_key = _get_anthropic_api_key()
    except ValueError as e:
        logger.error(f"API key error: {e}")
        sys.exit(1)

    # Read files
    logger.info(f"Reading report: {args.report_path}")
    report_content = _read_file(args.report_path)

    logger.info(f"Reading ground truth data: {args.data_path}")
    ground_truth_content = _read_file(args.data_path)

    # Verify report
    logger.info("Starting verification process...")
    verification_result = _verify_report_with_claude(
        report_content, ground_truth_content, api_key
    )

    # Output results
    print("\n" + "=" * 80)
    print("SRE REPORT VERIFICATION RESULTS")
    print("=" * 80)
    print(verification_result)
    print("=" * 80)

    # Save to output file if specified
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write("# SRE Report Verification Results\n\n")
                f.write(f"**Report**: {args.report_path}\n")
                f.write(f"**Ground Truth**: {args.data_path}\n")
                f.write(f"**Verified on**: {Path().cwd()}\n\n")
                f.write("---\n\n")
                f.write(verification_result)
            logger.info(f"Verification results saved to: {args.output}")
        except Exception as e:
            logger.error(f"Error saving output file: {e}")

    logger.info("Verification complete!")


if __name__ == "__main__":
    main()
