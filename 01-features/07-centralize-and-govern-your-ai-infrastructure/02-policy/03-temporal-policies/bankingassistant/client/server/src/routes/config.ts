import { Router } from "express";

import type { AppConfig } from "../config.js";

/** GET /api/config — non-secret info the UI needs. */
export function configRouter(cfg: AppConfig): Router {
  const router = Router();
  router.get("/", (_req, res) => {
    res.json({
      gatewayUrl: cfg.gatewayUrl,
      region: cfg.region,
      mock: cfg.mockMcp,
    });
  });
  return router;
}
