import logging
import contextvars
from typing import Optional

# Request context for logging
request_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)


class RequestContextFormatter(logging.Formatter):
    """Custom formatter that includes request ID in log messages."""

    def format(self, record):
        """Format log record with request ID context."""
        request_id = request_id_context.get()
        if request_id:
            record.request_id = f"[{request_id}] "
        else:
            record.request_id = ""
        return super().format(record)


# Configure logging
logger = logging.getLogger("bedrock_agentcore.app")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = RequestContextFormatter("%(asctime)s - %(name)s - %(levelname)s - %(request_id)s%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def get_logger():
    return logger
