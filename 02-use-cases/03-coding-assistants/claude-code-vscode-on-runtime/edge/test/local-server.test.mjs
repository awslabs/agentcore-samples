import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';

import { startLocalServer, toFunctionUrlEvent } from '../src/local-server.mjs';
import { SERVER_ROOT, STATIC_FILES, WEBVIEW_ORIGIN, WORKBENCH_ORIGIN, envFor, makeFixtureDist } from './fixture.mjs';

// Ports 9460-9479 are this component's; try a few in case another test run holds one.
async function start(site, fixture) {
  for (let port = 9460; port < 9480; port++) {
    try {
      return await startLocalServer({ site, port, env: envFor(), distDir: fixture.dist });
    } catch (err) {
      if (err.code !== 'EADDRINUSE') throw err;
    }
  }
  throw new Error('no free port in 9460-9479');
}

let fixture;
let workbench;
let webview;

before(async () => {
  fixture = await makeFixtureDist();
  workbench = await start('workbench', fixture);
  webview = await start('webview', fixture);
});

after(async () => {
  await workbench?.close();
  await webview?.close();
  await fixture?.cleanup();
});

test('serves the workbench site', async () => {
  const res = await fetch(`${workbench.url}/`);
  assert.equal(res.status, 200);
  assert.match(res.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  assert.ok((await res.text()).includes('/devbox/loader.js'));
});

test('binary bodies arrive byte for byte', async () => {
  const res = await fetch(`${workbench.url}${SERVER_ROOT}/static/out/vs/code/browser/workbench/workbench.js`, {
    headers: { 'accept-encoding': 'br' },
  });
  assert.equal(res.headers.get('content-encoding'), 'br');
  // fetch decodes br itself.
  assert.deepEqual(Buffer.from(await res.arrayBuffer()), STATIC_FILES['out/vs/code/browser/workbench/workbench.js']);
});

test('each server is pinned to its site whatever the client sends', async () => {
  const spoofed = await fetch(`${workbench.url}${SERVER_ROOT}/static/out/vs/workbench/contrib/webview/browser/pre/index.html`, {
    headers: { 'x-devbox-site': 'webview' },
  });
  assert.equal(spoofed.status, 404);
  const pre = await fetch(`${webview.url}${SERVER_ROOT}/static/out/vs/workbench/contrib/webview/browser/pre/index.html`, {
    headers: { 'x-devbox-site': 'workbench' },
  });
  assert.equal(pre.status, 200);
  assert.equal(pre.headers.get('content-security-policy'), `frame-ancestors ${WORKBENCH_ORIGIN}`);
  assert.ok((await pre.text()).includes(WORKBENCH_ORIGIN));
  assert.equal((await fetch(`${webview.url}/`)).status, 404);
});

test('HEAD and 405 behave like the Lambda', async () => {
  const head = await fetch(`${workbench.url}/sw.js`, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(head.headers.get('service-worker-allowed'), '/');
  const post = await fetch(`${workbench.url}/`, { method: 'POST', body: 'x' });
  assert.equal(post.status, 405);
});

test('the config the loader reads comes from the environment', async () => {
  const cfg = await (await fetch(`${workbench.url}/devbox-config.json`)).json();
  assert.equal(cfg.webviewOrigin, WEBVIEW_ORIGIN);
});

test('builds a payload 2.0 event from a Node request', () => {
  const req = {
    url: '/a%20b/c?x=1&y=2',
    method: 'GET',
    httpVersion: '1.1',
    headers: { host: 'localhost:9402', 'x-devbox-site': 'webview', 'accept-encoding': 'br' },
    socket: { remoteAddress: '127.0.0.1' },
  };
  const ev = toFunctionUrlEvent(req, 'workbench');
  assert.equal(ev.rawPath, '/a%20b/c');
  assert.equal(ev.rawQueryString, 'x=1&y=2');
  assert.equal(ev.headers['x-devbox-site'], 'workbench');
  assert.equal(ev.requestContext.http.method, 'GET');
  assert.equal(ev.version, '2.0');
});
