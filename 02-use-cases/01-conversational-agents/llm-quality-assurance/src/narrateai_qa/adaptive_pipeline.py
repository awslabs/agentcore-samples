"""Adaptive Pipeline Orchestration: three-phase adaptive processing.

Queries are routed by total retrieved volume |D| against a threshold theta:
Phase 1 (Mode-Aware Consolidation) ranks and packs sections, |D| <= theta
-> fast path, |D| > theta -> normal path; Phase 2 (Bifurcated Analysis)
makes one LLM call on the fast path or N parallel calls on the normal
path; Phase 3 (Conditional Consolidation) merges the N analyses; the
fast path bypasses it.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The threshold theta: calibrated so 70-90% of queries land on the fast
# path while staying well below the model's context window.
DEFAULT_CONSOLIDATION_CHAR_THRESHOLD = 180_000


@dataclass
class SectionData:
    """One retrieved document section."""

    section_id: str
    section_title: str
    content: str
    relevance_score: float = 0.0
    template_rank: int = 1  # source-document priority; lower ranks first
    rank: int = 0  # position within the source document
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsolidatedSections:
    """Output of Mode-Aware Consolidation: ranked sections packed into chunks.

    mode "concatenated" = single chunk (fast path), content is a str.
    mode "batched" = multiple chunks (normal path), content is a list.
    The mode determines execution statically, which enables the
    path-specific handling downstream.
    """

    content: str | list[str]
    mode: str
    total_sections: int
    sections_per_chunk: list[int]
    total_characters: int
    chunks_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


class ChunkAnalyzer(ABC):
    """Phase 2 (Bifurcated Analysis): analyzes one chunk of consolidated
    sections against the question. Production implementations call an LLM."""

    @abstractmethod
    def analyze(self, chunk: str, question: str) -> str: ...


class ResultConsolidator(ABC):
    """Phase 3 (Conditional Consolidation): synthesizes N independent
    analyses into a unified response. Normal path only."""

    @abstractmethod
    def consolidate(self, analyses: list[str], question: str) -> str: ...


class EchoChunkAnalyzer(ChunkAnalyzer):
    """Offline stand-in: returns the chunk with a header instead of calling
    an LLM. Lets the routing/packing logic run without AWS."""

    def analyze(self, chunk: str, question: str) -> str:
        return f"[analysis of {len(chunk)} chars for: {question}]\n{chunk[:200]}"


class SimpleConcatConsolidator(ResultConsolidator):
    """Offline stand-in: joins partial analyses instead of an LLM merge."""

    def consolidate(self, analyses: list[str], question: str) -> str:
        joined = "\n\n".join(analyses)
        return f"[consolidated {len(analyses)} partial analyses]\n{joined}"


class SourceDataConsolidator:
    """Phase 1 (Mode-Aware Consolidation): rank sections, pack them with
    greedy first-fit, and determine the execution mode."""

    def __init__(self, consolidation_threshold: int | None = None):
        self.consolidation_threshold = (
            consolidation_threshold if consolidation_threshold is not None else DEFAULT_CONSOLIDATION_CHAR_THRESHOLD
        )

    def _rank_sections(self, sections: list[SectionData]) -> list[SectionData]:
        """Template rank has absolute priority, then relevance (descending),
        then position in the source document."""
        return sorted(
            sections,
            key=lambda s: (s.template_rank, -s.relevance_score, s.rank),
        )

    def _pack_sections_into_chunks(self, sections: list[SectionData], threshold: int) -> list[list[SectionData]]:
        """Greedy first-fit packing up to theta characters per chunk.
        Sections are atomic (never split); a new chunk starts only when
        the current one would overflow."""
        chunks = []
        current_chunk: list[SectionData] = []
        current_size = 0

        for section in sections:
            section_size = len(section.content)
            would_exceed = (current_size + section_size) > threshold

            if would_exceed and len(current_chunk) > 0:
                chunks.append(current_chunk)
                current_chunk = [section]
                current_size = section_size
            else:
                # Even an oversized first section goes in; sections are atomic
                current_chunk.append(section)
                current_size += section_size

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _build_chunk_text(self, chunk_sections: list[SectionData]) -> str:
        """Wrap each section in a context header so the LLM can tell
        similarly-titled sections apart."""
        blocks = []
        for section in chunk_sections:
            title = section.section_title.strip("[]").strip()
            blocks.append(f"--- CONTEXT: [{title}] ---\n{section.content}\n--- END CONTEXT ---")
        return "\n\n".join(blocks)

    def consolidate_sections(self, sections: list[SectionData], question: str) -> ConsolidatedSections:
        """Rank, pack, and choose the execution mode: |D| <= theta yields
        one chunk (fast path), |D| > theta yields batches (normal path)."""
        if not sections:
            raise ValueError("No sections provided for consolidation")

        threshold = self.consolidation_threshold
        ranked = self._rank_sections(sections)
        chunks = self._pack_sections_into_chunks(ranked, threshold)

        # The routing decision: one chunk streams directly (fast path),
        # several go through parallel analysis + consolidation (normal path).
        if len(chunks) == 1:
            content: str | list[str] = self._build_chunk_text(chunks[0])
            mode = "concatenated"
        else:
            content = [self._build_chunk_text(chunk) for chunk in chunks]
            mode = "batched"

        total_chars = sum(len(s.content) for s in ranked)
        result = ConsolidatedSections(
            content=content,
            mode=mode,
            total_sections=len(ranked),
            sections_per_chunk=[len(c) for c in chunks],
            total_characters=total_chars,
            chunks_count=len(chunks),
            metadata={"consolidation_threshold": threshold},
        )

        logger.info(
            f"Consolidation: {len(ranked)} sections, {total_chars} chars -> {len(chunks)} chunk(s), mode={mode}"
        )
        return result


class AdaptivePipeline:
    """Runs the three-phase adaptive pipeline: Mode-Aware Consolidation,
    Bifurcated Analysis, and Conditional Consolidation."""

    def __init__(
        self,
        chunk_analyzer: ChunkAnalyzer,
        result_consolidator: ResultConsolidator,
        consolidation_threshold: int | None = None,
        max_parallel_analyses: int = 4,
    ):
        self.consolidator = SourceDataConsolidator(consolidation_threshold)
        self.chunk_analyzer = chunk_analyzer
        self.result_consolidator = result_consolidator
        self.max_parallel_analyses = max_parallel_analyses

    def run(self, sections: list[SectionData], question: str) -> Iterator[str]:
        """Process a query, yielding answer text.

        Fast path: single analysis streamed directly, Phase 3 bypassed.
        Normal path: parallel batch analyses, then consolidated.
        """
        # Phase 1: Mode-Aware Consolidation (pack and route)
        consolidated = self.consolidator.consolidate_sections(sections, question)

        if consolidated.mode == "concatenated":
            # Fast path: one chunk, one LLM call, already consolidated
            logger.info("FAST PATH: single chunk, streaming analysis directly")
            yield self.chunk_analyzer.analyze(consolidated.content, question)
            logger.info("Bypassing Phase 3 (fast path response uses one analysis)")
            return

        # Normal path, Phase 2: Bifurcated Analysis (N parallel invocations)
        chunk_list = consolidated.content
        logger.info(f"NORMAL PATH: {len(chunk_list)} batches, analyzing in parallel")

        with ThreadPoolExecutor(max_workers=self.max_parallel_analyses) as executor:
            analyses = list(executor.map(lambda c: self.chunk_analyzer.analyze(c, question), chunk_list))

        # Phase 3: Conditional Consolidation (unify the N analyses)
        logger.info(f"Phase 3: consolidating {len(analyses)} partial analyses")
        yield self.result_consolidator.consolidate(analyses, question)
