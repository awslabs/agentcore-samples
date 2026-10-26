import json
import socket
import struct
from typing import Optional

HEADER_SIZE = 4
HEADER_FORMAT = ">I"


def send_message(sock: socket.socket, msg: dict) -> None:
    payload = json.dumps(msg).encode("utf-8")
    header = struct.pack(HEADER_FORMAT, len(payload))
    sock.sendall(header + payload)


def recv_message(sock: socket.socket) -> Optional[dict]:
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None
    (length,) = struct.unpack(HEADER_FORMAT, header)
    payload = _recv_exact(sock, length)
    if payload is None:
        return None
    message: dict = json.loads(payload.decode("utf-8"))
    return message


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
