const BASE = "";

async function json(path, options) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

const put = (body) => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getStatus = () => json("/api/status");
export const getHooks = () => json("/api/hooks");
export const putHooks = (settings) => json("/api/hooks", put({ settings }));
export const getChaos = () => json("/api/chaos");
export const putChaos = (mode) => json("/api/chaos", put({ mode }));
export const getNotifications = (since) =>
  json(`/api/notifications?since=${since}`);
export const getOrders = () => json("/api/orders");
export const resetOrders = () => json("/api/reset", { method: "POST" });

export async function streamChat(message, sessionId, tamper, onEvent) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, tamper }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch (e) {
          // skip malformed
        }
      }
    }
  }
}
