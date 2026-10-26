import os
import socket
import sys
import uuid
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.ipc import recv_message, send_message


class IPCClient:
    def __init__(self, socket_path: str):
        self._socket_path = socket_path
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self._socket_path)

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def _require_sock(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("IPC client is not connected")
        return self._sock

    def request(self, method: str, params: dict) -> Optional[dict]:
        sock = self._require_sock()
        msg = {
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        send_message(sock, msg)
        return recv_message(sock)

    def send_response(
        self,
        request_id: str,
        status: str,
        result: Optional[dict] = None,
        error: Optional[dict] = None,
    ) -> None:
        sock = self._require_sock()
        msg: dict = {"id": request_id, "status": status}
        if result is not None:
            msg["result"] = result
        if error is not None:
            msg["error"] = error
        send_message(sock, msg)

    def wait_for_message(self) -> Optional[dict]:
        sock = self._require_sock()
        return recv_message(sock)
