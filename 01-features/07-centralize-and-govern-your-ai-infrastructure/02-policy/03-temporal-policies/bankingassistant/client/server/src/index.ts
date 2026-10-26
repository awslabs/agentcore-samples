import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime";
import "dotenv/config";
import express from "express";

import { createCognitoTokenManager } from "./auth/cognitoTokenManager.js";
import { loadConfig } from "./config.js";
import { log } from "./logger.js";
import { configRouter } from "./routes/config.js";
import { sessionsRouter } from "./routes/sessions.js";
import { createSessionStore } from "./sessions/sessionStore.js";

function main(): void {
  const cfg = loadConfig();
  log.info("Starting banking-assistant server", {
    gatewayUrl: cfg.gatewayUrl,
    region: cfg.region,
    mockMcp: cfg.mockMcp,
    mockLlm: cfg.mockLlm,
  });

  // In mock mode there is no Cognito; provide a stub token so nothing calls out.
  const tokenManager = cfg.mockMcp
    ? { getToken: async () => "mock-token" }
    : createCognitoTokenManager(cfg);

  const bedrock = new BedrockRuntimeClient({ region: cfg.region });
  const store = createSessionStore({ cfg, getToken: () => tokenManager.getToken() });

  const app = express();
  app.use(express.json());
  app.use("/api/config", configRouter(cfg));
  app.use("/api/sessions", sessionsRouter({ cfg, store, bedrock }));
  app.get("/api/health", (_req, res) => res.json({ ok: true }));

  // The UI is served by Vite on :5173; this backend is API-only. Point stray
  // browser hits at the right place instead of a bare "Cannot GET /".
  app.get("/", (_req, res) =>
    res
      .type("text")
      .send("Banking Assistant API. Open the UI at http://localhost:5173"),
  );

  app.listen(cfg.port, () => {
    log.info(`Server listening on http://localhost:${cfg.port}`);
  });
}

main();
