"""Real-time streaming evaluation for LLM responses.

Streams LLM output through paragraph-level evaluation and auto-correction
without blocking generation, using a producer-consumer pipeline.

Usage:
    from narrateai_qa.streaming_evaluation import (
        StreamingEvaluationPipeline,
        StreamingEvalConfig,
    )

    pipeline = StreamingEvaluationPipeline(
        evaluator=my_evaluator,
        auto_corrector=my_corrector,
        config=StreamingEvalConfig(),
    )

    async for chunk in pipeline.process_stream(llm_stream):
        yield chunk
"""

from .buffer import LocalStreamBuffer
from .detector import ParagraphDetector
from .evaluation import AutoCorrector, EvaluationPipeline
from .evaluators import (
    CompositeCorrector,
    CompositeEvaluator,
    DataAccuracyCorrector,
    EmojiCorrector,
    EmojiEvaluator,
    RealTimeDataAccuracyEvaluator,
    WeaselWordCorrector,
    WeaselWordEvaluator,
)
from .models import (
    EvaluationResult,
    ParagraphBuffer,
    ParagraphStatus,
    StreamingEvalConfig,
)
from .pipeline import StreamingEvaluationPipeline
from .streamer import MultiWordStreamer

__all__ = [
    "AutoCorrector",
    "CompositeCorrector",
    "CompositeEvaluator",
    "DataAccuracyCorrector",
    "EmojiCorrector",
    "EmojiEvaluator",
    "EvaluationPipeline",
    "EvaluationResult",
    "LocalStreamBuffer",
    "MultiWordStreamer",
    "ParagraphBuffer",
    "ParagraphDetector",
    "ParagraphStatus",
    "RealTimeDataAccuracyEvaluator",
    "StreamingEvalConfig",
    "StreamingEvaluationPipeline",
    "WeaselWordCorrector",
    "WeaselWordEvaluator",
]
