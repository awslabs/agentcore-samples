"""Async in-memory buffer between the LLM producer and the evaluation consumer."""

import asyncio
import logging
from collections import deque
from datetime import datetime

from .models import ParagraphBuffer, ParagraphStatus, StreamingEvalConfig

logger = logging.getLogger(__name__)


class LocalStreamBuffer:
    """Bounded async queue of paragraphs with backpressure.

    put() blocks when full (slows the producer), get() blocks when empty
    and returns None once the buffer is closed and drained.
    """

    def __init__(self, config: StreamingEvalConfig | None = None):
        self.config = config or StreamingEvalConfig()
        self.buffer: deque[ParagraphBuffer] = deque(maxlen=self.config.max_buffer_size)

        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
        self._not_full = asyncio.Condition(self._lock)

        self._closed = False
        self._paragraph_counter = 0

    async def put(self, content: str, metadata: dict | None = None) -> int:
        """Add a paragraph; blocks while the buffer is full. Returns its id."""
        async with self._not_full:
            while len(self.buffer) >= self.config.max_buffer_size and not self._closed:
                await self._not_full.wait()

            if self._closed:
                raise RuntimeError("Cannot put to closed buffer")

            self._paragraph_counter += 1
            paragraph = ParagraphBuffer(
                content=content,
                paragraph_id=self._paragraph_counter,
                timestamp=datetime.now(),
                status=ParagraphStatus.PENDING,
                metadata=metadata or {},
            )
            self.buffer.append(paragraph)
            self._not_empty.notify()
            return paragraph.paragraph_id

    async def get(self) -> ParagraphBuffer | None:
        """Take the next paragraph; blocks while empty. None = end of stream."""
        async with self._not_empty:
            while len(self.buffer) == 0 and not self._closed:
                await self._not_empty.wait()

            if len(self.buffer) == 0 and self._closed:
                return None

            paragraph = self.buffer.popleft()
            self._not_full.notify()
            return paragraph

    async def close(self):
        """Signal end of stream and wake all waiters."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            logger.info(f"Buffer closed (in buffer: {len(self.buffer)}, total processed: {self._paragraph_counter})")
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def cleanup_old_entries(self) -> int:
        """Drop entries older than max_buffer_age_seconds. Returns count removed."""
        if not self.buffer:
            return 0

        current_time = datetime.now()
        removed_count = 0

        while self.buffer:
            oldest = self.buffer[0]
            age_seconds = (current_time - oldest.timestamp).total_seconds()
            if age_seconds > self.config.max_buffer_age_seconds:
                self.buffer.popleft()
                removed_count += 1
                logger.warning(f"Removed stale paragraph {oldest.paragraph_id} (age: {age_seconds:.1f}s)")
            else:
                break

        return removed_count

    @property
    def size(self) -> int:
        return len(self.buffer)

    @property
    def is_empty(self) -> bool:
        return len(self.buffer) == 0

    @property
    def is_full(self) -> bool:
        return len(self.buffer) >= self.config.max_buffer_size

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def total_processed(self) -> int:
        return self._paragraph_counter

    def get_stats(self) -> dict:
        return {
            "current_size": self.size,
            "max_size": self.config.max_buffer_size,
            "is_empty": self.is_empty,
            "is_full": self.is_full,
            "is_closed": self.is_closed,
            "total_processed": self.total_processed,
            "utilization": f"{(self.size / self.config.max_buffer_size) * 100:.1f}%",
        }

    def __repr__(self) -> str:
        return (
            f"LocalStreamBuffer(size={self.size}/{self.config.max_buffer_size}, "
            f"closed={self.is_closed}, total={self.total_processed})"
        )
