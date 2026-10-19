#!/usr/bin/env python3
"""Live end-to-end demo against real Amazon Bedrock (opt-in, requires AWS setup).

Wires the three techniques together the way the production system does:

    adaptive routing -> failover model provider -> streamed generation
                                                -> streaming evaluation

Configuration comes from a .env file, loaded automatically (see
.env.example for the variables). Without it this prints setup
instructions and exits 0, so it is safe to run anywhere.

Run:
    cp .env.example .env   # then fill in your values
    python examples/live_demo.py "Which regions are missing their targets?"
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

DEFAULT_QUESTION = "Summarize the regional performance in two short paragraphs."


def load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env next to the project root, if present.

    Values go into this process's environment only; your shell stays
    clean. Variables already exported in the shell take precedence.
    """
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

# Sample "retrieved" business data for the demo query. In production these
# sections come from the retrieval layer.
SAMPLE_SECTIONS = [
    (
        "[West > Metrics > Revenue]",
        "West region quarterly revenue was $414M, up 12.5% year over year, against a target of $400M.",
    ),
    (
        "[East > Metrics > Revenue]",
        "East region quarterly revenue was $389M, up 9.8% year over year, against a target of $395M.",
    ),
    (
        "[North > Metrics > Revenue]",
        "North region quarterly revenue was $221M, down 2.1% year over year, against a target of $240M.",
    ),
]


def check_setup() -> list:
    """Return the configured role ARNs, or print instructions and exit 0."""
    role_arns = [a.strip() for a in os.environ.get("BEDROCK_ROLE_ARN", "").split(",") if a.strip()]
    if role_arns:
        return role_arns

    print("Live demo not configured, skipping (this is not an error).")
    print()
    print("To run against real Amazon Bedrock:")
    print("  1. cp .env.example .env and fill in your values")
    print("     (the demo loads .env automatically, no export needed)")
    print("  2. Ensure the role has bedrock:InvokeModelWithResponseStream")
    print("     and model access is enabled in the Bedrock console.")
    print("  3. Re-run: python examples/live_demo.py")
    print()
    print("For the offline demos (no AWS needed), run:")
    print("  python examples/streaming_evaluation_demo.py")
    print("  python examples/adaptive_pipeline_demo.py")
    print("  python examples/multi_model_failover_demo.py")
    sys.exit(0)


async def main() -> None:
    role_arns = check_setup()

    # Imported after the guard so the offline exit needs no AWS deps loaded
    from narrateai_qa.adaptive_pipeline import SectionData, SourceDataConsolidator
    from narrateai_qa.multi_model_failover import MultiModelBedrockModel
    from narrateai_qa.streaming_evaluation import (
        CompositeCorrector,
        CompositeEvaluator,
        StreamingEvalConfig,
        StreamingEvaluationPipeline,
    )

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    region = os.environ.get("AWS_REGION", "us-west-2")
    rank1 = os.environ.get("MODEL_ID_RANK_1", "global.anthropic.claude-sonnet-5-20250929-v1:0")
    rank2 = os.environ.get("MODEL_ID_RANK_2", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

    model_configs = {
        1: {"model_id": rank1, "region": region, "client_kwargs": {"bedrock_role_arn_list": role_arns}},
        2: {"model_id": rank2, "region": region, "client_kwargs": {"bedrock_role_arn_list": role_arns}},
    }

    print("=" * 72)
    print("LIVE DEMO: real Amazon Bedrock")
    print("=" * 72)
    print(f"  question : {question}")
    print(f"  models   : {rank1} (rank 1), {rank2} (rank 2)")
    print(f"  accounts : {len(role_arns)} role(s) -> {2 * len(role_arns)} quota spaces")

    # ---- [1/3] ADAPTIVE PIPELINE: route by retrieved volume ----
    print()
    print("[1/3] ADAPTIVE PIPELINE: volume-based routing")
    sections = [
        SectionData(section_id=f"s{i}", section_title=title, content=body, relevance_score=1.0 - i * 0.1, rank=i)
        for i, (title, body) in enumerate(SAMPLE_SECTIONS)
    ]
    # CONSOLIDATION_THRESHOLD lets you force the normal path: with the
    # production default (180K chars) small demo data always fits one chunk.
    # Try CONSOLIDATION_THRESHOLD=150 to watch the batched path run.
    threshold = int(os.environ.get("CONSOLIDATION_THRESHOLD", "0")) or None
    consolidator = SourceDataConsolidator(consolidation_threshold=threshold)
    consolidated = consolidator.consolidate_sections(sections, question)
    path = "FAST PATH" if consolidated.mode == "concatenated" else "NORMAL PATH"
    print(
        f"  retrieved {len(sections)} sections, {consolidated.total_characters} chars"
        f" (threshold: {consolidator.consolidation_threshold:,})"
    )
    print(
        f"  -> {consolidated.chunks_count} chunk(s) -> {path}: "
        + (
            "single LLM call, consolidation phase bypassed"
            if path == "FAST PATH"
            else f"{consolidated.chunks_count} parallel analyses + consolidation"
        )
    )

    # ---- [2/3] MULTI-MODEL FAILOVER: capture its activity for the wrap-up ----
    print()
    print("[2/3] MULTI-MODEL FAILOVER: activity captured, summarized after")
    failover_events = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            failover_events.append(record.getMessage())

    failover_logger = logging.getLogger("narrateai_qa.multi_model_failover")
    failover_logger.setLevel(logging.INFO)
    failover_logger.propagate = False  # keep events out of the streamed output
    failover_logger.addHandler(_CaptureHandler())

    from strands import Agent

    SYSTEM_PROMPT = "You are a business analyst. Answer strictly from the provided data. Use plain, objective language."

    # Keep a reference for the wrap-up summary
    model = MultiModelBedrockModel(model_configs=model_configs)

    def new_agent():
        # One agent+model per request: a Strands Agent handles one request
        # at a time, and each failover walk needs its own client state
        return Agent(
            model=MultiModelBedrockModel(model_configs=model_configs),
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
        )

    async def stream_agent(prompt_text):
        async for event in new_agent().stream_async(prompt_text):
            if "data" in event:
                yield event["data"]

    async def collect(prompt_text):
        return "".join([c async for c in stream_agent(prompt_text)])

    if consolidated.mode == "concatenated":
        # Fast path: one streamed call with the complete context
        async def llm_stream():
            async for chunk in stream_agent(f"{consolidated.content}\n\nQuestion: {question}"):
                yield chunk
    else:
        # Normal path: analyze each batch (parallel), then stream consolidation
        async def llm_stream():
            partials = await asyncio.gather(
                *[
                    collect(f"{chunk}\n\nQuestion: {question}\nAnalyze only this data; be concise.")
                    for chunk in consolidated.content
                ]
            )
            merged = "\n\n---\n\n".join(partials)
            async for chunk in stream_agent(
                f"Partial analyses:\n{merged}\n\nQuestion: {question}\n"
                "Synthesize the partial analyses into one unified answer."
            ):
                yield chunk

    # ---- [3/3] STREAMING EVALUATION: QA in parallel with generation ----
    config = StreamingEvalConfig()
    pipeline = StreamingEvaluationPipeline(
        evaluator=CompositeEvaluator.from_config(
            config,
            consolidated_content="CONSOLIDATED CONTENT\n"
            + (consolidated.content if isinstance(consolidated.content, str) else "\n".join(consolidated.content)),
        ),
        auto_corrector=CompositeCorrector.from_config(config),
        config=config,
    )

    print()
    print("[3/3] STREAMING EVALUATION: response below is evaluated per")
    print("      paragraph WHILE the next paragraph is still generating")
    print()
    print("--- Evaluated response (streaming) ---")
    print()
    async for chunk in pipeline.process_stream(llm_stream()):
        print(chunk, end="", flush=True)

    # ---- Wrap-up: what each technique did on THIS request ----
    meta = pipeline.get_metadata()
    print()
    print()
    print("=" * 72)
    print("WHAT JUST HAPPENED: one request, three techniques")
    print("=" * 72)
    print(
        f"  [1] Adaptive pipeline : {path} "
        f"({consolidated.chunks_count} chunk(s), consolidation " + ("bypassed" if path == "FAST PATH" else "ran") + ")"
    )
    print(f"  [2] Failover          : served by {model.config['model_id']}")
    for ev in failover_events:
        print(f"        {ev}")
    attempts = sum(1 for ev in failover_events if ev.startswith("Attempting"))
    throttles = sum(1 for ev in failover_events if ev.startswith("Throttled"))
    print(
        f"        ({attempts} attempt(s), {throttles} throttle(s); "
        + (
            "no failover needed this request; under load the walk covers all quota spaces"
            if throttles == 0
            else "failover engaged"
        )
        + ")"
    )
    print(
        f"  [3] Streaming eval    : {meta.total_paragraphs} paragraphs evaluated in-flight, "
        f"{meta.corrected_paragraphs} corrected,"
    )
    print(
        f"                          avg {meta.avg_evaluation_time_ms:.1f} ms/paragraph "
        f"(vs seconds if evaluated after generation)"
    )
    corrected = [p for p in meta.paragraph_logs if p.get("was_corrected")]
    if corrected:
        for p in corrected:
            issues = p.get("eval_result", {}).get("issues", [])
            detail = "; ".join(issues) if issues else "corrected (see output above)"
            print(f"      paragraph {p['paragraph_id']}: {detail}")
        print("      values the LLM DERIVED (correct math, but absent from source")
        print('      data) are labeled with "(LLM Reasoning)" instead of silently trusted.')
    print()
    print("Live demo complete.")


if __name__ == "__main__":
    asyncio.run(main())
