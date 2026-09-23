import logging

logger = logging.getLogger(__name__)


async def handle_http_request(params: dict) -> dict:
    return {
        "status": "error",
        "error": {
            "code": "NOT_IMPLEMENTED",
            "message": "HTTP proxy not implemented in Phase 1",
        },
    }
