// Just enough of the Chrome DevTools Protocol to drive headless Chrome from node:test, with Node's
// built-in WebSocket client (no puppeteer).

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

export const chromeAvailable = () => existsSync(CHROME) && typeof WebSocket === 'function';

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Attach to every worker the page starts, without pausing it.
const AUTO_ATTACH = { autoAttach: true, waitForDebuggerOnStart: false, flatten: true };

export async function launchChrome({ port }) {
  const profile = await mkdtemp(join(tmpdir(), 'devbox-edge-chrome-'));
  const proc = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-extensions',
    'about:blank',
  ], { stdio: 'ignore' });

  let page;
  for (let i = 0; i < 100 && !page; i++) {
    await sleep(100);
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      page = targets.find(t => t.type === 'page');
    } catch {
      // not listening yet
    }
  }
  if (!page) {
    proc.kill();
    throw new Error('Chrome did not start');
  }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
  let seq = 0;
  const pending = new Map();
  const logs = [];
  const contexts = new Map();
  // Workers (and anything else Chrome auto-attaches) arrive as flattened child sessions on this socket,
  // so CSP violations in workers reach the same log as the page's.
  const children = new Map();
  const attached = [];   // every target ever attached, e.g. 'worker blob:http://…'
  ws.addEventListener('message', ev => {
    const msg = JSON.parse(ev.data);
    // Execution context ids are per target: only the page's own contexts go into `contexts` (evaluate uses them).
    const child = msg.sessionId ? children.get(msg.sessionId) : null;
    const from = child ? `[${child}] ` : '';
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
    } else if (msg.method === 'Target.attachedToTarget') {
      const { sessionId, targetInfo } = msg.params;
      children.set(sessionId, `${targetInfo.type} ${targetInfo.url}`);
      attached.push(children.get(sessionId));
      // Each call is best effort: a target can go away before it answers.
      const ignore = () => {};
      sendTo(sessionId, 'Runtime.enable').catch(ignore);
      sendTo(sessionId, 'Log.enable').catch(ignore);
      sendTo(sessionId, 'Target.setAutoAttach', AUTO_ATTACH).catch(ignore);   // workers started by workers
    } else if (msg.method === 'Target.detachedFromTarget') {
      children.delete(msg.params.sessionId);
    } else if (msg.method === 'Runtime.consoleAPICalled') {
      logs.push(`${from}console.${msg.params.type}: ${msg.params.args.map(a => a.value ?? a.description ?? '').join(' ')}`);
    } else if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails;
      logs.push(`${from}exception: ${d.exception?.description ?? d.text}`);
    } else if (msg.method === 'Runtime.executionContextCreated' && !msg.sessionId) {
      const c = msg.params.context;
      contexts.set(c.id, { id: c.id, origin: c.origin, frameId: c.auxData?.frameId });
    } else if (msg.method === 'Runtime.executionContextDestroyed' && !msg.sessionId) {
      contexts.delete(msg.params.executionContextId);
    } else if (msg.method === 'Log.entryAdded') {
      const { level, text, url } = msg.params.entry;
      logs.push(`${from}${level}: ${text}${url && !text.includes(url) ? ` (${url})` : ''}`);
    }
  });
  const sendTo = (sessionId, method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const send = (method, params = {}) => sendTo(null, method, params);
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Target.setAutoAttach', AUTO_ATTACH);

  async function evaluate(expression, contextId) {
    const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, ...(contextId ? { contextId } : {}) });
    if (res.exceptionDetails) throw new Error(res.exceptionDetails.exception?.description ?? res.exceptionDetails.text);
    return res.result.value;
  }

  async function navigate(url) {
    await send('Page.navigate', { url });
    for (let i = 0; i < 100; i++) {
      await sleep(50);
      try {
        if (await evaluate(`location.href === ${JSON.stringify(url)} && document.readyState === 'complete'`)) return;
      } catch {
        // the old document is going away
      }
    }
    throw new Error(`timed out loading ${url}`);
  }

  async function close() {
    try {
      ws.close();
    } catch {
      // already closed
    }
    if (proc.exitCode === null) {
      const exited = new Promise(r => proc.once('exit', r));
      proc.kill();
      await Promise.race([exited, sleep(5000)]);
    }
    await rm(profile, { recursive: true, force: true });
  }

  return { send, evaluate, navigate, logs, contexts, attached, close };
}
