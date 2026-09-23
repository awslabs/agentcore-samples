const http = require("http");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

process.on("uncaughtException", (err) => {
  console.error("[FATAL] uncaughtException:", err.message, err.stack);
});
process.on("unhandledRejection", (err) => {
  console.error("[FATAL] unhandledRejection:", err);
});

const PORT = process.env.PORT || 8080;
const CODEX_HOME = process.env.CODEX_HOME || "/mnt/efs/.codex";
const WORKSPACE_DIR = process.env.WORKSPACE_DIR || "/mnt/efs/workspace";
// Codex's native skills directory. Anything written here by one session is
// discovered automatically by every later session that mounts the same EFS.
const SKILLS_DIR = process.env.SKILLS_DIR || path.join(CODEX_HOME, "skills");
const BEDROCK_REGION = process.env.BEDROCK_REGION || process.env.AWS_REGION || "us-west-2";
const CODEX_MODEL = process.env.CODEX_MODEL || "openai.gpt-5.6-terra";
const REASONING_EFFORT = process.env.CODEX_REASONING_EFFORT || "medium";

// ── Persistent state on EFS ──────────────────────────────────────────────────
//
// CODEX_HOME lives on EFS, so Codex threads (rollouts) written by one runtime
// session are resumable from any other session that mounts the same volume.
// The workspace is a git repo because Codex refuses to run in a non-repo unless
// skipGitRepoCheck is set.

function codexConfigToml() {
  return [
    'model_provider = "amazon-bedrock"',
    `model = "${CODEX_MODEL}"`,
    `model_reasoning_effort = "${REASONING_EFFORT}"`,
    "check_for_update_on_startup = false",
    "",
    "[model_providers.amazon-bedrock.aws]",
    `region = "${BEDROCK_REGION}"`,
    "",
    "[otel]",
    'exporter = "none"',
    'metrics_exporter = "none"',
    'trace_exporter = "none"',
    "log_user_prompt = false",
    "",
  ].join("\n");
}

function writeFileIfMissing(filePath, contents, mode) {
  try {
    fs.writeFileSync(filePath, contents, { flag: "wx", mode });
  } catch (err) {
    if (err.code !== "EEXIST") throw err;
  }
}

// config.toml is rewritten on every boot, not just when missing. CODEX_HOME is
// on EFS and outlives the container, so a write-once file would pin the model
// and region chosen by the very first deployment and silently ignore every
// later `CODEX_MODEL=... python deploy.py` or update.py.
//
// Codex appends its own state to this file (e.g. [projects."<ws>"] trust_level),
// so preserve anything after the block this function owns.
const MANAGED_MARKER = "# ── managed by server.js: rewritten on every boot ──";

function writeCodexConfig() {
  const filePath = path.join(CODEX_HOME, "config.toml");
  let appended = "";
  try {
    const existing = fs.readFileSync(filePath, "utf8");
    const idx = existing.indexOf(MANAGED_MARKER);
    if (idx === -1) {
      // Written by an older image, or by Codex itself. Keep it verbatim.
      appended = existing.trim();
    } else {
      const rest = existing.slice(idx + MANAGED_MARKER.length);
      const endOfManaged = rest.indexOf("\n# ── end managed ──");
      if (endOfManaged !== -1) {
        appended = rest.slice(endOfManaged + "\n# ── end managed ──".length).trim();
      }
    }
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }

  const managed = [MANAGED_MARKER, codexConfigToml().trimEnd(), "# ── end managed ──"].join("\n");
  const contents = appended ? `${managed}\n\n${appended}\n` : `${managed}\n`;
  fs.writeFileSync(filePath, contents, { mode: 0o600 });
}

function initPersistentState() {
  fs.mkdirSync(CODEX_HOME, { recursive: true, mode: 0o700 });
  fs.mkdirSync(WORKSPACE_DIR, { recursive: true, mode: 0o750 });
  fs.mkdirSync(SKILLS_DIR, { recursive: true, mode: 0o750 });

  // Fail fast and loudly if the EFS access point is not writable.
  const probe = path.join(CODEX_HOME, ".write-probe");
  fs.writeFileSync(probe, "ok", { mode: 0o600 });
  fs.rmSync(probe);

  writeCodexConfig();
  writeFileIfMissing(
    path.join(CODEX_HOME, "gitconfig"),
    ["[user]", "\tname = AgentCore Bot", "\temail = agentcore@example.com", ""].join("\n"),
    0o600,
  );

  if (!fs.existsSync(path.join(WORKSPACE_DIR, ".git", "HEAD"))) {
    const res = require("child_process").spawnSync("git", ["init", "-b", "main"], {
      cwd: WORKSPACE_DIR,
      env: { ...process.env, GIT_CONFIG_GLOBAL: path.join(CODEX_HOME, "gitconfig") },
    });
    if (res.status !== 0) {
      throw new Error(`git init failed: ${res.stderr}`);
    }
  }

  console.log(`[init] CODEX_HOME=${CODEX_HOME} WORKSPACE_DIR=${WORKSPACE_DIR} SKILLS_DIR=${SKILLS_DIR}`);
  console.log(`[init] model=${CODEX_MODEL} region=${BEDROCK_REGION}`);
}

// ── Codex ────────────────────────────────────────────────────────────────────
//
// The Codex SDK is ESM-only, so it is loaded with a dynamic import and cached.

let codexPromise = null;

function getCodex() {
  if (!codexPromise) {
    codexPromise = import("@openai/codex-sdk").then(({ Codex }) => {
      // Strip any inherited credentials that would make Codex bypass the
      // runtime execution role.
      const env = { ...process.env };
      delete env.OPENAI_API_KEY;
      delete env.CODEX_API_KEY;
      delete env.AWS_BEARER_TOKEN_BEDROCK;
      delete env.AWS_PROFILE;

      return new Codex({
        env: {
          ...env,
          CODEX_HOME,
          GIT_CONFIG_GLOBAL: path.join(CODEX_HOME, "gitconfig"),
          AWS_REGION: BEDROCK_REGION,
          AWS_DEFAULT_REGION: BEDROCK_REGION,
        },
      });
    });
  }
  return codexPromise;
}

// sandboxMode "workspace-write" makes only workingDirectory (plus temp dirs)
// writable. SKILLS_DIR lives outside the workspace, so it has to be granted
// explicitly — the SDK forwards additionalDirectories as `codex --add-dir`.
// Without it Codex cannot write the shared skill, and approvalPolicy "never"
// leaves it no way to ask.
const THREAD_OPTIONS = {
  additionalDirectories: [SKILLS_DIR],
  approvalPolicy: "never",
  model: CODEX_MODEL,
  modelReasoningEffort: REASONING_EFFORT,
  sandboxMode: "workspace-write",
  skipGitRepoCheck: false,
  workingDirectory: WORKSPACE_DIR,
};

async function runCodex(prompt, threadId) {
  const codex = await getCodex();

  // threadId is the Codex thread, persisted in CODEX_HOME on EFS. It is what
  // makes a conversation resumable from a different AgentCore session.
  const thread = threadId
    ? codex.resumeThread(threadId, THREAD_OPTIONS)
    : codex.startThread(THREAD_OPTIONS);

  // config.toml sets otel log_user_prompt = false; don't undo that by echoing
  // the prompt into CloudWatch. Length only.
  console.log(`[runCodex] threadId=${threadId || "(new)"} promptChars=${prompt.length}`);

  let turn;
  try {
    turn = await thread.run(prompt);
  } catch (err) {
    // Bedrock returns transient "Internal server error" responses. Surface the
    // thread ID anyway so the caller can resume instead of losing the
    // conversation, and log the full error for CloudWatch.
    const resumeId = thread.id || threadId || null;
    console.error(`[runCodex] turn failed. threadId=${resumeId} error=${err.stack || err}`);
    const wrapped = new Error(`Codex turn failed: ${err.message}`);
    wrapped.threadId = resumeId;
    throw wrapped;
  }

  const resolvedThreadId = thread.id || threadId || null;

  console.log(`[runCodex] done. threadId=${resolvedThreadId}`);
  if (turn.usage) {
    console.log(
      `[runCodex] usage: input=${turn.usage.input_tokens} ` +
        `cached=${turn.usage.cached_input_tokens} output=${turn.usage.output_tokens}`,
    );
  }

  return {
    response: turn.finalResponse,
    threadId: resolvedThreadId,
    usage: turn.usage || null,
  };
}

// ── HTTP server ──────────────────────────────────────────────────────────────

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
      console.log(`[POST] ${body.length} bytes`);

      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "body must be valid JSON" }));
        return;
      }

      const { prompt, threadId } = parsed;
      if (!prompt || typeof prompt !== "string") {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "prompt is required and must be a string" }));
        return;
      }
      if (threadId !== undefined && typeof threadId !== "string") {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "threadId must be a string" }));
        return;
      }
      const result = await runCodex(prompt, threadId);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(result));
    } catch (err) {
      console.error(`[POST] failed: ${err.stack || err.message}`);
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          error: err.message,
          ...(err.threadId ? { threadId: err.threadId } : {}),
        }),
      );
    }
    return;
  }

  res.writeHead(405);
  res.end();
});

// The uncaughtException handler above only logs, so an EFS failure here would
// otherwise exit 0 with nothing listening. Fail loudly instead.
try {
  initPersistentState();
} catch (err) {
  console.error("[FATAL] initPersistentState failed:", err.stack || err);
  process.exit(1);
}

server.listen(PORT, () => {
  console.log(`Codex agent listening on port ${PORT}`);
});
