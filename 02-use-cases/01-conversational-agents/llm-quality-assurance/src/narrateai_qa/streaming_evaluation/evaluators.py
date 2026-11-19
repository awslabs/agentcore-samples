"""Evaluators and correctors for content quality checks.

Three checks target orthogonal failure modes: WeaselWord (subjective
language), Emoji (formatting standards), and DataAccuracy (numerical
grounding). CompositeEvaluator/CompositeCorrector aggregate them.
"""

import logging
import re
from typing import cast

from .evaluation import AutoCorrector, Evaluator, NoOpCorrector
from .models import EvaluationResult, StreamingEvalConfig

logger = logging.getLogger(__name__)


class WeaselWordEvaluator:
    """Flags subjective language that should be replaced with objective
    metrics. Pure regex, no LLM call."""

    WEASEL_WORDS = [
        # Performance adjectives
        "strong",
        "weak",
        "robust",
        "solid",
        "healthy",
        "poor",
        "excellent",
        "outstanding",
        "exceptional",
        "subpar",
        # Magnitude modifiers
        "slightly",
        "significantly",
        "substantially",
        "moderately",
        "dramatically",
        "explosive",
        "massive",
        "minimal",
        "considerable",
        # Vague qualifiers
        "somewhat",
        "fairly",
        "relatively",
        "quite",
        "rather",
        "pretty",
        "reasonably",
        # Subjective assessments
        "impressive",
        "concerning",
        "worrying",
        "encouraging",
        "disappointing",
        "mixed",
        "favorable",
        "unfavorable",
        "notable",
        # Intensity words
        "very",
        "extremely",
        "highly",
        "deeply",
        "severely",
        "critically",
        # Evaluative terms
        "good",
        "bad",
        "great",
        "terrible",
        "wonderful",
        "awful",
        # Context words often used subjectively
        "momentum",
        "demonstrates",
        "showcases",
        "highlights",
    ]

    def __init__(self):
        self.patterns = [re.compile(rf"\b{word}\b", re.IGNORECASE) for word in self.WEASEL_WORDS]

    async def evaluate(self, content: str) -> EvaluationResult:
        found_words = []
        for pattern in self.patterns:
            found_words.extend(f"'{m}'" for m in pattern.findall(content))

        if found_words:
            unique = set(found_words)
            return EvaluationResult(
                passed=False,
                needs_correction=True,
                critical_violation=False,
                score=0.5,
                issues=[f"Weasel words found: {', '.join(unique)}"],
                corrections=[f"Remove weasel words: {', '.join(unique)}"],
                metadata={"weasel_words": list(unique), "count": len(found_words)},
            )

        return EvaluationResult(
            passed=True,
            needs_correction=False,
            critical_violation=False,
            score=1.0,
            issues=[],
            metadata={"weasel_words": [], "count": 0},
        )


class WeaselWordCorrector(AutoCorrector):
    """Strips weasel words with regex replacement, then cleans up spacing."""

    WEASEL_WORDS = WeaselWordEvaluator.WEASEL_WORDS

    def __init__(self):
        # Each pattern removes the word plus any trailing space
        self.patterns = [re.compile(rf"\b{word}\b\s*", re.IGNORECASE) for word in self.WEASEL_WORDS]

    async def correct(self, content: str, eval_result: EvaluationResult) -> str:
        corrected = content
        for pattern in self.patterns:
            corrected = pattern.sub("", corrected)

        # Collapse doubled spaces left by removals (preserve newlines)
        corrected = re.sub(r" +", " ", corrected)
        return corrected.strip()


EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map symbols
    "\U0001f700-\U0001f77f"  # alchemical symbols
    "\U0001f780-\U0001f7ff"  # Geometric Shapes Extended
    "\U0001f800-\U0001f8ff"  # Supplemental Arrows-C
    "\U0001f900-\U0001f9ff"  # Supplemental Symbols and Pictographs
    "\U0001fa00-\U0001fa6f"  # Chess Symbols
    "\U0001fa70-\U0001faff"  # Symbols and Pictographs Extended-A
    "\U00002702-\U000027b0"  # Dingbats
    "\U000024c2-\U0001f251"  # Various other symbols
    "]+",
    re.UNICODE,
)


class EmojiEvaluator:
    """Flags emoji characters; business reports should not contain them."""

    def __init__(self):
        self.emoji_pattern = EMOJI_PATTERN

    async def evaluate(self, content: str) -> EvaluationResult:
        emojis = self.emoji_pattern.findall(content)

        if emojis:
            emoji_list = list(set(emojis))
            return EvaluationResult(
                passed=False,
                needs_correction=True,
                critical_violation=False,
                score=0.7,
                issues=[f"Emojis found: {', '.join(emoji_list)}"],
                corrections=[f"Remove emojis: {', '.join(emoji_list)}"],
                metadata={"emojis": emoji_list, "count": len(emojis)},
            )

        return EvaluationResult(
            passed=True,
            needs_correction=False,
            critical_violation=False,
            score=1.0,
            issues=[],
            metadata={"emojis": [], "count": 0},
        )


class EmojiCorrector(AutoCorrector):
    """Removes all emoji characters, then cleans up spacing."""

    def __init__(self):
        self.emoji_pattern = EMOJI_PATTERN

    async def correct(self, content: str, eval_result: EvaluationResult) -> str:
        corrected = self.emoji_pattern.sub("", content)
        # Collapse doubled spaces left by removal (preserve newlines)
        corrected = re.sub(r" +", " ", corrected)
        return corrected.strip()


class RealTimeDataAccuracyEvaluator:
    """Verifies that every numerical metric in a response exists in the
    source data. Extracts currency, percentages, K/M/B-suffixed numbers,
    multipliers, and ranges; anything not found in the source is treated
    as fabricated and annotated. Exact matching only, no LLM call.
    """

    ANNOTATION_LABEL = "(LLM Reasoning)"

    def __init__(self, consolidated_source_content: str):
        """consolidated_source_content: the formatted retrieval output the
        response is supposed to be grounded in."""
        self.numeric_pattern = (
            r"(?<!\w)(?:"
            r"(?:(?:nearly|approximately|about|roughly|almost|around|over|more\s+than|less\s+than|up\s+to)\s+)?\d+\.?\d*\s+times\s+(?:larger|smaller)|"
            r"(?:(?:nearly|approximately|about|roughly|almost|around|over|more\s+than|less\s+than|up\s+to)\s+)?\d+\.?\d*x|"
            r"-?\$[\d,]+\.?\d*[KMB]?|"  # currency: $444.82M, $1.2B
            r"[+-]?\d+\.?\d*%|"  # percentages: 16.14%, +5.2%
            r"[\d,]+\.?\d*[KMB]?|"  # numbers with suffixes: 2.78B, 444M
            r"\d+-\d+%?"  # ranges: 1-2%, 15-16
            r")(?!\w)"
        )

        self.min_data_point_length = 2

        # Context window around each match, used to spot metadata vs. business metrics
        self.context_before = 50
        self.context_after = 30

        # System/metadata lines whose numbers should not count as metrics
        self.ignore_patterns = [
            r"Chunks Created:\s*\d+",
            r"Total Characters:\s*[\d,]+",
            r"Consolidation Threshold:\s*[\d,]+",
            r"Sections Filtered Out:\s*\d+",
            r"Sections per Chunk:",
            r"Chunk\s+\d+:\s*\d+\s+sections",
            r"Total Sections:\s*\d+",
            r"Total Files:\s*\d+",
            r"Generated:\s*\d{4}-\d{2}-\d{2}",
            r"\d+\.\s+page_\d+_template",
        ]

        self.system_context_keywords = [
            "chunks created",
            "total characters",
            "consolidation",
            "threshold",
            "truncation",
            "sections filtered",
            "sections per chunk",
            "chunk",
            "generated",
            "metadata",
        ]

        self.source_content = self._extract_consolidated_content_section(consolidated_source_content)
        self.source_metrics = self._extract_data_points(self.source_content)

        logger.info(f"[DATA_ACCURACY] Initialized with {len(self.source_metrics)} source metrics")

    def _extract_consolidated_content_section(self, full_content: str) -> str:
        """Keep only the CONSOLIDATED CONTENT section, dropping headers and
        SECTION METADATA whose numbers would pollute the source metric set."""
        content_start = full_content.find("CONSOLIDATED CONTENT")
        if content_start == -1:
            return full_content

        content_end = full_content.find("SECTION METADATA", content_start)
        if content_end == -1:
            content_end = len(full_content)

        content = full_content[content_start:content_end]

        # Skip header/separator lines at the top
        lines = content.split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            if "=" * 40 in line or "CONSOLIDATED CONTENT" in line or "(As sent to LLM" in line or not line.strip():
                continue
            start_idx = i
            break

        return "\n".join(lines[start_idx:])

    def _should_ignore_data_point(self, value: str, context: str) -> bool:
        context_lower = context.lower()

        for pattern in self.ignore_patterns:
            if re.search(pattern, context, re.IGNORECASE):
                return True

        system_keyword_count = sum(1 for keyword in self.system_context_keywords if keyword in context_lower)
        if system_keyword_count >= 2:
            return True

        # Bullet numbers like "1."
        if re.match(r"^\d+\.$", value):
            return True

        return False

    def _extract_data_points(self, text: str) -> set[str]:
        if not text or not text.strip():
            return set()

        data_points = set()

        for match in re.finditer(self.numeric_pattern, text, re.IGNORECASE):
            value = match.group().strip()

            if len(value) < self.min_data_point_length:
                continue
            if value in [",", ".", ":", ";", "-", "(", ")", "[", "]", "{", "}"]:
                continue

            start_pos = max(0, match.start() - self.context_before)
            end_pos = min(len(text), match.end() + self.context_after)
            context = re.sub(r" +", " ", text[start_pos:end_pos].strip())

            if self._should_ignore_data_point(value, context):
                continue

            data_points.add(value)

        return data_points

    async def evaluate(self, content: str) -> EvaluationResult:
        response_metrics = self._extract_data_points(content)

        if not response_metrics:
            return EvaluationResult(
                passed=True,
                needs_correction=False,
                critical_violation=False,
                score=1.0,
                issues=[],
                metadata={
                    "total_metrics": 0,
                    "matched_metrics": 0,
                    "fabricated_metrics": 0,
                    "accuracy_rate": 1.0,
                },
            )

        matched_metrics = response_metrics & self.source_metrics
        fabricated_metrics = response_metrics - self.source_metrics

        total_metrics = len(response_metrics)
        matched_count = len(matched_metrics)
        fabricated_count = len(fabricated_metrics)
        accuracy_rate = matched_count / total_metrics if total_metrics > 0 else 1.0

        logger.info(
            f"[DATA_ACCURACY] Accuracy: {accuracy_rate:.2%} "
            f"({matched_count}/{total_metrics} matched, {fabricated_count} fabricated)"
        )

        has_fabrication = fabricated_count > 0

        issues = []
        if has_fabrication:
            issues.append(f"Fabricated metrics detected: {', '.join(sorted(fabricated_metrics))}")

        annotated_content = self._annotate_response(content, list(fabricated_metrics))

        return EvaluationResult(
            passed=accuracy_rate == 1.0,
            needs_correction=has_fabrication,
            critical_violation=False,
            score=accuracy_rate,
            issues=issues,
            corrections=(
                [f"Label fabricated data: {', '.join(sorted(fabricated_metrics))}"] if has_fabrication else []
            ),
            metadata={
                "total_metrics": total_metrics,
                "matched_metrics": matched_count,
                "fabricated_metrics": fabricated_count,
                "accuracy_rate": accuracy_rate,
                "fabricated_values": sorted(fabricated_metrics),
                "annotated_content": annotated_content,
            },
        )

    def _annotate_response(self, response: str, fabricated_values: list[str]) -> str:
        """Label each fabricated value; matched metrics stay untouched."""
        if not fabricated_values:
            return response

        annotated = response

        # Longest first so partial values don't clobber longer ones.
        # (?<!\w)/(?!\w) matches extraction's boundaries, so values inside
        # parentheses still get labeled; \b doesn't work next to $ or %.
        for value in sorted(fabricated_values, key=len, reverse=True):
            pattern = rf"(?<!\w){re.escape(value)}(?!\w)"
            annotated = re.sub(pattern, f"{value} {self.ANNOTATION_LABEL}", annotated)

        return annotated


class DataAccuracyCorrector(AutoCorrector):
    """Labels each fabricated metric in the content with "(LLM Reasoning)".

    Annotates the incoming content rather than replacing it with the
    evaluator's pre-annotated copy, so corrections applied earlier in the
    chain (weasel words, emojis) survive. Run this LAST in the composite
    chain: text modification first, then character removal, then annotation.
    """

    ANNOTATION_LABEL = "(LLM Reasoning)"

    async def correct(self, content: str, eval_result: EvaluationResult) -> str:
        # CompositeEvaluator nests per-evaluator metadata under "evaluator_results";
        # fall back to top-level metadata when used standalone.
        evaluator_results = eval_result.metadata.get("evaluator_results", {})
        data_accuracy_meta = evaluator_results.get("RealTimeDataAccuracyEvaluator", {})
        fabricated_values = data_accuracy_meta.get(
            "fabricated_values", eval_result.metadata.get("fabricated_values", [])
        )

        if not fabricated_values:
            return content

        annotated = content
        # Longest first so partial values don't clobber longer ones
        for value in sorted(fabricated_values, key=len, reverse=True):
            pattern = rf"(?<!\w){re.escape(value)}(?!\w)"
            annotated = re.sub(pattern, f"{value} {self.ANNOTATION_LABEL}", annotated)

        logger.info(f"[DATA_ACCURACY] Annotated {len(fabricated_values)} fabricated value(s)")
        return annotated


class CompositeEvaluator:
    """Aggregates independent evaluators: issues and corrections are merged,
    the lowest score wins, and any critical violation fails the paragraph."""

    def __init__(self, evaluators: list[Evaluator]):
        self.evaluators = evaluators

    @classmethod
    def from_config(cls, config: StreamingEvalConfig, consolidated_content: str | None = None) -> "CompositeEvaluator":
        """Build from config flags. consolidated_content is the source data
        for the data accuracy evaluator; without it that check is skipped."""
        evaluators: list[Evaluator] = []

        if config.enable_weasel_word_evaluation:
            evaluators.append(cast(Evaluator, WeaselWordEvaluator()))

        if config.enable_emoji_evaluation:
            evaluators.append(cast(Evaluator, EmojiEvaluator()))

        if config.enable_data_accuracy_evaluation and consolidated_content:
            evaluators.append(cast(Evaluator, RealTimeDataAccuracyEvaluator(consolidated_content)))
        elif config.enable_data_accuracy_evaluation:
            logger.warning("Data accuracy evaluation enabled but no source content provided, skipping")

        return cls(evaluators)

    async def evaluate(self, content: str) -> EvaluationResult:
        if not self.evaluators:
            return EvaluationResult(
                passed=True,
                needs_correction=False,
                critical_violation=False,
                score=1.0,
                issues=[],
                metadata={"evaluators_run": 0},
            )

        all_issues = []
        all_corrections = []
        all_metadata = {}
        has_critical = False
        needs_any_correction = False
        min_score = 1.0

        for evaluator in self.evaluators:
            result = await evaluator.evaluate(content)

            all_issues.extend(result.issues)
            all_corrections.extend(result.corrections)
            all_metadata[type(evaluator).__name__] = result.metadata

            has_critical = has_critical or result.critical_violation
            needs_any_correction = needs_any_correction or result.needs_correction
            min_score = min(min_score, result.score)

        return EvaluationResult(
            passed=not has_critical and not needs_any_correction,
            needs_correction=needs_any_correction,
            critical_violation=has_critical,
            score=min_score,
            issues=all_issues,
            corrections=all_corrections,
            metadata={
                "evaluators_run": len(self.evaluators),
                "evaluator_results": all_metadata,
            },
        )


class CompositeCorrector(AutoCorrector):
    """Applies each enabled corrector in order to the same content."""

    def __init__(self, correctors: list[AutoCorrector]):
        self.correctors = correctors

    @classmethod
    def from_config(cls, config: StreamingEvalConfig) -> "CompositeCorrector":
        correctors: list[AutoCorrector] = []

        # Order matters: text modification first, then character removal,
        # then annotation, so each corrector gets stable input.
        if config.enable_weasel_word_correction:
            correctors.append(cast(AutoCorrector, WeaselWordCorrector()))

        if config.enable_emoji_correction:
            correctors.append(cast(AutoCorrector, EmojiCorrector()))

        if config.enable_data_accuracy_correction:
            correctors.append(cast(AutoCorrector, DataAccuracyCorrector()))

        if not correctors:
            correctors.append(cast(AutoCorrector, NoOpCorrector()))

        return cls(correctors)

    async def correct(self, content: str, eval_result: EvaluationResult) -> str:
        corrected = content
        for corrector in self.correctors:
            corrected = await corrector.correct(corrected, eval_result)
        return corrected
