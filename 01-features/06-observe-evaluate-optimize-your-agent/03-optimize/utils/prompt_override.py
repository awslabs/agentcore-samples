"""Build-time system-prompt replacement for the sample Runtime."""

from __future__ import annotations

import re
from pathlib import Path

_SYSTEM_PROMPT_ASSIGNMENT = re.compile(
    r'DEFAULT_SYSTEM_PROMPT\s*=\s*""".*?"""',
    re.DOTALL,
)


def apply_system_prompt(source: str, prompt: str) -> str:
    """Replace exactly one embedded DEFAULT_SYSTEM_PROMPT assignment."""
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("System prompt override must not be empty")
    updated, replacements = _SYSTEM_PROMPT_ASSIGNMENT.subn(
        lambda _: f"DEFAULT_SYSTEM_PROMPT = {prompt!r}",
        source,
        count=1,
    )
    if replacements != 1:
        raise ValueError("Expected one DEFAULT_SYSTEM_PROMPT assignment")
    return updated


def apply_system_prompt_file(source: str, path: Path | None) -> str:
    """Apply a prompt file when supplied; otherwise preserve source exactly."""
    if path is None:
        return source
    return apply_system_prompt(source, path.read_text(encoding="utf-8"))
