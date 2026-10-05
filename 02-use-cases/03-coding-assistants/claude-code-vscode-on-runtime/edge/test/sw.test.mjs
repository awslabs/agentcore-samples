import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { makeSandbox, runScript } from './sandbox.mjs';

const ORIGIN = 'https://d111111abcdef8.cloudfront.net';
const COMMIT = '072586267e68ece9a47aa43f8c108e0dcbf44622';
const ROOT = `/stable-${COMMIT}`;
const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_ada-AbC123';
const BASE = 'https://bedrock-agentcore.us-east-1.amazonaws.com';
const SESSION = `dbx-${'b'.repeat(64)}`;
const RESOURCE = `${ORIGIN}${ROOT}/vscode-remote-resource?path=%2Fmnt%2Fworkspace%2Fprojects%2Flogo.png`;

let listeners;
let fetches;
let replies;
let windows;
let claims;
let skipped;

// Objects made inside the worker's context have that context's prototypes.
const plain = value => JSON.parse(JSON.stringify(value));

function tokenMessage(overrides = {}) {
  return { type: 'devbox-token', token: 'tok-1', sessionId: SESSION, runtimeArn: ARN, agentcoreBase: BASE, serverRoot: ROOT, ...overrides };
}

function jsonResponse(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function envelope(overrides = {}) {
  return { v: 1, ok: true, status: 200, headers: { 'content-type': 'image/png', etag: 'W/"1-2"' }, bodyB64: Buffer.from('PNGDATA').toString('base64'), ...overrides };
}

beforeEach(() => {
  listeners = {};
  fetches = [];
  replies = [];
  claims = 0;
  skipped = 0;
  windows = [];
  const sandbox = makeSandbox({
    location: new URL(`${ORIGIN}/sw.js`),
    // Retry backoff runs instantly; the 5 s wait for a token becomes 50 ms.
    setTimeout: (fn, ms) => setTimeout(fn, ms >= 5000 ? 50 : 0),
    clearTimeout,
    skipWaiting: async () => { skipped++; },
    clients: {
      claim: async () => { claims++; },
      matchAll: async () => windows,
    },
    fetch: async (url, init) => {
      fetches.push({ url, init });
      const next = replies.shift();
      if (!next) throw new Error('no reply queued');
      if (next instanceof Error) throw next;
      return next;
    },
  });
  sandbox.self = sandbox;
  sandbox.addEventListener = (type, fn) => { listeners[type] = fn; };
  runScript(sandbox, 'sw.js');
});

const windowClient = () => ({ type: 'window', posted: [], postMessage(m) { this.posted.push(m); } });

function message(data, source = windowClient()) {
  const waits = [];
  listeners.message({ data, source, waitUntil: p => waits.push(p) });
  return Promise.all(waits);
}

// Dispatches a fetch event; returns the Response if the worker took it, or null if it let it pass.
async function dispatchFetch(url, { method = 'GET', headers = {}, mode = 'cors', destination = 'image' } = {}) {
  let responded = null;
  const request = { url, method, headers: new Headers(headers), mode, destination };
  listeners.fetch({ request, respondWith: p => { responded = p; } });
  return responded ? await responded : null;
}

describe('lifecycle', () => {
  test('takes over at once', async () => {
    const waits = [];
    listeners.install({ waitUntil: p => waits.push(p) });
    listeners.activate({ waitUntil: p => waits.push(p) });
    await Promise.all(waits);
    assert.equal(skipped, 1);
    assert.equal(claims, 1);
  });

  test('claims the page when asked (after a hard reload)', async () => {
    await message({ type: 'devbox-claim' });
    assert.equal(claims, 1);
  });
});

describe('what it intercepts', () => {
  test('leaves everything but same-origin vscode-remote-resource GET/HEAD alone', async () => {
    await message(tokenMessage());
    const passes = [
      [`${ORIGIN}${ROOT}/static/out/vs/code/browser/workbench/workbench.js`],
      [`${ORIGIN}/`],
      [`${ORIGIN}/devbox-config.json`],
      [`https://other.example.com${ROOT}/vscode-remote-resource?path=x`],
      [`${ORIGIN}${ROOT}/vscode-remote-resource?path=x`, { method: 'POST' }],
      [`${ORIGIN}/stable-0000000000000000000000000000000000000000/vscode-remote-resource?path=x`],
      [`${ORIGIN}${ROOT}/vscode-remote-resource/extra`],
      [`${BASE}/runtimes/x/invocations`],
    ];
    for (const [url, opts] of passes) assert.equal(await dispatchFetch(url, opts), null, url);
    assert.equal(fetches.length, 0);
  });
});

describe('answering remote resources', () => {
  test('calls op: http on AgentCore and returns the file', async () => {
    await message(tokenMessage());
    replies.push(jsonResponse(200, envelope()));
    const res = await dispatchFetch(RESOURCE);
    assert.equal(fetches.length, 1);
    const { url, init } = fetches[0];
    assert.equal(url, `${BASE}/runtimes/${encodeURIComponent(ARN)}/invocations?qualifier=DEFAULT`);
    assert.equal(init.method, 'POST');
    assert.equal(init.credentials, 'omit');
    assert.deepEqual(plain(init.headers), {
      Authorization: 'Bearer tok-1',
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': SESSION,
    });
    assert.deepEqual(JSON.parse(init.body), {
      v: 1, op: 'http', method: 'GET', path: `${ROOT}/vscode-remote-resource`, query: 'path=%2Fmnt%2Fworkspace%2Fprojects%2Flogo.png',
    });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get('content-type'), 'image/png');
    assert.equal(res.headers.get('etag'), 'W/"1-2"');
    assert.equal(res.headers.get('cache-control'), 'no-store');
    assert.equal(res.headers.get('x-content-type-options'), 'nosniff');
    assert.equal(res.headers.get('content-security-policy'), 'sandbox');
    assert.equal(Buffer.from(await res.arrayBuffer()).toString(), 'PNGDATA');
  });

  test('forwards If-None-Match and passes a 304 through without a body', async () => {
    await message(tokenMessage());
    replies.push(jsonResponse(200, envelope({ status: 304, bodyB64: '' })));
    const res = await dispatchFetch(RESOURCE, { headers: { 'if-none-match': 'W/"1-2"' } });
    assert.deepEqual(JSON.parse(fetches[0].init.body).headers, { 'if-none-match': 'W/"1-2"' });
    assert.equal(res.status, 304);
    assert.equal(res.body, null);
  });

  test('HEAD asks the box for GET and returns no body', async () => {
    await message(tokenMessage());
    replies.push(jsonResponse(200, envelope()));
    const res = await dispatchFetch(RESOURCE, { method: 'HEAD' });
    assert.equal(JSON.parse(fetches[0].init.body).method, 'GET');
    assert.equal(res.status, 200);
    assert.equal(res.body, null);
  });

  test('passes the box status through (404, 413)', async () => {
    await message(tokenMessage());
    replies.push(jsonResponse(200, envelope({ status: 404, headers: { 'content-type': 'text/plain' }, bodyB64: Buffer.from('nope').toString('base64') })));
    assert.equal((await dispatchFetch(RESOURCE)).status, 404);
    replies.push(jsonResponse(200, envelope({ status: 413, headers: {}, bodyB64: '' })));
    const big = await dispatchFetch(RESOURCE);
    assert.equal(big.status, 413);
    assert.equal(big.headers.get('content-type'), 'application/octet-stream');
  });

  test('a malformed or refused envelope becomes a 502', async () => {
    await message(tokenMessage());
    for (const body of [{ v: 1, ok: false, error: 'wrong session' }, { v: 2, ok: true, status: 200 }, { v: 1, ok: true, status: 99 }, { v: 1, ok: true, status: 200, bodyB64: '%%%' }]) {
      replies.push(jsonResponse(200, body));
      assert.equal((await dispatchFetch(RESOURCE)).status, 502, JSON.stringify(body));
    }
    replies.push(new Response('not json', { status: 200 }));
    assert.equal((await dispatchFetch(RESOURCE)).status, 502);
  });

  test('retries 409, 429, 424 and network errors, then gives up', async () => {
    await message(tokenMessage());
    replies.push(jsonResponse(409, {}), new TypeError('network'), jsonResponse(200, envelope()));
    assert.equal((await dispatchFetch(RESOURCE)).status, 200);
    assert.equal(fetches.length, 3);

    fetches = [];
    replies.push(jsonResponse(429, {}), jsonResponse(424, {}), jsonResponse(503, {}));
    assert.equal((await dispatchFetch(RESOURCE)).status, 502);
    assert.equal(fetches.length, 3);

    fetches = [];
    replies.push(new TypeError('a'), new TypeError('b'), new TypeError('c'));
    assert.equal((await dispatchFetch(RESOURCE)).status, 504);
  });

  test('on 401 it asks the page for a fresh token once', async () => {
    const page = windowClient();
    windows = [page];
    await message(tokenMessage(), page);
    replies.push(jsonResponse(401, {}), jsonResponse(200, envelope()));
    const pending = dispatchFetch(RESOURCE);
    await new Promise(r => setTimeout(r, 5));
    assert.deepEqual(plain(page.posted), [{ type: 'devbox-need-token' }]);
    await message(tokenMessage({ token: 'tok-2' }), page);
    assert.equal((await pending).status, 200);
    assert.equal(fetches[1].init.headers.Authorization, 'Bearer tok-2');
  });

  test('refuses to render workspace files as pages', async () => {
    await message(tokenMessage());
    for (const opts of [{ mode: 'navigate', destination: 'document' }, { destination: 'iframe' }, { destination: 'object' }, { destination: 'embed' }]) {
      assert.equal((await dispatchFetch(RESOURCE, opts)).status, 403, JSON.stringify(opts));
    }
    assert.equal(fetches.length, 0);
  });
});

describe('getting the token', () => {
  test('a restarted worker asks the pages and waits for the answer', async () => {
    const page = windowClient();
    windows = [page];
    replies.push(jsonResponse(200, envelope()));
    const pending = dispatchFetch(RESOURCE);
    await new Promise(r => setTimeout(r, 5));
    assert.deepEqual(plain(page.posted), [{ type: 'devbox-need-token' }]);
    await message(tokenMessage(), page);
    assert.equal((await pending).status, 200);
  });

  test('without an answer it returns 503 and calls nothing', async () => {
    windows = [windowClient()];
    const res = await dispatchFetch(RESOURCE);
    assert.equal(res.status, 503);
    assert.equal(fetches.length, 0);
  });

  test('ignores tokens from non-window clients and malformed hand-overs', async () => {
    await message(tokenMessage(), { type: 'worker', postMessage() {} });
    await message(tokenMessage({ sessionId: 'not-a-session' }));
    await message(tokenMessage({ serverRoot: '/../x' }));
    await message(tokenMessage({ token: '' }));
    await message(tokenMessage(), null);
    windows = [];
    assert.equal((await dispatchFetch(RESOURCE)).status, 503);
  });

  test('forgets the token on sign-out', async () => {
    await message(tokenMessage());
    await message({ type: 'devbox-signed-out' });
    windows = [];
    assert.equal((await dispatchFetch(RESOURCE)).status, 503);
  });
});
