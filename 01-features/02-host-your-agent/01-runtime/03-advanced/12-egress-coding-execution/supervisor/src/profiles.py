import os
from typing import Optional

CONTAINERD_SOCKET = os.environ.get("CONTAINERD_SOCKET", "/run/containerd/containerd.sock")
IPC_DIR = "/run/containerd/sandbox-ipc"
WORKSPACE_DIR = "/run/containerd/sandbox-workspace"


def get_broker_ctr_args(container_name: str, image_uri: str, env: Optional[dict] = None) -> list:
    args = [
        "ctr",
        "-a",
        CONTAINERD_SOCKET,
        "run",
        "-d",
        "--net-host",
        "--mount",
        f"type=bind,src={IPC_DIR},dst=/tmp/ipc,options=rbind:rw",
        "--mount",
        f"type=bind,src={WORKSPACE_DIR},dst=/tmp/workspace,options=rbind:rw",
        "--env",
        "IPC_SOCKET_PATH=/tmp/ipc/broker.sock",
        "--env",
        "WORKSPACE_PATH=/tmp/workspace",
    ]

    if env:
        for key, value in env.items():
            args.extend(["--env", f"{key}={value}"])

    args.extend([image_uri, container_name])
    return args


def get_agent_ctr_args(container_name: str, image_uri: str) -> list:
    args = [
        "ctr",
        "-a",
        CONTAINERD_SOCKET,
        "run",
        "-d",
        "--mount",
        f"type=bind,src={IPC_DIR},dst=/tmp/ipc,options=rbind:rw",
        "--mount",
        f"type=bind,src={WORKSPACE_DIR},dst=/tmp/workspace,options=rbind:rw",
        "--env",
        "BROKER_SOCKET_PATH=/tmp/ipc/broker.sock",
        "--env",
        "SUPERVISOR_SOCKET_PATH=/tmp/ipc/supervisor.sock",
        "--env",
        "WORKSPACE_PATH=/tmp/workspace",
        image_uri,
        container_name,
    ]
    return args
