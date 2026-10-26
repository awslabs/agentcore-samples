// Integration tests: the real proxy app on an ephemeral port in front of a fake VS Code server.

import assert from 'node:assert/strict';
import http from 'node:http';
import net from 'node:net';
import { after, before, describe, test } from 'node:test';
import {
  COMMIT, FakeClock, SERVER_ROOT, SESSION, b64url, connectBrowser, goodTarget, invoke, startFakeVscode,
  startProxy, startSilentUpstream, waitFor,
} from './helpers.mjs';

let vscode;
let proxy;
const state = {
  volume: 'mounted', vscode: 'ready', serverStartId: 'start-1', signedIn: true,
  lastSession: { sessionId: 'abc', cwd: '/mnt/workspace/projects', transcriptPath: '/x.jsonl', ts: 5, source: 'startup', extra: 'dropped' },
};

before(async () => {
  vscode = await startFakeVscode();
  proxy = await startProxy({ upstreamPort: vscode.port, state });
});
after(async () => {
  await proxy.close();
  await vscode.close();
});

describe('/ping', () => {
  test('answers at once with the contract shape', async () => {
    const res = await fetch(`http://127.0.0.1:${proxy.port}/ping`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.deepEqual(Object.keys(body).sort(), ['status', 'time_of_last_update']);
    assert.equal(body.status, 'Healthy');
    assert.equal(typeof body.time_of_last_update, 'number');
  });

  test('other paths are 404 and never reach VS Code', async () => {
    const before = vscode.seen.http.length;
    for (const path of ['/', '/version', `${SERVER_ROOT}/vscode-remote-resource?path=/etc/passwd`]) {
      const res = await fetch(`http://127.0.0.1:${proxy.port}${path}`);
      assert.equal(res.status, 404, path);
    }
    assert.equal(vscode.seen.http.length, before);
  });
});

describe('/invocations envelope', () => {
  test('status returns the box state and the configured owner, commit and session', async () => {
    const { status, contentType, json } = await invoke(proxy.port, { v: 1, op: 'status' });
    assert.equal(status, 200);
    assert.match(contentType, /application\/json/);
    assert.deepEqual(json, {
      v: 1, ok: true, owner: 'ada', sessionId: SESSION, volume: 'mounted', vscode: 'ready', commit: COMMIT,
      serverStartId: 'start-1', signedIn: true,
      lastSession: { sessionId: 'abc', cwd: '/mnt/workspace/projects', transcriptPath: '/x.jsonl', ts: 5 },
    });
  });

  test('status before the supervisor wrote anything', async () => {
    const early = await startProxy({ upstreamPort: vscode.port, state: {} });
    try {
      const { json } = await invoke(early.port, { v: 1, op: 'status' });
      assert.equal(json.volume, 'waiting');
      assert.equal(json.vscode, 'waiting');
      assert.equal(json.serverStartId, null);
      assert.equal(json.lastSession, null);
      assert.equal(json.signedIn, false);
    } finally {
      await early.close();
    }
  });

  test('every failure is still HTTP 200 with ok:false', async () => {
    const cases = [
      ['not json', 'body is not JSON'],
      ['[1,2]', 'body must be a JSON object'],
      ['null', 'body must be a JSON object'],
      [{ v: 2, op: 'status' }, 'unsupported envelope version'],
      [{ v: 1, op: 'shell' }, 'unknown op'],
      [{ v: 1 }, 'unknown op'],
      ['x'.repeat(1024 * 1024 + 1), 'request too large'],
    ];
    for (const [body, message] of cases) {
      const res = await invoke(proxy.port, body);
      assert.equal(res.status, 200);
      assert.deepEqual(res.json, { v: 1, ok: false, error: message });
    }
    const get = await invoke(proxy.port, '', { method: 'GET' });
    assert.equal(get.status, 200);
    assert.equal(get.json.ok, false);
  });

  test('a missing or different session id is refused before anything else runs', async () => {
    const before = vscode.seen.http.length;
    for (const session of [null, 'dbx-other', `${SESSION}x`]) {
      const res = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path: '/version', query: '' }, { session });
      assert.equal(res.status, 200);
      assert.deepEqual(res.json, { v: 1, ok: false, error: 'wrong session' });
    }
    assert.equal(vscode.seen.http.length, before);
  });

  test('no session check when DEVBOX_SESSION_ID is not set', async () => {
    const open = await startProxy({ upstreamPort: vscode.port, sessionId: '', state });
    try {
      const { json } = await invoke(open.port, { v: 1, op: 'status' }, { session: 'dbx-whatever-0000000000000000000000000000' });
      assert.equal(json.ok, true);
      assert.equal(json.sessionId, 'dbx-whatever-0000000000000000000000000000');
    } finally {
      await open.close();
    }
  });

  test('a handler that throws still answers 200', async () => {
    const broken = await startProxy({ upstreamPort: vscode.port, state: () => { throw new Error('boom'); } });
    try {
      const res = await invoke(broken.port, { v: 1, op: 'status' });
      assert.equal(res.status, 200);
      assert.deepEqual(res.json, { v: 1, ok: false, error: 'internal error' });
    } finally {
      await broken.close();
    }
  });
});

describe('op: http', () => {
  test('GET /version returns the commit', async () => {
    const { json } = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path: '/version', query: '' });
    assert.equal(json.ok, true);
    assert.equal(json.status, 200);
    assert.deepEqual(json.headers, { 'content-type': 'text/plain' }, 'only content-type and etag come back');
    assert.equal(Buffer.from(json.bodyB64, 'base64').toString(), COMMIT);
  });

  test('remote resource: body, etag, and a 304 for a matching if-none-match', async () => {
    const path = `${SERVER_ROOT}/vscode-remote-resource`;
    const first = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path, query: 'path=%2Fmnt%2Fworkspace%2Fa.png' });
    assert.equal(first.json.status, 200);
    assert.deepEqual(first.json.headers, { 'content-type': 'image/png', etag: 'W/"1"' });
    assert.equal(Buffer.from(first.json.bodyB64, 'base64').toString(), 'file:/mnt/workspace/a.png');
    assert.equal(vscode.seen.http.at(-1).url, `${path}?path=%2Fmnt%2Fworkspace%2Fa.png`);

    const again = await invoke(proxy.port, {
      v: 1, op: 'http', method: 'GET', path, query: 'path=%2Fa.png',
      headers: { 'If-None-Match': 'W/"1"', authorization: 'Bearer secret', cookie: 'c=1' },
    });
    assert.equal(again.json.status, 304);
    assert.equal(again.json.bodyB64, '');
    const forwarded = vscode.seen.http.at(-1).headers;
    assert.equal(forwarded['if-none-match'], 'W/"1"');
    assert.equal(forwarded.authorization, undefined);
    assert.equal(forwarded.cookie, undefined);
  });

  test('HEAD returns the status without a body', async () => {
    const { json } = await invoke(proxy.port, { v: 1, op: 'http', method: 'HEAD', path: '/version', query: '' });
    assert.equal(json.status, 200);
    assert.equal(json.bodyB64, '');
  });

  test('paths outside the allowlist are 404 and never reach VS Code', async () => {
    const before = vscode.seen.http.length;
    const paths = ['/', '', '/version/', '//version', '/%76ersion', '/Version', `${SERVER_ROOT}`,
      `${SERVER_ROOT}/static/out/nls.messages.js`, `${SERVER_ROOT}/vscode-remote-resource/../../version`,
      `${SERVER_ROOT}/vscode-remote-resource/`, `/stable-x/vscode-remote-resource`, '/delay-shutdown'];
    for (const path of paths) {
      const { json } = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path, query: '' });
      assert.equal(json.ok, true, path);
      assert.equal(json.status, 404, path);
    }
    assert.equal(vscode.seen.http.length, before);
  });

  test('bad method, bad query, wrong types', async () => {
    const post = await invoke(proxy.port, { v: 1, op: 'http', method: 'POST', path: '/version', query: '' });
    assert.equal(post.json.status, 405);
    for (const query of ['a b', 'a\r\nHost: evil', 'x#y', 'a"b']) {
      const { json } = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path: '/version', query });
      assert.equal(json.status, 400, JSON.stringify(query));
    }
    const types = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path: 5 });
    assert.equal(types.json.ok, false);
  });

  test('bodies over 50 MB become 413', async () => {
    const { json } = await invoke(proxy.port, { v: 1, op: 'http', method: 'GET', path: `${SERVER_ROOT}/vscode-remote-resource`, query: 'path=/big' });
    assert.equal(json.ok, true);
    assert.equal(json.status, 413);
    assert.equal(json.bodyB64, '');
  });

  test('VS Code down becomes 502, still HTTP 200', async () => {
    const closed = net.createServer();
    await new Promise((r) => closed.listen(0, '127.0.0.1', r));
    const port = closed.address().port;
    await new Promise((r) => closed.close(r));
    const down = await startProxy({ upstreamPort: port, state });
    try {
      const res = await invoke(down.port, { v: 1, op: 'http', method: 'GET', path: '/version', query: '' });
      assert.equal(res.status, 200);
      assert.equal(res.json.status, 502);
    } finally {
      await down.close();
    }
  });
});

describe('op: diag', () => {
  test('records header names (never values) and the session id', async () => {
    await invoke(proxy.port, { v: 1, op: 'diag' }, { headers: { authorization: 'Bearer eyJ-secret-token' } });
    const { json } = await invoke(proxy.port, { v: 1, op: 'diag' }, { headers: { authorization: 'Bearer eyJ-secret-token' } });
    assert.equal(json.ok, true);
    assert.ok(json.headersSeen.invocations.names.includes('authorization'));
    assert.equal(json.headersSeen.invocations.sessionId, SESSION);
    assert.equal(json.headersSeen.invocations.peer, 'IPv4');
    assert.equal(JSON.stringify(json).includes('eyJ-secret-token'), false);
    assert.equal(typeof json.proxy.uid, 'number');
  });
});

describe('/ws relay', () => {
  test('connects to the server root with the original query, no subprotocol, no compression', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget() });
    await browser.opened;
    const up = await waitFor(() => vscode.seen.ws.at(-1));
    assert.equal(up.url, `${SERVER_ROOT}?reconnectionToken=0f8e2c1a-1111-4222-8333-444455556666&reconnection=false&skipWebSocketFrames=false`);
    assert.equal(up.headers['sec-websocket-protocol'], undefined);
    assert.equal(up.headers['sec-websocket-extensions'], undefined);
    assert.equal(up.headers.authorization, undefined);
    assert.equal(browser.ws.extensions, '');
    browser.ws.close();
    await browser.closed;
  });

  test('never selects a subprotocol, even if one is offered', async () => {
    const response = await new Promise((resolve, reject) => {
      const req = http.request({
        host: '127.0.0.1', port: proxy.port, path: '/ws', headers: {
          connection: 'Upgrade', upgrade: 'websocket', 'sec-websocket-version': '13',
          'sec-websocket-key': Buffer.alloc(16, 3).toString('base64'),
          'sec-websocket-protocol': 'base64UrlBearerAuthorization.abc, base64UrlBearerAuthorization',
          'sec-websocket-extensions': 'permessage-deflate; client_max_window_bits',
          'x-amzn-bedrock-agentcore-runtime-session-id': SESSION,
          'x-amzn-bedrock-agentcore-runtime-custom-vscodepath': goodTarget(),
        },
      });
      req.on('upgrade', (res, socket) => { socket.destroy(); resolve(res); });
      req.on('response', (res) => resolve(res));
      req.on('error', reject);
      req.end();
    });
    assert.equal(response.statusCode, 101);
    assert.equal(response.headers['sec-websocket-protocol'], undefined);
    assert.equal(response.headers['sec-websocket-extensions'], undefined);
  });

  test('the target can come from the query parameter instead of the header', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget('from-query'), viaQuery: true });
    await browser.opened;
    const up = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('from-query')));
    assert.ok(up);
    browser.ws.close();
    await browser.closed;
  });

  test('a wrong session or a bad target closes with 1008 and never reaches VS Code', async () => {
    const before = vscode.seen.ws.length;
    const cases = [
      { target: goodTarget(), session: 'dbx-someone-else-000000000000000000000000000', reason: 'wrong session' },
      { target: b64url('/'), reason: 'target path not allowed' },
      { target: b64url(`${SERVER_ROOT}?reconnectionToken=a&tkn=x`), reason: 'target query key not allowed' },
      { target: b64url(`${SERVER_ROOT}?skipWebSocketFrames=true`), reason: 'target query value not allowed' },
      { target: '***', reason: 'target is not base64url' },
    ];
    for (const c of cases) {
      const browser = connectBrowser(proxy.port, c);
      const closed = await browser.closed;
      assert.equal(closed.code, 1008, c.reason);
      assert.equal(closed.reason, c.reason);
    }
    const noTarget = new (await import('./helpers.mjs')).WebSocket(`ws://127.0.0.1:${proxy.port}/ws`, {
      headers: { 'x-amzn-bedrock-agentcore-runtime-session-id': SESSION },
    });
    const closed = await new Promise((r) => noTarget.on('close', (code) => r(code)));
    assert.equal(closed, 1008);
    assert.equal(vscode.seen.ws.length, before);
  });

  test('big VS Code messages arrive as pieces of at most 32000 bytes; browser messages pass through whole', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget('chunks') });
    await browser.opened;
    const up = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('chunks')));
    const message = Buffer.alloc(1_000_000);
    for (let i = 0; i < message.length; i++) message[i] = (i * 7) % 256;
    up.ws.send(message);
    await waitFor(() => browser.received.reduce((n, m) => n + m.data.length, 0) >= message.length);
    assert.equal(browser.received.length, Math.ceil(1_000_000 / 32_000));
    assert.ok(browser.received.every((m) => m.data.length <= 32_000));
    assert.deepEqual(Buffer.concat(browser.received.map((m) => m.data)), message);

    const fromBrowser = Buffer.alloc(100_000, 9);
    browser.ws.send(fromBrowser);
    await waitFor(() => up.received.length === 1);
    assert.equal(up.received[0].data.length, 100_000);
    assert.equal(up.received[0].isBinary, true);
    browser.ws.close();
    await browser.closed;
  });

  test('the relay is paced at 200 messages a second', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget('pacing') });
    await browser.opened;
    const up = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('pacing')));
    const started = Date.now();
    for (let i = 0; i < 5; i++) up.ws.send(Buffer.alloc(90 * 32_000, i)); // 450 pieces
    await waitFor(() => browser.received.length >= 450, { timeout: 10_000 });
    const elapsed = browser.received.at(-1).at - started;
    assert.ok(elapsed >= 1900, `450 pieces took ${elapsed} ms; at 200/s it can't be under 2 s`);
    assert.ok(elapsed < 4000, `450 pieces took ${elapsed} ms`);
    const firstSecond = browser.received.filter((m) => m.at - started < 900).length;
    assert.ok(firstSecond <= 200, `${firstSecond} pieces in the first 0.9 s`);
    browser.ws.close();
    await browser.closed;
  });

  test('messages sent before VS Code accepts are delivered in order', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget('early') });
    await browser.opened;
    browser.ws.send(Buffer.from('one'));
    browser.ws.send(Buffer.from('two'));
    const up = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('early')));
    await waitFor(() => up.received.length === 2);
    assert.deepEqual(up.received.map((m) => m.data.toString()), ['one', 'two']);
    browser.ws.close();
    await browser.closed;
  });

  test('closes travel both ways', async () => {
    const a = connectBrowser(proxy.port, { target: goodTarget('close-up') });
    await a.opened;
    const upA = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('close-up')));
    upA.ws.close(4001, 'bye');
    assert.deepEqual(await a.closed, { code: 4001, reason: 'bye' });

    const b = connectBrowser(proxy.port, { target: goodTarget('close-down') });
    await b.opened;
    const upB = await waitFor(() => vscode.seen.ws.find((e) => e.url.includes('close-down')));
    b.ws.close(1000);
    await waitFor(() => upB.closedWith !== undefined);
    assert.equal(upB.closedWith, 1000);
  });

  test('a browser close ends the VS Code socket at once, though VS Code never answers a Close frame', async () => {
    const silent = await startSilentUpstream();
    const relayed = await startProxy({ upstreamPort: silent.port, state });
    try {
      const browser = connectBrowser(relayed.port, { target: goodTarget('silent') });
      await browser.opened;
      const up = await waitFor(() => silent.connections[0]);
      silent.sendBinary(up, 'hi');
      await waitFor(() => browser.received.length === 1); // the proxy's upstream side is open
      const closedAt = Date.now();
      browser.ws.close(1000);
      await browser.closed;
      await waitFor(() => up.endedAt, { timeout: 5000 });
      const lingered = up.endedAt - closedAt;
      assert.ok(lingered < 1000, `VS Code's socket stayed open ${lingered} ms after the browser closed`);
      assert.ok(up.bytes > 0, 'the Close frame was still sent, for politeness');
      await waitFor(() => relayed.busy.open === 0);
    } finally {
      await relayed.close();
      await silent.close();
    }
  });

  test('VS Code not listening closes the browser with 1011', async () => {
    const closed = net.createServer();
    await new Promise((r) => closed.listen(0, '127.0.0.1', r));
    const port = closed.address().port;
    await new Promise((r) => closed.close(r));
    const down = await startProxy({ upstreamPort: port, state });
    try {
      const browser = connectBrowser(down.port, { target: goodTarget() });
      await browser.opened;
      assert.equal((await browser.closed).code, 1011);
    } finally {
      await down.close();
    }
  });

  test('diag shows the header names of the last upgrade', async () => {
    const browser = connectBrowser(proxy.port, { target: goodTarget('diag'), headers: { authorization: 'Bearer ws-secret' } });
    await browser.opened;
    const { json } = await invoke(proxy.port, { v: 1, op: 'diag' });
    assert.ok(json.headersSeen.ws.names.includes('x-amzn-bedrock-agentcore-runtime-custom-vscodepath'));
    assert.ok(json.headersSeen.ws.names.includes('authorization'));
    assert.equal(json.headersSeen.ws.sessionId, SESSION);
    assert.equal(JSON.stringify(json).includes('ws-secret'), false);
    browser.ws.close();
    await browser.closed;
  });
});

describe('/ping follows open sockets', () => {
  test('HealthyBusy while open, then for 120 s after the last close (fake clock)', async () => {
    const clock = new FakeClock(1_800_000_000_000);
    const tracked = await startProxy({ upstreamPort: vscode.port, state, now: clock.now });
    const ping = async () => (await fetch(`http://127.0.0.1:${tracked.port}/ping`)).json();
    try {
      assert.deepEqual(await ping(), { status: 'Healthy', time_of_last_update: 1_800_000_000 });
      clock.advance(10_000);
      const browser = connectBrowser(tracked.port, { target: goodTarget('busy') });
      await browser.opened;
      await waitFor(() => tracked.busy.open === 1);
      assert.deepEqual(await ping(), { status: 'HealthyBusy', time_of_last_update: 1_800_000_010 });
      clock.advance(30_000);
      browser.ws.close();
      await browser.closed;
      await waitFor(() => tracked.busy.open === 0);
      clock.advance(119_999);
      assert.deepEqual(await ping(), { status: 'HealthyBusy', time_of_last_update: 1_800_000_010 });
      clock.advance(1);
      assert.deepEqual(await ping(), { status: 'Healthy', time_of_last_update: 1_800_000_160 });
    } finally {
      await tracked.close();
    }
  });

  test("Claude working on a turn (the supervisor's agentBusy) keeps the box busy with no socket open", async () => {
    const clock = new FakeClock(1_800_000_000_000);
    const box = { volume: 'mounted', vscode: 'ready', agentBusy: false };
    const tracked = await startProxy({ upstreamPort: vscode.port, state: () => box, now: clock.now });
    const ping = async () => (await fetch(`http://127.0.0.1:${tracked.port}/ping`)).json();
    try {
      assert.deepEqual(await ping(), { status: 'Healthy', time_of_last_update: 1_800_000_000 });
      clock.advance(5_000);
      box.agentBusy = true;
      assert.deepEqual(await ping(), { status: 'HealthyBusy', time_of_last_update: 1_800_000_005 });
      clock.advance(3_600_000); // an hour later the turn is still going: no change, same timestamp
      assert.deepEqual(await ping(), { status: 'HealthyBusy', time_of_last_update: 1_800_000_005 });
      const { json } = await invoke(tracked.port, { v: 1, op: 'diag' });
      assert.equal(json.websockets.agentBusy, true);
      assert.equal(json.websockets.open, 0);
      box.agentBusy = false; // the turn ended: the same 120 s tail as after the last socket
      assert.equal((await ping()).status, 'HealthyBusy');
      clock.advance(119_999);
      assert.equal((await ping()).status, 'HealthyBusy');
      clock.advance(1);
      assert.deepEqual(await ping(), { status: 'Healthy', time_of_last_update: 1_800_003_725 });
    } finally {
      await tracked.close();
    }
  });

  test('a refused socket does not make the box busy', async () => {
    const tracked = await startProxy({ upstreamPort: vscode.port, state });
    try {
      const browser = connectBrowser(tracked.port, { target: goodTarget(), session: 'dbx-wrong-000000000000000000000000000000000' });
      await browser.closed;
      const body = await (await fetch(`http://127.0.0.1:${tracked.port}/ping`)).json();
      assert.equal(body.status, 'Healthy');
    } finally {
      await tracked.close();
    }
  });
});
