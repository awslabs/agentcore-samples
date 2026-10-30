import { randomUUID } from "node:crypto";

import type { AppConfig } from "../config.js";
import { log } from "../logger.js";
import { createMcpClient } from "../mcp/mcpClientFactory.js";
import type { ProtocolVersion } from "../mcp/types.js";
import { ChatMessage, Session, SessionSummary, toSummary } from "./types.js";

export interface CreateSessionOptions {
  label?: string;
  /** If provided, this policy session id is used (source "user") instead of the omit-then-capture flow. */
  policySessionId?: string;
}

export interface SessionStore {
  list(): SessionSummary[];
  get(id: string): Session | undefined;
  create(protocol: ProtocolVersion, opts?: CreateSessionOptions): Promise<Session>;
  appendMessage(id: string, m: ChatMessage): void;
}

export function createSessionStore(deps: {
  cfg: AppConfig;
  getToken: () => Promise<string>;
}): SessionStore {
  const sessions = new Map<string, Session>();

  return {
    list(): SessionSummary[] {
      return [...sessions.values()].map(toSummary);
    },

    get(id: string): Session | undefined {
      return sessions.get(id);
    },

    async create(
      protocol: ProtocolVersion,
      opts: CreateSessionOptions = {},
    ): Promise<Session> {
      const id = randomUUID();
      // Number from the live map size (survives the counter, which the caller
      // never sees), and append a short id suffix so labels are always unique
      // even if the server restarts and the count resets.
      const n = sessions.size + 1;
      const suffix = id.slice(0, 4);

      const userPolicyId = opts.policySessionId?.trim() || "";

      const session: Session = {
        id,
        label: opts.label || `Session ${n} · ${protocol} · ${suffix}`,
        protocol,
        mcpSessionId: null,
        // If the user supplied a policy session id, use it and mark it "user";
        // otherwise start blank and capture the gateway-issued id on first call.
        policySessionId: userPolicyId || null,
        policySessionSource: userPolicyId ? "user" : null,
        messages: [],
        // client + tools filled in below
        client: undefined as never,
        tools: [],
      };

      session.client = createMcpClient(
        protocol,
        {
          mcpUrl: deps.cfg.mcpUrl,
          getToken: deps.getToken,
          getPolicySessionId: () => session.policySessionId,
          onPolicySessionId: (pid) => {
            // Never let the gateway override a user-provided id.
            if (session.policySessionSource === "user") return;
            session.policySessionId = pid;
            session.policySessionSource = "gateway";
            log.info("Captured policy session id", { session: id, policyId: pid });
          },
        },
        { mock: deps.cfg.mockMcp },
      );

      await session.client.connect();
      session.mcpSessionId = session.client.mcpSessionId;
      session.tools = await session.client.listTools();
      log.info("Session created", {
        id,
        protocol,
        tools: session.tools.length,
        mcpSessionId: session.mcpSessionId,
      });

      sessions.set(id, session);
      return session;
    },

    appendMessage(id: string, m: ChatMessage): void {
      sessions.get(id)?.messages.push(m);
    },
  };
}
