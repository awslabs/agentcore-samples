import type {
  AppConfigDTO,
  ProtocolVersion,
  SessionDTO,
  SessionSummary,
  ToolEvent,
} from "../types";

/** Events streamed from POST /api/sessions/:id/messages (newline-delimited JSON). */
export type StreamEvent =
  | { type: "text"; delta: string }
  | { type: "tool"; event: ToolEvent }
  | {
      type: "ids";
      mcpSessionId: string | null;
      policySessionId: string | null;
    }
  | { type: "tool_start"; name: string; args: unknown }
  | { type: "done"; session: SessionDTO }
  | { type: "error"; error: string; code?: string; hint?: string };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = json as { error?: string; hint?: string };
    throw new Error(err.hint ? `${err.error} — ${err.hint}` : err.error || `HTTP ${res.status}`);
  }
  return json as T;
}

export const api = {
  getConfig: () => request<AppConfigDTO>("/api/config"),

  listSessions: () =>
    request<{ sessions: SessionSummary[] }>("/api/sessions").then((r) => r.sessions),

  getSession: (id: string) =>
    request<{ session: SessionDTO }>(`/api/sessions/${id}`).then((r) => r.session),

  createSession: (protocol: ProtocolVersion, policySessionId?: string) =>
    request<{ session: SessionDTO }>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ protocol, policySessionId }),
    }).then((r) => r.session),

  refreshTools: (id: string) =>
    request<{ session: SessionDTO }>(`/api/sessions/${id}/refresh-tools`, {
      method: "POST",
    }).then((r) => r.session),

  /**
   * Send a message and stream the reply. Calls `onEvent` for each event as it
   * arrives (text deltas, tool calls, ids), ending with a `done` or `error`.
   */
  async sendMessageStream(
    id: string,
    text: string,
    onEvent: (ev: StreamEvent) => void,
  ): Promise<void> {
    const res = await fetch(`/api/sessions/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok || !res.body) {
      const msg = await res.text().catch(() => `HTTP ${res.status}`);
      onEvent({ type: "error", error: msg || `HTTP ${res.status}` });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (line) onEvent(JSON.parse(line) as StreamEvent);
      }
    }
    const tail = buffer.trim();
    if (tail) onEvent(JSON.parse(tail) as StreamEvent);
  },
};
