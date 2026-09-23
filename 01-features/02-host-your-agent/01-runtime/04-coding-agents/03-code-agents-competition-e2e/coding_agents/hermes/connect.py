"""
Connect to Hermes on AgentCore Runtime via WebSocket Shell.

This gives you an interactive terminal session on the microVM.
Use it to run `hermes-agent` interactively, just like a local terminal.

Usage:
    # New session (launches hermes-agent interactive)
    python connect.py

    # Reuse an existing runtime session (same microVM)
    python connect.py --session <session-id>

    # Run a prompt in headless mode (one-shot, exits when done)
    python connect.py --prompt "Build a REST API"

    # Run a raw shell command on the microVM
    python connect.py --cmd "ls /mnt/s3files/"

Environment:
    AWS_REGION                                  (default: us-west-2)
"""

import argparse
import asyncio
import json
import os
import sys
import termios
import tty
import uuid

from bedrock_agentcore.runtime import AgentCoreRuntimeClient
from bedrock_agentcore.runtime.shell import ShellChannel, ShellSession


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REGION = os.environ.get("AWS_REGION", "us-west-2")


def load_config() -> dict:
    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


async def interactive_pty(shell: ShellSession, initial_cmd: str | None = None):
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())

        cols, rows = os.get_terminal_size()
        await shell.resize(cols, rows)

        if initial_cmd:
            await shell.send(initial_cmd)

        loop = asyncio.get_event_loop()
        stdin_fd = sys.stdin.fileno()

        async def read_stdin():
            while True:
                data = await loop.run_in_executor(None, os.read, stdin_fd, 4096)
                if not data:
                    break
                await shell.send_bytes(data)

        stdin_task = asyncio.create_task(read_stdin())

        try:
            async for frame in shell:
                if frame.channel == ShellChannel.STDOUT:
                    os.write(sys.stdout.fileno(), frame.payload)
                elif frame.channel == ShellChannel.STDERR:
                    os.write(sys.stderr.fileno(), frame.payload)
                elif frame.channel == ShellChannel.STATUS:
                    break
                elif frame.channel == ShellChannel.CLOSE:
                    break
        finally:
            stdin_task.cancel()
            try:
                await stdin_task
            except asyncio.CancelledError:
                pass

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        print()


async def stream_output(shell: ShellSession, initial_cmd: str):
    await shell.send(initial_cmd)

    async for frame in shell:
        if frame.channel == ShellChannel.STDOUT:
            print(frame.text, end="", flush=True)
        elif frame.channel == ShellChannel.STDERR:
            print(frame.text, end="", file=sys.stderr, flush=True)
        elif frame.channel == ShellChannel.STATUS:
            break
        elif frame.channel == ShellChannel.CLOSE:
            break


async def run(args):
    config = load_config()
    runtime_arn = config["runtime_arn"]

    session_id = args.session or str(uuid.uuid4())
    shell_id = str(uuid.uuid4())

    client = AgentCoreRuntimeClient(region=REGION)

    print("Connecting to AgentCore Runtime...")
    print(f"  Runtime: {runtime_arn}")
    print(f"  Session: {session_id}")
    print()

    model_flag = f" --model {args.model}" if args.model else ""

    async with client.open_shell(
        runtime_arn=runtime_arn,
        session_id=session_id,
        shell_id=shell_id,
    ) as shell:
        if args.prompt:
            safe_prompt = args.prompt.replace("'", "'\\''")
            cmd = f"/app/run.sh{model_flag} '{safe_prompt}'; exit\n"
            print(f"Running prompt: {args.prompt}\n")
            await stream_output(shell, cmd)
        elif args.cmd:
            cmd = f"{args.cmd}; exit\n"
            print(f"Running command: {args.cmd}\n")
            await stream_output(shell, cmd)
        else:
            cmd = f"/app/run.sh{model_flag}\n"
            print("Connected! Launching Hermes...\n")
            await interactive_pty(shell, cmd)

    print(f"\nTo reconnect: python connect.py --session {session_id}")


def main():
    parser = argparse.ArgumentParser(description="Connect to Hermes on AgentCore via WebSocket PTY")
    parser.add_argument("--session", help="Runtime session ID (reuse same microVM)")
    parser.add_argument("--prompt", help="Run a prompt in headless mode (one-shot, exits when done)")
    parser.add_argument("--cmd", help="Run a raw shell command on the microVM")
    parser.add_argument("--model", help="Model ID to pass to run.sh (e.g. global.anthropic.claude-opus-4-8)")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nDisconnecting...")


if __name__ == "__main__":
    main()
