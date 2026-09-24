"""Evaluation and auto-correction of paragraphs."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Protocol

from .models import EvaluationResult, ParagraphBuffer, ParagraphStatus, StreamingEvalConfig

logger = logging.getLogger(__name__)


class Evaluator(Protocol):
    """Anything with an async evaluate(content) -> EvaluationResult."""

    async def evaluate(self, content: str) -> EvaluationResult: ...


class AutoCorrector(ABC):
    """Base class for correctors that fix content flagged by an evaluator."""

    @abstractmethod
    async def correct(self, content: str, eval_result: EvaluationResult) -> str: ...


class NoOpCorrector(AutoCorrector):
    """Returns content unchanged; used when correction is disabled."""

    async def correct(self, content: str, eval_result: EvaluationResult) -> str:
        return content


class EvaluationPipeline:
    """Runs one paragraph through evaluation and, if needed, correction.

    Handles eval timeouts (fail-open or fail-closed per config) and rejects
    paragraphs with critical violations.
    """

    def __init__(
        self,
        evaluator: Evaluator,
        auto_corrector: AutoCorrector | None = None,
        config: StreamingEvalConfig | None = None,
    ):
        self.evaluator = evaluator
        self.auto_corrector = auto_corrector or NoOpCorrector()
        self.config = config or StreamingEvalConfig()

    async def process_paragraph(self, paragraph: ParagraphBuffer) -> tuple[str, dict]:
        """Evaluate a paragraph. Returns (content_to_stream, metadata)."""
        paragraph.status = ParagraphStatus.EVALUATING
        start_time = time.time()

        try:
            eval_result = await asyncio.wait_for(
                self.evaluator.evaluate(paragraph.content), timeout=self.config.eval_timeout_seconds
            )

            eval_time_ms = (time.time() - start_time) * 1000
            paragraph.eval_result = eval_result

            if eval_result.critical_violation:
                paragraph.status = ParagraphStatus.REJECTED
                logger.warning(f"Paragraph {paragraph.paragraph_id} rejected: critical violation")
                metadata = {
                    "paragraph_id": paragraph.paragraph_id,
                    "status": "rejected",
                    "critical_violation": True,
                    "violation_reason": eval_result.issues[0] if eval_result.issues else "Unknown",
                    "eval_time_ms": eval_time_ms,
                }
                error_message = "\n[Content filtered due to policy violation. Please rephrase your question.]\n"
                return error_message, metadata

            if eval_result.needs_correction and self.config.enable_auto_correction:
                final_content = await self._apply_correction(paragraph, eval_result)
                metadata = {
                    "paragraph_id": paragraph.paragraph_id,
                    "status": "corrected",
                    "was_corrected": True,
                    "original_issues": eval_result.issues,
                    "corrections_applied": eval_result.corrections,
                    "eval_time_ms": eval_time_ms,
                    "correction_time_ms": paragraph.metadata.get("correction_time_ms", 0),
                }
                return final_content, metadata

            paragraph.status = ParagraphStatus.APPROVED
            metadata = {
                "paragraph_id": paragraph.paragraph_id,
                "status": "approved",
                "was_corrected": False,
                "eval_score": eval_result.score,
                "eval_time_ms": eval_time_ms,
            }
            return paragraph.content, metadata

        except asyncio.TimeoutError:
            eval_time_ms = (time.time() - start_time) * 1000
            logger.warning(
                f"Evaluation timeout for paragraph {paragraph.paragraph_id} "
                f"(timeout={self.config.eval_timeout_seconds}s)"
            )

            if self.config.fail_open_on_timeout:
                paragraph.status = ParagraphStatus.APPROVED
                metadata = {
                    "paragraph_id": paragraph.paragraph_id,
                    "status": "approved_on_timeout",
                    "was_corrected": False,
                    "eval_timeout": True,
                    "eval_time_ms": eval_time_ms,
                }
                return paragraph.content, metadata

            paragraph.status = ParagraphStatus.REJECTED
            metadata = {
                "paragraph_id": paragraph.paragraph_id,
                "status": "rejected_on_timeout",
                "eval_timeout": True,
                "eval_time_ms": eval_time_ms,
            }
            return "\n[Content unavailable due to evaluation timeout]\n", metadata

        except Exception as e:
            # Fail open on evaluator errors: stream the original content
            eval_time_ms = (time.time() - start_time) * 1000
            logger.error(f"Evaluation error for paragraph {paragraph.paragraph_id}: {e}")

            paragraph.status = ParagraphStatus.APPROVED
            metadata = {
                "paragraph_id": paragraph.paragraph_id,
                "status": "approved_on_error",
                "was_corrected": False,
                "eval_error": str(e),
                "eval_time_ms": eval_time_ms,
            }
            return paragraph.content, metadata

    async def _apply_correction(self, paragraph: ParagraphBuffer, eval_result: EvaluationResult) -> str:
        paragraph.status = ParagraphStatus.CORRECTING
        correction_start = time.time()

        try:
            attempts = 0
            corrected_content = paragraph.content

            while attempts < self.config.max_correction_attempts:
                attempts += 1
                corrected_content = await self.auto_corrector.correct(corrected_content, eval_result)
                break

            correction_time_ms = (time.time() - correction_start) * 1000

            paragraph.corrected_content = corrected_content
            paragraph.status = ParagraphStatus.CORRECTED
            paragraph.metadata["correction_time_ms"] = correction_time_ms

            return corrected_content

        except Exception as e:
            logger.error(f"Auto-correction failed for paragraph {paragraph.paragraph_id}: {e}")
            paragraph.status = ParagraphStatus.APPROVED
            return paragraph.content

    def get_stats(self) -> dict:
        return {
            "evaluator": type(self.evaluator).__name__,
            "auto_corrector": type(self.auto_corrector).__name__,
            "eval_timeout_seconds": self.config.eval_timeout_seconds,
            "auto_correction_enabled": self.config.enable_auto_correction,
            "fail_open_on_timeout": self.config.fail_open_on_timeout,
            "max_correction_attempts": self.config.max_correction_attempts,
        }
