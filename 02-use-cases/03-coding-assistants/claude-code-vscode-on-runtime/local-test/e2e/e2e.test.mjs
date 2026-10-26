// End-to-end test of the local stack in headless Chrome. ./run.sh e2e starts a fresh
// stack and runs this; to run it against a stack that is already up: cd e2e && npm test.
// Steps run in order and share one browser. When an essential step fails, the rest are skipped, and each
// failure leaves a screenshot and a text dump in e2e/artifacts/.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import * as vs from './lib/vscode.mjs';
import { solidPng } from './lib/png.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.join(HERE, 'artifacts');

// run.sh records the host ports it chose (a VPN client may hold 9400); pick them up before loading the config.
const portsFile = path.join(HERE, '..', 'generated', 'ports.env');
if (existsSync(portsFile)) {
  for (const line of readFileSync(portsFile, 'utf8').split('\n')) {
    const m = /^(\w+)=(\d+)$/.exec(line.trim());
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
  }
}
const stack = await import('../config/stack.mjs');
const { signIn, decodeJwt } = await import('../lib/oidc-client.mjs');
const { sessionIdFor, bearerSubprotocols, base64url } = await import('../lib/ids.mjs');

const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const COLD_START_TIMEOUT = Number(process.env.E2E_COLD_START_TIMEOUT_MS || 6 * 60_000);
const WB = stack.ORIGINS.workbench;
const AC = stack.ORIGINS.agentcore;
const BOX = stack.BOXES[0];
const ADA = stack.USERS.ada;
const ADA_SESSION = sessionIdFor(ADA.uid, BOX.generation);
const PROJECTS = '/mnt/workspace/projects';
const JWT_LIKE = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;

const ONE_MB = Array.from({ length: 16384 }, (_, i) => `${i.toString(36).padStart(8, '0')}abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345`.slice(0, 63) + '\n').join('');
const ONE_MB_SHA = crypto.createHash('sha256').update(ONE_MB).digest('hex');
const PNG = solidPng(7, 5, [200, 30, 60]);

let browser;
let page;
let cdp;
let profileDir;
let failedEssential = null;
let stepNo = 0;
const consoleLines = [];
const cspViolations = [];   // From the page, its frames and (via auto-attach) its workers
const attachedWorkers = [];
const requests = [];
const remoteResourceResponses = [];
const wsUrls = [];
const overlayTexts = new Set();

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const acStats = async () => (await fetch(`${AC}/_fake/stats`)).json();

async function waitFor(predicate, timeout, what) {
  const deadline = Date.now() + timeout;
  for (;;) {
    if (await predicate()) return;
    if (Date.now() > deadline) throw new Error(`timed out after ${timeout} ms waiting for ${what}`);
    await sleep(500);
  }
}

async function dumpState(name) {
  const slug = `${String(stepNo).padStart(2, '0')}-${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 60)}`;
  await page.screenshot({ path: path.join(ART, `${slug}.png`) }).catch(() => {});
  const text = await page.evaluate(() => document.body?.innerText ?? '').catch((e) => `(no page text: ${e.message})`);
  const term = await vs.terminalText(page).catch(() => '');
  const alerts = await vs.workbenchAlerts(page).catch(() => []);
  const stats = await acStats().catch((e) => ({ error: e.message }));
  writeFileSync(path.join(ART, `${slug}.txt`), [
    `URL: ${page.url()}`, '', '--- page text ---', text, '', '--- terminal ---', term, '',
    '--- alerts ---', ...alerts, '', '--- fake AgentCore stats ---', JSON.stringify(stats, null, 2),
  ].join('\n'));
}

// One scenario. `essential` steps stop the run on failure, since later steps depend on them.
function step(name, fn, { essential = false, timeout = 120_000 } = {}) {
  test(name, { timeout }, async (t) => {
    stepNo += 1;
    if (failedEssential) { t.skip(`skipped because "${failedEssential}" failed`); return; }
    try {
      await fn();
    } catch (err) {
      if (essential) failedEssential = name;
      await dumpState(name);
      throw err;
    }
  });
}

async function statusFromPage(p = page) {
  return p.evaluate(async () => {
    const d = window.__devbox;
    const res = await fetch(`${d.agentcoreBase}/runtimes/${encodeURIComponent(d.runtimeArn)}/invocations?qualifier=DEFAULT`, {
      method: 'POST',
      headers: { authorization: `Bearer ${d.getToken()}`, 'content-type': 'application/json', accept: 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': d.sessionId },
      body: JSON.stringify({ v: 1, op: 'status' }),
    });
    return { status: res.status, body: await res.json().catch(() => null) };
  });
}

async function statusFromNode(token, sessionId = ADA_SESSION) {
  const res = await fetch(`${AC}/runtimes/${encodeURIComponent(BOX.runtimeArn)}/invocations?qualifier=DEFAULT`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', accept: 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': sessionId },
    body: JSON.stringify({ v: 1, op: 'status' }),
  });
  return { status: res.status, type: res.headers.get('x-amzn-errortype') };
}

function noReconnectFailure(alerts) {
  const bad = alerts.filter((a) => /cannot reconnect|reload the window|could not (re)?establish|connection.*(lost|failed)/i.test(a));
  assert.deepEqual(bad, [], `the workbench shows a connection failure:\n${bad.join('\n')}`);
}

before(async () => {
  mkdirSync(ART, { recursive: true });
  for (const f of readdirSync(ART)) if (/\.(png|txt)$/.test(f) || f === 'console.log') rmSync(path.join(ART, f));
  assert.ok(existsSync(CHROME), `Chrome not found at ${CHROME} (set CHROME_PATH)`);
  profileDir = mkdtempSync(path.join(os.tmpdir(), 'devbox-e2e-'));
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    userDataDir: profileDir,
    protocolTimeout: 300_000,
    defaultViewport: { width: 1600, height: 1000 },
    args: [
      '--disable-3d-apis', // xterm.js then renders to the DOM, so the terminal text is readable
      '--no-first-run', '--no-default-browser-check', '--window-size=1600,1000',
      // The shim paces frames with timers; a backgrounded tab must not slow them down.
      '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    ],
  });
  page = await browser.newPage();
  page.on('console', (m) => {
    consoleLines.push(`[${m.type()}] ${m.text()}`);
    if (/Content Security Policy/.test(m.text())) cspViolations.push({ from: 'page', url: m.location()?.url ?? '', text: m.text() });
  });
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${e.message}`));
  page.on('request', (r) => requests.push({ url: r.url(), method: r.method(), headers: r.headers() }));
  page.on('response', (r) => {
    if (r.url().includes('/vscode-remote-resource')) {
      remoteResourceResponses.push({ url: r.url(), status: r.status(), fromServiceWorker: r.fromServiceWorker(), headers: r.headers() });
    }
  });
  cdp = await page.createCDPSession();
  await cdp.send('Network.enable');
  cdp.on('Network.webSocketCreated', ({ url }) => wsUrls.push(url));
  // CSP violations in workers too. Auto-attach to every worker (and the workers they start),
  // then Runtime.enable + Log.enable on each, into the same log.
  const autoAttach = { autoAttach: true, waitForDebuggerOnStart: false, flatten: true };
  const labels = new Map();   // session id → "worker blob:http://…"
  const watch = (session) => {
    session.on('Target.attachedToTarget', ({ sessionId, targetInfo }) => {
      labels.set(sessionId, `${targetInfo.type} ${targetInfo.url.slice(0, 100)}`);
      attachedWorkers.push(labels.get(sessionId));
    });
    session.on('sessionattached', (child) => {
      watch(child);
      child.on('Log.entryAdded', ({ entry }) => {
        const where = labels.get(child.id()) ?? 'worker';
        consoleLines.push(`[${where}] [${entry.level}] ${entry.text}`);
        if (/Content Security Policy/.test(entry.text)) cspViolations.push({ from: where, url: entry.url ?? '', text: entry.text });
      });
      for (const [method, params] of [['Runtime.enable'], ['Log.enable'], ['Target.setAutoAttach', autoAttach]]) {
        child.send(method, params).catch(() => {});   // a worker can end before it answers
      }
    });
  };
  watch(cdp);
  await cdp.send('Target.setAutoAttach', autoAttach);
});

after(async () => {
  writeFileSync(path.join(ART, 'console.log'), consoleLines.join('\n'));
  if (!failedEssential && page) await page.screenshot({ path: path.join(ART, 'final.png') }).catch(() => {});
  await browser?.close();
  if (profileDir) rmSync(profileDir, { recursive: true, force: true });
});

// Another caller already provisioning ada's box when the page loads (a second tab, or a reload during a cold
// start), so the loader's first op:status calls meet 409 RetryableConflictException and must retry.
let concurrentCaller = null;

step('sign-in: PKCE S256 with response_mode=fragment, back on the workbench with the token in memory', async () => {
  const early = (await signIn({ issuer: stack.ISSUER, clientId: stack.SPA_CLIENT_ID, redirectUri: `${WB}/callback`, loginHint: 'ada' })).access_token;
  concurrentCaller = statusFromNode(early);
  await page.goto(`${WB}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    try { return /^[\w-]+\.[\w-]+\.[\w-]+$/.test(window.__devbox?.getToken?.() ?? ''); } catch { return false; }
  }, { timeout: 60_000, polling: 250 }).catch(async (err) => {
    throw new Error(`window.__devbox.getToken() never returned a token; the page is at ${page.url()}`, { cause: err });
  });
  const info = await page.evaluate(() => ({
    href: location.href, sessionId: window.__devbox.sessionId, runtimeArn: window.__devbox.runtimeArn,
    agentcoreBase: window.__devbox.agentcoreBase, commit: window.__devbox.commit, token: window.__devbox.getToken(),
  }));
  assert.equal(new URL(info.href).origin, WB);
  assert.doesNotMatch(info.href, /code=|state=|access_token|id_token/, 'the authorization response is cleared from the URL');
  const claims = decodeJwt(info.token).payload;
  assert.equal(claims.uid, ADA.uid, 'signed in as ada');
  assert.equal(info.sessionId, ADA_SESSION, 'sessionId = "dbx-" + hex(sha256(uid + ":" + generation))');
  assert.equal(info.runtimeArn, BOX.runtimeArn);
  assert.equal(info.agentcoreBase, AC);
  assert.equal(info.commit, stack.COMMIT);
  const authorize = requests.find((r) => r.url.startsWith(`${stack.ISSUER}/v1/authorize`));
  assert.ok(authorize, 'the browser went to Okta /v1/authorize');
  const q = new URL(authorize.url).searchParams;
  assert.equal(q.get('code_challenge_method'), 'S256');
  assert.equal(q.get('response_mode'), 'fragment');
  assert.equal(q.get('client_id'), stack.SPA_CLIENT_ID);
  assert.deepEqual(q.get('scope').split(' ').sort(), stack.SCOPES.split(' ').sort());
}, { essential: true, timeout: 90_000 });

step('cold start: the loader shows the wait, retries through 409 and polls op:status until VS Code is ready', async () => {
  const t0 = Date.now();
  let done = false;
  // Every 250 ms: the shortest retry backoff shows its message for 500 ms.
  const sampler = (async () => {
    while (!done) {
      const text = await page.evaluate(() => document.body?.innerText ?? '').catch(() => '');
      if (text) overlayTexts.add(text.slice(0, 400));
      await sleep(250);
    }
  })();
  try {
    await vs.waitForWorkbench(page, COLD_START_TIMEOUT);
  } finally {
    done = true;
    await sampler;
  }
  const seen = [...overlayTexts].join('\n');
  assert.match(seen, /cold start takes 1.8 minutes/i, `the loader never showed the cold-start hint; it showed:\n${seen}`);
  const { settings, sessions } = await acStats();
  const s = sessions.find((x) => x.sessionId === ADA_SESSION);
  assert.ok(s, "fake AgentCore saw ada's session");
  assert.equal(s.coldStarts, 1, 'exactly one cold start, although two callers asked for the box');
  assert.equal(s.state, 'ready');
  assert.ok(s.invocations >= 2, 'op:status went through /invocations');
  assert.equal((await concurrentCaller).status, 200, 'the other caller got through once the box was up');
  // Sign-in takes about 2 s, so only a longer cold start guarantees the loader arrives while it is running.
  if (settings.coldSeconds >= 4) {
    assert.ok(s.conflicts >= 1, 'the loader met 409 RetryableConflictException during provisioning');
    assert.match(seen, /AgentCore said 409/, `the loader never reported retrying the 409; it showed:\n${seen}`);
  }
  console.log(`# workbench ready ${((Date.now() - t0) / 1000).toFixed(1)} s after sign-in (409s during provisioning: ${s.conflicts})`);
}, { essential: true, timeout: COLD_START_TIMEOUT + 30_000 });

step('the workbench connects: the explorer shows the projects folder and there is no "cannot reconnect"', async () => {
  await page.waitForFunction(() => /PROJECTS/i.test(document.querySelector('.explorer-folders-view, .explorer-viewlet, .sidebar')?.innerText ?? ''), { timeout: 120_000 })
    .catch((err) => { throw new Error('the explorer never showed the PROJECTS folder (no remote file system?)', { cause: err }); });
  await sleep(2000);
  noReconnectFailure(await vs.workbenchAlerts(page));
  assert.ok(wsUrls.some((u) => u.startsWith(`${AC.replace(/^http/, 'ws')}/runtimes/`)), 'VS Code opened its WebSocket to AgentCore (through the shim)');
}, { essential: true, timeout: 150_000 });

step('the folder-open task starts the resumable Claude Code terminal, and Claude Code is at its prompt', async () => {
  // It also grabs the terminal panel when it starts, so the next step waits for it before making its own terminal.
  assert.ok(await vs.waitForTaskTerminal(page, 'Claude Code', 45_000), 'no "Claude Code" task terminal appeared after the folder opened');
  // A fresh home must not stop Claude Code at a first-run question (the theme picker, then the folder trust
  // prompt): the box pre-answers them, so the person lands on the prompt.
  const { screen, text } = await vs.claudeScreen(page, 60_000);
  assert.equal(screen, 'prompt', `Claude Code's terminal is stuck at ${screen}:\n${text}`);
}, { timeout: 120_000 });

step('the workbench shows no Chat panel (VS Code\'s built-in AI chat)', async () => {
  // Checked after the Claude Code step, so the workbench has settled its layout (the Chat view opens in the
  // secondary side bar on a first start unless the workbench config turns it off).
  const chat = await vs.visibleChat(page);
  assert.equal(chat, null, `the Chat panel is on screen: ${chat}`);
}, { timeout: 30_000 });

step('the loader tells the person how to sign in to AWS in the box, in a banner they can dismiss', async () => {
  // The local box is never signed in (no IAM Identity Center here), so the hint is always up.
  const DEVICE_LOGIN = 'aws sso login --sso-session devbox --use-device-code --no-browser';
  let banners = [];
  await waitFor(async () => (banners = await vs.loaderBanners(page)).some((b) => b.text.includes(DEVICE_LOGIN)), 10_000, 'the AWS sign-in banner')
    .catch(() => {});
  const hint = banners.find((b) => b.text.includes(DEVICE_LOGIN));
  assert.ok(hint, `no banner with the device-login command; banners: ${JSON.stringify(banners)}`);
  const p = await vs.paletteRect(page);
  const r = hint.rect;
  if (r.x < p.x + p.width && p.x < r.x + r.width && r.y < p.y + p.height && p.y < r.y + r.height) {
    console.log(`# note: the AWS sign-in banner (${Math.round(r.x)},${Math.round(r.y)} ${Math.round(r.width)}x${Math.round(r.height)}) covers the command palette (${Math.round(p.x)},${Math.round(p.y)} ${Math.round(p.width)}x${Math.round(p.height)}) until it is dismissed`);
  }
  assert.equal(await vs.dismissLoaderBanners(page), banners.length);
  assert.deepEqual(await vs.loaderBanners(page), [], 'the banners are gone after "Dismiss"');
}, { timeout: 30_000 });

step('a terminal opens and `echo hi` prints hi', async () => {
  // The loader's banners sit where the command palette opens; a person clears them first.
  await vs.dismissLoaderBanners(page);
  await vs.newTerminal(page);
  await vs.typeInTerminal(page, 'echo hi');
  await vs.waitForTerminalText(page, /^hi\s*$/m, 30_000);
}, { essential: true, timeout: 90_000 });

step('a 1 MB file created and saved in the editor reads back byte-exact in the terminal (sha256sum)', async () => {
  const file = `${PROJECTS}/e2e-1mb.txt`;
  await vs.shell(page, `: > ${file} && echo "created-$((20+1))"`, 'created-21');
  await vs.openFile(page, 'e2e-1mb.txt');
  const attempts = await vs.pasteIntoEditor(page, ONE_MB);
  if (attempts > 1) console.log(`# the 1 MB paste landed on attempt ${attempts}`);
  await vs.saveActiveEditor(page);
  // The save lands in 64 KB writes over the WebSocket; wait until the file has its full size, then hash it once.
  await vs.shell(page, `for i in $(seq 1 60); do [ "$(stat -c %s ${file})" = 1048576 ] && break; sleep 1; done; sha256sum ${file}; echo "size-$(stat -c %s ${file})-after-$i"`, /size-\d+-after-\d+/, 90_000);
  const out = await vs.terminalText(page);
  assert.match(out, /size-1048576-after-/, `the file never reached 1048576 bytes:\n${out}`);
  assert.ok(out.includes(`${ONE_MB_SHA}  ${file}`), `sha256sum differs from what the editor saved (expected ${ONE_MB_SHA}):\n${out}`);
  const { counters } = await acStats();
  assert.deepEqual(counters.violations, [], 'no WebSocket frame over 32768 bytes or 250 frames/s (the shim and the proxy chunk and pace)');
}, { timeout: 180_000 });

step('an image in the workspace renders through the Service Worker (vscode-remote-resource)', async () => {
  const file = `${PROJECTS}/e2e.png`;
  await vs.shell(page, `echo ${PNG.toString('base64')} | base64 -d > ${file} && echo "png-$((3*3))"`, 'png-9');
  const url = `${stack.SERVER_ROOT}/vscode-remote-resource?path=${encodeURIComponent(file)}`;
  const pixel = await page.evaluate(async (u) => {
    const img = new Image();
    img.src = u;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    return { w: img.naturalWidth, h: img.naturalHeight, rgb: [...g.getImageData(3, 2, 1, 1).data].slice(0, 3) };
  }, url);
  assert.deepEqual(pixel, { w: 7, h: 5, rgb: [200, 30, 60] }, 'the image arrived byte-exact');
  const r = remoteResourceResponses.find((x) => x.url === `${WB}${url}`);
  assert.ok(r, `the page requested ${url}`);
  assert.equal(r.fromServiceWorker, true, 'served by the Service Worker; the edge never proxies remote resources');
  assert.equal(r.status, 200);
  assert.equal(r.headers['content-type'], 'image/png');
  assert.equal(r.headers['cache-control'], 'no-store');
  assert.equal(r.headers['x-content-type-options'], 'nosniff');
}, { timeout: 90_000 });

step('a forced WebSocket cutoff (FAKE_AC_WS_MAX_SECONDS) reconnects without losing the terminal', async () => {
  const { settings } = await acStats();
  await vs.shell(page, 'export E2E_MARK=m$((6*7)); echo "mark-$E2E_MARK"', 'mark-m42');
  const counts = async () => { const { counters } = await acStats(); return { cut: counters.wsClosedByCode['1008'] ?? 0, opened: counters.wsOpened }; };
  const start = await counts();
  await waitFor(async () => (await counts()).cut > start.cut, (settings.wsMaxSeconds + 30) * 1000, 'fake AgentCore to cut a WebSocket at its maximum duration');
  // Every socket cut since the mark has been replaced by a new one.
  await waitFor(async () => { const c = await counts(); return c.opened - start.opened >= c.cut - start.cut; }, 60_000, 'VS Code to reopen its WebSockets');
  await vs.shell(page, 'echo "still-$E2E_MARK"', 'still-m42', 60_000);
  noReconnectFailure(await vs.workbenchAlerts(page));
}, { timeout: 180_000 });

step('op:status reports the pointer after a stub SessionStart hook run', async () => {
  const payload = JSON.stringify({
    session_id: 'e2e-hook-0001', transcript_path: '/mnt/workspace/home/.claude/projects/-mnt-workspace-projects/e2e-hook-0001.jsonl',
    cwd: PROJECTS, hook_event_name: 'SessionStart', source: 'startup',
  });
  // Run every managed SessionStart hook command the way Claude Code would: the event JSON on stdin.
  await vs.shell(page,
    `jq -r '.hooks.SessionStart[].hooks[].command' /etc/claude-code/managed-settings.json | while IFS= read -r H; do printf '%s' '${payload}' | sh -c "$H" || echo "hook-failed-$((1+1))"; done; echo "hooks-done-$((5+5))"`,
    'hooks-done-10', 30_000);
  assert.doesNotMatch(await vs.terminalText(page), /hook-failed-2/);
  let body;
  await waitFor(async () => {
    const r = await statusFromPage();
    body = r.body;
    return r.status === 200 && body?.lastSession?.sessionId === 'e2e-hook-0001';
  }, 15_000, `op:status to report lastSession e2e-hook-0001 (last answer: ${JSON.stringify(body)})`);
  assert.equal(body.lastSession.cwd, PROJECTS);
  assert.equal(body.owner, 'ada');
  assert.equal(body.sessionId, ADA_SESSION);
  assert.equal(body.vscode, 'ready');
  assert.equal(body.volume, 'mounted');
}, { timeout: 90_000 });

step('Claude Code is installed (claude --version) and the managed settings parse, in the box', async () => {
  await vs.shell(page, 'claude --version', /2\.1\.277/, 60_000);
  await vs.shell(page, 'jq -e . /etc/claude-code/managed-settings.json > /dev/null && echo "managed-settings-ok-$((6*7))"', 'managed-settings-ok-42');
}, { timeout: 120_000 });

step('the box runs with no capabilities and no sudo, as on AgentCore Instances', async () => {
  // Every marker is computed by the shell, so the echoed command line can never match it.
  await vs.shell(page,
    'echo "uid-$(id -u)"; echo "cap-$(awk \'/^CapEff/{print $2}\' /proc/self/status)"; (touch /etc/claude-code/e2e 2>/dev/null && rm -f /etc/claude-code/e2e && echo "etc-w-$((1+1))") || echo "etc-ro-$((1+3))"; command -v sudo > /dev/null && echo "sudo-yes-$((2+1))" || echo "sudo-no-$((2+3))"; echo "inv-done-$((4+4))"',
    'inv-done-8');
  const out = await vs.terminalText(page);
  // Single-user mode: AgentCore gives the container uid 0 (in a user namespace) with no capabilities,
  // so the terminal is that uid. What must hold is no capabilities and no sudo.
  assert.match(out, /^cap-0000000000000000\s*$/m, 'the terminal must have no capabilities');
  assert.match(out, /^sudo-no-5\s*$/m, 'there must be no sudo');
  assert.doesNotMatch(out, /^sudo-yes-3/m);
  // Known gap: in single-user mode /etc/claude-code is owned by the running uid, so it is
  // writable from the terminal. Record it instead of hiding it; flip this when the ownership hardening lands.
  console.log(`    note: /etc/claude-code is ${/^etc-w-2\s*$/m.test(out) ? 'writable (known gap)' : 'read-only'} from the box terminal`);
}, { timeout: 60_000 });

step('a second test user is rejected by the authorizer', async () => {
  const context = await browser.createBrowserContext();
  try {
    const p2 = await context.newPage();
    // mallory signs in to (fake) Okta in her own browser profile, then opens the workbench.
    await p2.goto(`${stack.ORIGINS.okta}/_fake/session?user=mallory&return_to=${encodeURIComponent(`${WB}/`)}`, { waitUntil: 'domcontentloaded' });
    await p2.waitForFunction(() => /no dev box for you/i.test(document.body?.innerText ?? ''), { timeout: 60_000 })
      .catch((err) => { throw new Error('the loader never said "No dev box for you" to mallory', { cause: err }); });
    let token = await p2.evaluate(() => { try { return window.__devbox?.getToken?.() ?? null; } catch { return null; } });
    if (!token) token = (await signIn({ issuer: stack.ISSUER, clientId: stack.SPA_CLIENT_ID, redirectUri: `${WB}/callback`, loginHint: 'mallory' })).access_token;
    assert.equal(decodeJwt(token).payload.uid, stack.USERS.mallory.uid);
    // Straight at ada's runtime with mallory's token, over HTTP and WebSocket.
    const http = await p2.evaluate(async (base, arn, sid, t) => {
      const res = await fetch(`${base}/runtimes/${encodeURIComponent(arn)}/invocations?qualifier=DEFAULT`, {
        method: 'POST', headers: { authorization: `Bearer ${t}`, 'content-type': 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': sid },
        body: JSON.stringify({ v: 1, op: 'status' }),
      });
      return res.status;
    }, AC, BOX.runtimeArn, ADA_SESSION, token);
    assert.equal(http, 401, 'HTTP invoke with mallory\'s token');
    const target = `${stack.SERVER_ROOT}?reconnectionToken=${crypto.randomUUID()}&reconnection=false&skipWebSocketFrames=false`;
    const wsUrl = `${AC.replace(/^http/, 'ws')}/runtimes/${encodeURIComponent(BOX.runtimeArn)}/ws?${new URLSearchParams({
      qualifier: 'DEFAULT', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': ADA_SESSION, 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath': base64url(target),
    })}`;
    const wsCode = await p2.evaluate((u, protocols) => new Promise((resolve) => {
      const ws = new WebSocket(u, protocols);
      ws.onopen = () => { ws.close(); resolve('opened'); };
      ws.onclose = (e) => resolve(e.code);
    }), wsUrl, bearerSubprotocols(token));
    assert.equal(wsCode, 1006, 'the WebSocket handshake is refused');
    // grace is in devbox-users but does not own this box: rejected too.
    const grace = (await signIn({ issuer: stack.ISSUER, clientId: stack.SPA_CLIENT_ID, redirectUri: `${WB}/callback`, loginHint: 'grace' })).access_token;
    assert.equal((await statusFromNode(grace)).status, 401, 'grace (wrong uid)');
  } finally {
    await context.close();
  }
}, { timeout: 120_000 });

step('invariants: no token in any URL, cookie, localStorage or sessionStorage, and none sent to the edge', async () => {
  const storage = await page.evaluate(() => ({ local: JSON.stringify({ ...localStorage }), session: JSON.stringify({ ...sessionStorage }), cookie: document.cookie }));
  for (const [where, value] of Object.entries(storage)) assert.doesNotMatch(value, JWT_LIKE, `a token in ${where}Storage/cookie`);
  const { cookies } = await cdp.send('Network.getAllCookies');
  for (const c of cookies) assert.doesNotMatch(c.value, JWT_LIKE, `a token in cookie ${c.name} (${c.domain})`);
  for (const r of requests) assert.doesNotMatch(r.url, JWT_LIKE, `a token in the URL of ${r.method} ${r.url.slice(0, 120)}`);
  for (const u of wsUrls) assert.doesNotMatch(u, JWT_LIKE, 'a token in a WebSocket URL');
  const edgeOrigins = [WB, stack.ORIGINS.webview];
  const toEdge = requests.filter((r) => edgeOrigins.some((o) => r.url.startsWith(o)));
  assert.ok(toEdge.length > 0);
  for (const r of toEdge) {
    assert.equal(r.headers.authorization, undefined, `Authorization sent to the edge: ${r.url.slice(0, 120)}`);
    assert.doesNotMatch(JSON.stringify(r.headers), JWT_LIKE, `a token in headers sent to the edge: ${r.url.slice(0, 120)}`);
  }
  const { counters } = await acStats();
  assert.equal(counters.tokenInUrl, 0, 'fake AgentCore saw a token in a URL query');
  assert.deepEqual(counters.violations, [], 'WebSocket frame limit violations during the run');
  assert.equal(counters.pingContractWarnings, 0, '/ping contract warnings (time_of_last_update moving without a status change)');
}, { timeout: 30_000 });

step('a page reload signs in again through the Okta session (no prompt=none, no sign-in page) and reconnects', async () => {
  const before = requests.length;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await vs.waitForWorkbench(page, 180_000);
  const silent = requests.slice(before).find((r) => r.url.startsWith(`${stack.ISSUER}/v1/authorize`));
  assert.ok(silent, 'the reload went through /v1/authorize');
  assert.equal(new URL(silent.url).searchParams.get('prompt'), null, 'real Okta answers prompt=none without a session with a dead-end 400 page');
  await page.waitForFunction(() => /PROJECTS/i.test(document.querySelector('.explorer-folders-view, .explorer-viewlet, .sidebar')?.innerText ?? ''), { timeout: 120_000 });
  await sleep(2000);
  noReconnectFailure(await vs.workbenchAlerts(page));
  const chat = await vs.visibleChat(page);
  assert.equal(chat, null, `the Chat panel is on screen after the reload: ${chat}`);
}, { timeout: 330_000 });

step('sign-out revokes the refresh token and ends the Okta session with a form POST to /v1/logout', async () => {
  const before = requests.length;
  // signOut() navigates away, so don't wait for its promise inside the page.
  await page.evaluate(() => { window.__devbox.signOut(); });
  await page.waitForFunction(() => /you are signed out/i.test(document.body?.innerText ?? ''), { timeout: 30_000, polling: 250 })
    .catch((err) => { throw new Error(`the page never said "You are signed out."; it is at ${page.url()}`, { cause: err }); });
  assert.equal(new URL(page.url()).origin, WB, 'Okta sent the browser back to the workbench');
  const after = requests.slice(before);
  assert.ok(after.some((r) => r.method === 'POST' && r.url === `${stack.ISSUER}/v1/revoke`), 'the refresh token was revoked');
  assert.ok(after.some((r) => r.method === 'POST' && r.url === `${stack.ISSUER}/v1/logout`), 'the Okta session was ended with a form POST');
  for (const r of after) assert.doesNotMatch(r.url, JWT_LIKE, `a token in the URL of ${r.method} ${r.url.slice(0, 120)}`);
  const { cookies } = await cdp.send('Network.getAllCookies');
  assert.deepEqual(cookies.filter((c) => c.name === 'fake-okta-sid' && c.value), [], 'the fake Okta session cookie is gone');
}, { timeout: 60_000 });

// Last, so it covers every step, the reload and the sign-out. Fail on any CSP violation in the
// page (and its frames) and in its workers. One kind is only noted: a violation of an extension's own
// webview content policy, which the extension builds from webview.cspSource (https://*.vscode-cdn.net).
// Neither our workbench CSP nor the patched webview shell's CSP names that host, so it can't hide ours.
// Claude Code's webview does this: its CSS inlines a data: font that its own `font-src ${cspSource}` blocks.
step('no Content Security Policy violation in the page, its frames or its workers', async () => {
  assert.ok(attachedWorkers.some((w) => /^worker blob:/.test(w)),
    `the CSP watch covers the workbench's blob: workers (attached: ${attachedWorkers.join(', ') || 'none'})`);
  const extensionOwn = (v) => /directive: "[^"]*https:\/\/\*\.vscode-cdn\.net[^"]*"/.test(v.text);
  const noted = cspViolations.filter(extensionOwn);
  if (noted.length) console.log(`# note: ${noted.length} violation(s) of an extension's own webview CSP (not ours): ${[...new Set(noted.map((v) => v.text.replace(/'data:[^']{40,}'/g, "'data:…'").slice(0, 160)))].join(' | ')}`);
  const ours = cspViolations.filter((v) => !extensionOwn(v)).map((v) => `${v.from}: ${v.text.slice(0, 300)} (${v.url.slice(0, 120)})`);
  assert.deepEqual(ours, [], 'CSP violations in the page, its frames or its workers');
}, { timeout: 10_000 });
