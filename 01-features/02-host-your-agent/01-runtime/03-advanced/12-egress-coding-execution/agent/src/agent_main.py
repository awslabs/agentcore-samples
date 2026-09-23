import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from .ipc_client import IPCClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

SUPERVISOR_SOCKET = os.environ.get("SUPERVISOR_SOCKET_PATH", "/tmp/ipc/supervisor.sock")
BROKER_SOCKET = os.environ.get("BROKER_SOCKET_PATH", "/tmp/ipc/broker.sock")


def _handle_ping_domain(params: dict) -> dict:
    domain = params.get("domain")
    count = params.get("count", 3)
    timeout = params.get("timeout", 5)

    if not domain:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Missing 'domain' parameter",
            "domain": "",
            "count": count,
            "timeout": timeout,
        }

    client = IPCClient(BROKER_SOCKET)
    try:
        client.connect()
        response = client.request("ping", {"domain": domain, "count": count, "timeout": timeout})
    finally:
        client.close()

    if response and response.get("status") == "ok":
        result: dict = response.get("result", {})
        return result
    else:
        error = response.get("error", {}) if response else {}
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": error.get("message", "Ping request failed"),
            "domain": domain,
            "count": count,
            "timeout": timeout,
        }


def main():
    logger.info("Agent starting, connecting to supervisor at %s", SUPERVISOR_SOCKET)

    supervisor = IPCClient(SUPERVISOR_SOCKET)

    retries = 0
    while retries < 30:
        try:
            supervisor.connect()
            logger.info("Connected to supervisor")
            break
        except (ConnectionRefusedError, FileNotFoundError):
            retries += 1
            time.sleep(1)
    else:
        logger.error("Failed to connect to supervisor after 30 retries")
        sys.exit(1)

    try:
        while True:
            logger.info("Waiting for task...")
            msg = supervisor.wait_for_message()
            if msg is None:
                logger.info("Supervisor disconnected, shutting down")
                break

            request_id = msg.get("id", "unknown")
            method = msg.get("method", "")
            params = msg.get("params", {})

            logger.info("Received task %s: method=%s", request_id, method)

            if method == "ping_domain":
                result = _handle_ping_domain(params)
                supervisor.send_response(request_id, "ok", result=result)
            else:
                supervisor.send_response(
                    request_id,
                    "error",
                    error={
                        "code": "UNKNOWN_METHOD",
                        "message": f"Unknown method: {method}",
                    },
                )
    finally:
        supervisor.close()


if __name__ == "__main__":
    main()
