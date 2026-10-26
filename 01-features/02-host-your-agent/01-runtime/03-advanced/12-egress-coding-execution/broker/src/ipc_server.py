import asyncio
import json
import logging
import os
import struct
from pathlib import Path

from .config import broker_config
from .handlers.http import handle_http_request
from .handlers.ping import handle_ping

logger = logging.getLogger(__name__)

HEADER_FORMAT = ">I"
HEADER_SIZE = 4

# Method -> async handler. `heartbeat` and `configure` are handled inline in
# _handle_connection (they need direct access to the connection/config), so they
# are not registered here.
HANDLERS = {
    "ping": handle_ping,
    "http_request": handle_http_request,
}


async def _recv_message(reader: asyncio.StreamReader) -> dict | None:
    try:
        header = await reader.readexactly(HEADER_SIZE)
    except asyncio.IncompleteReadError:
        return None
    length = struct.unpack(HEADER_FORMAT, header)[0]
    payload = await reader.readexactly(length)
    message: dict = json.loads(payload.decode("utf-8"))
    return message


async def _send_message(writer: asyncio.StreamWriter, msg: dict) -> None:
    payload = json.dumps(msg).encode("utf-8")
    header = struct.pack(HEADER_FORMAT, len(payload))
    writer.write(header + payload)
    await writer.drain()


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername")
    logger.info("Agent connected: %s", peer)

    try:
        while True:
            msg = await _recv_message(reader)
            if msg is None:
                logger.info("Agent disconnected")
                break

            request_id = msg.get("id", "unknown")
            method = msg.get("method", "")
            params = msg.get("params", {})

            logger.info("Request %s: method=%s", request_id, method)

            if method == "heartbeat":
                response = {"id": request_id, "status": "ok", "result": {"alive": True}}
            elif method == "configure":
                updated = broker_config.update(
                    allowed_ping_domains=params.get("allowed_ping_domains"),
                    allowed_domains=params.get("allowed_domains"),
                )
                response = {
                    "id": request_id,
                    "status": "ok",
                    "result": {"updated": updated},
                }
            elif method in HANDLERS:
                handler = HANDLERS[method]
                result = await handler(params)
                response = {"id": request_id, **result}
            else:
                response = {
                    "id": request_id,
                    "status": "error",
                    "error": {
                        "code": "UNKNOWN_METHOD",
                        "message": f"Unknown method: {method}",
                    },
                }

            await _send_message(writer, response)
    except Exception as e:
        logger.exception("Error handling connection: %s", e)
    finally:
        writer.close()
        await writer.wait_closed()


async def start_server(socket_path: str):
    path = Path(socket_path)
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    server = await asyncio.start_unix_server(_handle_connection, path=socket_path)
    # Intentional: the socket is created by the (root) broker, but the untrusted
    # agent runs as UID 1000 and must be able to connect to it. Cross-UID access
    # to this IPC socket is core to the sandbox design.
    os.chmod(socket_path, 0o777)  # nosec B103
    logger.info("Broker IPC server listening on %s", socket_path)

    async with server:
        await server.serve_forever()
