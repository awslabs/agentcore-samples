/**
 * A coding agent that keeps a Vite website running in its own session.
 *
 * POST /invocations on port 8080 is where InvokeAgentRuntime sends your prompt. The
 * agent edits the site's files, and the Vite dev server on port 8090 pushes each
 * change to every browser that has the page open, over its hot reload WebSocket.
 * Port 8081 serves GET /status, the agent's own record of what it changed.
 */
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";
import { Agent, tool } from "@strands-agents/sdk";
import { createTwoFilesPatch } from "diff";
import { BedrockModel } from "@strands-agents/sdk/models/bedrock";
import z from "zod";

const SERVING_PORT = 8080;
const PREVIEW_PORT = 8090;
const STATUS_PORT = 8081;
// deploy.py --model sets MODEL_ID; the default is the smaller, faster model.
const MODEL_ID = process.env.MODEL_ID || "us.anthropic.claude-haiku-5-5";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TEMPLATE = path.join(HERE, "site");
const SITE = "/tmp/site";

const edits: { prompt: string; files: string[]; at: string }[] = [];
let previewReady = false;

// --- the website ------------------------------------------------------------

function startPreview(): void {
  // The package may be read only, so the site is copied somewhere the agent can write.
  if (!existsSync(SITE)) {
    mkdirSync(SITE, { recursive: true });
    for (const entry of readdirSync(TEMPLATE)) {
      if (entry === "node_modules") continue;
      cpSync(path.join(TEMPLATE, entry), path.join(SITE, entry), { recursive: true });
    }
    symlinkSync(path.join(TEMPLATE, "node_modules"), path.join(SITE, "node_modules"));
  }
  const vite = spawn(process.execPath, [path.join(TEMPLATE, "node_modules/vite/bin/vite.js")], {
    cwd: SITE,
    stdio: "inherit",
  });
  vite.on("exit", (code) => {
    previewReady = false;
    console.log(`vite exited with ${code}`);
  });
}

async function waitForPreview(seconds = 30): Promise<boolean> {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    try {
      const answer = await fetch(`http://127.0.0.1:${PREVIEW_PORT}/`);
      if (answer.ok) return (previewReady = true);
    } catch {
      // not listening yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

// --- the agent's tools ------------------------------------------------------

function inSite(relative: string): string {
  const full = path.resolve(SITE, relative);
  if (!full.startsWith(SITE + path.sep) || full.includes(`${path.sep}node_modules`)) {
    throw new Error(`${relative} is outside the site`);
  }
  return full;
}

let changed: string[] = [];
// Each changed file's contents before the first edit of the current request, for the diff.
let originals: Record<string, string> = {};
function remember(relative: string, full: string): void {
  if (!(relative in originals)) originals[relative] = existsSync(full) ? readFileSync(full, "utf-8") : "";
}
// Progress for the current request, sent to the caller as it happens when it asked to stream.
let report: (step: string) => void = () => {};

const listFiles = tool({
  name: "list_files",
  description: "List the website's source files, relative to the site root.",
  inputSchema: z.object({}),
  callback: () => {
    report("Looking at the site's files");
    const found: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        if (entry === "node_modules") continue;
        const full = path.join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else found.push(path.relative(SITE, full));
      }
    };
    walk(SITE);
    return found.join("\n");
  },
});

const readFile = tool({
  name: "read_file",
  description: "Read one of the website's files.",
  inputSchema: z.object({ path: z.string() }),
  callback: ({ path: relative }) => {
    report(`Reading ${relative}`);
    return readFileSync(inSite(relative), "utf-8");
  },
});

const writeFile = tool({
  name: "write_file",
  description: "Replace one of the website's files with new contents. The browser updates on save.",
  inputSchema: z.object({ path: z.string(), contents: z.string() }),
  callback: ({ path: relative, contents }) => {
    remember(relative, inSite(relative));
    writeFileSync(inSite(relative), contents, "utf-8");
    if (!changed.includes(relative)) changed.push(relative);
    report(`Saved ${relative}`);
    return `wrote ${relative}`;
  },
});

const SYSTEM_PROMPT = `You maintain a web app built with Vite, in the directory the tools work on.
The Vite dev server is running and reloads every change in the browser as soon as a file is saved.
Read the files you need before changing them. Make the change the user asks for and keep everything
else as it is. Prefer edit_file for changes to existing files, and match the app's existing style and
class names, and don't add emojis. If src/data.js exists, it holds recorded data: treat it as read only and compute anything
new from it in code. When you are done, reply in at most two plain sentences, with no markdown and no lists, and end by
saying where on the page the user can see the change. Don't quote numbers or counts the app computes;
the page shows them.`;

const editFile = tool({
  name: "edit_file",
  description:
    "Replace one exact piece of text in a file with new text. old_text must appear exactly once in the file.",
  inputSchema: z.object({ path: z.string(), old_text: z.string(), new_text: z.string() }),
  callback: ({ path: relative, old_text, new_text }) => {
    const full = inSite(relative);
    const current = readFileSync(full, "utf-8");
    const count = current.split(old_text).length - 1;
    if (count !== 1) return `old_text appears ${count} times in ${relative}; give a longer, unique excerpt`;
    remember(relative, full);
    writeFileSync(full, current.replace(old_text, new_text), "utf-8");
    if (!changed.includes(relative)) changed.push(relative);
    report(`Saved ${relative}`);
    return `edited ${relative}`;
  },
});

const agent = new Agent({
  model: new BedrockModel({ region: process.env.AWS_REGION || "us-west-2", modelId: MODEL_ID }),
  systemPrompt: SYSTEM_PROMPT,
  tools: [listFiles, readFile, writeFile, editFile],
});

// --- the servers ------------------------------------------------------------

function readBody(request: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    const stream = request.headers["content-encoding"] === "gzip" ? request.pipe(zlib.createGunzip()) : request;
    stream.on("data", (chunk: Buffer) => chunks.push(chunk));
    stream.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    stream.on("error", reject);
  });
}

function json(response: http.ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

http
  .createServer(async (request, response) => {
    if (request.url === "/ping") return json(response, 200, { status: "Healthy" });
    if (request.method !== "POST" || request.url !== "/invocations") return json(response, 404, {});

    let prompt = "";
    let stream = false;
    try {
      const body = JSON.parse((await readBody(request)) || "{}");
      prompt = body.prompt || "";
      stream = Boolean(body.stream);
    } catch {
      return json(response, 400, { error: "send a JSON body with a prompt" });
    }

    const ready = previewReady || (await waitForPreview());
    if (!prompt) return json(response, 200, { preview_port: PREVIEW_PORT, ready });

    // With stream: true the answer is server-sent events, one per step, so a caller can
    // show the agent working instead of waiting for the whole edit.
    const send = (event: object) => response.write(`data: ${JSON.stringify(event)}\n\n`);
    if (stream) {
      response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      report = (step) => send({ step });
    }

    changed = [];
    originals = {};
    try {
      const result = await agent.invoke(prompt);
      const reply = result.toString().trim();
      edits.push({ prompt, files: [...changed], at: new Date().toISOString() });
      const diffs = changed.map((relative) => ({
        path: relative,
        patch: createTwoFilesPatch(`a/${relative}`, `b/${relative}`, originals[relative] ?? "",
          readFileSync(inSite(relative), "utf-8"), "", "", { context: 2 }).slice(0, 40000),
      }));
      const done = { preview_port: PREVIEW_PORT, ready, changed, reply, diffs };
      if (stream) {
        send({ done });
        return response.end();
      }
      return json(response, 200, done);
    } catch (error) {
      console.error("agent failed:", error);
      if (stream) {
        send({ error: String(error) });
        return response.end();
      }
      return json(response, 500, { error: String(error), changed });
    } finally {
      report = () => {};
    }
  })
  .listen(SERVING_PORT, "0.0.0.0");

http
  .createServer((request, response) => {
    if (request.method === "GET" && request.url === "/status") {
      return json(response, 200, { preview_ready: previewReady, edits, served_on_port: STATUS_PORT });
    }
    json(response, 404, {});
  })
  .listen(STATUS_PORT, "0.0.0.0");

startPreview();
