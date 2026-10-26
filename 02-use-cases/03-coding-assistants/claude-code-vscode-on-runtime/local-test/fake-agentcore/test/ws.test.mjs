// WS /runtimes/<arn>/ws: subprotocol bearer auth, header mapping, frame size/rate limits, max duration,
// close propagation, /ws/shells and /ping monitoring. Ports 9430-9438.
import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startStack, tokenFor, openWs, wsUrl, nextClose, nextMessage, vscodePath, ADA_SESSION, sleep, randomSession } from './helpers.mjs';

let stack;
let ada;

before(async () => {
  stack = await startStack({ oktaPort: 9430, boxPort: 9431, acPort: 9432 });
  ada = await tokenFor(stack.okta, 'ada');
});
after(() => stack.close());

const lastUpgrade = () => stack.box.seen.upgrades.at(-1);
const VSCODE_TARGET = '/stable-072586267e68ece9a47aa43f8c108e0dcbf44622?reconnectionToken=abc&reconnection=false&skipWebSocketFrames=false';

describe('the WebSocket handshake', () => {
  test('bearer subprotocol: the edge answers base64UrlBearerAuthorization and the box negotiates nothing', async () => {
    const { ws, protocol, status } = await openWs(wsUrl(stack.base, { custom: { Vscodepath: vscodePath(VSCODE_TARGET) } }), { token: ada, headers: { origin: 'http://localhost:9402', cookie: 'a=b' } });
    assert.equal(status, undefined, 'the upgrade succeeds');
    assert.equal(protocol, 'base64UrlBearerAuthorization');
    const up = lastUpgrade();
    assert.equal(up.url, '/ws', 'the container sees /ws without the caller query');
    assert.equal(up.headers['sec-websocket-protocol'], undefined, 'the bearer never reaches the box in Sec-WebSocket-Protocol');
    assert.equal(up.headers.authorization, `Bearer ${ada}`, 'Authorization is allowlisted, so the edge forwards the verified bearer');
    assert.equal(up.headers['x-amzn-bedrock-agentcore-runtime-session-id'], ADA_SESSION);
    assert.equal(up.headers['x-amzn-bedrock-agentcore-runtime-custom-vscodepath'], vscodePath(VSCODE_TARGET), 'Custom-* query params become lower-case headers');
    for (const name of ['origin', 'cookie', 'accept-encoding', 'sec-websocket-extensions']) assert.equal(up.headers[name], undefined, `${name} must not reach the box`);
    ws.close();
  });

  test('a Custom-* query param that is not allowlisted is dropped', async () => {
    const { ws } = await openWs(wsUrl(stack.base, { custom: { Other: 'x' } }), { token: ada });
    assert.equal(lastUpgrade().headers['x-amzn-bedrock-agentcore-runtime-custom-other'], undefined);
    ws.close();
  });

  test('an Authorization header works for non-browser clients (no subprotocol answered)', async () => {
    const { ws, protocol } = await openWs(wsUrl(stack.base), { headers: { authorization: `Bearer ${ada}` } });
    assert.equal(protocol, '');
    ws.close();
  });

  test('the session id may come as a header instead of the query', async () => {
    const { ws } = await openWs(wsUrl(stack.base, { sessionId: null }), { token: ada, headers: { 'x-amzn-bedrock-agentcore-runtime-session-id': ADA_SESSION } });
    assert.ok(ws);
    ws.close();
  });

  const refused = async (url, opts, status, why) => {
    const before = stack.box.seen.upgrades.length;
    const r = await openWs(url, opts);
    assert.equal(r.ws, undefined, `${why}: must not open`);
    assert.equal(r.status, status, `${why}: expected ${status}, got ${r.status} ${r.body}`);
    assert.equal(stack.box.seen.upgrades.length, before, `${why}: the box never sees the upgrade`);
    return r;
  };

  test('refusals happen before the upgrade: wrong uid, missing group, no token, no session, unsupported subprotocol', async () => {
    await refused(wsUrl(stack.base), { token: await tokenFor(stack.okta, 'grace') }, 401, 'wrong uid');
    await refused(wsUrl(stack.base), { token: await tokenFor(stack.okta, 'mallory') }, 401, 'missing group');
    await refused(wsUrl(stack.base), {}, 401, 'no token');
    await refused(wsUrl(stack.base), { protocols: ['base64UrlBearerAuthorization'] }, 401, 'sentinel only');
    await refused(wsUrl(stack.base, { sessionId: null }), { token: ada }, 400, 'no session id');
    await refused(wsUrl(stack.base, { sessionId: 'short' }), { token: ada }, 400, 'bad session id');
    const r = await refused(wsUrl(stack.base), { protocols: ['vscode', 'base64UrlBearerAuthorization'] }, 400, 'other subprotocol');
    assert.equal(r.errorType, 'ValidationException');
  });

  test('a box that refuses the upgrade surfaces as 424', async () => {
    stack.box.rejectUpgradeWith = 403;
    try {
      const r = await openWs(wsUrl(stack.base), { token: ada });
      assert.equal(r.status, 424);
      assert.equal(r.errorType, 'RuntimeClientError');
    } finally { stack.box.rejectUpgradeWith = 0; }
  });

  test('an upgrade on /invocations is 405', async () => {
    const url = wsUrl(stack.base).replace('/ws?', '/invocations?');
    assert.equal((await openWs(url, { token: ada })).status, 405);
  });

  test('/ws/shells is denied by the resource policy (no root shell from AgentCore)', async () => {
    const url = wsUrl(stack.base).replace('/ws?', '/ws/shells?');
    const r = await openWs(url, { token: ada });
    assert.equal(r.status, 403);
    assert.equal(r.errorType, 'AccessDeniedException');
  });

  test('with the microVM box policy (terminal allowed), /ws/shells passes the policy and is not emulated', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9438, runtime: { resourcePolicy: 'box' } });
    try {
      const url = wsUrl(s.base).replace('/ws?', '/ws/shells?');
      const r = await openWs(url, { token: ada });
      assert.equal(r.status, 501);
      assert.equal(r.errorType, 'NotImplemented');
    } finally { await s.close(); }
  });
});

describe('relaying', () => {
  test('binary and text messages pass through with their type', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    ws.send(Buffer.from([1, 2, 3]));
    const bin = await nextMessage(ws);
    assert.equal(bin.isBinary, true);
    assert.deepEqual([...bin.data], [1, 2, 3]);
    ws.send('hello');
    const txt = await nextMessage(ws);
    assert.equal(txt.isBinary, false);
    assert.equal(txt.data.toString(), 'hello');
    ws.close();
  });

  test('a box close code reaches the caller (1008 wrong session)', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    const closed = nextClose(ws);
    ws.send('close:1008');
    assert.equal((await closed).code, 1008);
  });

  test('the fake counts open sockets per session', async () => {
    const session = randomSession();
    const { ws } = await openWs(wsUrl(stack.base, { sessionId: session }), { token: ada });
    const snap = stack.ac.snapshot().sessions.find((s) => s.sessionId === session);
    assert.equal(snap.openSockets, 1);
    const closed = nextClose(ws);
    ws.close(1000);
    await closed;
    await sleep(50);
    assert.equal(stack.ac.snapshot().sessions.find((s) => s.sessionId === session).openSockets, 0);
  });
});

describe('frame limits', () => {
  test('exactly 32768 bytes passes; 32769 from the caller closes with 1009', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    ws.send(Buffer.alloc(32768, 1));
    assert.equal((await nextMessage(ws)).data.length, 32768);
    const closed = nextClose(ws);
    ws.send(Buffer.alloc(32769, 1));
    assert.equal((await closed).code, 1009);
  });

  test('a box frame over 32768 bytes closes the caller with 1009', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    const closed = nextClose(ws);
    ws.send('big:262144'); // what openvscode-server sends unchunked
    assert.equal((await closed).code, 1009);
    const v = stack.ac.snapshot().counters.violations.at(-1);
    assert.equal(v.direction, 'box->caller');
    assert.equal(v.kind, 'frame-size');
  });

  test('more than 250 frames in a second from the caller closes with 1008', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    const closed = nextClose(ws);
    for (let i = 0; i < 300; i += 1) ws.send(Buffer.from([i & 255]));
    assert.equal((await closed).code, 1008);
  });

  test('a box flood closes the caller with 1008', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    const closed = nextClose(ws);
    ws.send('flood:300');
    assert.equal((await closed).code, 1008);
  });

  test('200 frames/s for a second and a half stays open', async () => {
    const { ws } = await openWs(wsUrl(stack.base), { token: ada });
    let got = 0;
    ws.on('message', () => { got += 1; });
    let closedCode = null;
    ws.on('close', (c) => { closedCode = c; });
    ws.send('pace:300');
    await sleep(1800);
    assert.equal(closedCode, null, 'paced traffic must not trip the limit');
    assert.ok(got >= 250, `received ${got}`);
    ws.close();
  });
});

describe('connection lifetime', () => {
  test('connections are cut at FAKE_AC_WS_MAX_SECONDS (1008) on both sides', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9433, settings: { wsMaxSeconds: 1 } });
    try {
      const t0 = Date.now();
      const { ws } = await openWs(wsUrl(s.base), { token: ada });
      const { code, reason } = await nextClose(ws);
      assert.equal(code, 1008);
      assert.match(reason, /Maximum connection duration/);
      assert.ok(Date.now() - t0 >= 950);
      await sleep(100);
      assert.equal(s.ac.snapshot().sessions[0].openSockets, 0);
    } finally { await s.close(); }
  });

  test('a WS on a provisioning session gets 409, and the first one waits for the cold start', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9434, settings: { coldSeconds: 1 } });
    try {
      const session = randomSession();
      const first = openWs(wsUrl(s.base, { sessionId: session }), { token: ada });
      await sleep(200);
      const second = await openWs(wsUrl(s.base, { sessionId: session }), { token: ada });
      assert.equal(second.status, 409);
      assert.equal(second.errorType, 'RetryableConflictException');
      const { ws } = await first;
      assert.ok(ws, 'the first connection opens after provisioning');
      ws.close();
    } finally { await s.close(); }
  });

  test('FAKE_AC_WS_FORWARD_QUERY passes the caller query to the box', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9435, settings: { wsForwardQuery: true } });
    try {
      const { ws } = await openWs(wsUrl(s.base, { custom: { Vscodepath: 'eA' } }), { token: ada });
      assert.match(lastUpgrade().url, /^\/ws\?qualifier=DEFAULT&X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=/);
      ws.close();
    } finally { await s.close(); }
  });

  test('rate scope "connection" counts both directions together', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9436, settings: { rateScope: 'connection' } });
    try {
      const { ws } = await openWs(wsUrl(s.base), { token: ada });
      const closed = nextClose(ws);
      // 140 echoed frames = 280 frames through the edge in well under a second.
      for (let i = 0; i < 140; i += 1) ws.send(Buffer.from([1]));
      assert.equal((await closed).code, 1008);
    } finally { await s.close(); }
  });
});

describe('/ping monitoring', () => {
  test('flags a time_of_last_update that moves without a status change', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9437, settings: { pingSeconds: 0.2 } });
    try {
      stack.box.driftPingTimestamp = true;
      const { ws } = await openWs(wsUrl(s.base, { sessionId: randomSession() }), { token: ada });
      await sleep(700);
      assert.ok(s.ac.snapshot().counters.pingContractWarnings >= 1);
      ws.close();
    } finally {
      stack.box.driftPingTimestamp = false;
      await s.close();
    }
  });
});
