"""The validator's decision tools: the validator decides, and acts, through them.

The validator agent ends its review by calling exactly one of two tools:

  approve_expense   save the expense (Cedar still gates it at the Gateway)
  send_to_review    route it to a person, with the reviewer note written for them

The tools are **pinned**: they take the validator's confidence, notes and concerns, never
an amount or a field. The expense they save is the one the extractor produced, supplied
here by the orchestrator, exactly as the chat tools pin the verified user id. So the model
chooses, and cannot alter what is written.

The orchestrator keeps these guarantees in code:
  - exactly one outcome: a second decision is refused, not acted on
  - fail safe: if the validator decides nothing, `fallback_review` routes to review
  - degraded rungs: with forceReview, only `send_to_review` is offered
  - a Cedar denial of the save files a review instead

Because the validator calls the tools, Strands traces the decision as the validator's own
tool call, with the Gateway's save_expense or human_review call traced beneath it.

Pure orchestration: the Gateway calls and the note writer are injected, so this is
unit-testable with no AWS.
"""

from collections.abc import Callable
from typing import Any

from strands import tool

CEDAR_BLOCKED_REASON = "blocked by policy (amount over threshold)"


class ReceiptDecision:
    """One receipt's decision: recorded once, carried out once."""

    def __init__(
        self,
        save: Callable[[], Any],
        review: Callable[[str], Any],
        write_note: Callable[[str], str],
        is_denied: Callable[[Any], bool],
    ):
        self._save = save
        self._review = review
        self._write_note = write_note
        self._is_denied = is_denied
        self.decided = False
        self.validation: dict = {}
        self.status: str | None = None
        self.result: Any = None
        self.cedar_blocked = False
        # A Gateway failure inside a tool is re-raised by the orchestrator after the agent
        # returns, so the run is recorded as an error rather than swallowed as a tool error.
        self.error: Exception | None = None

    def _record(self, routing: str, confidence: Any, notes: str, concerns: str) -> None:
        try:
            confidence = max(0, min(100, int(confidence)))
        except (TypeError, ValueError):
            confidence = 0
        self.validation = {"routing": routing, "confidence": confidence, "notes": notes, "concerns": concerns}

    def _file_review(self, reason: str) -> None:
        note = self._write_note(reason)
        self.result = self._review(note or reason)
        self.status = "needs_review"

    def approve(self, confidence: Any, notes: str) -> str:
        if self.decided:
            return "A decision was already recorded for this receipt; nothing more was done."
        self.decided = True
        self._record("AUTO_PERSIST", confidence, notes, "None")
        try:
            saved = self._save()
            if self._is_denied(saved):
                self.cedar_blocked = True
                self._file_review(CEDAR_BLOCKED_REASON)
                return "The save was blocked by policy (amount over the limit), so it was sent to human review."
            self.result = saved
            self.status = "processed"
            return "Saved."
        except Exception as exc:
            self.error = exc
            raise

    def send_to_review(self, confidence: Any, notes: str, concerns: str) -> str:
        if self.decided:
            return "A decision was already recorded for this receipt; nothing more was done."
        self.decided = True
        self._record("NEEDS_REVIEW", confidence, notes, concerns or "None")
        try:
            self._file_review(concerns if concerns and concerns != "None" else "validator routed to review")
            return "Sent to human review."
        except Exception as exc:
            self.error = exc
            raise

    def fallback_review(self, reason: str) -> None:
        """Route to review when the validator decided nothing, or was shed on this rung."""
        if self.decided:
            return
        self.decided = True
        self._record("NEEDS_REVIEW", 0, reason, "None")
        self._file_review(reason)

    def tools(self, allow_approve: bool = True) -> list:
        """The tools offered to the validator. With forceReview, approve is not offered."""

        @tool
        def approve_expense(confidence: int, notes: str) -> str:
            """Approve the extracted expense and save it. Call this only when the extraction is
            clearly correct and reconciles. The expense saved is the extractor's, unchanged.

            Args:
                confidence: Your 0-100 confidence that the extraction is correct and safe to save.
                notes: A brief assessment of the extraction.
            """
            return self.approve(confidence, notes)

        @tool
        def send_to_review(confidence: int, notes: str, concerns: str) -> str:
            """Send the expense to a human reviewer instead of saving it. Call this when anything
            is off: totals don't reconcile, a field is unsupported by the receipt, the merchant or
            category is questionable, or the amount is large with weak evidence.

            Args:
                confidence: Your 0-100 confidence that the extraction is correct.
                notes: A brief assessment of the extraction.
                concerns: The specific problems a reviewer should check.
            """
            return self.send_to_review(confidence, notes, concerns)

        return [approve_expense, send_to_review] if allow_approve else [send_to_review]
