import logging
import os
import subprocess

logger = logging.getLogger(__name__)

CONTAINERD_SOCKET = os.environ.get("CONTAINERD_SOCKET", "/run/containerd/containerd.sock")


def pull_image(image_uri: str, ecr_token: str) -> None:
    cmd = [
        "ctr",
        "-a",
        CONTAINERD_SOCKET,
        "images",
        "pull",
        "--user",
        f"AWS:{ecr_token}",
        image_uri,
    ]
    logger.info("Pulling image: %s", image_uri)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Image pull failed: {result.stderr}")
    logger.info("Image pulled successfully: %s", image_uri)


def start_container(ctr_args: list) -> None:
    logger.info("Starting container: %s", " ".join(ctr_args))
    result = subprocess.run(ctr_args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Container start failed: {result.stderr}")
    logger.info("Container started successfully")


def stop_container(container_name: str) -> None:
    kill_cmd = ["ctr", "-a", CONTAINERD_SOCKET, "tasks", "kill", container_name]
    logger.info("Killing container task: %s", container_name)
    subprocess.run(kill_cmd, capture_output=True, text=True, timeout=30)

    task_del_cmd = ["ctr", "-a", CONTAINERD_SOCKET, "tasks", "delete", container_name]
    logger.info("Deleting task: %s", container_name)
    subprocess.run(task_del_cmd, capture_output=True, text=True, timeout=30)

    delete_cmd = [
        "ctr",
        "-a",
        CONTAINERD_SOCKET,
        "containers",
        "delete",
        container_name,
    ]
    logger.info("Deleting container: %s", container_name)
    result = subprocess.run(delete_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.warning("Container delete may have failed: %s", result.stderr)


def get_container_status(container_name: str) -> str:
    cmd = ["ctr", "-a", CONTAINERD_SOCKET, "tasks", "ls"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return "unknown"

    for line in result.stdout.strip().split("\n"):
        parts = line.split()
        if parts and parts[0] == container_name:
            return parts[2].lower() if len(parts) > 2 else "exists"

    return "not_started"


def list_containers() -> dict:
    """Return the raw containerd view from inside the runtime microVM.

    Captures `ctr tasks ls` (running tasks + PIDs + state) and
    `ctr containers ls` (all created containers + their images).
    """
    tasks = subprocess.run(
        ["ctr", "-a", CONTAINERD_SOCKET, "tasks", "ls"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    containers = subprocess.run(
        ["ctr", "-a", CONTAINERD_SOCKET, "containers", "ls"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return {
        "tasks_ls": tasks.stdout if tasks.returncode == 0 else tasks.stderr,
        "containers_ls": (containers.stdout if containers.returncode == 0 else containers.stderr),
    }
