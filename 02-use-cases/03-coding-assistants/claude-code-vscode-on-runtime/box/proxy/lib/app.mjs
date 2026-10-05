// The box's only listener on :8080 (IPv4 and IPv6): GET /ping, POST /invocations, GET /ws (upgrade).

import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import { WebSocketServer } from 'ws';
import { BusyTracker } from './busy.mjs';
import { MAX_REQUEST_BYTES, error, fetchUpstream, handleInvocation } from './invocations.mjs';
import { relay } from './relay.mjs';
import { SESSION_HEADER, parseTarget, sessionFrom, targetFrom } from './target.mjs';

const BROWSER_MAX_MESSAGE = 1024 * 1024;

export function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

// Which address family AgentCore reaches the box from (a spike question). The listener is
// dual-stack, so an IPv4 peer shows up as ::ffff:a.b.c.d.
export function peerFamily(address) {
  if (typeof address !== 'string') return null;
  if (net.isIPv4(address) || /^::ffff:\d+\.\d+\.\d+\.\d+$/i.test(address)) return 'IPv4';
  return net.isIPv6(address) ? 'IPv6' : null;
}

// Header names only; the session id is the one value worth seeing in the spike.
function headersSeen(req) {
  return {
    at: Math.floor(Date.now() / 1000),
    names: Object.keys(req.headers).sort(),
    sessionId: typeof req.headers[SESSION_HEADER] === 'string' ? req.headers[SESSION_HEADER] : null,
    peer: peerFamily(req.socket.remoteAddress),
  };
}

// What a request looked like, for diagnosing how AgentCore delivers /ws. The path and the hop-by-hop
// WebSocket header values are not secret; the query string (it carries the session id) is left out.
function requestShape(req, url) {
  const h = req.headers;
  return {
    ...headersSeen(req), method: req.method, httpVersion: req.httpVersion, path: url ? url.pathname : String(req.url).split('?')[0],
    connection: h.connection ?? null, upgrade: h.upgrade ?? null,
    secWebSocketVersion: h['sec-websocket-version'] ?? null, hasSecWebSocketKey: typeof h['sec-websocket-key'] === 'string',
  };
}

// AgentCore delivers the browser's WebSocket to /ws; accept it on any path that ends in /ws in case
// AgentCore keeps a prefix.
const isWsPath = (pathname) => pathname === '/ws' || pathname.endsWith('/ws');

function sendJson(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(text) });
  res.end(text);
}

export function createApp(config, {
  now = Date.now,
  readState = () => readJson(config.stateFile, {}),
  fetch = (request) => fetchUpstream(config.upstream, request),
  diagExtra = () => ({}),
  pacer = {},
  log = (line) => console.log(`[proxy] ${line}`),
} = {}) {
  const busy = new BusyTracker(now);
  const seen = { invocations: null, ws: null, lastUpgrade: null, lastOther: null };
  const wss = new WebSocketServer({
    noServer: true,
    perMessageDeflate: false,
    handleProtocols: () => false, // AgentCore's edge answers the browser's subprotocol, never the box
    maxPayload: BROWSER_MAX_MESSAGE,
    clientTracking: false,
  });

  // Claude working on a turn keeps the box busy too; the supervisor publishes it in the state file.
  const pingNow = (state = readState()) => {
    busy.agent(state?.agentBusy === true);
    return busy.ping();
  };

  const diag = () => {
    const state = readState();
    return {
      proxy: { pid: process.pid, uid: process.getuid?.(), gid: process.getgid?.(), groups: process.getgroups?.() },
      websockets: { open: busy.open, ping: pingNow(state), agentBusy: busy.agentBusy },
      headersSeen: seen,
      ...diagExtra(state),
    };
  };
  const ctx = { config, readState, fetch, diag };

  const server = http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url, 'http://box');
    } catch {
      res.writeHead(400).end();
      return;
    }
    if (url.pathname === '/ping' && req.method === 'GET') {
      sendJson(res, 200, pingNow());
      return;
    }
    if (url.pathname !== '/invocations') {
      seen.lastOther = requestShape(req, url);
      const o = seen.lastOther;
      log(`unexpected ${o.method} ${o.path} HTTP/${o.httpVersion} (connection: ${o.connection ?? '-'}, upgrade: ${o.upgrade ?? '-'}, ws key: ${o.hasSecWebSocketKey}); answering 404`);
      res.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
      return;
    }
    seen.invocations = headersSeen(req);
    const parts = [];
    let size = 0;
    req.on('data', (part) => {
      size += part.length;
      if (size <= MAX_REQUEST_BYTES) parts.push(part);
    });
    req.on('end', async () => {
      let body;
      try {
        body = size > MAX_REQUEST_BYTES ? error('request too large')
          : await handleInvocation(Buffer.concat(parts).toString('utf8'), req.headers[SESSION_HEADER], ctx);
      } catch (err) {
        log(`invocation failed: ${err.message}`);
        body = error('internal error');
      }
      sendJson(res, 200, body);
    });
  });

  server.on('upgrade', (req, socket, head) => {
    let url;
    try {
      url = new URL(req.url, 'http://box');
    } catch {
      socket.destroy();
      return;
    }
    seen.lastUpgrade = requestShape(req, url);
    if (!isWsPath(url.pathname)) {
      log(`WebSocket upgrade on ${url.pathname} refused (only /ws is served)`);
      socket.end('HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    seen.ws = headersSeen(req);
    wss.handleUpgrade(req, socket, head, (browser) => {
      if (config.sessionId && sessionFrom(req.headers, url.searchParams) !== config.sessionId) {
        browser.close(1008, 'wrong session');
        return;
      }
      const target = parseTarget(targetFrom(req.headers, url.searchParams), config.serverRoot);
      if (!target.ok) {
        browser.close(1008, target.reason);
        return;
      }
      const { host, port } = config.upstream;
      const upstreamUrl = `ws://${host}:${port}${target.path}${target.query ? `?${target.query}` : ''}`;
      relay(browser, upstreamUrl, { busy, pacer, log });
    });
  });

  return { server, busy, seen };
}
