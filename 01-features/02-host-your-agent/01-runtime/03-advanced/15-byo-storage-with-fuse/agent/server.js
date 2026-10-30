// FUSE mount agent: plain Node http server (GET=health, POST=invoke),
// no AgentCore SDK. Mounts sessions/<sid>/ via mount-s3 (real S3 calls
// signed by the execution role, which carries aws:AgentCoreRuntimeSourceSid
// / ...Arn), then spawns the Strands harness CLI to write/read a note
// through that mount with its own built-in file tool -- same shape as
// awslabs/agentcore-samples' 01-claude-code-with-s3-files/server.js,
// `strands` standing in for `claude`.
//
// A session mounts AT MOST ONCE for its life (isMounted() short-circuits
// after the first success), so ../infra/test.sh always tries the OTHER
// user's prefix first (denied, no mount formed) before its own.

const http = require("http");
const { spawn } = require("child_process");
const fs = require("fs");

const PORT = process.env.PORT || 8080;
const BUCKET = process.env.FUSE_BUCKET;
const MODEL_ID = process.env.FUSE_MODEL_ID || "us.anthropic.claude-haiku-5-5";
const MOUNT = "/mnt/s3";
const SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id";

// A note on session IDs: a session id lands literally inside both an
// S3 key and an IAM resource match, so a "/" lets one session's grant
// cover another's
// (sessions/team/* also matches sessions/team/alice/*), and a "." or
// ".." resolves as a relative path segment and escapes the session
// folder entirely. Reject anything that doesn't look like an opaque,
// safe segment before it ever reaches a prefix.
const SESSION_ID_RE = /^[A-Za-z0-9_-]{33,256}$/;

function isMounted(path) {
  try {
    const mounts = fs.readFileSync("/proc/mounts", "utf8");
    return mounts.split("\n").some((line) => line.split(" ")[1] === path);
  } catch {
    return false;
  }
}

function mountPrefix(prefix) {
  return new Promise((resolve) => {
    try {
      fs.mkdirSync(MOUNT, { recursive: true });
    } catch (err) {
      resolve({ mount: "ERROR", error: String(err) });
      return;
    }
    if (isMounted(MOUNT)) {
      resolve({ mount: "already-mounted" });
      return;
    }
    const proc = spawn("mount-s3", ["--prefix", prefix, "--allow-overwrite", BUCKET, MOUNT]);
    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => {
      if (code === 0) {
        resolve({ mount: "mounted" });
      } else {
        resolve({ mount: "DENIED", stderr: stderr.slice(-2000) });
      }
    });
    proc.on("error", (err) => resolve({ mount: "ERROR", error: String(err) }));
  });
}

// -p: answer and exit, no chat loop. --effort off: some models (e.g.
// Haiku) reject the harness's default extended-thinking config without
// it. --memory off: the harness's long-term memory feature calls a
// different, hardcoded Bedrock model in the background (not MODEL_ID),
// which the execution role's IAM policy -- scoped to MODEL_ID only --
// denies; this example has no use for cross-session memory anyway.
function runStrands(prompt, sessionId) {
  return new Promise((resolve, reject) => {
    const args = [
      "-p", prompt, "--session-id", sessionId, "--model", MODEL_ID,
      "--effort", "off", "--memory", "off",
    ];
    console.log(`[runStrands] sessionId=${sessionId} model=${MODEL_ID}`);
    const proc = spawn("strands", args, {
      env: { ...process.env, HOME: "/root" },
      cwd: "/root",
      timeout: 300_000,
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => {
      if (code !== 0) {
        resolve({ response: null, error: `strands exited ${code}: ${stderr.slice(-2000)}` });
        return;
      }
      resolve({ response: stdout.trim() });
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

  if (req.method !== "POST") {
    res.writeHead(405);
    res.end();
    return;
  }

  try {
    const raw = await readBody(req);
    const body = raw ? JSON.parse(raw) : {};
    const sid = req.headers[SESSION_HEADER] || body.sessionId || "unknown-session";
    if (!SESSION_ID_RE.test(sid)) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ session: sid, error: `session id does not match ${SESSION_ID_RE}` }));
      return;
    }
    const targetPrefix = body.prefix || `sessions/${sid}/`;

    const mountResult = await mountPrefix(targetPrefix);
    if (mountResult.mount !== "mounted" && mountResult.mount !== "already-mounted") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ session: sid, prefix: targetPrefix, ...mountResult }));
      return;
    }

    const notePath = `${MOUNT}/note-from-${sid}.txt`;
    const prompt =
      body.prompt ||
      `Use your file tool to write exactly one short sentence to the file at ` +
        `path '${notePath}', saying hello from session ${sid}. Then read it ` +
        `back to confirm. Do not ask for confirmation, just do it.`;

    const strandsResult = await runStrands(prompt, sid);

    let noteContent;
    try {
      noteContent = fs.readFileSync(notePath, "utf8").trim();
    } catch (err) {
      noteContent = `ERROR:${err.code}`;
    }

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        session: sid,
        prefix: targetPrefix,
        mount: mountResult.mount,
        strands: strandsResult.response,
        strands_error: strandsResult.error || null,
        note_content: noteContent,
      })
    );
  } catch (err) {
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: err.message }));
  }
});

server.listen(PORT, () => {
  console.log(`fuse + strands harness agent listening on ${PORT}`);
});
