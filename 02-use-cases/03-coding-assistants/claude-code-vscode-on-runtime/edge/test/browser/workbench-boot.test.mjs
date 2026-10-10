// Boots the real VS Code workbench through the edge: our page, CSP, loader, shim and Service Worker,
// against the real openvscode-server 1.109.5 (from the verified tarball) in a throwaway container.
// The Okta and AgentCore parts are minimal stand-ins written for this test (local-test/ has the
// faithful ones); they only need to be good enough to prove the edge side works with real VS Code.
//
// Needs: build/build.sh (dist/ and the extracted tarball in .cache/), Docker with debian:bookworm-slim
// (arm64), Google Chrome. Ports (localhost): 9488 Chrome DevTools, 9495 openvscode-server,
// 9496 fake Okta, 9497 fake AgentCore, 9498 workbench site, 9499 webview site.

import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { existsSync, readdirSync } from 'node:fs';
import http from 'node:http';
import { join } from 'node:path';

import { OVS, SERVER_ROOT } from '../../build/pins.mjs';
import { startLocalServer } from '../../src/local-server.mjs';
import { EDGE } from '../fixture.mjs';
import { chromeAvailable, launchChrome } from './cdp.mjs';

const OVS_PORT = 9495;
const OKTA = 'http://localhost:9496';
const AGENTCORE = 'http://localhost:9497';
const WORKBENCH = 'http://localhost:9498';
const WEBVIEW = 'http://localhost:9499';
const UID = '00u-edge-boot-test';
const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_boot-Test1';
const CONTAINER = 'devbox-edge-boot-test';

const sha = t => createHash('sha256').update(t).digest('hex');
const b64url = obj => Buffer.from(JSON.stringify(obj)).toString('base64url');
const fakeJwt = claims => `${b64url({ alg: 'none' })}.${b64url(claims)}.c2ln`;

function ovsDir() {
  const cache = join(EDGE, '.cache');
  if (!existsSync(cache)) return null;
  for (const d of readdirSync(cache)) {
    if (!d.startsWith('ovs-')) continue;
    for (const inner of readdirSync(join(cache, d))) if (existsSync(join(cache, d, inner, 'bin', 'openvscode-server'))) return join(cache, d, inner);
  }
  return null;
}

const dockerOk = spawnSync('docker', ['image', 'inspect', 'debian:bookworm-slim'], { stdio: 'ignore' }).status === 0;
const OVS_DIR = ovsDir();
const skip = !chromeAvailable() ? 'needs Google Chrome'
  : !existsSync(join(EDGE, 'dist', 'manifest.json')) || !OVS_DIR ? 'needs build/build.sh'
    : !dockerOk ? 'needs Docker with debian:bookworm-slim' : false;

const seen = { authorize: [], token: [], revoke: [], logout: [], status: 0, http: [], sockets: 0, maxFrame: 0, serverStartId: 'start-1', signedIn: false };

function fakeOkta() {
  const codes = new Map();
  return http.createServer((req, res) => {
    const url = new URL(req.url, OKTA);
    if (url.pathname === '/oauth2/default/v1/authorize') {
      const p = url.searchParams;
      seen.authorize.push({ prompt: p.get('prompt'), mode: p.get('response_mode'), method: p.get('code_challenge_method') });
      const code = randomBytes(12).toString('hex');
      codes.set(code, { nonce: p.get('nonce'), challenge: p.get('code_challenge'), redirect: p.get('redirect_uri') });
      res.writeHead(302, { location: `${p.get('redirect_uri')}#code=${code}&state=${encodeURIComponent(p.get('state'))}` });
      return res.end();
    }
    if ((url.pathname === '/oauth2/default/v1/revoke' || url.pathname === '/oauth2/default/v1/logout') && req.method === 'POST') {
      let body = '';
      req.on('data', c => { body += c; });
      req.on('end', () => {
        const form = Object.fromEntries(new URLSearchParams(body));
        if (url.pathname.endsWith('/revoke')) {
          seen.revoke.push({ form, query: url.search });
          res.writeHead(200, { 'access-control-allow-origin': req.headers.origin || '*' });
          return res.end();
        }
        seen.logout.push({ form, query: url.search });
        res.writeHead(302, { location: form.post_logout_redirect_uri });
        return res.end();
      });
      return undefined;
    }
    if (url.pathname === '/oauth2/default/v1/token' && req.method === 'POST') {
      let body = '';
      req.on('data', c => { body += c; });
      req.on('end', () => {
        const form = new URLSearchParams(body);
        const cors = { 'access-control-allow-origin': req.headers.origin || '*', 'content-type': 'application/json' };
        const grant = codes.get(form.get('code'));
        const pkceOk = grant && createHash('sha256').update(form.get('code_verifier') || '').digest('base64url') === grant.challenge;
        seen.token.push({ grant: form.get('grant_type'), pkceOk });
        if (form.get('grant_type') === 'authorization_code' && !pkceOk) {
          res.writeHead(400, cors);
          return res.end(JSON.stringify({ error: 'invalid_grant' }));
        }
        const now = Math.floor(Date.now() / 1000);
        res.writeHead(200, cors);
        res.end(JSON.stringify({
          token_type: 'Bearer',
          expires_in: 3600,
          access_token: fakeJwt({ uid: UID, scp: ['devbox'], groups: ['devbox-users'], aud: 'api://default', exp: now + 3600 }),
          id_token: fakeJwt({ sub: UID, nonce: grant?.nonce, exp: now + 3600 }),
          refresh_token: randomBytes(16).toString('hex'),
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

function frame(opcode, payload) {
  const head = payload.length < 126 ? Buffer.from([0x80 | opcode, payload.length])
    : payload.length < 65536 ? Buffer.from([0x80 | opcode, 126, payload.length >> 8, payload.length & 255])
      : (() => { const h = Buffer.alloc(10); h[0] = 0x80 | opcode; h[1] = 127; h.writeBigUInt64BE(BigInt(payload.length), 2); return h; })();
  return Buffer.concat([head, payload]);
}

// The box's HTTP and WebSocket contract, just enough for VS Code to run.
function fakeAgentCore() {
  const server = http.createServer((req, res) => {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, CORS);
      return res.end();
    }
    const chunks = [];
    req.on('data', c => chunks.push(c));
    req.on('end', async () => {
      const reply = obj => { res.writeHead(200, { ...CORS, 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
      if (!/^Bearer ey/.test(req.headers.authorization || '')) { res.writeHead(401, CORS); return res.end(); }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (body.op === 'status') {
        seen.status++;
        return reply({ v: 1, ok: true, owner: 'boot', sessionId: req.headers['x-amzn-bedrock-agentcore-runtime-session-id'], volume: 'mounted', vscode: 'ready', commit: OVS.commit, serverStartId: seen.serverStartId, lastSession: null, signedIn: seen.signedIn });
      }
      if (body.op === 'http') {
        seen.http.push(body.path);
        const upstream = await fetch(`http://127.0.0.1:${OVS_PORT}${body.path}?${body.query}`);
        const buf = Buffer.from(await upstream.arrayBuffer());
        const headers = { 'content-type': upstream.headers.get('content-type') || 'application/octet-stream' };
        if (upstream.headers.get('etag')) headers.etag = upstream.headers.get('etag');
        return reply({ v: 1, ok: true, status: upstream.status, headers, bodyB64: buf.toString('base64') });
      }
      return reply({ v: 1, ok: false, error: 'unknown op' });
    });
    return undefined;
  });
  server.on('upgrade', (req, socket) => {
    seen.sockets++;
    const url = new URL(req.url, AGENTCORE);
    const target = Buffer.from(url.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath') || '', 'base64url').toString('utf8');
    const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
    socket.write(['HTTP/1.1 101 Switching Protocols', 'Upgrade: websocket', 'Connection: Upgrade',
      `Sec-WebSocket-Accept: ${accept}`, 'Sec-WebSocket-Protocol: base64UrlBearerAuthorization', '', ''].join('\r\n'));
    const upstream = new WebSocket(`ws://127.0.0.1:${OVS_PORT}${target}`);
    upstream.binaryType = 'arraybuffer';
    const early = [];
    upstream.addEventListener('open', () => { for (const m of early.splice(0)) upstream.send(m); });
    upstream.addEventListener('message', ev => {
      const data = Buffer.from(ev.data);
      for (let i = 0; i < data.length; i += 32000) socket.write(frame(2, data.subarray(i, i + 32000)));
    });
    upstream.addEventListener('close', () => socket.end(frame(8, Buffer.from([0x03, 0xe8]))));
    upstream.addEventListener('error', () => socket.destroy());
    let buf = Buffer.alloc(0);
    let parts = [];
    socket.on('data', chunk => {
      buf = Buffer.concat([buf, chunk]);
      for (;;) {
        if (buf.length < 2) return;
        const fin = Boolean(buf[0] & 0x80);
        const opcode = buf[0] & 0x0f;
        let len = buf[1] & 0x7f;
        let off = 2;
        if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
        else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
        if (buf.length < off + 4 + len) return;
        const mask = buf.subarray(off, off + 4);
        const payload = Buffer.from(buf.subarray(off + 4, off + 4 + len).map((b, i) => b ^ mask[i % 4]));
        buf = buf.subarray(off + 4 + len);
        if (opcode === 8) { upstream.close(); socket.end(frame(8, payload)); return; }
        if (opcode === 9) { socket.write(frame(10, payload)); continue; }
        seen.maxFrame = Math.max(seen.maxFrame, len);
        parts.push(payload);
        if (!fin) continue;
        const message = Buffer.concat(parts);
        parts = [];
        if (upstream.readyState === WebSocket.OPEN) upstream.send(message);
        else early.push(message);
      }
    });
    socket.on('error', () => upstream.close());
    socket.on('close', () => upstream.close());
  });
  return server;
}

const servers = [];
const edges = [];
let chrome;

before(async () => {
  if (skip) return;
  spawnSync('docker', ['rm', '-f', CONTAINER], { stdio: 'ignore' });
  const run = spawnSync('docker', ['run', '-d', '--rm', '--name', CONTAINER, '--platform', 'linux/arm64',
    '-p', `127.0.0.1:${OVS_PORT}:3000`, '-v', `${OVS_DIR}:/ovs:ro`, 'debian:bookworm-slim', 'sh', '-c',
    'mkdir -p /mnt/workspace/projects && printf "# Hello from the box\\n" > /mnt/workspace/projects/README.md && '
    + 'exec /ovs/bin/openvscode-server --host 0.0.0.0 --port 3000 --without-connection-token --telemetry-level off '
    + '--disable-workspace-trust --server-data-dir /tmp/sdd --default-folder /mnt/workspace/projects --accept-server-license-terms'],
  { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);

  const config = {
    region: 'us-east-1', commit: OVS.commit, serverRoot: SERVER_ROOT, agentcoreBase: AGENTCORE,
    okta: { issuer: `${OKTA}/oauth2/default`, clientId: 'boot-test', scopes: 'openid profile email offline_access devbox' },
    webviewOrigin: WEBVIEW,
    boxes: { [sha(UID)]: { name: 'boot', runtimeArn: ARN, generation: 1 } },
  };
  const env = { WORKBENCH_ORIGIN: WORKBENCH, WEBVIEW_ORIGIN: WEBVIEW, DEVBOX_CONFIG_JSON: JSON.stringify(config) };
  edges.push(await startLocalServer({ site: 'workbench', port: 9498, env }));
  edges.push(await startLocalServer({ site: 'webview', port: 9499, env }));
  for (const [server, port] of [[fakeOkta(), 9496], [fakeAgentCore(), 9497]]) {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
    servers.push(server);
  }
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${OVS_PORT}/version`)).ok) break;
    } catch {
      // still starting
    }
    await new Promise(r => setTimeout(r, 200));
  }
  chrome = await launchChrome({ port: 9488 });
});

after(async () => {
  await chrome?.close();
  for (const e of edges) await e.close();
  for (const s of servers) {
    s.closeAllConnections?.();
    await new Promise(r => s.close(r));
  }
  if (!skip) spawnSync('docker', ['rm', '-f', CONTAINER], { stdio: 'ignore' });
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(expression, ms) {
  for (let t = 0; t < ms; t += 250) {
    try {
      const v = await chrome.evaluate(expression);
      if (v) return v;
    } catch {
      // navigating
    }
    await sleep(250);
  }
  return null;
}

test('the real workbench boots through the edge and connects', { skip, timeout: 120000 }, async () => {
  await chrome.send('Page.navigate', { url: `${WORKBENCH}/` });
  const explorer = await waitFor(`(() => {
    const rows = [...document.querySelectorAll('.explorer-folders-view .monaco-list-row')].map(r => r.getAttribute('aria-label') || r.textContent);
    return rows.some(r => /README\\.md/.test(r)) && rows;
  })()`, 60000);
  const state = await chrome.evaluate(`({
    url: location.href,
    overlay: Boolean(document.getElementById('devbox-overlay')),
    workbench: Boolean(document.querySelector('.monaco-workbench')),
    title: document.title,
    dialogs: [...document.querySelectorAll('.monaco-dialog-box, .notification-toast')].map(d => d.textContent.slice(0, 200)),
    controlled: Boolean(navigator.serviceWorker.controller),
    storage: JSON.stringify({ ...sessionStorage }) + JSON.stringify({ ...localStorage }),
    awsHint: document.getElementById('devbox-aws-hint')?.textContent ?? null,
  })`);
  const csp = chrome.logs.filter(l => /Content Security Policy/.test(l));
  // VS Code probes for Microsoft's proprietary vsda signing module, which openvscode-server does not ship
  // (the upstream server 404s it too); everything else must load.
  const failed = chrome.logs.filter(l => /Failed to load resource|Refused to execute/.test(l) && !/\/node_modules\/vsda\//.test(l));
  if (!explorer || csp.length || failed.length) console.log(chrome.logs.join('\n'));

  assert.ok(explorer, 'the explorer lists README.md from the box');
  assert.equal(state.url, `${WORKBENCH}/`, 'the code and state are gone from the URL');
  assert.equal(state.overlay, false);
  assert.equal(state.workbench, true);
  assert.equal(state.controlled, true);
  assert.ok(!state.dialogs.some(d => /reconnect|cannot|could not/i.test(d)), state.dialogs.join(' | '));
  assert.deepEqual(csp, [], 'no CSP violations');
  // The workbench starts its workers from blob: modules; the CSP watch must cover them.
  assert.ok(chrome.attached.some(t => /^worker blob:/.test(t)), `the log also covers the workbench's workers (attached: ${chrome.attached.join(', ')})`);
  assert.deepEqual(failed, [], 'nothing else failed to load');
  assert.ok(!/eyJ/.test(state.storage), 'no token in sessionStorage or localStorage');
  assert.match(state.awsHint ?? '', /Claude Code shows a sign-in URL and a code in its terminal.*aws sso login --sso-session devbox --use-device-code --no-browser/,
    'the box says signedIn: false, so the page says where the AWS device code shows up');

  assert.deepEqual(seen.authorize[0], { prompt: null, mode: 'fragment', method: 'S256' });
  assert.ok(seen.token.some(t => t.grant === 'authorization_code' && t.pkceOk));
  assert.ok(seen.status >= 1);
  assert.ok(seen.sockets >= 2, `VS Code opened ${seen.sockets} sockets (management + extension host)`);
  assert.ok(seen.maxFrame <= 32000, `largest browser frame ${seen.maxFrame}`);
});

test('VS Code\'s built-in AI is off: no Chat view, no Copilot setup, no title-bar Chat button', { skip, timeout: 60000 }, async () => {
  // The Chat view (with GitHub Copilot's "Build with Agent" setup) opens in the secondary side bar on a
  // first start unless chat.disableAIFeatures is set; give the layout time to settle first. This server
  // has no Claude Code extension, so the secondary side bar may open empty: an empty Chat container (no
  // views in it) is fine, any chat content is not.
  await sleep(3000);
  const found = await chrome.evaluate(`(() => {
    const onScreen = e => Boolean(e && e.checkVisibility && e.checkVisibility()) && e.getBoundingClientRect().width > 0;
    const text = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
    const container = document.getElementById('workbench.panel.chat');
    const chat = [...document.querySelectorAll('.interactive-session, .chat-widget, .chat-setup-view')].filter(onScreen).map(text);
    if (onScreen(container) && text(container)) chat.push(text(container));
    const titleBar = [...document.querySelectorAll('.part.titlebar .action-item, .part.titlebar .codicon')]
      .filter(onScreen)
      .filter(e => /chat|copilot/i.test((e.getAttribute('aria-label') || '') + ' ' + (e.title || '') + ' ' + e.className))
      .map(e => e.getAttribute('aria-label') || e.className);
    const aux = document.querySelector('.part.auxiliarybar');
    return {
      chat,
      titleBar,
      buildWithAgent: /Build with Agent/i.test(document.querySelector('.monaco-workbench')?.innerText || ''),
      aux: aux && onScreen(aux) ? text(aux) : null,
    };
  })()`);
  assert.deepEqual(found.chat, [], `the Chat view is on screen: ${JSON.stringify(found)}`);
  assert.equal(found.buildWithAgent, false, `"Build with Agent" is on screen: ${JSON.stringify(found)}`);
  assert.deepEqual(found.titleBar, [], `a Chat/Copilot button is in the title bar: ${JSON.stringify(found)}`);
  assert.ok(!/\bCHAT\b|Copilot/i.test(found.aux || ''), `the secondary side bar shows chat: ${found.aux}`);
});

test('a workspace image loads through the Service Worker', { skip, timeout: 60000 }, async () => {
  const before = seen.http.length;
  const r = await chrome.evaluate(`(async () => {
    const res = await fetch('${SERVER_ROOT}/vscode-remote-resource?path=' + encodeURIComponent('/ovs/resources/server/code-192.png'));
    return { status: res.status, type: res.headers.get('content-type'), bytes: (await res.arrayBuffer()).byteLength };
  })()`);
  assert.equal(r.status, 200);
  assert.equal(r.type, 'image/png');
  assert.ok(r.bytes > 1000);
  assert.ok(seen.http.length > before);
});

const KEYS = {
  F1: { key: 'F1', code: 'F1', windowsVirtualKeyCode: 112 },
  Backspace: { key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 },
  Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' },
};

async function press(name) {
  const k = KEYS[name];
  await chrome.send('Input.dispatchKeyEvent', { type: 'keyDown', ...k, nativeVirtualKeyCode: k.windowsVirtualKeyCode });
  await chrome.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k.key, code: k.code, windowsVirtualKeyCode: k.windowsVirtualKeyCode, nativeVirtualKeyCode: k.windowsVirtualKeyCode });
  await sleep(150);
}

async function command(text) {
  await press('F1');
  await waitFor(`Boolean(document.querySelector('.quick-input-widget:not([style*="display: none"]) input'))`, 5000);
  await chrome.send('Input.insertText', { text });
  await sleep(400);
  await press('Enter');
}

test('a Markdown preview (a webview) renders from the webview site', { skip, timeout: 60000 }, async () => {
  // Quick open README.md (F1 opens the palette in command mode; drop the '>' to search files).
  await press('F1');
  await press('Backspace');
  await chrome.send('Input.insertText', { text: 'README.md' });
  await sleep(800);
  await press('Enter');
  assert.ok(await waitFor(`[...document.querySelectorAll('.tab')].some(t => /README\\.md/.test(t.textContent))`, 10000), 'README.md opened');

  await command('Markdown: Open Preview to the Side');
  // The webview writes its content into an inner frame with document.write (which also gives that
  // frame the shell's URL), so read the rendered text from inside the webview shell.
  let rendered = null;
  let shell = null;
  for (let i = 0; i < 80 && !rendered; i++) {
    await sleep(250);
    for (const c of chrome.contexts.values()) {
      if (c.origin !== WEBVIEW) continue;
      const got = await chrome.evaluate(`JSON.stringify({
        href: location.href,
        marks: performance.getEntriesByType('mark').map(m => m.name.replace('webview/index.html/', '')),
        text: document.getElementById('active-frame')?.contentDocument?.body?.textContent ?? null,
      })`, c.id).then(JSON.parse).catch(() => null);
      if (got?.marks.includes('signalingReady')) shell = got;
      if (got?.text && got.text.includes('Hello from the box')) rendered = got;
    }
  }
  if (!rendered) console.log(JSON.stringify(shell), '\n', chrome.logs.join('\n'));
  assert.ok(shell, 'a webview shell started (its parent check passed)');
  const url = new URL(shell.href);
  assert.equal(url.origin + url.pathname, `${WEBVIEW}${SERVER_ROOT}/static/out/vs/workbench/contrib/webview/browser/pre/index.html`);
  assert.equal(url.searchParams.get('parentOrigin'), WORKBENCH);
  assert.ok(rendered, `the Markdown preview rendered README.md (marks: ${shell.marks.join(', ')})`);
  for (const mark of ['signalingReady', 'content/workerReady', 'content/innerFrameLoaded', 'content/wroteInnerContent']) {
    assert.ok(rendered.marks.includes(mark), mark);
  }
  assert.deepEqual(chrome.logs.filter(l => /Content Security Policy|frame-ancestors|is not the dev box workbench/.test(l)), []);
});

test('a restarted box is noticed and the window offers to reload; the AWS hint goes once signed in', { skip, timeout: 60000 }, async () => {
  seen.serverStartId = 'start-2';
  seen.signedIn = true;
  const banner = await waitFor(`document.getElementById('devbox-banner')?.textContent ?? ''`, 45000);
  assert.match(banner, /Your dev box restarted/);
  assert.equal(await chrome.evaluate(`Boolean(document.getElementById('devbox-aws-hint'))`), false, 'signed in now: the AWS hint is gone');
  // Keep this page: the next test signs out from it.
  await chrome.evaluate(`[...document.querySelectorAll('#devbox-banner button')].find(b => b.textContent === 'Not now').click()`);
  assert.match(await chrome.evaluate(`document.getElementById('devbox-banner').textContent`), /Reload this window to reconnect/);
});

test('sign-out revokes the refresh token and ends the Okta session by form POST', { skip, timeout: 60000 }, async () => {
  chrome.logs.length = 0;
  await chrome.evaluate('__devbox.signOut()');
  const shown = await waitFor(`location.href === '${WORKBENCH}/' && document.getElementById('devbox-status')?.textContent`, 20000);
  assert.equal(shown, 'You are signed out.');
  assert.equal(seen.revoke.length, 1);
  assert.equal(seen.revoke[0].form.token_type_hint, 'refresh_token');
  assert.equal(seen.revoke[0].form.client_id, 'boot-test');
  assert.equal(seen.logout.length, 1);
  assert.equal(seen.logout[0].query, '', 'nothing in the logout URL');
  assert.match(seen.logout[0].form.id_token_hint, /^ey/);
  assert.equal(seen.logout[0].form.post_logout_redirect_uri, `${WORKBENCH}/`);
  assert.deepEqual(chrome.logs.filter(l => /Content Security Policy|form-action/.test(l)), []);
  assert.equal(await chrome.evaluate(`JSON.stringify({ ...sessionStorage })`), '{}');
});
