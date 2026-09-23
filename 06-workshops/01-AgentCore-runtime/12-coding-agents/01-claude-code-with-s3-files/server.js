const http = require("http");
const { spawn } = require("child_process");

const PORT = process.env.PORT || 8080;

function runClaude(prompt, sessionId) {
  return new Promise((resolve, reject) => {
    const args = ["-p", "--dangerously-skip-permissions", "--output-format", "json"];
    if (sessionId) {
      args.push("--resume", sessionId);
    } else {
      args.push("--continue");
    }
    args.push(prompt);

    console.log(`[runClaude] sessionId=${sessionId || "(none, --continue)"} prompt="${prompt}"`);
    console.log(`[runClaude] args: ${JSON.stringify(args)}`);

    const proc = spawn("claude", args, {
      env: { ...process.env, HOME: "/home/agent" },
      cwd: "/home/agent",
      timeout: 300_000,
    });

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));

    proc.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`claude exited ${code}: ${stderr}`));
        return;
      }
      try {
        const parsed = JSON.parse(stdout);
        resolve({
          response: parsed.result || stdout.trim(),
          sessionId: parsed.session_id || null,
        });
      } catch {
        resolve({ response: stdout.trim(), sessionId: null });
      }
    });
    proc.on("error", reject);
  });
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => resolve(data));
  });
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "healthy" }));
    return;
  }

  if (req.method === "POST") {
    try {
      const body = await readBody(req);
      const { prompt, sessionId } = JSON.parse(body);
      const result = await runClaude(prompt, sessionId);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(result));
    } catch (err) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: err.message }));
    }
    return;
  }

  res.writeHead(405);
  res.end();
});

server.listen(PORT, () => {
  console.log(`Claude Code agent listening on port ${PORT}`);
});
