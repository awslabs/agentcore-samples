"""Streams evaluated paragraphs to the user in multi-word chunks."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator

from .models import StreamingEvalConfig

logger = logging.getLogger(__name__)


class MultiWordStreamer:
    """Yields paragraph content a few words at a time with small delays,
    keeping the incremental feel of live generation after evaluation."""

    def __init__(
        self,
        config: StreamingEvalConfig | None = None,
        words_per_chunk: int | None = None,
        chunk_delay_ms: int | None = None,
    ):
        self.config = config or StreamingEvalConfig()
        self.words_per_chunk = words_per_chunk if words_per_chunk is not None else self.config.words_per_chunk
        self.chunk_delay_ms = chunk_delay_ms if chunk_delay_ms is not None else self.config.chunk_delay_ms
        self.delay_seconds = self.chunk_delay_ms / 1000.0

    async def stream_paragraph(self, content: str) -> AsyncGenerator[str, None]:
        """Yield ~words_per_chunk words at a time, preserving whitespace and newlines."""
        if not content:
            return

        # Split on spaces but keep newlines as their own tokens
        tokens = [t for t in re.split(r"( +|\n)", content) if t]
        if not tokens:
            return

        chunk_buffer = []
        word_count = 0

        for i, token in enumerate(tokens):
            chunk_buffer.append(token)
            if token.strip():
                word_count += 1

            is_last_token = i == len(tokens) - 1
            if (word_count >= self.words_per_chunk or is_last_token) and chunk_buffer:
                yield "".join(chunk_buffer)
                chunk_buffer = []
                word_count = 0

                if not is_last_token and self.delay_seconds > 0:
                    await asyncio.sleep(self.delay_seconds)

    async def stream_paragraph_fast(self, content: str) -> AsyncGenerator[str, None]:
        """Chunked streaming without delays."""
        if not content:
            return

        words = content.split()
        total_words = len(words)

        for i in range(0, total_words, self.words_per_chunk):
            chunk = " ".join(words[i : i + self.words_per_chunk])
            if i + self.words_per_chunk < total_words:
                chunk += " "
            yield chunk

    async def stream_word_by_word(self, content: str, word_delay_ms: int | None = None) -> AsyncGenerator[str, None]:
        """One word at a time; slower, but useful for demos."""
        if not content:
            return

        delay = (word_delay_ms or self.chunk_delay_ms) / 1000.0
        words = content.split()
        total_words = len(words)

        for i, word in enumerate(words):
            yield word + " " if i < total_words - 1 else word
            if i < total_words - 1 and delay > 0:
                await asyncio.sleep(delay)

    def estimate_streaming_time(self, content: str) -> float:
        """Estimated seconds of artificial delay for a paragraph."""
        if not content:
            return 0.0

        total_words = len(content.split())
        if total_words == 0:
            return 0.0

        num_chunks = (total_words + self.words_per_chunk - 1) // self.words_per_chunk
        return (num_chunks - 1) * self.delay_seconds

    def get_stats(self) -> dict:
        return {
            "words_per_chunk": self.words_per_chunk,
            "chunk_delay_ms": self.chunk_delay_ms,
            "delay_seconds": self.delay_seconds,
        }
