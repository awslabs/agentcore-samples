// Real-browser checks for the pieces only a browser can prove: the patched webview shell (its CSP
// hash, parent check and frame-ancestors), the WebSocket subclass, and the Service Worker. Headless
// Chrome is driven over CDP. Needs the real dist/ (build/build.sh) and Google Chrome.
//
// Ports (localhost): 9490 webview edge site, 9491 test "workbench" pages, 9492 fake AgentCore,
// 9493 Chrome DevTools, 9494 a foreign origin.

import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import http from 'node:http';
import { join } from 'node:path';

import { SERVER_ROOT } from '../../build/pins.mjs';
import { startLocalServer } from '../../src/local-server.mjs';
import { EDGE } from '../fixture.mjs';
import { chromeAvailable, launchChrome } from './cdp.mjs';

const WEBVIEW = 'http://localhost:9490';
const WORKBENCH = 'http://localhost:9491';
const AGENTCORE = 'http://localhost:9492';
const FOREIGN = 'http://localhost:9494';
const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_test-AbC';
const SESSION = `dbx-${'c'.repeat(64)}`;
// Fake tokens, built at run time.
const fakeJwt = (header, payload, signature) => [header, payload, signature].map(s => Buffer.from(s).toString('base64url')).join('.');
const TOKEN = fakeJwt('{"alg":"RS256"}', '{"uid":"00u-test"}', 'signature');
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

const ready = existsSync(join(EDGE, 'dist', 'manifest.json')) && chromeAvailable();
const opts = { skip: ready ? false : 'needs build/build.sh and Google Chrome', timeout: 60000 };

const webFile = rel => readFileSync(join(EDGE, 'web', ...rel.split('/')));

// Pages that stand in for the workbench page (and a foreign site) in these tests.
const PAGES = {
  '/webview-host.html': `<!DOCTYPE html><html><body><script>
window.runWebview = (parentOrigin) => new Promise(resolve => {
  const id = crypto.randomUUID();
  const params = new URLSearchParams({
    id, parentId: '1', origin: crypto.randomUUID(), swVersion: '4', extensionId: 'test.ext', platform: 'browser',
    'vscode-resource-base-authority': 'vscode-resource.vscode-cdn.net', parentOrigin: parentOrigin || location.origin,
    remoteAuthority: location.host,
  });
  const frame = document.createElement('iframe');
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-pointer-lock allow-downloads');
  frame.src = '${WEBVIEW}${SERVER_ROOT}/static/out/vs/workbench/contrib/webview/browser/pre/index.html?' + params;
  const result = { ready: false, channels: [] };
  const timer = setTimeout(() => resolve(result), 6000);
  window.addEventListener('message', e => {
    if (!e.data || e.data.target !== id || e.data.channel !== 'webview-ready') return;
    result.ready = true;
    result.origin = e.origin;
    const port = e.ports[0];
    port.onmessage = m => {
      result.channels.push(m.data.channel);
      if (m.data.channel === 'updated-intrinsic-content-size') { clearTimeout(timer); resolve(result); }
    };
    port.postMessage({ channel: 'content', args: {
      contents: '<html><body><p>hello from the webview</p></body></html>',
      options: { allowScripts: false }, state: undefined, cspSource: '', confirmBeforeClose: 'never',
    } });
  });
  document.body.appendChild(frame);
});
</script></body></html>`,
  '/shim-host.html': `<!DOCTYPE html><html><head><script src="/devbox/shim.js"></script><script>
window.__devbox = { getToken: () => ${JSON.stringify(TOKEN)}, sessionId: ${JSON.stringify(SESSION)}, runtimeArn: ${JSON.stringify(ARN)},
  agentcoreBase: '${AGENTCORE}', commit: 'x', serverRoot: '${SERVER_ROOT}' };
window.runShim = async () => {
  const ws = new WebSocket('ws://' + location.host + '${SERVER_ROOT}?reconnectionToken=abc-123&reconnection=false&skipWebSocketFrames=false');
  const out = { instanceOfWebSocket: ws instanceof WebSocket, url: ws.url, binaryType: ws.binaryType };
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('socket error')); });
  out.protocol = ws.protocol;
  const big = new Uint8Array(100000);
  for (let i = 0; i < big.length; i++) big[i] = i % 251;
  const reply = new Promise(resolve => ws.addEventListener('message', e => resolve(e.data), { once: true }));
  ws.send(big);
  ws.send(new Blob([new Uint8Array(40000).fill(7)]));
  out.bufferedAfterSend = ws.bufferedAmount;
  const data = await reply;
  out.replyIsBlob = data instanceof Blob;
  out.replyBytes = Array.from(new Uint8Array(await data.arrayBuffer()));
  const closed = new Promise(resolve => ws.onclose = e => resolve({ code: e.code, reason: e.reason }));
  ws.close(1000, 'done');
  out.close = await closed;
  return out;
};
</script></head><body></body></html>`,
  '/sw-host.html': `<!DOCTYPE html><html><body><script>
const R = '${SERVER_ROOT}/vscode-remote-resource?path=%2Fmnt%2Fworkspace%2Fprojects%2Fdot.png';
window.runSw = async () => {
  await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  if (!navigator.serviceWorker.controller) {
    await new Promise(r => navigator.serviceWorker.addEventListener('controllerchange', r, { once: true }));
  }
  navigator.serviceWorker.controller.postMessage({ type: 'devbox-token', token: ${JSON.stringify(TOKEN)}, sessionId: ${JSON.stringify(SESSION)},
    runtimeArn: ${JSON.stringify(ARN)}, agentcoreBase: '${AGENTCORE}', serverRoot: '${SERVER_ROOT}' });
  await new Promise(r => setTimeout(r, 200));
  const res = await fetch(R);
  const out = { status: res.status, type: res.headers.get('content-type'), cacheControl: res.headers.get('cache-control'), bytes: (await res.arrayBuffer()).byteLength };
  const img = new Image();
  img.src = R + '&img=1';
  await img.decode();
  out.imageWidth = img.naturalWidth;
  out.passThrough = await (await fetch('/plain.txt')).text();
  const frame = document.createElement('iframe');
  frame.src = R + '&as=page';
  await new Promise(r => { frame.onload = r; document.body.appendChild(frame); });
  out.asPage = frame.contentDocument.body.textContent;
  return out;
};
</script></body></html>`,
};

function pageServer(origin) {
  return http.createServer((req, res) => {
    const path = new URL(req.url, origin).pathname;
    if (PAGES[path]) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(PAGES[path]);
    }
    if (path === '/devbox/shim.js') {
      res.writeHead(200, { 'content-type': 'text/javascript' });
      return res.end(webFile('devbox/shim.js'));
    }
    if (path === '/sw.js') {
      // The headers the edge sends with /sw.js.
      res.writeHead(200, {
        'content-type': 'text/javascript', 'service-worker-allowed': '/', 'cache-control': 'no-cache',
        'content-security-policy': `default-src 'none'; connect-src ${AGENTCORE}`,
      });
      return res.end(webFile('sw.js'));
    }
    if (path === '/plain.txt') {
      res.writeHead(200, { 'content-type': 'text/plain' });
      return res.end('served by the network');
    }
    res.writeHead(404);
    res.end();
  });
}

// A stand-in for the AgentCore data plane: CORS, op: http, and a WebSocket that answers the
// subprotocol the way AgentCore does and records the frames it gets.
const seen = { invocations: [], handshake: null, frames: [] };

function fakeAgentCore() {
  const cors = {
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'POST',
    'access-control-allow-headers': 'authorization, content-type, accept, x-amzn-bedrock-agentcore-runtime-session-id',
  };
  const server = http.createServer((req, res) => {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, cors);
      return res.end();
    }
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      seen.invocations.push({ url: req.url, authorization: req.headers.authorization, session: req.headers['x-amzn-bedrock-agentcore-runtime-session-id'], body: JSON.parse(body) });
      res.writeHead(200, { ...cors, 'content-type': 'application/json' });
      res.end(JSON.stringify({ v: 1, ok: true, status: 200, headers: { 'content-type': 'image/png' }, bodyB64: PNG.toString('base64') }));
    });
  });
  server.on('upgrade', (req, socket) => {
    const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
    seen.handshake = { url: req.url, protocols: (req.headers['sec-websocket-protocol'] || '').split(',').map(s => s.trim()) };
    socket.write(['HTTP/1.1 101 Switching Protocols', 'Upgrade: websocket', 'Connection: Upgrade',
      `Sec-WebSocket-Accept: ${accept}`, 'Sec-WebSocket-Protocol: base64UrlBearerAuthorization', '', ''].join('\r\n'));
    let buf = Buffer.alloc(0);
    let received = 0;
    socket.on('data', chunk => {
      buf = Buffer.concat([buf, chunk]);
      for (;;) {
        if (buf.length < 2) return;
        const opcode = buf[0] & 0x0f;
        const fin = Boolean(buf[0] & 0x80);
        let len = buf[1] & 0x7f;
        let off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        const maskAt = off;
        off += 4;
        if (buf.length < off + len) return;
        const mask = buf.subarray(maskAt, maskAt + 4);
        const payload = Buffer.from(buf.subarray(off, off + len).map((b, i) => b ^ mask[i % 4]));
        buf = buf.subarray(off + len);
        if (opcode === 8) {
          socket.end(Buffer.concat([Buffer.from([0x88, payload.length]), payload]));
          return;
        }
        seen.frames.push({ opcode, fin, len });
        received += len;
        if (received === 140000) socket.write(Buffer.from([0x82, 3, 1, 2, 3]));
      }
    });
    socket.on('error', () => {});
  });
  return server;
}

const listen = (server, port) => new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(port, '127.0.0.1', resolve);
});

let chrome;
const servers = [];
let edge;

before(async () => {
  if (!ready) return;
  const config = {
    commit: SERVER_ROOT.slice('/stable-'.length), serverRoot: SERVER_ROOT, agentcoreBase: AGENTCORE,
    okta: { issuer: 'http://localhost:9400/oauth2/default', clientId: 'test' }, webviewOrigin: WEBVIEW, boxes: {},
  };
  edge = await startLocalServer({
    site: 'webview', port: 9490, host: '127.0.0.1',
    env: { WORKBENCH_ORIGIN: WORKBENCH, WEBVIEW_ORIGIN: WEBVIEW, DEVBOX_CONFIG_JSON: JSON.stringify(config) },
  });
  for (const [server, port] of [[pageServer(WORKBENCH), 9491], [fakeAgentCore(), 9492], [pageServer(FOREIGN), 9494]]) {
    await listen(server, port);
    servers.push(server);
  }
  chrome = await launchChrome({ port: 9493 });
});

after(async () => {
  await chrome?.close();
  await edge?.close();
  for (const s of servers) {
    s.closeAllConnections?.();
    await new Promise(r => s.close(r));
  }
});

function dumpLogsOnFailure(fn) {
  return async () => {
    try {
      await fn();
    } catch (err) {
      console.log(chrome.logs.join('\n'));
      throw err;
    }
  };
}

describe('patched webview shell in Chrome', () => {
  test('starts for the workbench origin and renders content through fake.html', opts, dumpLogsOnFailure(async () => {
    await chrome.navigate(`${WORKBENCH}/webview-host.html`);
    const r = await chrome.evaluate('runWebview()');
    assert.equal(r.ready, true, 'webview-ready arrived: CSP hash accepted, parent check passed');
    assert.equal(r.origin, WEBVIEW);
    assert.ok(r.channels.includes('updated-intrinsic-content-size'), `content rendered (got ${r.channels.join(', ')})`);
    assert.ok(!chrome.logs.some(l => /Content Security Policy|Refused/.test(l)), chrome.logs.join('\n'));
  }));

  test('does not start for any other parent origin', opts, dumpLogsOnFailure(async () => {
    chrome.logs.length = 0;
    await chrome.navigate(`${WORKBENCH}/webview-host.html`);
    const r = await chrome.evaluate(`runWebview(${JSON.stringify(FOREIGN)})`);
    assert.equal(r.ready, false);
    assert.ok(chrome.logs.some(l => l.includes(`Webview parent '${FOREIGN}' is not the dev box workbench.`)), chrome.logs.join('\n'));
  }));

  test('cannot be framed by a foreign site', opts, dumpLogsOnFailure(async () => {
    chrome.logs.length = 0;
    await chrome.navigate(`${FOREIGN}/webview-host.html`);
    const r = await chrome.evaluate('runWebview()');
    assert.equal(r.ready, false);
    assert.ok(chrome.logs.some(l => /frame-ancestors/.test(l)), `expected a frame-ancestors refusal:\n${chrome.logs.join('\n')}`);
  }));
});

describe('WebSocket shim in Chrome', () => {
  test('connects to AgentCore with the token subprotocol and splits outgoing data', opts, dumpLogsOnFailure(async () => {
    await chrome.navigate(`${WORKBENCH}/shim-host.html`);
    const r = await chrome.evaluate('runShim()');
    assert.equal(r.instanceOfWebSocket, true);
    assert.equal(r.url, `ws://localhost:9491${SERVER_ROOT}?reconnectionToken=abc-123&reconnection=false&skipWebSocketFrames=false`);
    assert.equal(r.binaryType, 'blob');
    assert.equal(r.protocol, '');
    assert.equal(r.replyIsBlob, true);
    assert.deepEqual(r.replyBytes, [1, 2, 3]);
    assert.deepEqual(r.close, { code: 1000, reason: 'done' });

    const u = new URL(seen.handshake.url, AGENTCORE);
    assert.equal(u.pathname, `/runtimes/${encodeURIComponent(ARN)}/ws`);
    assert.equal(u.searchParams.get('qualifier'), 'DEFAULT');
    assert.equal(u.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'), SESSION);
    const vscodePath = Buffer.from(u.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath'), 'base64url').toString();
    assert.equal(vscodePath, `${SERVER_ROOT}?reconnectionToken=abc-123&reconnection=false&skipWebSocketFrames=false`);
    assert.ok(!seen.handshake.url.includes(TOKEN));
    assert.deepEqual(seen.handshake.protocols, [`base64UrlBearerAuthorization.${Buffer.from(TOKEN).toString('base64url')}`, 'base64UrlBearerAuthorization']);

    assert.ok(seen.frames.every(f => f.opcode === 2 && f.fin && f.len <= 32000), JSON.stringify(seen.frames));
    assert.equal(seen.frames.reduce((n, f) => n + f.len, 0), 140000);
  }));
});

describe('Service Worker in Chrome', () => {
  test('answers vscode-remote-resource through AgentCore and leaves the rest alone', opts, dumpLogsOnFailure(async () => {
    await chrome.navigate(`${WORKBENCH}/sw-host.html`);
    const r = await chrome.evaluate('runSw()');
    assert.equal(r.status, 200);
    assert.equal(r.type, 'image/png');
    assert.equal(r.cacheControl, 'no-store');
    assert.equal(r.bytes, PNG.length);
    assert.equal(r.imageWidth, 1, 'an <img> works through the worker');
    assert.equal(r.passThrough, 'served by the network');
    assert.match(r.asPage, /not opened as pages/);

    assert.ok(seen.invocations.length >= 2);
    for (const call of seen.invocations) {
      assert.equal(call.url, `/runtimes/${encodeURIComponent(ARN)}/invocations?qualifier=DEFAULT`);
      assert.equal(call.authorization, `Bearer ${TOKEN}`);
      assert.equal(call.session, SESSION);
      assert.equal(call.body.op, 'http');
      assert.equal(call.body.path, `${SERVER_ROOT}/vscode-remote-resource`);
    }
  }));
});
