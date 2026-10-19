// Fake AgentCore Runtime data plane for local tests: the part of bedrock-agentcore.<region>.amazonaws.com
// that the browser talks to. Faithful to the behaviour verified in
// /tmp/remote-research/agentcore-contract.md (+ factcheck). Where the real service's behaviour is unknown,
// the fake picks the stricter choice so client bugs show up locally, and says so in README.md.
import http from 'node:http';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, WebSocket } from 'ws';
import { createAuthorizer } from './lib/authorizer.mjs';
import { ACTIONS, d2Policy, evaluatePolicy } from './lib/policy.mjs';
import { CUSTOM_PREFIX, SESSION_HEADER, REQUEST_ID_HEADER, MAX_HEADER_VALUE_BYTES, validateAllowlist, looksLikeJwt } from './lib/headers.mjs';
import { relay } from './lib/ws-relay.mjs';

const SUBPROTOCOL = 'base64UrlBearerAuthorization';
const SESSION_ID = /^[a-zA-Z0-9][a-zA-Z0-9-_]{32,99}$/;
const EXPOSE_HEADERS = 'smithy-protocol,baggage,WWW-Authenticate,Date,Mcp-Session-Id,X-Amzn-Trace-Id,X-Amzn-Bedrock-AgentCore-Runtime-Session-Id,Mcp-Protocol-Version,tracestate,x-amzn-ErrorMessage,traceparent,x-amzn-RequestId,x-amzn-ErrorType';
const ROUTE = /^\/runtimes\/(.+)\/(invocations|commands|stopruntimesession|ws\/shells|ws)$/;

export const DEFAULT_SETTINGS = {
  coldSeconds: 0,
  wsMaxSeconds: 3600,
  maxFrameBytes: 32768,
  maxFramesPerSecond: 250,
  rateScope: 'direction',
  wsForwardAuthorization: true,
  wsForwardQuery: false,
  boxReadyTimeoutSeconds: 120,
  pingSeconds: 0,
  resourcePolicyMode: 'config',
  clockSkewSeconds: 0,
  maxPayloadBytes: 100_000_000,
  publicBase: 'http://localhost:9401',
};

class AwsError extends Error {
  constructor(status, type, message, headers = {}) {
    super(message);
    Object.assign(this, { status, type, headers });
  }
}

async function defaultFetchJson(url) {
  const res = await fetch(url, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`GET ${new URL(url).pathname} returned ${res.status}`);
  return res.json();
}

function parseRuntimeArn(arn) {
  const m = /^arn:aws:bedrock-agentcore:([a-z0-9-]+):(\d{12}):runtime\/([A-Za-z0-9_-]+)$/.exec(arn);
  if (!m) throw new Error(`not a runtime ARN: ${arn}`);
  return { region: m[1], accountId: m[2], agentId: m[3] };
}

const shortSession = (id) => (id ? `${id.slice(0, 12)}...(${id.length})` : '-');

export function createFakeAgentCore({ config, settings: overrides = {}, log = (line) => console.log(`[fake-agentcore] ${line}`), fetchJson = defaultFetchJson } = {}) {
  const settings = { ...DEFAULT_SETTINGS, ...(config.settings ?? {}), ...overrides };
  const rewrites = config.oidcFetchRewrite ?? [];
  const rewriteUrl = (u) => rewrites.reduce((acc, r) => (acc.startsWith(r.from) ? r.to + acc.slice(r.from.length) : acc), u);

  const runtimes = config.runtimes.map((r) => {
    const ids = parseRuntimeArn(r.agentRuntimeArn);
    const hasJwt = Boolean(r.authorizerConfiguration?.customJWTAuthorizer);
    const policy = settings.resourcePolicyMode === 'd2' ? d2Policy(r.agentRuntimeArn)
      : settings.resourcePolicyMode === 'none' ? null : (r.resourcePolicy ?? null);
    return {
      arn: r.agentRuntimeArn,
      ...ids,
      boxUrl: r.boxUrl.replace(/\/$/, ''),
      authorizer: createAuthorizer(r.authorizerConfiguration, { fetchJson, rewriteUrl, clockSkewSeconds: settings.clockSkewSeconds }),
      allowlist: validateAllowlist(r.requestHeaderConfiguration?.requestHeaderAllowlist ?? [], { hasJwtAuthorizer: hasJwt }),
      policy,
    };
  });

  const sessions = new Map(); // `${arn}|${sessionId}` -> session record
  const counters = { requests: 0, byStatus: {}, tokenInUrl: 0, wsOpened: 0, wsClosedByCode: {}, violations: [], pingContractWarnings: 0 };

  function sessionRecord(runtime, sessionId) {
    const key = `${runtime.arn}|${sessionId}`;
    if (!sessions.has(key)) {
      sessions.set(key, { runtime, sessionId, state: 'cold', sockets: new Set(), coldStarts: 0, conflicts: 0, invocations: 0, ping: null, pingTimer: null });
    }
    return sessions.get(key);
  }

  async function waitForBox(runtime) {
    const deadline = Date.now() + settings.boxReadyTimeoutSeconds * 1000;
    for (;;) {
      try {
        const res = await fetch(`${runtime.boxUrl}/ping`, { signal: AbortSignal.timeout(2000) });
        if (res.ok) return;
      } catch { /* not up yet */ }
      if (Date.now() > deadline) throw new Error('the box never answered /ping');
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  // AgentCore polls /ping; watch the contract: time_of_last_update may move only when status changes.
  function startPingMonitor(s) {
    if (!settings.pingSeconds || s.pingTimer) return;
    const poll = async () => {
      try {
        const res = await fetch(`${s.runtime.boxUrl}/ping`, { signal: AbortSignal.timeout(5000) });
        const body = await res.json();
        const prev = s.ping;
        const bad = res.status !== 200 || !['Healthy', 'HealthyBusy'].includes(body.status);
        if (bad || (prev && prev.status === body.status && prev.time_of_last_update !== body.time_of_last_update)) {
          counters.pingContractWarnings += 1;
          log(`WARN /ping contract: status ${res.status} ${JSON.stringify(body)} after ${JSON.stringify(prev)}`);
        }
        if (!prev || prev.status !== body.status) log(`ping session=${shortSession(s.sessionId)} ${prev?.status ?? '-'} -> ${body.status}`);
        s.ping = { status: body.status, time_of_last_update: body.time_of_last_update, at: Date.now() };
      } catch (err) {
        log(`WARN /ping failed for session=${shortSession(s.sessionId)}: ${err.message}`);
      }
    };
    poll();
    s.pingTimer = setInterval(poll, settings.pingSeconds * 1000);
    s.pingTimer.unref();
  }

  // The first call for a cold session holds while the "instance" provisions; concurrent calls get 409.
  async function enterSession(runtime, sessionId) {
    const s = sessionRecord(runtime, sessionId);
    if (s.state === 'ready') return s;
    if (s.state === 'provisioning') {
      s.conflicts += 1;
      throw new AwsError(409, 'RetryableConflictException', 'Session operation in progress, please retry');
    }
    s.state = 'provisioning';
    s.coldStarts += 1;
    const started = Date.now();
    try {
      await new Promise((r) => setTimeout(r, settings.coldSeconds * 1000));
      await waitForBox(runtime);
    } catch (err) {
      s.state = 'cold';
      log(`cold start failed session=${shortSession(sessionId)}: ${err.message}`);
      throw new AwsError(500, 'InternalServerException', 'An internal error occurred. Please retry later.');
    }
    s.state = 'ready';
    log(`session ready session=${shortSession(sessionId)} after ${((Date.now() - started) / 1000).toFixed(1)}s cold start`);
    startPingMonitor(s);
    return s;
  }

  function findRuntime(identifier, query) {
    if (identifier.startsWith('arn:')) return runtimes.find((r) => r.arn === identifier) ?? null;
    const accountId = query.get('accountId');
    if (!accountId) throw new AwsError(400, 'ValidationException', 'accountId is required when agentRuntimeArn is an agent ID');
    return runtimes.find((r) => r.agentId === identifier && r.accountId === accountId) ?? null;
  }

  function bearerFrom(req, transport) {
    const auth = req.headers.authorization;
    if (auth) {
      if (/^AWS4-HMAC-SHA256 /.test(auth)) throw new AwsError(403, 'AccessDeniedException', 'Authorization method mismatch');
      const m = /^Bearer (.+)$/i.exec(auth);
      if (m) return m[1];
    }
    if (transport === 'ws' && req.headers['sec-websocket-protocol']) {
      const offered = req.headers['sec-websocket-protocol'].split(',').map((p) => p.trim()).filter(Boolean);
      const others = offered.filter((p) => p !== SUBPROTOCOL && !p.startsWith(`${SUBPROTOCOL}.`));
      if (others.length) throw new AwsError(400, 'ValidationException', `Subprotocols other than ${SUBPROTOCOL} are not supported`);
      const carrier = offered.find((p) => p.startsWith(`${SUBPROTOCOL}.`));
      if (carrier) {
        if (!offered.includes(SUBPROTOCOL)) throw new AwsError(400, 'ValidationException', `The ${SUBPROTOCOL} sentinel subprotocol is missing`);
        try {
          return Buffer.from(carrier.slice(SUBPROTOCOL.length + 1), 'base64url').toString('utf8');
        } catch {
          throw new AwsError(401, 'UnauthorizedException', 'Invalid bearer token encoding');
        }
      }
    }
    return null;
  }

  // Everything the edge checks before a request reaches the container. Returns { runtime, sessionId, claims, token }.
  async function admit(req, route, transport, note) {
    const url = new URL(req.url, 'http://fake');
    for (const [name, value] of url.searchParams) {
      if (looksLikeJwt(value)) {
        counters.tokenInUrl += 1;
        log(`WARN a token-like value was sent in URL query parameter ${name}`);
      }
    }
    let identifier;
    try {
      identifier = decodeURIComponent(route.identifier);
    } catch {
      throw new AwsError(400, 'ValidationException', 'Malformed agentRuntimeArn');
    }
    const token = bearerFrom(req, transport);
    const runtime = findRuntime(identifier, url.searchParams);
    if (!runtime) {
      if (!token) throw new AwsError(403, 'AccessDeniedException', 'Missing Authentication Token');
      throw new AwsError(404, 'ResourceNotFoundException', `No endpoint or agent found with qualifier 'DEFAULT' for agent '${identifier}'`);
    }
    note.runtime = runtime.agentId;
    const qualifier = url.searchParams.get('qualifier') ?? 'DEFAULT';
    if (qualifier !== 'DEFAULT') {
      throw new AwsError(404, 'ResourceNotFoundException', `No endpoint or agent found with qualifier '${qualifier}' for agent '${runtime.arn}'`);
    }
    if (!token) {
      const escaped = encodeURIComponent(runtime.arn);
      throw new AwsError(401, 'UnauthorizedException', 'Authentication required', {
        'WWW-Authenticate': `Bearer resource_metadata="${settings.publicBase}/runtimes/${escaped}/invocations/.well-known/oauth-protected-resource?qualifier=DEFAULT"`,
      });
    }
    const verdict = await runtime.authorizer.authorize(token);
    if (!verdict.ok) {
      note.reason = verdict.reason;
      if (verdict.unavailable) throw new AwsError(500, 'InternalServerException', 'An internal error occurred. Please retry later.');
      throw new AwsError(401, 'UnauthorizedException', 'Authorization failed for the provided token', { 'WWW-Authenticate': 'Bearer error="invalid_token"' });
    }
    note.user = verdict.claims.uid ?? verdict.claims.sub;
    const action = { invocations: ACTIONS.invoke, ws: ACTIONS.ws, commands: ACTIONS.command, 'ws/shells': ACTIONS.shell, stopruntimesession: ACTIONS.stop }[route.op];
    const decision = evaluatePolicy(runtime.policy, action, runtime.arn);
    if (decision !== 'allow') {
      note.reason = `resource policy ${decision} for ${action}`;
      throw new AwsError(403, 'AccessDeniedException',
        `User is not authorized to perform: ${action} on resource: ${runtime.arn} because ${decision === 'explicit-deny' ? 'of an explicit deny in' : 'no'} a resource-based policy${decision === 'explicit-deny' ? '' : ' allows it'}`);
    }
    let sessionId = req.headers[SESSION_HEADER];
    if (!sessionId && transport === 'ws') {
      sessionId = url.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id') ?? url.searchParams.get(SESSION_HEADER);
    }
    if (!sessionId) {
      // The real service would mint a new session here, which on Instances means a new EC2 box and volume.
      throw new AwsError(400, 'ValidationException', 'fake-agentcore requires X-Amzn-Bedrock-AgentCore-Runtime-Session-Id (the real service would silently create a new session)');
    }
    note.session = sessionId;
    if (!SESSION_ID.test(sessionId)) {
      throw new AwsError(400, 'ValidationException', 'runtimeSessionId must be 33-100 characters matching [a-zA-Z0-9][a-zA-Z0-9-_]*');
    }
    return { runtime, sessionId, claims: verdict.claims, token, url };
  }

  // Headers the container gets: AgentCore's own, then allowlisted caller headers (and, on /ws, Custom-* query params).
  function containerHeaders(req, admitted, transport, note) {
    const { runtime, sessionId, url } = admitted;
    const out = { [SESSION_HEADER]: sessionId, [REQUEST_ID_HEADER]: crypto.randomUUID() };
    const candidates = new Map();
    for (const [name, value] of Object.entries(req.headers)) candidates.set(name, value);
    if (transport === 'ws') {
      for (const [name, value] of url.searchParams) {
        if (name.toLowerCase().startsWith(CUSTOM_PREFIX)) candidates.set(name.toLowerCase(), value);
      }
      if (admitted.fromSubprotocol && settings.wsForwardAuthorization) candidates.set('authorization', `Bearer ${admitted.token}`);
    }
    const dropped = [];
    for (const [name, value] of candidates) {
      if (!runtime.allowlist.has(name)) {
        if (name.startsWith(CUSTOM_PREFIX)) dropped.push(name);
        continue;
      }
      const v = Array.isArray(value) ? value.join(', ') : String(value);
      if (Buffer.byteLength(v) > MAX_HEADER_VALUE_BYTES) {
        throw new AwsError(400, 'ValidationException', `Header ${name} exceeds ${MAX_HEADER_VALUE_BYTES} bytes`);
      }
      out[name] = v;
    }
    if (dropped.length) note.dropped = dropped.join(',');
    return out;
  }

  function corsHeaders() {
    return { 'Access-Control-Allow-Origin': '*', 'Access-Control-Expose-Headers': EXPOSE_HEADERS };
  }

  function sendError(res, err, requestId) {
    const status = err instanceof AwsError ? err.status : 500;
    const type = err instanceof AwsError ? err.type : 'InternalServerException';
    const message = err instanceof AwsError ? err.message : 'An internal error occurred. Please retry later.';
    const body = JSON.stringify({ message });
    res.writeHead(status, {
      'Content-Type': 'application/json',
      'x-amzn-ErrorType': type,
      'x-amzn-RequestId': requestId,
      ...corsHeaders(),
      ...(err.headers ?? {}),
    });
    res.end(body);
    return status;
  }

  function readBody(req) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (c) => {
        size += c.length;
        if (size > settings.maxPayloadBytes) {
          reject(new AwsError(400, 'ValidationException', `payload exceeds ${settings.maxPayloadBytes} bytes`));
          req.destroy();
          return;
        }
        chunks.push(c);
      });
      req.on('end', () => resolve(Buffer.concat(chunks)));
      req.on('error', reject);
    });
  }

  async function invoke(req, res, admitted, note, requestId) {
    const body = await readBody(req);
    const s = await enterSession(admitted.runtime, admitted.sessionId);
    const headers = {
      ...containerHeaders(req, admitted, 'http', note),
      // Content-Type and Accept are API parameters of InvokeAgentRuntime, so the container sees them
      // even though they can't be allowlisted.
      'content-type': req.headers['content-type'] ?? 'application/json',
      accept: req.headers.accept ?? '*/*',
      'content-length': String(body.length),
    };
    s.invocations += 1;
    const upstream = await new Promise((resolve, reject) => {
      const r = http.request(`${admitted.runtime.boxUrl}/invocations`, { method: 'POST', headers, timeout: 15 * 60 * 1000 }, resolve);
      r.on('error', reject);
      r.on('timeout', () => r.destroy(new Error('the box did not answer within 15 minutes')));
      r.end(body);
    }).catch((err) => {
      note.reason = `box unreachable: ${err.message}`;
      throw new AwsError(500, 'InternalServerException', 'An internal error occurred. Please retry later.');
    });
    if (upstream.statusCode >= 400) {
      // The container's own status, headers and body never reach the caller.
      upstream.resume();
      note.reason = `box answered ${upstream.statusCode}`;
      throw new AwsError(424, 'RuntimeClientError', `Received error (${upstream.statusCode}) from runtime. Please check your CloudWatch logs for more information.`);
    }
    res.writeHead(upstream.statusCode, {
      'Content-Type': upstream.headers['content-type'] ?? 'application/octet-stream',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': admitted.sessionId,
      'x-amzn-RequestId': requestId,
      ...corsHeaders(),
    });
    upstream.pipe(res);
    await new Promise((resolve) => res.on('close', resolve));
    return upstream.statusCode;
  }

  function stopSession(res, admitted, requestId) {
    const s = sessionRecord(admitted.runtime, admitted.sessionId);
    for (const r of s.sockets) r.close(1001, 'Session stopped');
    s.state = 'cold';
    res.writeHead(200, { 'Content-Type': 'application/json', 'x-amzn-RequestId': requestId, ...corsHeaders() });
    res.end(JSON.stringify({ runtimeSessionId: admitted.sessionId, statusCode: 200 }));
    return 200;
  }

  function snapshot() {
    return {
      settings,
      counters,
      sessions: [...sessions.values()].map((s) => ({
        runtime: s.runtime.agentId, sessionId: s.sessionId, state: s.state, openSockets: s.sockets.size,
        coldStarts: s.coldStarts, conflicts: s.conflicts, invocations: s.invocations, ping: s.ping,
      })),
    };
  }

  function admin(req, res, p) {
    if (req.method === 'GET' && p === '/_fake/health') return json(res, 200, { ok: true });
    if (req.method === 'GET' && p === '/_fake/stats') return json(res, 200, snapshot());
    if (req.method === 'POST' && p === '/_fake/reset') {
      for (const s of sessions.values()) {
        for (const r of s.sockets) r.close(1001, 'Going away');
        clearInterval(s.pingTimer);
      }
      sessions.clear();
      return json(res, 200, { ok: true });
    }
    return json(res, 404, { message: 'Not Found' });
  }

  function json(res, status, obj) {
    res.writeHead(status, { 'Content-Type': 'application/json', ...corsHeaders() });
    res.end(JSON.stringify(obj));
    return status;
  }

  function record(method, what, status, started, note) {
    counters.requests += 1;
    counters.byStatus[status] = (counters.byStatus[status] ?? 0) + 1;
    const parts = [`${method} ${what}`, `runtime=${note.runtime ?? '-'}`, `session=${shortSession(note.session)}`];
    if (note.user) parts.push(`user=${note.user}`);
    parts.push(`-> ${status}`, `${Date.now() - started}ms`);
    if (note.reason) parts.push(`(${note.reason})`);
    if (note.dropped) parts.push(`[not allowlisted, dropped: ${note.dropped}]`);
    log(parts.join(' '));
  }

  async function onRequest(req, res) {
    const started = Date.now();
    const requestId = crypto.randomUUID();
    const url = new URL(req.url, 'http://fake');
    const note = {};
    if (url.pathname.startsWith('/_fake/')) return admin(req, res, url.pathname);
    if (req.method === 'OPTIONS') {
      res.writeHead(200, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': req.headers['access-control-request-headers'] ?? '',
        'Access-Control-Allow-Methods': req.headers['access-control-request-method'] ?? 'POST',
        'Access-Control-Max-Age': '172800',
        'Access-Control-Expose-Headers': EXPOSE_HEADERS,
      });
      res.end();
      return;
    }
    const m = ROUTE.exec(url.pathname);
    let status;
    try {
      if (!m) throw new AwsError(404, 'UnknownOperationException', 'Not Found');
      const route = { identifier: m[1], op: m[2] };
      if (route.op === 'ws' || route.op === 'ws/shells') throw new AwsError(400, 'ValidationException', 'This endpoint needs a WebSocket upgrade');
      if (req.method !== 'POST') throw new AwsError(405, 'MethodNotAllowed', 'Method Not Allowed. Use POST.', { Allow: 'POST' });
      const admitted = await admit(req, route, 'http', note);
      if (route.op === 'invocations') status = await invoke(req, res, admitted, note, requestId);
      else if (route.op === 'stopruntimesession') status = stopSession(res, admitted, requestId);
      else throw new AwsError(501, 'NotImplemented', 'fake-agentcore does not emulate InvokeAgentRuntimeCommand; the real service would run the command inside the box as root, outside the proxy');
    } catch (err) {
      if (!(err instanceof AwsError)) note.reason = `internal: ${err.message}`;
      status = res.headersSent ? res.statusCode : sendError(res, err, requestId);
    }
    record(req.method, m ? m[2] : url.pathname, status, started, note);
  }

  function rejectUpgrade(socket, err) {
    const status = err instanceof AwsError ? err.status : 500;
    const type = err instanceof AwsError ? err.type : 'InternalServerException';
    const body = JSON.stringify({ message: err instanceof AwsError ? err.message : 'An internal error occurred. Please retry later.' });
    const extra = Object.entries(err.headers ?? {}).map(([k, v]) => `${k}: ${v}`);
    if (!socket.destroyed) {
      socket.end([
        `HTTP/1.1 ${status} ${http.STATUS_CODES[status] ?? 'Error'}`,
        'Content-Type: application/json', `Content-Length: ${Buffer.byteLength(body)}`, `x-amzn-ErrorType: ${type}`,
        'Access-Control-Allow-Origin: *', 'Connection: close', ...extra, '', body,
      ].join('\r\n'));
    }
    return status;
  }

  const wss = new WebSocketServer({
    noServer: true,
    perMessageDeflate: false,
    maxPayload: 256 * 1024 * 1024, // the relay enforces the real limit itself, so it can close both sides
    // The edge answers the browser's bearer subprotocol itself; the container never negotiates one.
    handleProtocols: (protocols) => (protocols.has(SUBPROTOCOL) ? SUBPROTOCOL : false),
  });

  async function onUpgrade(req, socket, head) {
    const started = Date.now();
    const url = new URL(req.url, 'http://fake');
    const m = ROUTE.exec(url.pathname);
    const note = {};
    let status;
    socket.on('error', () => {});
    try {
      if (!m) throw new AwsError(404, 'UnknownOperationException', 'Not Found');
      const route = { identifier: m[1], op: m[2] };
      if (route.op === 'invocations') throw new AwsError(405, 'MethodNotAllowed', 'Method Not Allowed. Use POST.', { Allow: 'POST' });
      if (route.op !== 'ws' && route.op !== 'ws/shells') throw new AwsError(400, 'ValidationException', 'WebSocket upgrades are served at /ws');
      const admitted = await admit(req, route, 'ws', note);
      admitted.fromSubprotocol = !req.headers.authorization;
      if (route.op === 'ws/shells') {
        throw new AwsError(501, 'NotImplemented', 'fake-agentcore does not emulate InvokeAgentRuntimeCommandShell; the real service would open a root shell inside the box, outside the proxy');
      }
      const s = await enterSession(admitted.runtime, admitted.sessionId);
      const headers = containerHeaders(req, admitted, 'ws', note);
      const target = `${admitted.runtime.boxUrl.replace(/^http/, 'ws')}/ws${settings.wsForwardQuery ? url.search : ''}`;
      const box = new WebSocket(target, { headers, perMessageDeflate: false, maxPayload: 256 * 1024 * 1024, handshakeTimeout: 30000 });
      await new Promise((resolve, reject) => {
        box.once('open', resolve);
        box.once('unexpected-response', (_r, res) => {
          res.resume();
          note.reason = `box refused the upgrade with ${res.statusCode}`;
          reject(new AwsError(424, 'RuntimeClientError', `Received error (${res.statusCode}) from runtime. Please check your CloudWatch logs for more information.`));
        });
        box.once('error', (err) => {
          note.reason = `box unreachable: ${err.message}`;
          reject(new AwsError(500, 'InternalServerException', 'An internal error occurred. Please retry later.'));
        });
      });
      if (socket.destroyed) {
        box.terminate();
        throw new AwsError(499, 'ClientClosed', 'the caller went away during the handshake');
      }
      wss.handleUpgrade(req, socket, head, (caller) => {
        counters.wsOpened += 1;
        const r = relay(caller, box, {
          maxFrameBytes: settings.maxFrameBytes, maxFramesPerSecond: settings.maxFramesPerSecond,
          rateScope: settings.rateScope, maxSeconds: settings.wsMaxSeconds,
        }, (kind, detail) => {
          if (kind === 'violation') counters.violations.push({ ...detail, session: admitted.sessionId, at: new Date().toISOString() });
          if (kind === 'closed') {
            s.sockets.delete(r);
            counters.wsClosedByCode[detail.code] = (counters.wsClosedByCode[detail.code] ?? 0) + 1;
            log(`WS closed runtime=${admitted.runtime.agentId} session=${shortSession(admitted.sessionId)} code=${detail.code} "${detail.reason}" (${detail.cause}) after ${detail.seconds.toFixed(1)}s frames caller->box=${detail.callerToBox} box->caller=${detail.boxToCaller}`);
          }
        });
        s.sockets.add(r);
      });
      status = 101;
    } catch (err) {
      if (!(err instanceof AwsError)) note.reason = `internal: ${err.message}`;
      status = rejectUpgrade(socket, err);
    }
    record('WS', m ? m[2] : url.pathname, status, started, note);
  }

  const server = http.createServer((req, res) => { onRequest(req, res); });
  server.on('upgrade', (req, socket, head) => { onUpgrade(req, socket, head); });

  return {
    server,
    settings,
    snapshot,
    listen(port, host = '127.0.0.1') {
      return new Promise((resolve) => server.listen(port, host, () => resolve(server.address().port)));
    },
    async close() {
      for (const s of sessions.values()) {
        clearInterval(s.pingTimer);
        for (const r of s.sockets) r.close(1001, 'Going away');
      }
      wss.close();
      server.closeAllConnections?.();
      await new Promise((resolve) => server.close(() => resolve()));
    },
  };
}

function envSettings(env) {
  const num = (name) => (env[name] === undefined || env[name] === '' ? undefined : Number(env[name]));
  const bool = (name) => (env[name] === undefined || env[name] === '' ? undefined : env[name] === '1' || env[name] === 'true');
  const out = {
    coldSeconds: num('FAKE_AC_COLD_SECONDS'),
    wsMaxSeconds: num('FAKE_AC_WS_MAX_SECONDS'),
    maxFrameBytes: num('FAKE_AC_WS_MAX_FRAME_BYTES'),
    maxFramesPerSecond: num('FAKE_AC_WS_MAX_FRAMES_PER_SECOND'),
    rateScope: env.FAKE_AC_WS_RATE_SCOPE || undefined,
    wsForwardAuthorization: bool('FAKE_AC_WS_FORWARD_AUTH'),
    wsForwardQuery: bool('FAKE_AC_WS_FORWARD_QUERY'),
    boxReadyTimeoutSeconds: num('FAKE_AC_BOX_READY_TIMEOUT_SECONDS'),
    pingSeconds: num('FAKE_AC_PING_SECONDS'),
    resourcePolicyMode: env.FAKE_AC_RESOURCE_POLICY || undefined,
    clockSkewSeconds: num('FAKE_AC_CLOCK_SKEW_SECONDS'),
    publicBase: env.FAKE_AC_PUBLIC_BASE || undefined,
  };
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v !== undefined));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const configPath = process.env.FAKE_AC_CONFIG || path.join(path.dirname(fileURLToPath(import.meta.url)), 'runtimes.json');
  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  const ac = createFakeAgentCore({ config, settings: envSettings(process.env) });
  const port = Number(process.env.FAKE_AC_PORT || 9401);
  const host = process.env.FAKE_AC_HOST || '127.0.0.1';
  await ac.listen(port, host);
  const s = ac.settings;
  console.log(`[fake-agentcore] listening on ${host}:${port}: ${config.runtimes.length} runtime(s), cold ${s.coldSeconds}s, ws max ${s.wsMaxSeconds}s, frame ${s.maxFrameBytes} B, ${s.maxFramesPerSecond} frames/s per ${s.rateScope}, resource policy ${s.resourcePolicyMode}`);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => ac.close().then(() => process.exit(0)));
}
