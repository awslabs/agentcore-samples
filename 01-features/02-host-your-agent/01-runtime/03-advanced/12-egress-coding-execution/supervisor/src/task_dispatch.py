import logging
import os
import socket
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.ipc import recv_message, send_message

logger = logging.getLogger(__name__)

SUPERVISOR_SOCKET_PATH = os.environ.get("SUPERVISOR_SOCKET_PATH", "/run/containerd/sandbox-ipc/supervisor.sock")


class TaskDispatcher:
    def __init__(self, socket_path: Optional[str] = None):
        self._socket_path = socket_path or SUPERVISOR_SOCKET_PATH
        self._server_sock: Optional[socket.socket] = None
        self._agent_conn: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def start(self):
        path = Path(self._socket_path)
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._socket_path)
        self._server_sock.listen(1)
        # Intentional: the socket is created by the (root) supervisor, but the
        # untrusted agent runs as UID 1000 and must be able to connect to it.
        # Cross-UID access to this IPC socket is core to the sandbox design.
        os.chmod(self._socket_path, 0o777)  # nosec B103
        logger.info("Task dispatch socket listening at %s", self._socket_path)

    def wait_for_agent(self, timeout: float = 60) -> bool:
        if self._server_sock is None:
            raise RuntimeError("Dispatcher not started; call start() first")
        self._server_sock.settimeout(timeout)
        try:
            self._agent_conn, addr = self._server_sock.accept()
            logger.info("Agent connected to task dispatch socket")
            return True
        except socket.timeout:
            logger.error("Timed out waiting for agent to connect")
            return False

    def dispatch_task(self, method: str, params: dict, timeout: float = 660) -> dict:
        with self._lock:
            if not self._agent_conn:
                return {
                    "status": "error",
                    "error": {"code": "NO_AGENT", "message": "Agent not connected"},
                }

            request_id = str(uuid.uuid4())
            msg = {"id": request_id, "method": method, "params": params}

            self._agent_conn.settimeout(timeout)
            try:
                send_message(self._agent_conn, msg)
                response = recv_message(self._agent_conn)
                return (
                    response
                    if response
                    else {
                        "status": "error",
                        "error": {
                            "code": "NO_RESPONSE",
                            "message": "Agent disconnected",
                        },
                    }
                )
            except socket.timeout:
                return {
                    "status": "error",
                    "error": {"code": "TIMEOUT", "message": "Agent task timed out"},
                }
            except Exception as e:
                return {
                    "status": "error",
                    "error": {"code": "DISPATCH_ERROR", "message": str(e)},
                }

    def stop(self):
        if self._agent_conn:
            self._agent_conn.close()
            self._agent_conn = None
        if self._server_sock:
            self._server_sock.close()
            self._server_sock = None
        path = Path(self._socket_path)
        if path.exists():
            path.unlink()
