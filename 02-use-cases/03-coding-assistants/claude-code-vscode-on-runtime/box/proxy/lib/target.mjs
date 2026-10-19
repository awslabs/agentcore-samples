// Which VS Code WebSocket the browser asked for. AgentCore only lets custom values through as
// X-Amzn-Bedrock-AgentCore-Runtime-Custom-* headers (or query params on /ws), so the browser sends
// the original "path?query" base64url-encoded in one of them. We only ever connect to the server
// root with the three query keys VS Code itself uses.

export const TARGET_HEADER = 'x-amzn-bedrock-agentcore-runtime-custom-vscodepath';
export const SESSION_HEADER = 'x-amzn-bedrock-agentcore-runtime-session-id';

const QUERY_RULES = {
  reconnectionToken: /^[A-Za-z0-9-]{1,128}$/,
  reconnection: /^(true|false)$/,
  // "true" would make the server drop WebSocket framing after the 101, which this relay can't carry.
  skipWebSocketFrames: /^false$/,
};

export function base64UrlDecode(text) {
  if (typeof text !== 'string' || text.length === 0 || text.length > 4096) return null;
  if (!/^[A-Za-z0-9_-]+={0,2}$/.test(text)) return null;
  return Buffer.from(text, 'base64url').toString('utf8');
}

// Returns {ok: true, path, query} with a re-serialized query, or {ok: false, reason}.
export function parseTarget(encoded, serverRoot) {
  const decoded = base64UrlDecode(encoded);
  if (decoded === null) return { ok: false, reason: 'target is not base64url' };
  if (!/^[\x21-\x7e]+$/.test(decoded)) return { ok: false, reason: 'target has invalid characters' };
  const q = decoded.indexOf('?');
  const path = q === -1 ? decoded : decoded.slice(0, q);
  const query = q === -1 ? '' : decoded.slice(q + 1);
  if (path !== serverRoot) return { ok: false, reason: 'target path not allowed' };
  const seen = new Map();
  for (const [key, value] of new URLSearchParams(query)) {
    const rule = QUERY_RULES[key];
    if (!rule) return { ok: false, reason: 'target query key not allowed' };
    if (seen.has(key)) return { ok: false, reason: 'target query key repeated' };
    if (!rule.test(value)) return { ok: false, reason: 'target query value not allowed' };
    seen.set(key, value);
  }
  return { ok: true, path, query: new URLSearchParams([...seen]).toString() };
}

// The header wins; AgentCore documents the query form for WebSocket clients, so accept it too.
export function targetFrom(headers, searchParams) {
  const fromHeader = headers[TARGET_HEADER];
  if (typeof fromHeader === 'string' && fromHeader) return fromHeader;
  return paramIgnoringCase(searchParams, TARGET_HEADER);
}

export function sessionFrom(headers, searchParams) {
  const fromHeader = headers[SESSION_HEADER];
  if (typeof fromHeader === 'string' && fromHeader) return fromHeader;
  return paramIgnoringCase(searchParams, SESSION_HEADER);
}

function paramIgnoringCase(searchParams, name) {
  if (!searchParams) return null;
  for (const [key, value] of searchParams) if (key.toLowerCase() === name) return value;
  return null;
}
