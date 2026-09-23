import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime";
import { Router } from "express";

import type { AppConfig } from "../config.js";
import { runConverseStream } from "../bedrock/converseLoop.js";
import { log } from "../logger.js";
import { ProtocolVersion, SessionInvalidatedError } from "../mcp/types.js";
import type { SessionStore } from "../sessions/sessionStore.js";
import { toDTO } from "../sessions/types.js";
import { SYSTEM_PROMPT } from "../systemPrompt.js";

const PROTOCOLS: ProtocolVersion[] = ["2025-11-25", "2026-07-28"];

export function sessionsRouter(deps: {
  cfg: AppConfig;
  store: SessionStore;
  bedrock: BedrockRuntimeClient;
}): Router {
  const { cfg, store, bedrock } = deps;
  const router = Router();

  // List all sessions (summaries).
  router.get("/", (_req, res) => {
    res.json({ sessions: store.list() });
  });

  // Create a new session with a protocol (and optional label).
  router.post("/", async (req, res) => {
    const protocol = (req.body?.protocol ?? "2025-11-25") as ProtocolVersion;
    if (!PROTOCOLS.includes(protocol)) {
      res.status(400).json({ error: `Unknown protocol: ${protocol}` });
      return;
    }
    try {
      const session = await store.create(protocol, {
        label: req.body?.label,
        policySessionId: req.body?.policySessionId,
      });
      res.status(201).json({ session: toDTO(session) });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.error("Session create failed", { error: msg });
      res.status(502).json({ error: `Failed to create session: ${msg}` });
    }
  });

  // Full session DTO (with transcript) — used when switching sessions.
  router.get("/:id", (req, res) => {
    const session = store.get(req.params.id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    res.json({ session: toDTO(session) });
  });

  // Re-fetch the tool list from the gateway (e.g. after a policy change adds a new tool).
  router.post("/:id/refresh-tools", async (req, res) => {
    const session = store.get(req.params.id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    try {
      session.tools = await session.client.listTools();
      res.json({ session: toDTO(session) });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.error("Refresh tools failed", { session: session.id, error: msg });
      res.status(502).json({ error: msg });
    }
  });

  // Send a chat message: run the Converse tool loop, append transcript.
  router.post("/:id/messages", async (req, res) => {
    const session = store.get(req.params.id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    const text = String(req.body?.text ?? "").trim();
    if (!text) {
      res.status(400).json({ error: "Message text is required" });
      return;
    }

    const now = Date.now();
    const history = [...session.messages];
    store.appendMessage(session.id, { role: "user", content: text, ts: now });

    // Stream the turn as newline-delimited JSON events. Each line is one event:
    //   {type:"text",delta}          incremental assistant text
    //   {type:"tool",event}          a completed tool call
    //   {type:"ids",...}             session ids (policy id may appear mid-turn)
    //   {type:"done",session}        final DTO (also refreshes ids on the client)
    //   {type:"error",error,code?}   failure (e.g. session invalidated)
    res.setHeader("Content-Type", "application/x-ndjson");
    res.setHeader("Cache-Control", "no-cache");
    const send = (obj: unknown) => res.write(JSON.stringify(obj) + "\n");

    try {
      const result = await runConverseStream(
        {
          client: bedrock,
          modelId: cfg.bedrockModelId,
          system: SYSTEM_PROMPT,
          history,
          userText: text,
          tools: session.tools,
          mcp: session.client,
          mockLlm: cfg.mockLlm,
        },
        (ev) => send(ev),
      );
      // Keep the session ids in sync with whatever the client captured.
      session.mcpSessionId = session.client.mcpSessionId;
      session.policySessionId = session.client.policySessionId;
      store.appendMessage(session.id, {
        role: "assistant",
        content: result.assistantText,
        toolEvents: result.toolEvents,
        ts: Date.now(),
      });
      send({ type: "done", session: toDTO(session) });
      res.end();
    } catch (err) {
      if (err instanceof SessionInvalidatedError) {
        send({
          type: "error",
          error: err.message,
          code: "SESSION_INVALIDATED",
          hint: "A policy was added or changed. Start a new session and try again.",
        });
        res.end();
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      log.error("Message handling failed", { session: session.id, error: msg });
      send({ type: "error", error: msg });
      res.end();
    }
  });

  return router;
}
