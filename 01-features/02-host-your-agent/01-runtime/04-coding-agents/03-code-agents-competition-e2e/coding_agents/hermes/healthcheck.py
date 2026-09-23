"""Minimal HTTP server for AgentCore Runtime /ping health check."""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "status": "Healthy",
                        "agent": "hermes",
                        "storage": "s3files",
                        "time_of_last_update": int(time.time()),
                    }
                ).encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/invocations":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"message": "Use WebSocket PTY or InvokeAgentRuntimeCommand to interact with Hermes"}
                ).encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("Health check server on :8080 — interact via WebSocket PTY or InvokeAgentRuntimeCommand")
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
