// POST /invocations. AgentCore turns any container 4xx/5xx into an opaque 424 and drops the
// response headers, so every answer here is HTTP 200 with a JSON envelope that carries the real
// outcome. Nothing in here ever returns a token or a header value (except the session id).

import http from 'node:http';

export const MAX_REQUEST_BYTES = 1024 * 1024;
export const MAX_BODY_BYTES = 50 * 1024 * 1024;
const UPSTREAM_TIMEOUT_MS = 30_000;
const QUERY_CHARS = /^[A-Za-z0-9\-._~%!$&'()*+,;=:@/?]*$/;

export function error(message) {
  return { v: 1, ok: false, error: message };
}

function httpResult(status, headers = {}, body = Buffer.alloc(0)) {
  return { v: 1, ok: true, status, headers, bodyB64: body.toString('base64') };
}

export function allowedHttpPaths(serverRoot) {
  return new Set([`${serverRoot}/vscode-remote-resource`, '/version']);
}

// GET from the VS Code server on loopback. Returns {status, headers, body} or throws.
export function fetchUpstream({ host, port }, { method, path, query, ifNoneMatch }) {
  return new Promise((resolve, reject) => {
    const headers = { host: `${host}:${port}` };
    if (ifNoneMatch) headers['if-none-match'] = ifNoneMatch;
    const req = http.request({ host, port, method, path: query ? `${path}?${query}` : path, headers,
      timeout: UPSTREAM_TIMEOUT_MS }, (res) => {
      const declared = Number(res.headers['content-length']);
      if (declared > MAX_BODY_BYTES) {
        res.destroy();
        resolve({ status: 413, headers: {}, body: Buffer.alloc(0) });
        return;
      }
      const parts = [];
      let size = 0;
      res.on('data', (part) => {
        size += part.length;
        if (size > MAX_BODY_BYTES) {
          res.destroy();
          resolve({ status: 413, headers: {}, body: Buffer.alloc(0) });
          return;
        }
        parts.push(part);
      });
      res.on('end', () => {
        const out = { 'content-type': res.headers['content-type'] || 'application/octet-stream' };
        if (res.headers.etag) out.etag = res.headers.etag;
        resolve({ status: res.statusCode, headers: out, body: Buffer.concat(parts) });
      });
      res.on('error', reject);
    });
    req.on('timeout', () => req.destroy(Object.assign(new Error('upstream timeout'), { code: 'ETIMEDOUT' })));
    req.on('error', reject);
    req.end();
  });
}

export async function opHttp(request, { serverRoot, fetch }) {
  const method = request.method ?? 'GET';
  if (method !== 'GET' && method !== 'HEAD') return httpResult(405);
  const { path, query = '' } = request;
  if (typeof path !== 'string' || typeof query !== 'string') return error('path and query must be strings');
  if (!allowedHttpPaths(serverRoot).has(path)) return httpResult(404);
  if (query.length > 8192 || !QUERY_CHARS.test(query)) return httpResult(400);
  let ifNoneMatch;
  if (request.headers && typeof request.headers === 'object') {
    for (const [key, value] of Object.entries(request.headers)) {
      if (key.toLowerCase() === 'if-none-match' && typeof value === 'string' && value.length <= 1024
        && !/[\r\n]/.test(value)) ifNoneMatch = value;
    }
  }
  try {
    const res = await fetch({ method, path, query, ifNoneMatch });
    return httpResult(res.status, res.headers, method === 'HEAD' ? Buffer.alloc(0) : res.body);
  } catch (err) {
    return httpResult(err.code === 'ETIMEDOUT' ? 504 : 502);
  }
}

export function opStatus({ config, state, sessionId }) {
  return {
    v: 1,
    ok: true,
    owner: config.owner,
    sessionId: config.sessionId || sessionId || '',
    volume: state.volume === 'mounted' ? 'mounted' : 'waiting',
    vscode: ['waiting', 'starting', 'ready', 'failed'].includes(state.vscode) ? state.vscode : 'waiting',
    commit: config.commit,
    serverStartId: typeof state.serverStartId === 'string' ? state.serverStartId : null,
    lastSession: cleanLastSession(state.lastSession),
    signedIn: state.signedIn === true,
  };
}

function cleanLastSession(value) {
  if (!value || typeof value !== 'object') return null;
  const text = (v) => (typeof v === 'string' ? v.slice(0, 4096) : null);
  if (!text(value.sessionId)) return null;
  return {
    sessionId: text(value.sessionId),
    cwd: text(value.cwd),
    transcriptPath: text(value.transcriptPath),
    ts: typeof value.ts === 'number' ? value.ts : null,
  };
}

// Parses and routes one /invocations body. `ctx` supplies config, state and the upstream fetch.
export async function handleInvocation(raw, sessionHeader, ctx) {
  if (ctx.config.sessionId && sessionHeader !== ctx.config.sessionId) return error('wrong session');
  let request;
  try {
    request = JSON.parse(raw);
  } catch {
    return error('body is not JSON');
  }
  if (!request || typeof request !== 'object' || Array.isArray(request)) return error('body must be a JSON object');
  if (request.v !== undefined && request.v !== 1) return error('unsupported envelope version');
  switch (request.op) {
    case 'status':
      return opStatus({ config: ctx.config, state: ctx.readState(), sessionId: sessionHeader });
    case 'http':
      return opHttp(request, { serverRoot: ctx.config.serverRoot, fetch: ctx.fetch });
    case 'diag':
      return { v: 1, ok: true, ...ctx.diag() };
    default:
      return error('unknown op');
  }
}
