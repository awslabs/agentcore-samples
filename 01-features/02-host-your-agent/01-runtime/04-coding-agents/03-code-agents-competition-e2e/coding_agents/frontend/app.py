#!/usr/bin/env python3
"""Coding Agents — Split-Pane Comparison Frontend.

Two side-by-side terminals connected to AgentCore runtimes.
A shared prompt bar at the top sends the same command to both agents.

Modes:
  - Command mode (default): type a prompt, hit Enter, it runs on both agents
    via the same WebSocket PTY that connect.py uses, streams output back.
  - TUI mode: full interactive terminal (xterm.js) connected via WebSocket PTY.

Usage:
    pip install -r requirements.txt
    python app.py
    Open http://127.0.0.1:5050
"""

from gevent import monkey

monkey.patch_all()

import json
import logging
import os
import sys
import time
import uuid

import websocket as ws_client
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

from bedrock_agentcore.runtime import AgentCoreRuntimeClient

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("frontend")

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(SCRIPT_DIR)

REGION = os.environ.get("AWS_REGION", "us-west-2")

agentcore_client = AgentCoreRuntimeClient(region=REGION)

AGENTS = {
    "claude-code": {
        "name": "Claude Code",
        "config_dir": os.path.join(AGENTS_DIR, "claude-code"),
        "run_cmd": "/app/run.sh {model_flag}'{prompt}'; exit",
        "default_model": "us.anthropic.claude-opus-4-6-v1",
    },
    "kiro": {
        "name": "Kiro",
        "config_dir": os.path.join(AGENTS_DIR, "kiro"),
        "run_cmd": "/app/run.sh {model_flag}chat '{prompt}'; exit",
        "default_model": "auto",
    },
    "cursor": {
        "name": "Cursor",
        "config_dir": os.path.join(AGENTS_DIR, "cursor"),
        "run_cmd": "/app/run.sh {model_flag}'{prompt}'; exit",
        "default_model": "auto",
    },
    "codex": {
        "name": "Codex",
        "config_dir": os.path.join(AGENTS_DIR, "codex"),
        "run_cmd": "/app/run.sh {model_flag}'{prompt}'; exit",
        "default_model": "openai.gpt-5.5",
    },
    "hermes": {
        "name": "Hermes",
        "config_dir": os.path.join(AGENTS_DIR, "hermes"),
        "run_cmd": "/app/run.sh {model_flag}'{prompt}'; exit",
        "default_model": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    },
    "open-code": {
        "name": "OpenCode",
        "config_dir": os.path.join(AGENTS_DIR, "open-code"),
        "run_cmd": "/app/run.sh {model_flag}'{prompt}'; exit",
        "default_model": "amazon-bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0",
    },
}


def load_runtime_arn(agent_key):
    config_path = os.path.join(AGENTS[agent_key]["config_dir"], "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f).get("runtime_arn", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def get_all_arns():
    return {key: load_runtime_arn(key) for key in AGENTS}


def create_ws_connection(runtime_arn, session_id):
    """Create a SigV4-signed WebSocket connection using bedrock-agentcore SDK."""
    shell_id = str(uuid.uuid4())

    ws_url, headers = agentcore_client.connect_shell(
        runtime_arn=runtime_arn,
        session_id=session_id,
        shell_id=shell_id,
    )

    header_list = [f"{k}: {v}" for k, v in headers.items()]
    upstream = ws_client.create_connection(ws_url, header=header_list, timeout=60)
    log.info("WS connected: arn=%s session=%s", runtime_arn.split("/")[-1], session_id[:20])
    return upstream


@app.route("/")
def index():
    arns = get_all_arns()
    agents_info = {
        k: {"name": v["name"], "arn": arns.get(k, ""), "default_model": v["default_model"]} for k, v in AGENTS.items()
    }
    log.info("Serving index page, agents: %s", {k: bool(v) for k, v in arns.items()})
    return render_template("index.html", agents=agents_info)


@app.route("/api/arns")
def get_arns():
    return jsonify(get_all_arns())


@app.route("/api/invoke", methods=["POST"])
def invoke():
    """Run a prompt on an agent via WebSocket PTY.

    Opens a WebSocket, sends '/app/run.sh <prompt>; exit', streams output as SSE.
    """
    data = request.get_json()
    runtime_arn = data.get("runtime_arn", "").strip()
    session_id = data.get("session_id", "").strip()
    prompt = data.get("command", "").strip()
    agent_type = data.get("agent_type", "").strip()
    model = data.get("model", "").strip()

    if not runtime_arn:
        return jsonify({"error": "No runtime_arn"}), 400
    if not prompt:
        return jsonify({"error": "No prompt"}), 400
    if not session_id:
        session_id = f"session-{int(time.time())}-{uuid.uuid4().hex}"

    if agent_type not in AGENTS:
        return jsonify({"error": f"Unknown agent: {agent_type}"}), 400

    safe_prompt = prompt.replace("'", "'\\''")
    model_flag = f"--model {model} " if model else ""
    full_cmd = AGENTS[agent_type]["run_cmd"].replace("{model_flag}", model_flag).replace("{prompt}", safe_prompt)

    log.info("invoke: agent=%s prompt=%s sid=%s", agent_type, prompt[:80], session_id[:20])

    def generate():
        upstream = None
        try:
            upstream = create_ws_connection(runtime_arn, session_id)
            upstream.send_binary(b"\x00" + (full_cmd + "\n").encode())
            upstream.settimeout(300)

            while True:
                try:
                    opcode, frame_data = upstream.recv_data()
                except ws_client.WebSocketTimeoutException:
                    continue
                except ws_client.WebSocketConnectionClosedException:
                    break

                if opcode == 0x8:
                    break
                if opcode == 0x2 and len(frame_data) > 0:
                    channel = frame_data[0]
                    payload = frame_data[1:].decode("utf-8", errors="replace")
                    if channel == 1:
                        yield f"data: {json.dumps({'type': 'stdout', 'text': payload})}\n\n"
                    elif channel == 2:
                        yield f"data: {json.dumps({'type': 'stderr', 'text': payload})}\n\n"
                    elif channel == 3:
                        try:
                            status = json.loads(frame_data[1:])
                            if status.get("status") == "Success":
                                continue
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                        break
                    elif channel == 0xFF:
                        break

        except Exception as e:
            log.error("invoke error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
        finally:
            if upstream:
                try:
                    upstream.close()
                except:
                    pass

        yield f"data: {json.dumps({'type': 'exit', 'code': 0})}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── WebSocket proxy for TUI mode ──────────────────────────────

import gevent
from geventwebsocket.handler import WebSocketHandler


class WebSocketMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        ws = environ.get("wsgi.websocket")
        if ws and path.startswith("/ws/proxy/"):
            session_id = path.split("/ws/proxy/")[-1].split("?")[0]
            from urllib.parse import parse_qs

            qs = parse_qs(environ.get("QUERY_STRING", ""))
            runtime_arn = qs.get("arn", [""])[0]
            agent_type = qs.get("agent_type", [""])[0]
            model = qs.get("model", [""])[0]
            log.info(
                "[TUI] New proxy: session=%s arn=%s agent=%s model=%s",
                session_id[:20],
                runtime_arn.split("/")[-1] if runtime_arn else "none",
                agent_type,
                model or "default",
            )
            self._proxy(ws, runtime_arn, session_id, agent_type=agent_type, model=model)
            return []
        return self.app(environ, start_response)

    def _proxy(self, ws, runtime_arn, session_id, agent_type="", model=""):
        if not runtime_arn:
            log.warning("[TUI] No ARN provided")
            ws.send(json.dumps({"error": "No ARN"}).encode(), binary=True)
            return

        try:
            upstream = create_ws_connection(runtime_arn, session_id)
        except Exception as e:
            log.error("[TUI] Upstream connection failed: %s", e)
            ws.send(json.dumps({"error": f"Upstream failed: {e}"}).encode(), binary=True)
            return

        ws.send(b"\x03" + json.dumps({"status": "connected", "session_id": session_id}).encode())

        model_flag = f" --model {model}" if model else ""
        run_cmd = f"/app/run.sh{model_flag}\n"
        upstream.send_binary(b"\x00" + run_cmd.encode())

        closed = [False]
        frame_count = [0]

        def keepalive():
            try:
                while not closed[0]:
                    gevent.sleep(30)
                    if closed[0]:
                        break
                    try:
                        upstream.ping()
                    except:
                        break
            except:
                pass

        def upstream_to_browser():
            try:
                while not closed[0]:
                    upstream.settimeout(5.0)
                    try:
                        opcode, data = upstream.recv_data()
                    except ws_client.WebSocketTimeoutException:
                        continue
                    except ws_client.WebSocketConnectionClosedException:
                        log.info("[TUI] Upstream closed: session=%s frames=%d", session_id[:20], frame_count[0])
                        break
                    except Exception as e:
                        log.warning("[TUI] Upstream recv error: %s", e)
                        break
                    if data is None or opcode == 0x8:
                        break
                    frame_count[0] += 1
                    try:
                        ws.send(data)
                    except Exception as e:
                        log.warning("[TUI] Browser send failed: %s", e)
                        break
            except Exception as e:
                log.error("[TUI] upstream_to_browser error: %s", e)
            finally:
                closed[0] = True

        reader = gevent.spawn(upstream_to_browser)
        pinger = gevent.spawn(keepalive)

        try:
            while not closed[0]:
                try:
                    msg = ws.receive()
                except Exception:
                    break
                if msg is None:
                    break
                if isinstance(msg, (bytes, bytearray)):
                    if msg and msg[0] == 0xFF:
                        break
                    upstream.send_binary(bytes(msg))
                else:
                    upstream.send_binary(b"\x00" + msg.encode())
        except Exception as e:
            log.warning("[TUI] browser_to_upstream error: %s", e)
        finally:
            closed[0] = True
            pinger.kill()
            reader.kill()
            try:
                upstream.close()
            except:
                pass
            log.info("[TUI] Proxy closed: session=%s total_frames=%d", session_id[:20], frame_count[0])


app.wsgi_app = WebSocketMiddleware(app.wsgi_app)

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("Coding Agents — Split-Pane Comparison UI")
    log.info("  http://127.0.0.1:5050")
    log.info("  Region: %s", REGION)
    log.info("  Agents: %s", ", ".join(AGENTS.keys()))
    arns = get_all_arns()
    for k, v in arns.items():
        status = v.split("/")[-1] if v else "NOT DEPLOYED"
        log.info("    %s: %s", k, status)
    log.info("=" * 50)

    from gevent.pywsgi import WSGIServer

    server = WSGIServer(("127.0.0.1", 5050), app, handler_class=WebSocketHandler, log=log)
    server.serve_forever()
