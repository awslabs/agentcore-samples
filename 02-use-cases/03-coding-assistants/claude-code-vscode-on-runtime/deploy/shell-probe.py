# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==17.0.1"]   # exact, tested version
# ///
"""shell-probe.py <user>: open the box's AgentCore terminal (InvokeAgentRuntimeCommandShell) the way a
browser would (Okta token in Sec-WebSocket-Protocol), run a few commands, and print what comes back.

By default the runtime ARN is looked up from deploy/.state.json by <user>. Pass --arn to use a runtime
ARN directly instead, skipping that lookup (useful when .state.json is stale or missing).

By default the token is read from the clipboard — first copy it in the dev box tab's JavaScript console:
    copy(__devbox.getToken())
The clipboard is cleared after reading, and the token is never printed. Pass --token-file to read the
token from a file instead (stripped of whitespace); the clipboard is left untouched in that case.

The session id and shell id are normally derived from the uid claim inside the token being used, so a
different token always lands on a different (new) session on the runtime, never an existing one. To
attach to an already-live session (e.g. testing whether one user's token can ride another user's open
terminal), pass its real --session and --shell-id explicitly, overriding the derived values.

Examples:
  uv run deploy/shell-probe.py grace
  uv run deploy/shell-probe.py grace --token-file /path/to/token.txt
  uv run deploy/shell-probe.py grace --arn arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/foo-abc123
  uv run deploy/shell-probe.py grace --arn <arn> --token-file ada-token.txt --session dbx-abc123 --shell-id probe-7
"""

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import websockets

STDIN, STDOUT, STDERR, STATUS, RESIZE, HEARTBEAT, CLOSE = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0xFF
COMMANDS = (
    "id; echo HOME=$HOME; pwd; ls -ld /mnt/workspace /mnt/workspace/home /mnt/workspace/projects; "
    "touch /mnt/workspace/home/.shell-probe && echo home-writable || echo home-NOT-writable; "
    "command -v claude devbox-claude; claude --version; echo PROBE-DONE\n"
)


def read_token(token_file):
    """The token from token_file, or from the clipboard (which is then cleared)."""
    if token_file:
        return Path(token_file).read_text().strip()
    token = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout.strip()
    subprocess.run(["pbcopy"], input="", text=True, check=False)
    return token


async def main(name, token_file=None, arn=None, session_override=None, shell_id_override=None, send_commands=True):
    token = await asyncio.to_thread(read_token, token_file)
    if not (token.startswith("ey") and token.count(".") == 2):
        source = token_file or "the clipboard"
        sys.exit(
            f"{source} doesn't hold a token. Run copy(__devbox.getToken()) in the dev box tab first, "
            "or pass --token-file with a file containing the token."
        )
    if arn:
        runtime_arn, generation = arn, "manual"
    else:
        state = json.loads((Path(__file__).parent / ".state.json").read_text())
        box = state.get("boxes", {}).get(name) or {}
        if not box.get("runtimeArn") or box.get("compute") != "microvm":
            sys.exit(
                f"No microVM box for {name} in deploy/.state.json (the terminal isn't supported on the old Instances boxes): "
                "run `uv run deploy/devbox.py deploy` first, or pass --arn directly."
            )
        runtime_arn, generation = box["runtimeArn"], box["generation"]
    uid = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))["uid"]
    session = session_override or ("dbx-" + hashlib.sha256(f"{uid}:{generation}".encode()).hexdigest())
    shell_id = shell_id_override or f"probe-{generation}"
    if session_override or shell_id_override:
        print(f"attaching to explicit session={session!r} shellId={shell_id!r} (not derived from this token's uid)")
    query = urllib.parse.urlencode(
        {"qualifier": "DEFAULT", "shellId": shell_id, "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session}
    )
    url = (
        f"wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(runtime_arn, safe='')}/ws/shells?{query}"
    )
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    protocols = [f"base64UrlBearerAuthorization.{encoded}", "base64UrlBearerAuthorization"]
    print(f"runtime {runtime_arn}\nsession {session}\nopening the terminal…")
    try:
        ws = await websockets.connect(url, subprotocols=protocols, max_size=None, open_timeout=60)
    except websockets.InvalidStatus as err:
        body = err.response.body.decode(errors="replace")[:300] if err.response.body else ""
        sys.exit(
            f"handshake refused: HTTP {err.response.status_code} "
            f"{err.response.headers.get('x-amzn-ErrorType', '')} {body}"
        )
    async with ws:
        # AgentCore should pick the bare name; if it ever picks the token-bearing one, don't print the token.
        chosen = ws.subprotocol and (ws.subprotocol.split(".", 1)[0] + (".<token>" if "." in ws.subprotocol else ""))
        print(f"connected (subprotocol {chosen!r})\n----- terminal output -----")
        await ws.send(bytes([RESIZE]) + json.dumps({"width": 120, "height": 40}).encode())
        sent = False
        deadline = asyncio.get_running_loop().time() + 60
        output = ""
        while asyncio.get_running_loop().time() < deadline:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                await ws.send(bytes([HEARTBEAT]))
                continue
            if isinstance(frame, str):
                frame = frame.encode()
            channel, payload = frame[0], frame[1:]
            if channel == STATUS:
                status = json.loads(payload)
                print(f"\n[status] {json.dumps(status)[:300]}")
                if not status.get("metadata", {}).get("shellId"):
                    break
                if not sent and send_commands:
                    await ws.send(bytes([STDIN]) + COMMANDS.encode())
                    sent = True
            elif channel in (STDOUT, STDERR):
                text = payload.decode(errors="replace")
                output += text
                sys.stdout.write(text if channel == STDOUT else f"[stderr] {text}")
                sys.stdout.flush()
                if "PROBE-DONE" in output.split("echo PROBE-DONE")[-1]:
                    break
            elif channel == HEARTBEAT:
                continue
            elif channel == CLOSE:
                print("\n[close from AgentCore]")
                break
        if send_commands:
            await ws.send(bytes([STDIN]) + b"exit\n")
        else:
            print("\n(not sending 'exit' — this looks like someone else's live session, leaving it running)")
    print("\n----- done -----")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Open a box's AgentCore terminal and run a probe.")
    parser.add_argument(
        "name", help="dev box user name (used for session naming, and for the .state.json lookup unless --arn is given)"
    )
    parser.add_argument("--arn", help="runtime ARN to use directly, skipping the deploy/.state.json lookup")
    parser.add_argument("--token-file", help="file containing the bearer token (default: read from the clipboard)")
    parser.add_argument(
        "--session",
        help="exact X-Amzn-Bedrock-AgentCore-Runtime-Session-Id to attach to, overriding the one derived from the token's uid",
    )
    parser.add_argument("--shell-id", help="exact shellId to attach to, overriding the derived one")
    parser.add_argument(
        "--send-commands",
        action="store_true",
        help="send the probe commands even when attaching via --session/--shell-id (default: observe only, since this would type into someone else's live terminal)",
    )
    args = parser.parse_args()
    send_commands = args.send_commands or not (args.session or args.shell_id)
    asyncio.run(
        main(
            args.name,
            token_file=args.token_file,
            arn=args.arn,
            session_override=args.session,
            shell_id_override=args.shell_id,
            send_commands=send_commands,
        )
    )
