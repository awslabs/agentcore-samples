const http = require("http");
const { spawn } = require("child_process");

const PORT = process.env.PORT || 8080;

function runCodex(prompt, sessionId) {
  return new Promise((resolve, reject) => {
    let args;
    if (sessionId) {
      args = ["exec", "resume", "--json", "-c", 'sandbox_mode="danger-full-access"', sessionId, prompt];
    } else {
      args = ["exec", prompt, "--json", "--sandbox", "danger-full-access", "--skip-git-repo-check"];
    }

    console.log(`[runCodex] sessionId=${sessionId || "(none)"} prompt="${prompt}"`);
    console.log(`[runCodex] args: ${JSON.stringify(args)}`);

    const proc = spawn("codex", args, {
      env: { ...process.env, HOME: "/home/agent" },
      cwd: "/home/agent",
      timeout: 300_000,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));

    proc.on("close", (code) => {
      console.log(`[runCodex] exited code=${code}`);
      console.log(`[runCodex] stderr: ${stderr}`);
      console.log(`[runCodex] stdout: ${stdout}`);
      if (code !== 0) {
        reject(new Error(`codex exited ${code}: ${stderr}`));
        return;
      }
      // --json emits one JSON object per line
      // thread_id is in the "thread.started" line; response text in "item.completed"
      const lines = stdout.trim().split("\n").filter(Boolean);
      let threadId = null;
      let responseText = null;
      for (const line of lines) {
        try {
          const obj = JSON.parse(line);
          if (obj.type === "thread.started" && obj.thread_id) {
            threadId = obj.thread_id;
          }
          if (obj.type === "item.completed" && obj.item && obj.item.text) {
            responseText = obj.item.text;
          }
        } catch {
          // skip non-JSON lines
        }
      }
      console.log(`[runCodex] thread_id=${threadId || "(none)"}`);
      resolve({
        response: responseText || stdout.trim(),
        sessionId: threadId,
      });
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
      const result = await runCodex(prompt, sessionId);
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
  console.log(`Codex agent listening on port ${PORT}`);
});
