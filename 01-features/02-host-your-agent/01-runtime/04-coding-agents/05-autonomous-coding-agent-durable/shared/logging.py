"""Structured JSON logging for all agent components.

Provides a consistent log format across orchestrator, coding agent, and sandbox.
Every log entry includes a correlation ID (session_id + ticket_id) so you can
trace a request across all three components in CloudWatch.

Usage:
    from shared.logging import get_logger
    logger = get_logger(__name__, session_id="abc", ticket_id="TICKET-1")
    logger.info("Processing ticket", extra={"action": "run_command", "cmd": "pytest"})
"""
import json
import logging
import time
import os
from typing import Optional


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as single-line JSON for CloudWatch ingestion."""

    def __init__(self, component: str = "unknown", session_id: str = "", ticket_id: str = ""):
        super().__init__()
        self.component = component
        self.session_id = session_id
        self.ticket_id = ticket_id

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "component": self.component,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": self.session_id,
            "ticket_id": self.ticket_id,
        }

        # Add extra fields (action, cmd, path, exit_code, etc.)
        for key in ("action", "cmd", "path", "exit_code", "decision",
                    "policy_decision", "duration_ms", "error", "boot_id"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        # Add any other extra fields passed via extra={}
        if hasattr(record, "_extra"):
            log_entry.update(record._extra)

        # Add exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class CorrelatedLogger(logging.LoggerAdapter):
    """Logger adapter that injects correlation IDs into every record."""

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        # Store extra fields as _extra for the formatter
        if extra:
            kwargs.setdefault("extra", {})["_extra"] = extra
        return msg, kwargs


def get_logger(
    name: str,
    component: Optional[str] = None,
    session_id: str = "",
    ticket_id: str = "",
    level: int = logging.INFO,
) -> CorrelatedLogger:
    """Create a structured JSON logger with correlation IDs.

    Args:
        name: Logger name (typically __name__)
        component: Component identifier (orchestrator, coding-agent, sandbox)
        session_id: Runtime session ID for cross-component correlation
        ticket_id: Ticket ID being processed
        level: Log level (default: INFO)

    Returns:
        A CorrelatedLogger that outputs structured JSON.
    """
    if component is None:
        component = os.environ.get("COMPONENT_NAME", "unknown")

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            StructuredJsonFormatter(
                component=component,
                session_id=session_id,
                ticket_id=ticket_id,
            )
        )
        logger.addHandler(handler)
        logger.propagate = False

    return CorrelatedLogger(logger, {})


def update_correlation(logger: CorrelatedLogger, session_id: str = "", ticket_id: str = ""):
    """Update the correlation IDs on an existing logger (e.g., after session is derived)."""
    for handler in logger.logger.handlers:
        if isinstance(handler.formatter, StructuredJsonFormatter):
            if session_id:
                handler.formatter.session_id = session_id
            if ticket_id:
                handler.formatter.ticket_id = ticket_id
