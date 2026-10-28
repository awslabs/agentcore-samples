#!/usr/bin/env python3
"""shell_session.py -- opens a real interactive PTY inside a running
AgentCore Runtime session, via the SDK's InvokeAgentRuntimeCommandShell
WebSocket API (bedrock_agentcore.runtime.shell). Unlike agentcore_boto3.py's
`exec` action (ExecuteCommand -- one-shot, non-interactive by design, see
../README.md), this is a real terminal: raw stdin/stdout relay, SIGWINCH
resize, and the server-side PTY survives a network drop if you reconnect
with the same --session-id/--shell-id pair.

Not modeled in boto3 -- the SDK signs the wss:// URL by hand instead of
going through a generated client -- so this talks to
bedrock_agentcore.runtime.shell directly rather than through
agentcore_boto3.py.

Usage:
    python3 shell_session.py --region us-east-1 \
        --agent-runtime-arn arn:aws:bedrock-agentcore:us-east-1:ACCOUNT:runtime/ID \
        --session-id <session-id> --shell-id <shell-id>
"""

import argparse
import asyncio
import os
import signal
import sys
import termios
import tty

from bedrock_agentcore.runtime.agent_core_runtime_client import AgentCoreRuntimeClient
from bedrock_agentcore.runtime.shell import ShellChannel


async def run(args: argparse.Namespace) -> int:
    client = AgentCoreRuntimeClient(region=args.region)
    loop = asyncio.get_running_loop()
    stdin_fd = sys.stdin.fileno()

    async with client.open_shell(
        args.agent_runtime_arn,
        session_id=args.session_id,
        shell_id=args.shell_id,
    ) as shell:
        # The installed SDK's ShellSession no longer exposes a `reconnected`
        # attribute (present in 1.14.0, gone by 1.25.0 -- confirmed by reading
        # the actually-installed source, not just its docstring, which still
        # mentions it). shell_id/session_id are the only state worth echoing.
        print(
            f"[shell] connected session={shell.session_id} shell_id={shell.shell_id} "
            "-- type 'exit' to leave\r",
            file=sys.stderr,
        )

        async def push_resize() -> None:
            cols, rows = os.get_terminal_size(sys.stdout.fileno())
            if cols > 0 and rows > 0:
                await shell.resize(cols, rows)

        await push_resize()
        loop.add_signal_handler(signal.SIGWINCH, lambda: asyncio.ensure_future(push_resize()))

        def on_stdin() -> None:
            try:
                data = os.read(stdin_fd, 4096)
            except OSError:
                data = b""
            if not data:
                loop.remove_reader(stdin_fd)
                return
            asyncio.ensure_future(shell.send_bytes(data))

        loop.add_reader(stdin_fd, on_stdin)
        try:
            async for frame in shell:
                if frame.channel == ShellChannel.STDOUT:
                    sys.stdout.buffer.write(frame.payload)
                    sys.stdout.buffer.flush()
                elif frame.channel == ShellChannel.STDERR:
                    sys.stderr.write(frame.text)
                    sys.stderr.flush()
        finally:
            loop.remove_reader(stdin_fd)
            loop.remove_signal_handler(signal.SIGWINCH)

    return shell.exit_code if shell.exit_code is not None else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--region", required=True)
    p.add_argument("--agent-runtime-arn", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--shell-id", required=True)
    args = p.parse_args()

    if not sys.stdin.isatty():
        print("[shell] stdin is not a TTY -- run this from an interactive terminal", file=sys.stderr)
        return 1

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    # Raw mode disables ISIG too, so Ctrl-C/Ctrl-D become ordinary bytes
    # forwarded to the remote PTY (ssh-like), not a local signal.
    tty.setraw(fd)
    try:
        exit_code = asyncio.run(run(args))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
    print(f"\r\n[shell] exited (exit_code={exit_code})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
