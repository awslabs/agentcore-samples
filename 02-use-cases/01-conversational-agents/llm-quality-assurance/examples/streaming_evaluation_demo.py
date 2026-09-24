#!/usr/bin/env python3
"""Streaming evaluation demo (offline, no AWS credentials required).

A fake LLM token stream flows through paragraph detection, evaluation
(weasel words, emojis, data accuracy), and auto-correction, then streams
to the "user" in multi-word chunks. Prints the raw LLM output next to the
evaluated output so the corrections are easy to see, and exits non-zero
if any expected correction did not happen.

Run:
    python examples/streaming_evaluation_demo.py
"""

import asyncio
import logging
import sys

from narrateai_qa.streaming_evaluation import (
    CompositeCorrector,
    CompositeEvaluator,
    StreamingEvalConfig,
    StreamingEvaluationPipeline,
)

logging.basicConfig(level=logging.WARNING)  # set to INFO to watch the pipeline internals

# Source data the response is verified against (normally the consolidated
# retrieval output; see the adaptive_pipeline sample).
SOURCE_DATA = """
CONSOLIDATED CONTENT
Quarterly revenue for the West region was $414M, up 12.5% year over year.
The East region closed at $389M with a 9.8% growth rate.
"""

# Fake LLM response with four seeded issues:
#   - "significantly" (weasel word)
#   - a rocket emoji
#   - "$441M" (fabricated: the source says $414M)
#   - "$52M" inside parentheses (fabricated: derived by the LLM, not in source)
LLM_RESPONSE = (
    "## Regional Performance Summary\n\n"
    "West region revenue reached $441M this quarter, growing significantly "
    "compared to last year. \U0001f680 The reported growth rate of 12.5% is in "
    "line with the plan.\n\n"
    "East region revenue was $389M with a growth rate of 9.8% (a gap of $52M "
    "versus West), consistent with prior guidance and regional forecasts for "
    "the remainder of the year."
)


async def fake_llm_stream(text: str, chunk_size: int = 8):
    """Yield text in small chunks, imitating an LLM token stream."""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        await asyncio.sleep(0.001)


async def main() -> None:
    config = StreamingEvalConfig(chunk_delay_ms=0)  # no artificial delay in the demo

    pipeline = StreamingEvaluationPipeline(
        evaluator=CompositeEvaluator.from_config(config, consolidated_content=SOURCE_DATA),
        auto_corrector=CompositeCorrector.from_config(config),
        config=config,
    )

    print("=" * 72)
    print("STREAMING EVALUATION DEMO")
    print("=" * 72)

    print("\n--- Original LLM output (before evaluation) ---\n")
    print(LLM_RESPONSE)

    print("\n--- Evaluated output streamed to user ---\n")
    output = ""
    async for chunk in pipeline.process_stream(fake_llm_stream(LLM_RESPONSE)):
        print(chunk, end="", flush=True)
        output += chunk

    print("\n\n--- What changed ---")
    print('  removed   : "significantly" (weasel word)')
    print("  removed   : \U0001f680 (emoji)")
    print("  annotated : $441M -> $441M (LLM Reasoning)  [not in source data]")
    print("  annotated : $52M  -> $52M (LLM Reasoning)   [derived, even inside parens]")
    print("  untouched : $389M, 12.5%, 9.8%              [verified against source]")

    meta = pipeline.get_metadata()
    print("\n--- Pipeline statistics ---")
    print(f"Paragraphs processed : {meta.total_paragraphs}")
    print(f"Paragraphs corrected : {meta.corrected_paragraphs}")
    print(f"Correction rate      : {meta.correction_rate:.0%}")
    print(f"Avg evaluation time  : {meta.avg_evaluation_time_ms:.1f} ms")

    # Pass/fail checks: exit 0 only if every expected correction happened
    checks = [
        ("weasel word 'significantly' removed", "significantly" not in output),
        ("emoji removed", "\U0001f680" not in output),
        ("fabricated $441M annotated with (LLM Reasoning)", "$441M (LLM Reasoning)" in output),
        (
            "fabricated $52M annotated even inside parentheses",
            "$52M (LLM Reasoning)" in output,
        ),
        (
            "genuine metrics left untouched",
            "$389M (LLM Reasoning)" not in output and "9.8% (LLM Reasoning)" not in output,
        ),
        ("structural header streamed via bypass", "## Regional Performance Summary" in output),
        ("correction recorded in metadata", meta.corrected_paragraphs >= 1),
    ]

    print("\n--- Invariant checks ---")
    all_passed = True
    for name, passed in checks:
        print(f"  [{'ok' if passed else 'x '}] {name}")
        all_passed = all_passed and passed

    if not all_passed:
        print("\nDemo FAILED.")
        sys.exit(1)

    print("\nAll invariants hold. Demo PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
