"""Small UI helpers so an example can narrate itself to a human reader.

The helpers respect the NO_COLOR env var (disables ANSI) and
INTERACTIVE_NO_PAUSE=1 (disables the "Press Enter" prompts, useful in tests
and non-interactive runs).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _colorize(code: str) -> str:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return ""
    return code


RESET = _colorize("\033[0m")
BOLD = _colorize("\033[1m")
DIM = _colorize("\033[2m")
CYAN = _colorize("\033[96m")
MAGENTA = _colorize("\033[95m")
YELLOW = _colorize("\033[93m")
GREEN = _colorize("\033[92m")
RED = _colorize("\033[91m")
BLUE = _colorize("\033[94m")


def header(title: str, subtitle: str = "") -> None:
    print()
    print(f"{BOLD}{CYAN}{'═' * 72}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    if subtitle:
        print(f"{DIM}  {subtitle}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 72}{RESET}")


def chapter(num: int, title: str, objective: str) -> None:
    print()
    print(f"{BOLD}{MAGENTA}━━━ Chapter {num}: {title} ━━━{RESET}")
    print(f"{DIM}Objective: {objective}{RESET}")
    print()


def explain(text: str) -> None:
    """Multi-line explanation. Leading/trailing whitespace stripped."""
    for line in text.strip().split("\n"):
        print(f"  {line}")
    print()


def observe(title: str, text: str) -> None:
    """Highlighted 'key observation' block — the learning moment."""
    print()
    print(f"{BOLD}{YELLOW}💡 {title}{RESET}")
    for line in text.strip().split("\n"):
        print(f"   {line}")
    print()


def success(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {BLUE}ℹ{RESET} {msg}")


def action(msg: str) -> None:
    print(f"  {CYAN}▶{RESET} {msg}")


def pause(prompt: str = "Press Enter to continue") -> None:
    if os.environ.get("INTERACTIVE_NO_PAUSE") == "1":
        return
    try:
        input(f"\n{DIM}  ↵ {prompt}...{RESET}")
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)


def show_claims(label: str, claims: dict[str, Any], highlight: list[str] | None = None) -> None:
    """Pretty-print JWT claims, optionally bolding specified keys."""
    highlight = highlight or []
    print(f"  {BOLD}{label}:{RESET}")
    # Preserve insertion order; highlighted keys first if present
    ordered_keys = [k for k in highlight if k in claims] + [k for k in claims if k not in highlight]
    for key in ordered_keys:
        value = claims[key]
        value_str = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
        # Truncate very long values
        if len(value_str) > 60:
            value_str = value_str[:57] + "..."
        if key in highlight:
            print(f"    {BOLD}{YELLOW}{key:<12}{RESET} = {value_str}")
        else:
            print(f"    {DIM}{key:<12}{RESET} = {value_str}")
    print()


def compare_claims(
    left_label: str,
    left: dict[str, Any],
    right_label: str,
    right: dict[str, Any],
    keys: list[str],
) -> None:
    """Side-by-side comparison of specific claims, showing matches vs changes."""
    print(f"  {BOLD}Side-by-side comparison:{RESET}")
    header_row = f"  {'Claim':<10} {left_label:<30} {right_label:<30} Change"
    print(f"{DIM}{header_row}{RESET}")
    print(f"{DIM}  {'─' * 10} {'─' * 30} {'─' * 30} {'─' * 7}{RESET}")
    for key in keys:
        lv = _format_claim(left.get(key, "—"))
        rv = _format_claim(right.get(key, "—"))
        if lv == rv:
            marker = f"{GREEN}same{RESET}"
        elif lv == "—":
            marker = f"{YELLOW}added{RESET}"
        elif rv == "—":
            marker = f"{YELLOW}removed{RESET}"
        else:
            marker = f"{YELLOW}changed{RESET}"
        print(f"  {key:<10} {_truncate(lv, 30):<30} {_truncate(rv, 30):<30} {marker}")
    print()


def _format_claim(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _truncate(s: str, width: int) -> str:
    return (s[: width - 3] + "...") if len(s) > width else s
