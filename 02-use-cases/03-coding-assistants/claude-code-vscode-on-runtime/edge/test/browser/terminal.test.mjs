// The /terminal page in headless Chrome: the real edge handler, page, loader, Service Worker, xterm.js
// from the pinned bundle and terminal.js, against a small stand-in for AgentCore that speaks the shell
// WebSocket protocol (/ws/shells): it confirms the shell (reconnected true when the shellId's PTY is still
// alive), echoes STDIN back on STDOUT like a PTY, echoes heartbeats, records every frame, and can drop the
// connection or end the shell on cue.
//
// Needs: build/build.sh (dist/) and Google Chrome. Ports (localhost): 9482 Chrome DevTools, 9483 fake Okta,
// 9484 fake AgentCore, 9485 workbench site (9486 is only the webview origin in the config; nothing listens).

import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import http from 'node:http';
import { join } from 'node:path';

import { OVS, SERVER_ROOT } from '../../build/pins.mjs';
import { startLocalServer } from '../../src/local-server.mjs';
import { EDGE } from '../fixture.mjs';
import { chromeAvailable, launchChrome } from './cdp.mjs';

const OKTA = 'http://localhost:9483';
const AGENTCORE = 'http://localhost:9484';
const WORKBENCH = 'http://localhost:9485';
const WEBVIEW = 'http://localhost:9486';
const UID = '00u-edge-terminal-test';
const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_term-Test1';
const GENERATION = 3;

const sha = t => createHash('sha256').update(t).digest('hex');
const SESSION = `dbx-${sha(`${UID}:${GENERATION}`)}`;
const b64url = obj => Buffer.from(JSON.stringify(obj)).toString('base64url');
const fakeJwt = claims => `${b64url({ alg: 'none' })}.${b64url(claims)}.c2ln`;

const skip = !chromeAvailable() ? 'needs Google Chrome' : !existsSync(join(EDGE, 'dist', 'manifest.json')) ? 'needs build/build.sh' : false;

const seen = { tokens: [], status: 0, refused: [] };

function fakeOkta() {
  const codes = new Map();
  return http.createServer((req, res) => {
    const url = new URL(req.url, OKTA);
    if (url.pathname === '/oauth2/default/v1/authorize') {
      const p = url.searchParams;
      const code = randomBytes(12).toString('hex');
      codes.set(code, { nonce: p.get('nonce'), challenge: p.get('code_challenge') });
      res.writeHead(302, { location: `${p.get('redirect_uri')}#code=${code}&state=${encodeURIComponent(p.get('state'))}` });
      return res.end();
    }
    if (url.pathname === '/oauth2/default/v1/token' && req.method === 'POST') {
      let body = '';
      req.on('data', c => { body += c; });
      req.on('end', () => {
        const form = new URLSearchParams(body);
        const cors = { 'access-control-allow-origin': req.headers.origin || '*', 'content-type': 'application/json' };
        const grant = codes.get(form.get('code'));
        const pkceOk = grant && createHash('sha256').update(form.get('code_verifier') || '').digest('base64url') === grant.challenge;
        if (form.get('grant_type') === 'authorization_code' && !pkceOk) {
          res.writeHead(400, cors);
          return res.end(JSON.stringify({ error: 'invalid_grant' }));
        }
        const now = Math.floor(Date.now() / 1000);
        const access = fakeJwt({ uid: UID, scp: ['devbox'], groups: ['devbox-users'], aud: 'api://default', exp: now + 3600, jti: randomBytes(6).toString('hex') });
        seen.tokens.push(access);
        res.writeHead(200, cors);
        return res.end(JSON.stringify({
          token_type: 'Bearer', expires_in: 3600, access_token: access,
          id_token: fakeJwt({ sub: UID, nonce: grant?.nonce, exp: now + 3600 }), refresh_token: randomBytes(16).toString('hex'),
        }));
      });
      return undefined;
    }
    res.writeHead(404);
    return res.end();
  });
}

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'POST',
  'access-control-allow-headers': 'authorization, content-type, accept, x-amzn-bedrock-agentcore-runtime-session-id',
};

function wsFrame(opcode, payload) {
  const head = payload.length < 126 ? Buffer.from([0x80 | opcode, payload.length])
    : payload.length < 65536 ? Buffer.from([0x80 | opcode, 126, payload.length >> 8, payload.length & 255])
      : (() => { const h = Buffer.alloc(10); h[0] = 0x80 | opcode; h[1] = 127; h.writeBigUInt64BE(BigInt(payload.length), 2); return h; })();
  return Buffer.concat([head, payload]);
}

const shellFrame = (channel, payload = Buffer.alloc(0)) => wsFrame(2, Buffer.concat([Buffer.from([channel]), Buffer.from(payload)]));
const statusFrame = obj => shellFrame(3, Buffer.from(JSON.stringify(obj)));

// AgentCore's side: /invocations for op: status, and /ws/shells.
const shells = new Map();       // shellId -> { alive }
const conns = [];               // every accepted shell connection
function fakeAgentCore() {
  const server = http.createServer((req, res) => {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, CORS);
      return res.end();
    }
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', () => {
      if (!/^Bearer ey/.test(req.headers.authorization || '')) { res.writeHead(401, CORS); return res.end(); }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      res.writeHead(200, { ...CORS, 'content-type': 'application/json' });
      if (body.op !== 'status') return res.end(JSON.stringify({ v: 1, ok: false, error: 'unknown op' }));
      seen.status++;
      // A cold start for the first two answers. Then VS Code is still starting, and even reports another
      // build: the terminal needs neither.
      const cold = seen.status <= 2;
      return res.end(JSON.stringify({
        v: 1, ok: true, owner: 'term', sessionId: req.headers['x-amzn-bedrock-agentcore-runtime-session-id'], volume: cold ? 'waiting' : 'mounted',
        vscode: cold ? 'waiting' : 'starting', commit: 'not-the-page-build', serverStartId: 's-1', lastSession: null, signedIn: false,
      }));
    });
    return undefined;
  });
  server.on('upgrade', (req, socket) => {
    const url = new URL(req.url, AGENTCORE);
    const protocols = (req.headers['sec-websocket-protocol'] || '').split(',').map(s => s.trim());
    const token = protocols[0]?.startsWith('base64UrlBearerAuthorization.')
      ? Buffer.from(protocols[0].slice('base64UrlBearerAuthorization.'.length), 'base64url').toString('utf8') : '';
    const refuse = (status, why) => {
      seen.refused.push(why);
      socket.end(`HTTP/1.1 ${status} Refused\r\nContent-Length: 0\r\n\r\n`);
    };
    if (url.pathname !== `/runtimes/${encodeURIComponent(ARN)}/ws/shells`) return refuse(404, `path ${url.pathname}`);
    if (!seen.tokens.includes(token) || protocols[1] !== 'base64UrlBearerAuthorization') return refuse(403, 'token');
    if (url.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id') !== SESSION) return refuse(400, 'session');
    const shellId = url.searchParams.get('shellId');
    if (!/^[^?#&]{1,128}$/.test(shellId || '')) return refuse(400, 'shellId');

    const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
    socket.write(['HTTP/1.1 101 Switching Protocols', 'Upgrade: websocket', 'Connection: Upgrade',
      `Sec-WebSocket-Accept: ${accept}`, 'Sec-WebSocket-Protocol: base64UrlBearerAuthorization', '', ''].join('\r\n'));
    const reconnected = Boolean(shells.get(shellId)?.alive);
    if (!reconnected) shells.set(shellId, { alive: true });
    const conn = { url: req.url, protocols, shellId, reconnected, frames: [], socket, closedByServer: false };
    conns.push(conn);
    socket.write(statusFrame({ kind: 'Status', apiVersion: 'v1', metadata: { shellId, reconnected }, status: 'Success' }));
    if (!reconnected) socket.write(shellFrame(2, Buffer.from('shell ready (AgentCore note)\n')));

    let buf = Buffer.alloc(0);
    socket.on('data', chunk => {
      buf = Buffer.concat([buf, chunk]);
      for (;;) {
        if (buf.length < 2) return;
        const opcode = buf[0] & 0x0f;
        let len = buf[1] & 0x7f;
        let off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (buf.length < off + 4 + len) return;
        const mask = buf.subarray(off, off + 4);
        const payload = Buffer.from(buf.subarray(off + 4, off + 4 + len).map((b, i) => b ^ mask[i % 4]));
        buf = buf.subarray(off + 4 + len);
        if (opcode === 8) { socket.end(wsFrame(8, payload.subarray(0, 2))); return; }
        if (opcode === 9) { socket.write(wsFrame(10, payload)); continue; }
        if (opcode !== 2 || payload.length === 0) continue;
        const channel = payload[0];
        const data = payload.subarray(1);
        conn.frames.push({ channel, data: Buffer.from(data), opcode });
        // A PTY in echo mode: what is typed comes back, Enter as a new line.
        if (channel === 0) socket.write(shellFrame(1, Buffer.from(data.toString('utf8').replace(/\r/g, '\r\n'))));
        if (channel === 5) socket.write(shellFrame(5));
      }
    });
    socket.on('error', () => {});
    return undefined;
  });
  return server;
}

const stdinOf = conn => conn.frames.filter(f => f.channel === 0).map(f => f.data.toString('utf8')).join('');
const resizesOf = conn => conn.frames.filter(f => f.channel === 4).map(f => JSON.parse(f.data.toString('utf8')));

const servers = [];
let edge;
let chrome;

before(async () => {
  if (skip) return;
  const config = {
    region: 'us-east-1', commit: OVS.commit, serverRoot: SERVER_ROOT, agentcoreBase: AGENTCORE,
    okta: { issuer: `${OKTA}/oauth2/default`, clientId: 'terminal-test', scopes: 'openid profile email offline_access devbox' },
    webviewOrigin: WEBVIEW,
    boxes: { [sha(UID)]: { name: 'term', runtimeArn: ARN, generation: GENERATION } },
  };
  edge = await startLocalServer({
    site: 'workbench', port: 9485, host: '127.0.0.1',
    env: { WORKBENCH_ORIGIN: WORKBENCH, WEBVIEW_ORIGIN: WEBVIEW, DEVBOX_CONFIG_JSON: JSON.stringify(config) },
  });
  for (const [server, port] of [[fakeOkta(), 9483], [fakeAgentCore(), 9484]]) {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
    servers.push(server);
  }
  chrome = await launchChrome({ port: 9482 });
  await chrome.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 700, deviceScaleFactor: 1, mobile: false });
});

after(async () => {
  await chrome?.close();
  await edge?.close();
  for (const c of conns) c.socket.destroy();
  for (const s of servers) {
    s.closeAllConnections?.();
    await new Promise(r => s.close(r));
  }
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function until(fn, ms, what) {
  for (let t = 0; t < ms; t += 100) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      // the page is navigating
    }
    await sleep(100);
  }
  if (chrome) console.log(chrome.logs.join('\n'));
  throw new Error(`timed out waiting for ${what}`);
}

const screenText = () => chrome.evaluate(`document.querySelector('#devbox-term-screen .xterm-rows')?.innerText ?? ''`);

async function pressEnter() {
  const k = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 };
  await chrome.send('Input.dispatchKeyEvent', { type: 'keyDown', ...k, text: '\r' });
  await chrome.send('Input.dispatchKeyEvent', { type: 'keyUp', ...k });
}

test('/terminal signs in, waits for the box, and starts Claude Code in AgentCore\'s terminal', { skip, timeout: 90000 }, async () => {
  await chrome.send('Page.navigate', { url: `${WORKBENCH}/terminal` });
  const overlay = await until(() => chrome.evaluate(`(() => {
    const modes = document.querySelector('#devbox-overlay #devbox-modes');
    if (!modes || modes.hidden || !document.getElementById('devbox-states') || document.getElementById('devbox-states').hidden) return null;
    return {
      path: location.pathname,
      modes: [...modes.querySelectorAll('.devbox-modes > *')].map(e => e.tagName + ':' + e.textContent + ':' + (e.getAttribute('href') || e.getAttribute('aria-current'))),
      status: document.getElementById('devbox-status').textContent,
      hint: document.getElementById('devbox-hint').textContent,
    };
  })()`), 20000, 'the loader overlay while the box starts');
  assert.equal(overlay.path, '/terminal');
  assert.deepEqual(overlay.modes, ['SPAN:Terminal:page', 'A:VS Code:/'], 'the Terminal · VS Code switch in the overlay');
  assert.match(overlay.status, /^Starting your dev box \(term\)/);
  assert.match(overlay.hint, /sign-in URL and a code in the terminal/);
  assert.doesNotMatch(overlay.hint, /New Terminal/);
  await until(() => conns.length >= 1 && stdinOf(conns[0]).includes('exec /usr/local/bin/devbox-claude'), 30000, 'exec /usr/local/bin/devbox-claude on the first connection');
  await until(async () => (await screenText()).includes('exec /usr/local/bin/devbox-claude'), 10000, 'the echo in xterm');

  const first = conns[0];
  const u = new URL(first.url, AGENTCORE);
  assert.equal(u.pathname, `/runtimes/${encodeURIComponent(ARN)}/ws/shells`);
  assert.deepEqual([...u.searchParams.entries()], [['qualifier', 'DEFAULT'], ['shellId', `claude-${GENERATION}`], ['X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', SESSION]]);
  assert.ok(!/eyJ/.test(first.url), 'no token in the URL');
  assert.equal(first.protocols.length, 2);
  assert.equal(first.protocols[1], 'base64UrlBearerAuthorization');
  assert.ok(seen.tokens.includes(Buffer.from(first.protocols[0].split('.').slice(1).join('.'), 'base64url').toString()), 'the Okta access token, in the subprotocol');
  assert.equal(first.reconnected, false);
  assert.ok(first.frames.every(f => f.opcode === 2), 'every message is binary');
  assert.equal(first.frames[0].channel, 4, 'the first thing sent is the size');
  assert.equal(stdinOf(first), 'exec /usr/local/bin/devbox-claude\r', 'sent exactly once');
  const [size] = resizesOf(first);
  assert.ok(size.width > 60 && size.height > 15, `a fitted size, got ${JSON.stringify(size)}`);

  const page = await chrome.evaluate(`({
    path: location.pathname,
    overlay: Boolean(document.getElementById('devbox-overlay')),
    title: document.title,
    workbench: Boolean(document.querySelector('.monaco-workbench')) || typeof globalThis._VSCODE_FILE_ROOT !== 'undefined',
    modes: [...document.querySelectorAll('.devbox-term-bar .devbox-modes > *')].map(e => ({ tag: e.tagName, text: e.textContent, href: e.getAttribute('href'), current: e.getAttribute('aria-current') })),
    cols: (() => { const t = document.querySelector('#devbox-term-screen .xterm-rows > div'); return t ? t.getBoundingClientRect().width : 0; })(),
    screen: document.getElementById('devbox-term-screen').getBoundingClientRect().width,
    dim: [...document.querySelectorAll('#devbox-term-screen .xterm-rows span')].filter(s => /xterm-dim/.test(s.className)).map(s => s.textContent).join(''),
    focused: document.activeElement?.className ?? '',
    storage: JSON.stringify({ ...sessionStorage }) + JSON.stringify({ ...localStorage }),
    awsHint: document.getElementById('devbox-aws-hint')?.textContent ?? null,
  })`);
  assert.equal(page.path, '/terminal', 'back on /terminal after the Okta redirect to /callback');
  assert.equal(page.overlay, false);
  assert.equal(page.title, 'Claude Code · Dev box');
  assert.equal(page.workbench, false, 'VS Code is never loaded');
  assert.deepEqual(page.modes, [
    { tag: 'SPAN', text: 'Terminal', href: null, current: 'page' },
    { tag: 'A', text: 'VS Code', href: '/', current: null },
  ]);
  assert.match(page.dim, /shell ready \(AgentCore note\)/, 'STDERR is shown dimmed');
  assert.match(page.focused, /xterm-helper-textarea/, 'the terminal has the keyboard');
  assert.ok(!/eyJ/.test(page.storage), 'no token in sessionStorage or localStorage');
  assert.match(page.awsHint ?? '', /Claude Code shows a sign-in URL and a code in this terminal/);
  assert.ok(seen.status >= 1);
});

test('typed text goes to the shell and its echo appears in xterm', { skip, timeout: 30000 }, async () => {
  await chrome.send('Input.insertText', { text: 'echo hello-from-xterm ✓' });
  await until(() => stdinOf(conns[0]).includes('echo hello-from-xterm ✓'), 10000, 'the typed text at the shell');
  await until(async () => (await screenText()).includes('echo hello-from-xterm ✓'), 10000, 'the typed text on screen');
});

test('a window resize refits the terminal and sends RESIZE', { skip, timeout: 30000 }, async () => {
  const before = resizesOf(conns[0]).at(-1);
  await chrome.send('Emulation.setDeviceMetricsOverride', { width: 700, height: 420, deviceScaleFactor: 1, mobile: false });
  const after = await until(() => {
    const last = resizesOf(conns[0]).at(-1);
    return last.width < before.width && last.height < before.height && last;
  }, 10000, 'a smaller RESIZE');
  const fitted = await chrome.evaluate(`(() => {
    const screen = document.getElementById('devbox-term-screen');
    const rows = document.querySelector('#devbox-term-screen .xterm-screen').getBoundingClientRect();
    return { screenW: screen.clientWidth, screenH: screen.clientHeight, termW: rows.width, termH: rows.height };
  })()`);
  assert.ok(after.width >= 40 && after.height >= 10, JSON.stringify(after));
  assert.ok(fitted.termW <= fitted.screenW && fitted.termH <= fitted.screenH, `the terminal fits its box: ${JSON.stringify(fitted)}`);
  assert.ok(fitted.screenW - fitted.termW < 40, `and uses its width: ${JSON.stringify(fitted)}`);
});

test('a connection the server drops reattaches to the same shell (reconnected), repaints, and does not start Claude Code again', { skip, timeout: 60000 }, async () => {
  const first = conns[0];
  first.socket.destroy();   // the browser sees 1006
  await until(() => conns.length >= 2 && resizesOf(conns[1]).length >= 2, 20000, 'the reattach');
  const second = conns[1];
  assert.equal(second.reconnected, true);
  assert.equal(second.shellId, first.shellId);
  const [nudge, size] = resizesOf(second);
  assert.equal(nudge.width, size.width);
  assert.equal(Math.abs(nudge.height - size.height), 1, 'one row away and back, so tmux repaints');
  assert.ok(!stdinOf(second).includes('exec /usr/local/bin/devbox-claude'), 'Claude Code is not started a second time');
  assert.equal(second.protocols[1], 'base64UrlBearerAuthorization');

  await chrome.send('Input.insertText', { text: 'still-here' });
  await until(() => stdinOf(second).includes('still-here'), 10000, 'typing on the new connection');
  await until(async () => (await screenText()).includes('still-here'), 10000, 'its echo on screen');
});

test('when the shell ends, the page says so, and Enter starts a new shell with Claude Code', { skip, timeout: 60000 }, async () => {
  const second = conns[1];
  shells.get(second.shellId).alive = false;
  second.socket.write(statusFrame({ kind: 'Status', apiVersion: 'v1', metadata: {}, status: 'Failure', reason: 'NonZeroExitCode',
    message: 'command terminated with non-zero exit code', details: { causes: [{ reason: 'ExitCode', message: '3' }] } }));
  second.socket.end(wsFrame(8, Buffer.from([0x03, 0xe8])));
  await until(async () => (await screenText()).includes('The terminal ended (exit 3). Press Enter to start a new one.'), 10000, 'the ended message');
  await sleep(1500);
  assert.equal(conns.length, 2, 'no reconnect until Enter');
  assert.match(await chrome.evaluate(`document.getElementById('devbox-term-state').textContent`), /Ended/);

  await pressEnter();
  await until(() => conns.length >= 3 && stdinOf(conns[2]).includes('exec /usr/local/bin/devbox-claude'), 20000, 'a new shell');
  assert.equal(conns[2].reconnected, false);
  assert.equal(stdinOf(conns[2]), 'exec /usr/local/bin/devbox-claude\r');
  assert.equal(await chrome.evaluate(`document.getElementById('devbox-term-state').textContent`), 'Connected');
});

test('no CSP violations, nothing failed to load, no handshake refused', { skip, timeout: 10000 }, async () => {
  const csp = chrome.logs.filter(l => /Content Security Policy|Refused to/.test(l));
  const failed = chrome.logs.filter(l => /Failed to load resource/.test(l));
  if (csp.length || failed.length) console.log(chrome.logs.join('\n'));
  assert.deepEqual(csp, []);
  assert.deepEqual(failed, []);
  assert.deepEqual(seen.refused, []);
});
