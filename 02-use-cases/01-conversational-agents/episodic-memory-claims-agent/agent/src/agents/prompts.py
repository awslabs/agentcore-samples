"""Shared prompt utilities."""

from datetime import datetime, timezone


def with_current_date(prompt: str) -> str:
    """Append the current date so the agent can resolve relative dates correctly."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"{prompt}\n\n"
        f"CURRENT DATE: today is {today} (UTC). When the policyholder gives a "
        f'relative date ("yesterday", "last night", "two nights ago", '
        f'"last week"), resolve it against today\'s date and ALWAYS use the '
        f"correct current year. Never assume a different year."
    )
