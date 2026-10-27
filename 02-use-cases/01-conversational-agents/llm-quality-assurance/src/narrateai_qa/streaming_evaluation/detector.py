"""Paragraph boundary detection over a streaming token sequence."""

import logging
import re

from .models import StreamingEvalConfig

logger = logging.getLogger(__name__)


class ParagraphDetector:
    """Decides when accumulated tokens form a complete paragraph.

    Boundary heuristics, in priority order: structural content (markdown
    headers / horizontal rules, flagged so the pipeline can skip evaluating
    them), max length, double newline, sentence-ending punctuation. The first
    paragraph uses a lower minimum length to cut time-to-first-token.
    """

    HEADER_PATTERN = re.compile(r"^#{1,6}\s+.+", re.MULTILINE)
    HORIZONTAL_RULE_PATTERN = re.compile(r"^[\-\*_]{3,}\s*$", re.MULTILINE)

    def __init__(self, config: StreamingEvalConfig | None = None):
        self.config = config or StreamingEvalConfig()
        self._paragraphs_detected = 0

    @property
    def _effective_min_length(self) -> int:
        if self._paragraphs_detected == 0:
            return self.config.first_paragraph_min_length
        return self.config.min_paragraph_length

    def is_boundary(self, accumulated_text: str) -> bool:
        """True if the accumulated text can be split into a paragraph."""
        text_length = len(accumulated_text)

        if self.config.enable_structural_bypass and self._detect_structural_split(accumulated_text) is not None:
            return True

        if text_length >= self.config.max_paragraph_length:
            return True

        if text_length < self._effective_min_length:
            return False

        # Don't split inside unclosed **bold** / *italic* spans
        if self._has_unclosed_markdown_marker(accumulated_text):
            return False

        if self._has_paragraph_marker(accumulated_text):
            return True

        if self._has_sentence_ending(accumulated_text):
            return True

        return False

    def extract_paragraph(self, accumulated_text: str) -> tuple[str, str]:
        """Split accumulated text into (paragraph, remainder) at the best boundary."""
        if self.config.enable_structural_bypass:
            structural_result = self._detect_structural_split(accumulated_text)
            if structural_result is not None:
                self._paragraphs_detected += 1
                return structural_result

        if "\n\n" in accumulated_text:
            split_idx = accumulated_text.rfind("\n\n") + 2
            paragraph = accumulated_text[:split_idx].strip()
            remainder = accumulated_text[split_idx:].strip()
            self._paragraphs_detected += 1
            return paragraph, remainder

        for marker in reversed(self.config.sentence_end_markers):
            if marker in accumulated_text:
                split_idx = accumulated_text.rfind(marker) + 1
                if split_idx < len(accumulated_text):
                    next_char = accumulated_text[split_idx]
                    if next_char in (" ", "\n", ""):
                        paragraph = accumulated_text[:split_idx].strip()
                        remainder = accumulated_text[split_idx:].strip()
                        self._paragraphs_detected += 1
                        return paragraph, remainder

        self._paragraphs_detected += 1
        return accumulated_text.strip(), ""

    def is_structural_content(self, text: str) -> bool:
        """Headers and horizontal rules carry no data-accuracy risk, so the
        pipeline can stream them without evaluation."""
        if not text or not text.strip():
            return False

        stripped = text.strip()

        if self.HEADER_PATTERN.fullmatch(stripped):
            return True
        if self.HORIZONTAL_RULE_PATTERN.fullmatch(stripped):
            return True

        # A lone header line (e.g. "## Revenue Analysis\n") with no body yet
        lines = stripped.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]
        if len(non_empty_lines) == 1 and self.HEADER_PATTERN.fullmatch(non_empty_lines[0].strip()):
            return True

        return False

    def _detect_structural_split(self, accumulated_text: str) -> tuple[str, str] | None:
        """If the text starts with a header/rule followed by a newline, return
        (structural_paragraph, remainder); otherwise None."""
        if not accumulated_text or not accumulated_text.strip():
            return None

        first_newline_idx = accumulated_text.find("\n")
        if first_newline_idx == -1:
            return None

        first_line = accumulated_text[:first_newline_idx].strip()
        if not first_line or len(first_line) < 2:
            return None

        is_header = bool(self.HEADER_PATTERN.fullmatch(first_line))
        is_rule = bool(self.HORIZONTAL_RULE_PATTERN.fullmatch(first_line))
        if not is_header and not is_rule:
            return None

        after_first_line = accumulated_text[first_newline_idx + 1 :]

        if after_first_line.startswith("\n"):
            return first_line, after_first_line[1:].strip()
        if after_first_line.strip():
            return first_line, after_first_line.strip()
        return first_line, ""

    def _has_unclosed_markdown_marker(self, text: str) -> bool:
        bold_count = text.count("**")
        if bold_count % 2 != 0:
            return True

        # Count standalone * after removing ** pairs
        text_without_bold = text.replace("**", "")
        if text_without_bold.count("*") % 2 != 0:
            return True

        return False

    def _has_paragraph_marker(self, text: str) -> bool:
        # Only look at the tail so old markers don't retrigger
        tail = text[-20:] if len(text) > 20 else text
        return "\n\n" in tail

    def _has_sentence_ending(self, text: str) -> bool:
        if not text:
            return False

        last_char = text[-1]
        if last_char not in self.config.sentence_end_markers:
            return False

        if last_char == "#":
            return True

        # Distinguish decimals from sentence ends: "73.32." ends a sentence,
        # "$331." might be mid-decimal, so wait for more tokens.
        if last_char == "." and len(text) >= 2 and text[-2].isdigit():
            if re.search(r"\d[\d,]*\.\d+\.$", text):
                return True
            return False

        if len(text) >= 3:
            last_three = text[-3:].lower()
            if last_three in ("dr.", "mr.", "ms.", "st.", "vs."):
                return False

        return True

    def get_stats(self) -> dict:
        return {
            "min_paragraph_length": self.config.min_paragraph_length,
            "first_paragraph_min_length": self.config.first_paragraph_min_length,
            "max_paragraph_length": self.config.max_paragraph_length,
            "sentence_end_markers": list(self.config.sentence_end_markers),
            "structural_bypass_enabled": self.config.enable_structural_bypass,
            "paragraphs_detected": self._paragraphs_detected,
        }
