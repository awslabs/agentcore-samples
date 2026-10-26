// Shared test helpers: a fake clock, a fake VS Code server (HTTP + WebSocket), and the proxy app
// on ephemeral ports.

import { createHash } from 'node:crypto';
import http from 'node:http';
import { createRequire } from 'node:module';
import { createApp } from '../proxy/lib/app.mjs';

const require = createRequire(new URL('../proxy/package.json', import.meta.url));
export const { WebSocket, WebSocketServer } = require('ws');

export const COMMIT = '072586267e68ece9a47aa43f8c108e0dcbf44622';
export const SERVER_ROOT = `/stable-${COMMIT}`;
export const SESSION = 'dbx-' + 'a'.repeat(64);

export class FakeClock {
  constructor(start = 1_700_000_000_000) {
    this.t = start;
    this.timers = [];
    this.now = () => this.t;
    this.setTimer = (fn, ms) => {
      const timer = { at: this.t + ms, fn };
      this.timers.push(timer);
      return timer;
    };
    this.clearTimer = (timer) => {
      this.timers = this.timers.filter((t) => t !== timer);
    };
  }

  advance(ms) {
    const end = this.t + ms;
    for (;;) {
      this.timers.sort((a, b) => a.at - b.at);
      const next = this.timers[0];
      if (!next || next.at > end) break;
      this.timers.shift();
      this.t = next.at;
      next.fn();
    }
    this.t = end;
  }
}

export function b64url(text) {
  return Buffer.from(text).toString('base64url');
}

export function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port)));
}

// Stands in for openvscode-server on loopback. Records what reaches it.
export async function startFakeVscode() {
  const seen = { http: [], ws: [] };
  const sockets = new Set();
  const server = http.createServer((req, res) => {
    seen.http.push({ method: req.method, url: req.url, headers: req.headers });
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/version') {
      res.writeHead(200, { 'content-type': 'text/plain', 'set-cookie': 'a=b', 'cache-control': 'public' });
      res.end(COMMIT);
    } else if (url.pathname === `${SERVER_ROOT}/vscode-remote-resource`) {
      const file = url.searchParams.get('path');
      if (file === '/big') {
        res.writeHead(200, { 'content-type': 'application/octet-stream' });
        res.end(Buffer.alloc(51 * 1024 * 1024, 1));
      } else if (req.headers['if-none-match'] === 'W/"1"') {
        res.writeHead(304, { etag: 'W/"1"' });
        res.end();
      } else {
        res.writeHead(200, { 'content-type': 'image/png', etag: 'W/"1"', 'x-extra': 'no' });
        res.end(Buffer.from(`file:${file}`));
      }
    } else {
      res.writeHead(404).end();
    }
  });
  const wss = new WebSocketServer({ server });
  wss.on('connection', (ws, req) => {
    const entry = { url: req.url, headers: req.headers, received: [], ws };
    seen.ws.push(entry);
    sockets.add(ws);
    ws.on('message', (data, isBinary) => entry.received.push({ data: Buffer.from(data), isBinary }));
    ws.on('close', (code) => { entry.closedWith = code; });
  });
  const port = await listen(server);
  return {
    port,
    seen,
    close: () => new Promise((resolve) => {
      for (const ws of sockets) ws.terminate();
      wss.close();
      server.close(() => resolve());
    }),
  };
}

// Closes the way the real VS Code server does: it accepts the upgrade, then never
// answers or even parses a Close frame, so only the proxy can end the connection. Records when each
// upstream TCP connection ended.
export async function startSilentUpstream() {
  const connections = [];
  const server = http.createServer((req, res) => res.writeHead(404).end());
  server.on('upgrade', (req, socket) => {
    const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest('base64');
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
    const entry = { url: req.url, socket, endedAt: null, bytes: 0 };
    connections.push(entry);
    socket.on('data', (data) => { entry.bytes += data.length; }); // Close frames included: dropped
    const ended = () => { entry.endedAt ??= Date.now(); };
    socket.on('end', ended);
    socket.on('close', ended);
    socket.on('error', () => {});
  });
  const port = await listen(server);
  return {
    port,
    connections,
    // An unmasked binary frame from the server: proof that the proxy's upstream socket is open.
    sendBinary: (entry, text) => entry.socket.write(Buffer.concat([Buffer.from([0x82, text.length]), Buffer.from(text)])),
    close: () => new Promise((resolve) => {
      for (const c of connections) c.socket.destroy();
      server.close(() => resolve());
    }),
  };
}

export async function startProxy({ upstreamPort, sessionId = SESSION, state = {}, now, pacer } = {}) {
  const config = {
    owner: 'ada',
    sessionId,
    commit: COMMIT,
    serverRoot: SERVER_ROOT,
    stateFile: '/nonexistent',
    upstream: { host: '127.0.0.1', port: upstreamPort },
  };
  const readState = typeof state === 'function' ? state : () => state;
  const app = createApp(config, { readState, now, pacer, log: () => {} });
  // Upgraded sockets aren't tracked by the HTTP server, so a failed test would hang close().
  const sockets = new Set();
  app.server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  const port = await listen(app.server);
  return {
    ...app,
    port,
    close: () => new Promise((resolve) => {
      for (const socket of sockets) socket.destroy();
      app.server.close(() => resolve());
    }),
  };
}

export async function invoke(port, body, { session = SESSION, method = 'POST', headers = {} } = {}) {
  const raw = typeof body === 'string' ? body : JSON.stringify(body);
  const res = await fetch(`http://127.0.0.1:${port}/invocations`, {
    method,
    headers: {
      'content-type': 'application/json',
      ...(session ? { 'x-amzn-bedrock-agentcore-runtime-session-id': session } : {}),
      ...headers,
    },
    body: method === 'GET' ? undefined : raw,
  });
  return { status: res.status, contentType: res.headers.get('content-type'), json: await res.json() };
}

export function connectBrowser(port, { target, session = SESSION, viaQuery = false, headers = {} } = {}) {
  const query = viaQuery
    ? `?X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=${session}&X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath=${target}`
    : '';
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws${query}`, {
    perMessageDeflate: false,
    headers: viaQuery ? headers : {
      'x-amzn-bedrock-agentcore-runtime-session-id': session,
      'x-amzn-bedrock-agentcore-runtime-custom-vscodepath': target,
      ...headers,
    },
  });
  const received = [];
  ws.on('message', (data) => received.push({ data: Buffer.from(data), at: Date.now() }));
  const closed = new Promise((resolve) => ws.on('close', (code, reason) => resolve({ code, reason: reason.toString() })));
  const opened = new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });
  return { ws, received, closed, opened };
}

export function waitFor(check, { timeout = 5000, interval = 10 } = {}) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      let value;
      try {
        value = check();
      } catch (err) {
        reject(err);
        return;
      }
      if (value) resolve(value);
      else if (Date.now() - start > timeout) reject(new Error('timed out waiting'));
      else setTimeout(tick, interval);
    };
    tick();
  });
}

export const goodTarget = (token = '0f8e2c1a-1111-4222-8333-444455556666') =>
  b64url(`${SERVER_ROOT}?reconnectionToken=${token}&reconnection=false&skipWebSocketFrames=false`);
