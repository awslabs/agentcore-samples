"""Main orchestrator: producer-consumer streaming with in-flight evaluation."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from .buffer import LocalStreamBuffer
from .detector import ParagraphDetector
from .evaluation import AutoCorrector, EvaluationPipeline, Evaluator
from .models import (
    ParagraphBuffer,
    ParagraphStatus,
    StreamingEvalConfig,
    StreamingMetadata,
)
from .streamer import MultiWordStreamer

logger = logging.getLogger(__name__)


class StreamingEvaluationPipeline:
    """Streams LLM output through evaluation without blocking generation.

        LLM stream -> producer -> buffer -> consumer -> multi-word chunks -> user
                                               |
                                     evaluation + auto-correction

    The producer accumulates tokens into paragraphs; the consumer evaluates
    each paragraph and streams it. Three optimizations keep validation
    overhead near zero:

    - Structural bypass: headers/rules skip evaluation entirely.
    - First-paragraph threshold: a lower minimum length gets the first
      content out sooner.
    - Lookahead evaluation: while paragraph N streams to the user,
      paragraph N+1 is already being evaluated in the background.
    """

    def __init__(
        self,
        evaluator: Evaluator,
        auto_corrector: AutoCorrector | None = None,
        config: StreamingEvalConfig | None = None,
    ):
        self.config = config or StreamingEvalConfig()

        self.buffer = LocalStreamBuffer(self.config)
        self.detector = ParagraphDetector(self.config)
        self.streamer = MultiWordStreamer(self.config)
        self.evaluation_pipeline = EvaluationPipeline(
            evaluator=evaluator,
            auto_corrector=auto_corrector,
            config=self.config,
        )

        self.metadata = StreamingMetadata()

    async def process_stream(
        self,
        llm_stream: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        """Run producer and consumer in parallel, yielding chunks for the user."""
        producer_task = asyncio.create_task(self._produce_paragraphs(llm_stream), name="llm_producer")

        try:
            async for chunk in self._consume_and_stream():
                yield chunk

            await producer_task

            logger.info(
                f"Pipeline complete: {self.metadata.total_paragraphs} paragraphs, "
                f"{self.metadata.corrected_paragraphs} corrected, "
                f"{self.metadata.rejected_paragraphs} rejected, "
                f"avg eval {self.metadata.avg_evaluation_time_ms:.2f}ms"
            )

        except Exception as e:
            logger.error(f"Error in streaming pipeline: {e}")
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except asyncio.CancelledError:
                    pass
            raise

    async def _produce_paragraphs(self, llm_stream: AsyncGenerator[str, None]):
        """Producer: accumulate tokens, split into paragraphs, buffer them."""
        accumulated = ""

        try:
            async for token in llm_stream:
                accumulated += token

                if self.detector.is_boundary(accumulated):
                    paragraph, remainder = self.detector.extract_paragraph(accumulated)
                    is_structural = self.config.enable_structural_bypass and self.detector.is_structural_content(
                        paragraph
                    )
                    # put() blocks when the buffer is full (backpressure)
                    await self.buffer.put(paragraph, metadata={"is_structural": is_structural})
                    accumulated = remainder

            # Whatever is left becomes the final paragraph
            if accumulated.strip():
                is_structural = self.config.enable_structural_bypass and self.detector.is_structural_content(
                    accumulated.strip()
                )
                await self.buffer.put(accumulated.strip(), metadata={"is_structural": is_structural})

        except Exception as e:
            logger.error(f"Error in producer task: {e}")
            raise

        finally:
            await self.buffer.close()

    async def _evaluate_paragraph(self, paragraph_buffer: ParagraphBuffer) -> tuple[str, dict, float]:
        eval_start = asyncio.get_event_loop().time()
        final_content, eval_metadata = await self.evaluation_pipeline.process_paragraph(paragraph_buffer)
        eval_time_ms = (asyncio.get_event_loop().time() - eval_start) * 1000
        return final_content, eval_metadata, eval_time_ms

    async def _consume_and_stream(self) -> AsyncGenerator[str, None]:
        """Consumer: evaluate each paragraph and stream it as chunks.

        With lookahead enabled, the timeline overlaps eval with streaming:

            [eval P1]
            [stream P1 + eval P2 in background]
            [stream P2 + eval P3 in background]   <- P2's result already ready
        """
        paragraphs_processed = 0

        # Pre-evaluated (paragraph, content, metadata, time) from the lookahead task
        lookahead_result: tuple[ParagraphBuffer, str, dict, float] | None = None

        try:
            while True:
                paragraph_buffer: ParagraphBuffer | None = None
                final_content: str | None = None
                eval_metadata: dict | None = None
                eval_time_ms: float = 0.0

                if lookahead_result is not None:
                    paragraph_buffer, final_content, eval_metadata, eval_time_ms = lookahead_result
                    lookahead_result = None
                else:
                    paragraph_buffer = await self.buffer.get()
                    if paragraph_buffer is None:
                        break

                    paragraph_buffer.is_structural = paragraph_buffer.metadata.get("is_structural", False)

                    if paragraph_buffer.is_structural and self.config.enable_structural_bypass:
                        final_content = paragraph_buffer.content
                        eval_metadata = {"status": "structural_bypass", "eval_time_ms": 0}
                        eval_time_ms = 0.0
                    else:
                        final_content, eval_metadata, eval_time_ms = await self._evaluate_paragraph(paragraph_buffer)

                paragraphs_processed += 1

                # Record metadata
                if paragraph_buffer.is_structural and self.config.enable_structural_bypass:
                    paragraph_buffer.status = ParagraphStatus.APPROVED
                else:
                    self.metadata.total_evaluation_time_ms += eval_time_ms
                    if eval_metadata and eval_metadata.get("was_corrected"):
                        self.metadata.total_correction_time_ms += eval_metadata.get("correction_time_ms", 0)

                self.metadata.add_paragraph_log(paragraph_buffer)

                # A rejection ends the stream after its error message
                if eval_metadata and eval_metadata.get("status") == "rejected":
                    logger.warning(f"Paragraph {paragraph_buffer.paragraph_id} rejected, stopping stream")
                    yield final_content
                    break

                if paragraphs_processed > 1:
                    yield "\n\n"

                if self.config.enable_lookahead_evaluation:
                    # Evaluate the next paragraph while this one streams
                    lookahead_task = asyncio.create_task(
                        self._lookahead_fetch_and_evaluate(),
                        name=f"lookahead_after_{paragraph_buffer.paragraph_id}",
                    )

                    async for chunk in self.streamer.stream_paragraph(final_content):
                        yield chunk

                    lookahead_result = await lookahead_task
                else:
                    async for chunk in self.streamer.stream_paragraph(final_content):
                        yield chunk

        except Exception as e:
            logger.error(f"Error in consumer task: {e}")
            raise

    async def _lookahead_fetch_and_evaluate(
        self,
    ) -> tuple[ParagraphBuffer, str, dict, float] | None:
        """Fetch and evaluate the next paragraph concurrently with streaming."""
        try:
            paragraph_buffer = await self.buffer.get()
            if paragraph_buffer is None:
                return None

            paragraph_buffer.is_structural = paragraph_buffer.metadata.get("is_structural", False)

            if paragraph_buffer.is_structural and self.config.enable_structural_bypass:
                return (
                    paragraph_buffer,
                    paragraph_buffer.content,
                    {"status": "structural_bypass", "eval_time_ms": 0},
                    0.0,
                )

            final_content, eval_metadata, eval_time_ms = await self._evaluate_paragraph(paragraph_buffer)
            return (paragraph_buffer, final_content, eval_metadata, eval_time_ms)

        except Exception as e:
            logger.error(f"Error in lookahead evaluation: {e}")
            return None

    def get_metadata(self) -> StreamingMetadata:
        return self.metadata

    def get_stats(self) -> dict:
        return {
            "config": {
                "buffer_size": self.config.max_buffer_size,
                "words_per_chunk": self.config.words_per_chunk,
                "eval_timeout_seconds": self.config.eval_timeout_seconds,
                "auto_correction_enabled": self.config.enable_auto_correction,
                "structural_bypass": self.config.enable_structural_bypass,
                "lookahead_evaluation": self.config.enable_lookahead_evaluation,
                "first_paragraph_min_length": self.config.first_paragraph_min_length,
            },
            "buffer_stats": self.buffer.get_stats(),
            "detector_stats": self.detector.get_stats(),
            "streamer_stats": self.streamer.get_stats(),
            "evaluation_stats": self.evaluation_pipeline.get_stats(),
            "performance": self.metadata.to_dict(),
        }

    def reset_metadata(self):
        self.metadata = StreamingMetadata()
