import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { fakeTime, makeSandbox, runScript } from './sandbox.mjs';
// Fake tokens, built at run time.
const fakeJwt = (header, payload, signature) => [header, payload, signature].map(s => Buffer.from(s).toString('base64url')).join('.');

const HOST = 'd111111abcdef8.cloudfront.net';
const COMMIT = '072586267e68ece9a47aa43f8c108e0dcbf44622';
const ROOT = `/stable-${COMMIT}`;
const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_ada-AbC123';
const SESSION = `dbx-${'a'.repeat(64)}`;
const VSCODE_URL = `wss://${HOST}:443${ROOT}?reconnectionToken=6f1c-9a&reconnection=false&skipWebSocketFrames=false`;

let clock;
let sockets;
let ctx;
let token;

function makeMock() {
  return class MockWebSocket extends EventTarget {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url, protocols) {
      super();
      this.args = [url, protocols];
      this.argCount = arguments.length;
      this.state = 0;
      this.sent = [];
      this.closedWith = null;
      this.nativeBuffered = 0;
      this.binaryType = 'blob';
      sockets.push(this);
    }

    get url() { return this.args[0]; }
    get protocol() { return 'base64UrlBearerAuthorization'; }
    get readyState() { return this.state; }
    get bufferedAmount() { return this.nativeBuffered; }

    send(data) {
      if (this.state === 0) throw new DOMException('connecting', 'InvalidStateError');
      this.sent.push({ data, at: clock.now() });
    }

    close(...args) {
      this.closedWith = args;
      this.state = 2;
    }

    // test helpers, playing the browser's part
    open() {
      this.state = 1;
      this.dispatchEvent(new Event('open'));
    }

    finish() {
      this.state = 3;
      this.dispatchEvent(new Event('close'));
    }
  };
}

function b64urlDecode(text) {
  return Buffer.from(text.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
}

async function bytesOf(message) {
  if (typeof message === 'string') return Buffer.from(message);
  if (typeof message.arrayBuffer === 'function' && !ArrayBuffer.isView(message)) return Buffer.from(await message.arrayBuffer());
  return Buffer.from(message.buffer, message.byteOffset, message.byteLength);
}

function pattern(size, offset = 0) {
  const buf = new Uint8Array(size);
  for (let i = 0; i < size; i++) buf[i] = (i + offset) % 251;
  return buf;
}

beforeEach(() => {
  clock = fakeTime();
  sockets = [];
  token = fakeJwt('{"alg":"RS256"}', '{"uid":"00u1"}', 'sig');
  ctx = makeSandbox({
    WebSocket: makeMock(),
    location: { href: `https://${HOST}/`, host: HOST, origin: `https://${HOST}` },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    Date: clock.Date,
    __devbox: {
      getToken: () => token,
      sessionId: SESSION,
      runtimeArn: ARN,
      agentcoreBase: 'https://bedrock-agentcore.us-east-1.amazonaws.com',
      commit: COMMIT,
      serverRoot: ROOT,
    },
  });
  runScript(ctx, 'devbox/shim.js');
});

function openSocket(url = VSCODE_URL) {
  const ws = new ctx.WebSocket(url);
  const native = sockets.at(-1);
  native.open();
  return { ws, native };
}

describe('URL rewrite', () => {
  test('sends VS Code connections to the AgentCore WebSocket', () => {
    const ws = new ctx.WebSocket(VSCODE_URL);
    const [url, protocols] = sockets[0].args;
    const u = new URL(url);
    assert.equal(u.protocol, 'wss:');
    assert.equal(u.host, 'bedrock-agentcore.us-east-1.amazonaws.com');
    assert.equal(u.pathname, `/runtimes/${encodeURIComponent(ARN)}/ws`);
    assert.ok(url.includes(`/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fdevbox_ada-AbC123/ws?`));
    assert.deepEqual([...u.searchParams.keys()], [
      'qualifier', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath',
    ]);
    assert.equal(u.searchParams.get('qualifier'), 'DEFAULT');
    assert.equal(u.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'), SESSION);
    const vscodePath = u.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath');
    assert.match(vscodePath, /^[A-Za-z0-9_-]+$/, 'base64url without padding');
    assert.equal(b64urlDecode(vscodePath), `${ROOT}?reconnectionToken=6f1c-9a&reconnection=false&skipWebSocketFrames=false`);
    assert.ok(!url.includes(token), 'the token is never in the URL');

    assert.equal(protocols.length, 2);
    assert.equal(protocols[1], 'base64UrlBearerAuthorization');
    assert.ok(protocols[0].startsWith('base64UrlBearerAuthorization.'));
    const encoded = protocols[0].slice('base64UrlBearerAuthorization.'.length);
    assert.match(encoded, /^[A-Za-z0-9_-]+$/);
    assert.equal(b64urlDecode(encoded), token);

    assert.equal(ws.url, VSCODE_URL, 'the page still sees the URL it asked for');
    assert.equal(ws.protocol, '');
  });

  test('uses ws:// for the local test base', () => {
    ctx.__devbox.agentcoreBase = 'http://localhost:9401';
    new ctx.WebSocket(VSCODE_URL);
    assert.ok(sockets[0].args[0].startsWith(`ws://localhost:9401/runtimes/${encodeURIComponent(ARN)}/ws?qualifier=DEFAULT&`));
  });

  test('reads the token when each socket is created', () => {
    new ctx.WebSocket(VSCODE_URL);
    token = fakeJwt('{"new"}', '{"uid":"00u1"}', 'new');
    new ctx.WebSocket(VSCODE_URL);
    const tokenOf = s => b64urlDecode(s.args[1][0].slice('base64UrlBearerAuthorization.'.length));
    assert.notEqual(tokenOf(sockets[0]), tokenOf(sockets[1]));
    assert.equal(tokenOf(sockets[1]), token);
  });

  test('leaves every other URL alone', () => {
    const others = [
      [`wss://other.example.com${ROOT}?reconnectionToken=x`],
      [`wss://${HOST}/something-else`],
      [`wss://${HOST}${ROOT}x?reconnectionToken=x`],
      ['wss://echo.example.com/', ['chat', 'v2']],
      ['wss://echo.example.com/', 'chat'],
    ];
    for (const args of others) {
      sockets = [];
      const ws = new ctx.WebSocket(...args);
      assert.equal(sockets[0].args[0], args[0]);
      assert.equal(sockets[0].argCount, args.length, 'protocols passed only when given');
      if (args.length > 1) assert.equal(sockets[0].args[1], args[1]);
      assert.equal(ws.url, args[0]);
      assert.equal(ws.protocol, 'base64UrlBearerAuthorization', 'native getter for pass-through sockets');
    }
  });

  test('passes the terminal\'s AgentCore /ws/shells socket through untouched', () => {
    const shells = `wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/${encodeURIComponent(ARN)}/ws/shells?qualifier=DEFAULT&shellId=claude-1&X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=${SESSION}`;
    const protocols = ['base64UrlBearerAuthorization.dG9r', 'base64UrlBearerAuthorization'];
    const local = `ws://localhost:9401/runtimes/${encodeURIComponent(ARN)}/ws/shells?qualifier=DEFAULT&shellId=claude-1`;
    // Even on this page's own host the path isn't under SERVER_ROOT.
    const sameHost = `wss://${HOST}/runtimes/${encodeURIComponent(ARN)}/ws/shells?shellId=claude-1`;
    for (const url of [shells, local, sameHost]) {
      sockets = [];
      assert.equal(ctx.WebSocket.devboxInternals.devboxTarget(url), null, url);
      const ws = new ctx.WebSocket(url, protocols);
      assert.equal(sockets[0].args[0], url);
      assert.deepEqual(sockets[0].args[1], protocols, 'the caller\'s subprotocols, not a second copy of the token');
      assert.equal(ws.url, url);
    }
  });

  test('passes through before the loader has set __devbox', () => {
    ctx.__devbox = undefined;
    new ctx.WebSocket(VSCODE_URL);
    assert.equal(sockets[0].args[0], VSCODE_URL);
  });

  test('also rewrites a sub-path of the server root', () => {
    new ctx.WebSocket(`wss://${HOST}${ROOT}/extra?reconnectionToken=x`);
    const u = new URL(sockets[0].args[0]);
    assert.equal(b64urlDecode(u.searchParams.get('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath')), `${ROOT}/extra?reconnectionToken=x`);
  });

  test('keeps the WebSocket API surface', () => {
    const { ws, native } = openSocket();
    assert.ok(ws instanceof sockets[0].constructor);
    assert.equal(ctx.WebSocket.OPEN, 1);
    assert.equal(ctx.WebSocket.CLOSED, 3);
    ws.binaryType = 'blob';
    assert.equal(native.binaryType, 'blob');
    const seen = [];
    const listener = e => seen.push(e.type);
    ws.addEventListener('message', listener);
    native.dispatchEvent(new Event('message'));
    ws.removeEventListener('message', listener);
    native.dispatchEvent(new Event('message'));
    assert.deepEqual(seen, ['message']);
    assert.equal(ws.readyState, 1);
  });
});

describe('sending', () => {
  test('send before open throws like the browser', () => {
    const ws = new ctx.WebSocket(VSCODE_URL);
    assert.throws(() => ws.send(new Uint8Array(4)), err => err.name === 'InvalidStateError');
  });

  test('splits an ArrayBuffer into 32000-byte messages', async () => {
    const { ws, native } = openSocket();
    const data = pattern(70000);
    ws.send(data.buffer);
    assert.deepEqual(await Promise.all(native.sent.map(m => bytesOf(m.data).then(b => b.length))), [32000, 32000, 6000]);
    assert.deepEqual(Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data)))), Buffer.from(data));
  });

  test('respects byteOffset and byteLength of views, and copies them', async () => {
    const { ws, native } = openSocket();
    const backing = pattern(100000);
    const view = new Uint8Array(backing.buffer, 1000, 50000);
    ws.send(view);
    backing.fill(0);
    const got = Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data))));
    assert.equal(got.length, 50000);
    assert.deepEqual(got, Buffer.from(pattern(100000).subarray(1000, 51000)));
    assert.ok(native.sent.every(m => m.data.byteLength <= 32000));

    native.sent = [];
    const dv = new DataView(pattern(40000).buffer, 7, 33000);
    ws.send(dv);
    const fromDv = Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data))));
    assert.deepEqual(fromDv, Buffer.from(pattern(40000).subarray(7, 33007)));
  });

  test('splits a Blob', async () => {
    const { ws, native } = openSocket();
    const data = pattern(40000, 3);
    ws.send(new Blob([data]));
    assert.equal(native.sent.length, 2);
    assert.ok(native.sent.every(m => m.data instanceof Blob));
    assert.deepEqual(native.sent.map(m => m.data.size), [32000, 8000]);
    assert.deepEqual(Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data)))), Buffer.from(data));
  });

  test('small messages go out immediately, one each', () => {
    const { ws, native } = openSocket();
    ws.send(new Uint8Array([1, 2, 3]));
    ws.send(new Uint8Array([4]));
    assert.equal(native.sent.length, 2);
    assert.equal(ws.bufferedAmount, 0);
  });

  test('text is sent as it is', () => {
    const { ws, native } = openSocket();
    ws.send('hello');
    assert.deepEqual(native.sent.map(m => m.data), ['hello']);
  });
});

describe('pacing', () => {
  test('never more than 200 messages in any second, and nothing is lost', async () => {
    const { ws, native } = openSocket();
    const chunks = [];
    for (let i = 0; i < 250; i++) {
      const c = pattern(32000, i);
      chunks.push(c);
      ws.send(c);
    }
    assert.equal(native.sent.length, 200);
    assert.equal(ws.bufferedAmount, 50 * 32000, 'queued bytes count as buffered');
    native.nativeBuffered = 17;
    assert.equal(ws.bufferedAmount, 50 * 32000 + 17, 'plus what the browser still buffers');
    clock.advance(999);
    assert.equal(native.sent.length, 200);
    clock.advance(1);
    assert.equal(native.sent.length, 250);
    assert.equal(ws.bufferedAmount, 17);
    const times = native.sent.map(m => m.at);
    for (let i = 0; i + 200 < times.length; i++) assert.ok(times[i + 200] - times[i] >= 1000);
    const got = Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data))));
    assert.deepEqual(got, Buffer.concat(chunks.map(c => Buffer.from(c))));
  });

  test('merges small messages that had to wait', async () => {
    const { ws, native } = openSocket();
    const all = [];
    for (let i = 0; i < 300; i++) {
      const c = pattern(100, i);
      all.push(c);
      ws.send(c);
    }
    assert.equal(native.sent.length, 200);
    clock.advance(1000);
    assert.equal(native.sent.length, 201, 'the 100 waiting messages leave as one');
    assert.equal(native.sent[200].data.byteLength, 100 * 100);
    const got = Buffer.concat(await Promise.all(native.sent.map(m => bytesOf(m.data))));
    assert.deepEqual(got, Buffer.concat(all.map(c => Buffer.from(c))));
  });

  test('merged messages still respect the size limit', async () => {
    const { ws, native } = openSocket();
    for (let i = 0; i < 200; i++) ws.send(new Uint8Array(1));
    ws.send(pattern(20000));
    ws.send(new Blob([pattern(20000, 5)]));
    ws.send(pattern(20000, 9));
    clock.advance(1000);
    const later = native.sent.slice(200);
    const sizes = await Promise.all(later.map(m => bytesOf(m.data).then(b => b.length)));
    assert.deepEqual(sizes, [32000, 28000]);
    assert.ok(later[0].data instanceof Blob, 'a Blob in the mix makes a Blob message');
  });
});

describe('closing', () => {
  test('close() waits for queued data, then closes with the same code and reason', () => {
    const { ws, native } = openSocket();
    for (let i = 0; i < 205; i++) ws.send(new Uint8Array(10));
    ws.close(1000, 'bye');
    assert.equal(native.closedWith, null);
    assert.equal(ws.readyState, 2, 'CLOSING while the queue drains');
    ws.send(new Uint8Array(10));
    clock.advance(1000);
    assert.equal(native.sent.length, 201, 'the 5 queued messages leave (merged); data sent after close() is dropped');
    assert.deepEqual(native.closedWith, [1000, 'bye']);
  });

  test('close() with nothing queued closes right away', () => {
    const { ws, native } = openSocket();
    ws.close();
    assert.deepEqual(native.closedWith, []);
  });

  test('close() rejects invalid codes', () => {
    const { ws } = openSocket();
    for (let i = 0; i < 201; i++) ws.send(new Uint8Array(1));
    assert.throws(() => ws.close(1006), err => err.name === 'InvalidAccessError');
  });

  test('a closed socket drops its queue and timer', () => {
    const { ws, native } = openSocket();
    for (let i = 0; i < 210; i++) ws.send(new Uint8Array(100));
    assert.ok(ws.bufferedAmount > 0);
    native.finish();
    assert.equal(ws.bufferedAmount, 0);
    assert.equal(clock.pending(), 0);
    ws.send(new Uint8Array(1));
    assert.equal(native.sent.length, 200);
  });
});
