import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .containerd import (
    get_container_status,
    list_containers,
    pull_image,
    start_container,
    stop_container,
)
from .ecr import get_ecr_token
from .profiles import IPC_DIR, WORKSPACE_DIR, get_agent_ctr_args, get_broker_ctr_args
from .task_dispatch import TaskDispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [supervisor] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

BROKER_CONTAINER = "broker"
AGENT_CONTAINER = "agent"

dispatcher = TaskDispatcher(socket_path=os.path.join(IPC_DIR, "supervisor.sock"))

Path(IPC_DIR).mkdir(parents=True, exist_ok=True)
Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
dispatcher.start()
logger.info("Supervisor ready")

app = BedrockAgentCoreApp()


@app.entrypoint
async def handle(payload):
    data = json.loads(payload) if isinstance(payload, str) else payload
    command = data.get("command")
    params = data.get("params", {})

    if command == "start_broker":
        return await _start_broker(params)
    elif command == "start_agent":
        return await _start_agent(params)
    elif command == "ping_domain":
        return await _ping_domain(params)
    elif command == "configure_broker":
        return await _configure_broker(params)
    elif command == "stop_broker":
        return await _stop_broker()
    elif command == "stop_agent":
        return await _stop_agent()
    elif command == "status":
        return _status()
    elif command == "list_containers":
        return _list_containers()
    else:
        return {"status": "error", "message": f"Unknown command: {command}"}


def _status():
    return {
        "status": "ok",
        "broker": get_container_status(BROKER_CONTAINER),
        "agent": get_container_status(AGENT_CONTAINER),
    }


def _list_containers():
    return {"status": "ok", "result": list_containers()}


async def _start_broker(params: dict):
    try:
        image_uri = params["image_uri"]
        env = params.get("env")

        region = os.environ.get("AWS_REGION", "us-east-1")
        token = get_ecr_token(region)
        pull_image(image_uri, token)

        ctr_args = get_broker_ctr_args(BROKER_CONTAINER, image_uri, env=env)
        start_container(ctr_args)

        return {"status": "ok", "message": "Broker started"}
    except Exception as e:
        logger.exception("Failed to start broker")
        return {"status": "error", "message": str(e)}


async def _start_agent(params: dict):
    try:
        image_uri = params["image_uri"]

        region = os.environ.get("AWS_REGION", "us-east-1")
        token = get_ecr_token(region)
        pull_image(image_uri, token)

        ctr_args = get_agent_ctr_args(AGENT_CONTAINER, image_uri)
        start_container(ctr_args)

        if not dispatcher.wait_for_agent(timeout=60):
            raise RuntimeError("Agent did not connect within 60 seconds")

        return {"status": "ok", "message": "Agent started and connected"}
    except Exception as e:
        logger.exception("Failed to start agent")
        return {"status": "error", "message": str(e)}


async def _ping_domain(params: dict):
    broker_status = get_container_status(BROKER_CONTAINER)
    agent_status = get_container_status(AGENT_CONTAINER)

    if broker_status != "running":
        return {
            "status": "error",
            "message": f"Broker not running (status: {broker_status})",
        }
    if agent_status != "running":
        return {
            "status": "error",
            "message": f"Agent not running (status: {agent_status})",
        }

    domain = params.get("domain")
    if not domain:
        return {"status": "error", "message": "Missing 'domain' parameter"}

    result = dispatcher.dispatch_task(
        "ping_domain",
        {
            "domain": domain,
            "count": params.get("count", 3),
            "timeout": params.get("timeout", 5),
        },
    )

    if result and result.get("status") == "ok":
        return {"status": "ok", "result": result.get("result", {})}
    else:
        error = result.get("error", {}) if result else {}
        return {"status": "error", "message": error.get("message", "Ping task failed")}


async def _configure_broker(params: dict):
    broker_status = get_container_status(BROKER_CONTAINER)
    if broker_status != "running":
        return {
            "status": "error",
            "message": f"Broker not running (status: {broker_status})",
        }

    import socket
    import uuid

    from shared.ipc import recv_message, send_message

    broker_socket_path = os.path.join(IPC_DIR, "broker.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(broker_socket_path)
        msg = {
            "id": str(uuid.uuid4()),
            "method": "configure",
            "params": {
                "allowed_ping_domains": params.get("allowed_ping_domains"),
                "allowed_domains": params.get("allowed_domains"),
            },
        }
        send_message(sock, msg)
        response = recv_message(sock)
        return response if response else {"status": "error", "message": "No response from broker"}
    except Exception as e:
        logger.exception("Failed to configure broker")
        return {"status": "error", "message": str(e)}
    finally:
        sock.close()


async def _stop_agent():
    try:
        stop_container(AGENT_CONTAINER)
        return {"status": "ok", "message": "Agent stopped"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _stop_broker():
    try:
        stop_container(BROKER_CONTAINER)
        return {"status": "ok", "message": "Broker stopped"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    app.run()
