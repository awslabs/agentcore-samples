import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from .ipc_server import start_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    socket_path = os.environ.get("IPC_SOCKET_PATH", "/tmp/ipc/broker.sock")
    logger.info("Starting broker, socket=%s", socket_path)
    asyncio.run(start_server(socket_path))


if __name__ == "__main__":
    main()
