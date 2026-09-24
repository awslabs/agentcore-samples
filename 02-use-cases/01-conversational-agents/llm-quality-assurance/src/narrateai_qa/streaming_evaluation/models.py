"""Data structures and configuration for the streaming evaluation pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass
class StreamingEvalConfig:
    """Tunables for buffering, paragraph detection, streaming, and evaluation."""

    # Buffer
    max_buffer_size: int = 10
    max_buffer_age_seconds: int = 300

    # Paragraph detection
    min_paragraph_length: int = 50
    first_paragraph_min_length: int = 10  # lower threshold to reduce TTFT
    max_paragraph_length: int = 600
    sentence_end_markers: tuple = (".", "!", "?", "#")

    # TTFT optimizations
    enable_structural_bypass: bool = True  # headers/rules skip evaluation
    enable_lookahead_evaluation: bool = True  # evaluate N+1 while streaming N

    # Multi-word streaming
    words_per_chunk: int = 5
    chunk_delay_ms: int = 50

    # Evaluation
    eval_timeout_seconds: float = 5.0
    enable_auto_correction: bool = True
    fail_open_on_timeout: bool = True
    max_correction_attempts: int = 1

    # Evaluators / correctors
    enable_weasel_word_evaluation: bool = True
    enable_weasel_word_correction: bool = True
    enable_emoji_evaluation: bool = True
    enable_emoji_correction: bool = True
    enable_data_accuracy_evaluation: bool = True
    enable_data_accuracy_correction: bool = True


class ParagraphStatus(Enum):
    PENDING = "pending"
    EVALUATING = "evaluating"
    APPROVED = "approved"
    CORRECTING = "correcting"
    CORRECTED = "corrected"
    REJECTED = "rejected"


@dataclass
class EvaluationResult:
    """Outcome of evaluating one paragraph."""

    passed: bool
    needs_correction: bool = False
    critical_violation: bool = False
    score: float = 1.0
    issues: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be between 0.0 and 1.0, got {self.score}")
        if self.critical_violation and self.passed:
            raise ValueError("Cannot have critical_violation=True and passed=True")


@dataclass
class ParagraphBuffer:
    """A complete paragraph flowing through the pipeline: buffered by the
    producer, evaluated (and possibly corrected) by the consumer, then
    streamed to the user."""

    content: str
    paragraph_id: int
    timestamp: datetime = field(default_factory=datetime.now)
    status: ParagraphStatus = ParagraphStatus.PENDING
    eval_result: EvaluationResult | None = None
    corrected_content: str | None = None
    is_structural: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def final_content(self) -> str:
        return self.corrected_content if self.corrected_content else self.content

    @property
    def was_corrected(self) -> bool:
        return self.corrected_content is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_id": self.paragraph_id,
            "content_length": len(self.content),
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "was_corrected": self.was_corrected,
            "eval_result": (
                {
                    "passed": self.eval_result.passed,
                    "score": self.eval_result.score,
                    "needs_correction": self.eval_result.needs_correction,
                    "critical_violation": self.eval_result.critical_violation,
                    "issues_count": len(self.eval_result.issues),
                    "issues": list(self.eval_result.issues),
                }
                if self.eval_result
                else None
            ),
            "metadata": self.metadata,
        }


@dataclass
class StreamingMetadata:
    """Aggregate pipeline statistics, collected per stream."""

    total_paragraphs: int = 0
    corrected_paragraphs: int = 0
    rejected_paragraphs: int = 0
    eval_timeouts: int = 0
    eval_errors: int = 0
    total_evaluation_time_ms: float = 0.0
    total_correction_time_ms: float = 0.0
    paragraph_logs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def correction_rate(self) -> float:
        if self.total_paragraphs == 0:
            return 0.0
        return self.corrected_paragraphs / self.total_paragraphs

    @property
    def rejection_rate(self) -> float:
        if self.total_paragraphs == 0:
            return 0.0
        return self.rejected_paragraphs / self.total_paragraphs

    @property
    def avg_evaluation_time_ms(self) -> float:
        if self.total_paragraphs == 0:
            return 0.0
        return self.total_evaluation_time_ms / self.total_paragraphs

    def add_paragraph_log(self, paragraph: ParagraphBuffer):
        self.total_paragraphs += 1
        if paragraph.was_corrected:
            self.corrected_paragraphs += 1
        if paragraph.status == ParagraphStatus.REJECTED:
            self.rejected_paragraphs += 1
        self.paragraph_logs.append(paragraph.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total_paragraphs": self.total_paragraphs,
                "corrected_paragraphs": self.corrected_paragraphs,
                "rejected_paragraphs": self.rejected_paragraphs,
                "eval_timeouts": self.eval_timeouts,
                "eval_errors": self.eval_errors,
                "correction_rate": f"{self.correction_rate:.2%}",
                "rejection_rate": f"{self.rejection_rate:.2%}",
                "avg_evaluation_time_ms": f"{self.avg_evaluation_time_ms:.2f}",
                "total_evaluation_time_ms": f"{self.total_evaluation_time_ms:.2f}",
                "total_correction_time_ms": f"{self.total_correction_time_ms:.2f}",
            },
            "detailed_logs": self.paragraph_logs,
        }
