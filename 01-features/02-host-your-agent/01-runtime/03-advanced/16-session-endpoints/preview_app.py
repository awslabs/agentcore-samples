"""
A local page for working with the agent: chat on the left, the live site on the right.

    from preview_app import PreviewApp
    app = PreviewApp().start()
    print("Open", app.url)

PreviewApp starts a session, opens a local endpoint for the site's port with the SDK's Session endpoint client, and
serves one page on localhost. Prompts typed into the page go to InvokeAgentRuntime from
this process, so your credentials stay here and never reach the browser. The site pane
loads through the local endpoint, which adds your credentials to each request and relays Vite's hot reload socket.
"""

import html
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bedrock_agentcore.runtime import AgentCoreRuntimeClient, iter_events, session_endpoint_url

HERE = os.path.dirname(os.path.abspath(__file__))


COFFEE_PROMPTS = [
    "Switch this website to a dark coffee-house color theme",
    'Change the headline to "Fresh roasts daily"',
    "Add a Pastries card to the menu, priced $3.25",
]


class PreviewApp:
    def __init__(self, config_path="runtime_config.json", ui_port=8087, proxy_port=8088, prompts=None):
        with open(os.path.join(HERE, config_path)) as handle:
            self.config = json.load(handle)
        self.client = AgentCoreRuntimeClient(region=self.config["region"])
        self.session_id = f"preview-{uuid.uuid4().hex}"
        self.ui_port, self.proxy_port = ui_port, proxy_port
        self.prompts = prompts or COFFEE_PROMPTS
        self.endpoint = self.local = self._server = None

    @property
    def url(self):
        return f"http://localhost:{self.ui_port}"

    def invoke(self, payload):
        return self.client.invoke_agent_runtime(
            agentRuntimeArn=self.config["runtime_arn"],
            runtimeSessionId=self.session_id,
            payload=json.dumps(payload).encode(),
            contentType="application/json",
        )

    def start(self):
        """Start the session and the site, then serve the page. Returns once it is ready."""
        answer = self.invoke({"prompt": ""})
        started = json.loads(answer["response"].read())
        if not started.get("ready"):
            raise RuntimeError(f"The site did not start: {started}")
        self.endpoint = self.client.session_endpoint_client(session_endpoint_url(answer))
        try:
            self.local = self.endpoint.open_local_endpoint(
                port=self.config["preview_port"], local_port=self.proxy_port
            ).start()
        except RuntimeError:
            # The port is taken, often by an earlier run in the same kernel. Use a free one.
            self.local = self.endpoint.open_local_endpoint(port=self.config["preview_port"], local_port=0).start()
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.ui_port), self._handler())
        except OSError:
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.ui_port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def ask(self, prompt):
        """Send a prompt from Python, as the page does, and yield each step."""
        yield from iter_events(self.invoke({"prompt": prompt, "stream": True}))

    def stop(self):
        """Stop the page, the proxy, and the session."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self.local:
            self.local.stop()
        self.client.stop_runtime_session(agentRuntimeArn=self.config["runtime_arn"], runtimeSessionId=self.session_id)

    def _handler(self):
        app = self
        with open(os.path.join(HERE, "ui.html"), encoding="utf-8") as handle:
            page = handle.read()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != "/":
                    return self.send_error(404)

                def chip(entry):
                    prompt, hint = entry if isinstance(entry, (tuple, list)) else (entry, "")
                    look = f'<small class="look">Look for: {html.escape(hint)}</small>' if hint else ""
                    return f'<div class="suggest"><button type="button">{html.escape(prompt)}</button>{look}</div>'

                buttons = "\n".join(chip(entry) for entry in app.prompts)
                body = (
                    page.replace("{{SITE_URL}}", app.local.url)
                    .replace("{{PROXY_PORT}}", str(app.local.listen_port))
                    .replace("{{PROMPT_BUTTONS}}", buttons)
                    .encode()
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                # The same checks as the proxy: only this page, on this machine. The port is
                # read here because start() may have moved to a free one after this was built.
                local = {f"localhost:{app.ui_port}", f"127.0.0.1:{app.ui_port}"}
                origin = self.headers.get("Origin", "")
                if (
                    self.path != "/prompt"
                    or self.headers.get("Host") not in local
                    or (origin and origin.split("//", 1)[-1] not in local)
                ):
                    return self.send_error(403)
                length = int(self.headers.get("Content-Length") or 0)
                prompt = json.loads(self.rfile.read(length) or b"{}").get("prompt", "").strip()
                if not prompt:
                    return self.send_error(400)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    for event in app.ask(prompt):
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                        self.wfile.flush()
                except Exception as error:  # noqa: BLE001 - shown in the page
                    self.wfile.write(f"data: {json.dumps({'error': str(error)})}\n\n".encode())

        return Handler
