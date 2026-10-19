// The /terminal page's wire format and connection state machine (web/devbox/terminal.js), with a fake
// WebSocket and fake timers. The browser half (xterm.js, fit, the real socket) is in
// test/browser/terminal.test.mjs.

import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { fakeTime, makeSandbox, runScript } from './sandbox.mjs';

const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_ada-AbC123';
const SESSION = `dbx-${'a'.repeat(64)}`;
const AWS = 'https://bedrock-agentcore.us-east-1.amazonaws.com';
// Fake tokens, built at run time.
const fakeJwt = (header, payload, signature) => [header, payload, signature].map(s => Buffer.from(s).toString('base64url')).join('.');
const TOKEN = fakeJwt('{"alg":"RS256"}', '{"uid":"00u1"}', 'signature');
const NEW_TOKEN = fakeJwt('{"new"}', '{"uid":"00u1"}', 'new');

const utf8 = text => Buffer.from(text, 'utf8');
const bytes = view => Array.from(view);
const text = view => Buffer.from(view).toString('utf8');
const b64url = s => Buffer.from(s).toString('base64url');
const flush = () => new Promise(resolve => setImmediate(resolve));

let T;

beforeEach(() => {
  const ctx = makeSandbox({});
  runScript(ctx, 'devbox/terminal.js');
  T = ctx.DevboxTerminal;
});

describe('shell id, URL and subprotocols', () => {
  test('the shell id is claude-<generation>, so a reload finds the same PTY', () => {
    assert.equal(T.shellIdFor(1), 'claude-1');
    assert.equal(T.shellIdFor(42), 'claude-42');
    for (const bad of [0, -1, 1.5, '1', NaN, null, undefined]) assert.throws(() => T.shellIdFor(bad), /generation/, String(bad));
  });

  test('the URL is AgentCore\'s /ws/shells with the ARN encoded and nothing secret in it', () => {
    const url = T.shellUrl({ agentcoreBase: AWS, runtimeArn: ARN, sessionId: SESSION, shellId: 'claude-1' });
    assert.equal(url, `wss://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/${encodeURIComponent(ARN)}/ws/shells`
      + `?qualifier=DEFAULT&shellId=claude-1&X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=${SESSION}`);
    assert.ok(url.includes('/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fdevbox_ada-AbC123/ws/shells?'));
    assert.ok(!/token|bearer|authorization/i.test(url));
    const u = new URL(url);
    assert.deepEqual([...u.searchParams.keys()], ['qualifier', 'shellId', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id']);
  });

  test('the local test base gives ws://', () => {
    const url = T.shellUrl({ agentcoreBase: 'http://localhost:9401/', runtimeArn: ARN, sessionId: SESSION, shellId: 'claude-2' });
    assert.ok(url.startsWith(`ws://localhost:9401/runtimes/${encodeURIComponent(ARN)}/ws/shells?qualifier=DEFAULT&shellId=claude-2&`));
  });

  test('refuses shell ids AgentCore would not take', () => {
    for (const shellId of ['', 'a?b', 'a#b', 'a&b', 'a b', 'x'.repeat(129), undefined]) {
      assert.throws(() => T.shellUrl({ agentcoreBase: AWS, runtimeArn: ARN, sessionId: SESSION, shellId }), /shell id/, String(shellId));
    }
    assert.doesNotThrow(() => T.shellUrl({ agentcoreBase: AWS, runtimeArn: ARN, sessionId: SESSION, shellId: 'x'.repeat(128) }));
  });

  test('the token travels only in the subprotocol, base64url without padding', () => {
    const protocols = Array.from(T.shellProtocols(TOKEN));
    assert.deepEqual(protocols, [`base64UrlBearerAuthorization.${b64url(TOKEN)}`, 'base64UrlBearerAuthorization']);
    assert.match(protocols[0], /^base64UrlBearerAuthorization\.[A-Za-z0-9_-]+$/);
    assert.equal(Buffer.from(protocols[0].split('.')[1], 'base64url').toString(), TOKEN);
  });

  test('refuses a missing token and one over AgentCore\'s 4096-character limit', () => {
    assert.throws(() => T.shellProtocols(''), /no access token/);
    assert.throws(() => T.shellProtocols(undefined), /no access token/);
    const limit = 'a'.repeat(3072);            // encodes to exactly 4096 characters
    assert.equal(T.shellProtocols(limit)[0].length, 'base64UrlBearerAuthorization.'.length + 4096);
    assert.throws(() => T.shellProtocols(`${limit}abc`), /too long.*4100 characters.*at most 4096/);
  });
});

describe('framing', () => {
  test('one channel byte, then the payload', () => {
    assert.deepEqual({ ...T.CHANNEL }, { STDIN: 0, STDOUT: 1, STDERR: 2, STATUS: 3, RESIZE: 4, HEARTBEAT: 5, CLOSE: 255 });
    assert.deepEqual(bytes(T.encodeFrame(T.CHANNEL.HEARTBEAT)), [5]);
    assert.deepEqual(bytes(T.encodeFrame(T.CHANNEL.STDIN, utf8('hi'))), [0, 0x68, 0x69]);
    const resize = T.resizeFrame(120, 40);
    assert.equal(resize[0], 4);
    assert.deepEqual(JSON.parse(text(resize.subarray(1))), { width: 120, height: 40 });
  });

  test('a message is at most 64 KB', () => {
    assert.doesNotThrow(() => T.encodeFrame(0, new Uint8Array(65535)));
    assert.throws(() => T.encodeFrame(0, new Uint8Array(65536)), { name: 'RangeError', message: /at most 65536 bytes/ });
  });

  test('decodes ArrayBuffers and views (respecting the offset)', () => {
    const buf = new Uint8Array([9, 9, 1, 0x6f, 0x6b]).buffer;
    const frame = T.decodeFrame(new Uint8Array(buf, 2));
    assert.equal(frame.channel, 1);
    assert.equal(text(frame.payload), 'ok');
    assert.equal(T.decodeFrame(new Uint8Array([3]).buffer).payload.length, 0);
    assert.equal(T.decodeFrame(new ArrayBuffer(0)), null);
    assert.equal(T.decodeFrame(null), null);
  });

  test('STDIN is UTF-8, split into messages well under 64 KB, and never inside a character', () => {
    assert.deepEqual(Array.from(T.stdinFrames('')), []);
    const [one] = T.stdinFrames('héllo');
    assert.deepEqual(bytes(one), [0, ...utf8('héllo')]);

    const ascii = 'x'.repeat(100_000);
    const frames = Array.from(T.stdinFrames(ascii));
    assert.equal(frames.length, 4);
    assert.ok(frames.every(f => f[0] === 0 && f.length <= T.STDIN_CHUNK_BYTES + 1 && f.length < 65536));
    assert.equal(frames.map(f => text(f.subarray(1))).join(''), ascii);

    // 3-byte and 4-byte characters straddling every chunk boundary.
    const mixed = `a${'€😀'.repeat(20_000)}`;
    const parts = Array.from(T.stdinFrames(mixed, 1000));
    const decoder = new TextDecoder('utf-8', { fatal: true });
    assert.ok(parts.length > 100);
    for (const part of parts) {
      assert.ok(part.length - 1 <= 1000);
      assert.doesNotThrow(() => decoder.decode(part.subarray(1)), 'each message is whole characters');
    }
    assert.equal(parts.map(p => text(p.subarray(1))).join(''), mixed);
  });

  test('binary strings from xterm keep one byte per character', () => {
    assert.deepEqual(bytes(T.binaryStringBytes('\x1b[M\xff\x80')), [0x1b, 0x5b, 0x4d, 0xff, 0x80]);
  });

  test('STATUS: a connect confirmation, a transient error, or the shell ending', () => {
    const c = s => JSON.parse(JSON.stringify(T.classifyStatus(s)));
    assert.deepEqual(c({ metadata: { shellId: 'claude-1', reconnected: false } }), { kind: 'connected', shellId: 'claude-1', reconnected: false });
    assert.deepEqual(c({ metadata: { shellId: 'claude-1', reconnected: true } }), { kind: 'connected', shellId: 'claude-1', reconnected: true });
    assert.equal(c({ metadata: { shellId: 'claude-1' } }).reconnected, false);
    assert.deepEqual(c({ status: 'Failure', reason: 'InternalError', code: 500, message: 'try again' }), { kind: 'transient', message: 'try again' });
    assert.deepEqual(c({ metadata: {}, status: 'Success' }), { kind: 'ended', exit: { code: 0, signal: null, message: '' } });
    assert.deepEqual(c({ status: 'Failure', reason: 'NonZeroExitCode', details: { causes: [{ reason: 'ExitCode', message: '2' }] } }).exit, { code: 2, signal: null, message: '' });
    assert.deepEqual(c({ status: 'Failure', details: { causes: [{ reason: 'Signal', message: '9' }] } }).exit, { code: null, signal: '9', message: '' });
    assert.deepEqual(c({ status: 'Failure', message: 'shell limit reached' }).exit, { code: null, signal: null, message: 'shell limit reached' });

    assert.equal(T.endedText({ code: 0, signal: null, message: '' }), 'The terminal ended (exit 0). Press Enter to start a new one.');
    assert.equal(T.endedText({ code: 127, signal: null, message: '' }), 'The terminal ended (exit 127). Press Enter to start a new one.');
    assert.equal(T.endedText({ code: null, signal: '9', message: '' }), 'The terminal ended (signal 9). Press Enter to start a new one.');
    assert.equal(T.endedText({ code: null, signal: null, message: 'gone' }), 'The terminal ended (exit unknown): gone. Press Enter to start a new one.');

    assert.equal(T.parseStatus(utf8('not json')), null);
    assert.equal(T.parseStatus(utf8('[1]')), null);
    assert.equal(T.parseStatus(utf8('{"status":"Success"}')).status, 'Success');
  });
});

describe('fit', () => {
  test('cols and rows from the space and the cell size, leaving room for the scrollbar', () => {
    assert.deepEqual(JSON.parse(JSON.stringify(T.fitSize({ width: 800, height: 600 }, { width: 9, height: 17 }))), { cols: 87, rows: 35 });
    assert.deepEqual(JSON.parse(JSON.stringify(T.fitSize({ width: 800, height: 600 }, { width: 9, height: 17 }, 0))), { cols: 88, rows: 35 });
    assert.deepEqual(JSON.parse(JSON.stringify(T.fitSize({ width: 10, height: 5 }, { width: 9, height: 17 }))), { cols: 2, rows: 1 }, 'xterm minimums');
    assert.equal(T.fitSize({ width: 800, height: 600 }, { width: 0, height: 17 }), null, 'not measured yet');
    assert.equal(T.fitSize({ width: 0, height: 0 }, { width: 9, height: 17 }), null, 'not laid out yet');
  });
});

// ---- the connection ----

class FakeSocket extends EventTarget {
  constructor(url, protocols) {
    super();
    this.url = url;
    this.protocols = Array.from(protocols);
    this.readyState = 0;
    this.binaryType = 'blob';
    this.sent = [];
    this.closeCalls = [];
    FakeSocket.all.push(this);
  }

  send(data) {
    if (this.readyState !== 1) throw new Error(`send in state ${this.readyState}`);
    this.sent.push(Uint8Array.from(data));
  }

  close(code) {
    this.closeCalls.push(code);
    if (this.readyState < 2) this.readyState = 2;
  }

  // The server's side.
  open() {
    this.readyState = 1;
    this.dispatchEvent(new Event('open'));
  }

  receive(channel, payload = []) {
    const ev = new Event('message');
    ev.data = Uint8Array.from([channel, ...payload]).buffer;
    this.dispatchEvent(ev);
  }

  status(obj) {
    this.receive(3, utf8(JSON.stringify(obj)));
  }

  confirm(reconnected = false, shellId = 'claude-1') {
    this.status({ kind: 'Status', metadata: { shellId, reconnected } });
  }

  drop(code = 1006, reason = '') {
    this.readyState = 3;
    const ev = new Event('close');
    ev.code = code;
    ev.reason = reason;
    this.dispatchEvent(ev);
  }

  // What the page sent, decoded.
  frames() {
    return this.sent.map(f => ({ channel: f[0], payload: f.subarray(1) }));
  }

  stdin() {
    return this.frames().filter(f => f.channel === 0).map(f => text(f.payload)).join('');
  }

  resizes() {
    return this.frames().filter(f => f.channel === 4).map(f => JSON.parse(text(f.payload)));
  }

  heartbeats() {
    return this.frames().filter(f => f.channel === 5 && f.payload.length === 0).length;
  }
}

function harness({ tokens } = {}) {
  FakeSocket.all = [];
  const clock = fakeTime();
  const h = {
    clock,
    outputs: [],
    errors: [],
    states: [],
    ended: [],
    connected: [],
    tokenCalls: 0,
    refreshes: 0,
    size: { cols: 100, rows: 30 },
    tokens: tokens || [TOKEN],
    get sockets() { return FakeSocket.all; },
    get last() { return FakeSocket.all.at(-1); },
  };
  h.session = T.createShellSession({
    WebSocket: FakeSocket,
    url: () => T.shellUrl({ agentcoreBase: AWS, runtimeArn: ARN, sessionId: SESSION, shellId: 'claude-1' }),
    getToken: async () => h.tokens[Math.min(h.tokenCalls++, h.tokens.length - 1)],
    refresh: async () => { h.refreshes++; },
    size: () => h.size,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    random: () => 1,
    onOutput: b => h.outputs.push(text(b)),
    onErrorText: t => h.errors.push(t),
    onConnected: info => h.connected.push({ ...info }),
    onEnded: t => h.ended.push(t),
    onState: s => h.states.push({ ...s }),
  });
  h.stateNames = () => h.states.map(s => s.state);
  return h;
}

async function started(h) {
  h.session.start();
  await flush();
  return h.last;
}

describe('connecting and starting Claude Code', () => {
  test('opens the shell URL with the token subprotocol, then starts Claude Code once the shell is confirmed', async () => {
    const h = harness();
    const ws = await started(h);
    assert.equal(h.sockets.length, 1);
    assert.equal(ws.url, T.shellUrl({ agentcoreBase: AWS, runtimeArn: ARN, sessionId: SESSION, shellId: 'claude-1' }));
    assert.ok(!ws.url.includes(TOKEN));
    assert.deepEqual(ws.protocols, [`base64UrlBearerAuthorization.${b64url(TOKEN)}`, 'base64UrlBearerAuthorization']);
    assert.equal(ws.binaryType, 'arraybuffer');
    assert.equal(h.session.state, 'connecting');

    ws.open();
    assert.equal(ws.sent.length, 0, 'nothing is sent before AgentCore confirms the shell');
    ws.confirm(false);
    assert.equal(h.session.state, 'connected');
    assert.deepEqual(ws.resizes(), [{ width: 100, height: 30 }], 'sized before anything runs in it');
    assert.equal(ws.stdin(), 'exec /usr/local/bin/devbox-claude\r');
    assert.deepEqual(ws.frames().map(f => f.channel), [4, 0], 'resize first, then the command');
    assert.deepEqual(h.connected, [{ reconnected: false, shellId: 'claude-1' }]);

    ws.confirm(false);
    assert.equal(ws.stdin(), 'exec /usr/local/bin/devbox-claude\r', 'a second confirmation on the same socket starts nothing');
  });

  test('output goes to the terminal, AgentCore text to the dim channel, heartbeat echoes nowhere', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.receive(1, utf8('hello \u001b[1mworld'));
    ws.receive(5);
    const euro = utf8('€ note\n');
    ws.receive(2, euro.subarray(0, 2));
    ws.receive(2, euro.subarray(2));
    ws.receive(0x42, [1, 2, 3]);
    assert.deepEqual(h.outputs, ['hello \u001b[1mworld']);
    assert.equal(h.errors.join(''), '€ note\n', 'STDERR is decoded as a stream (a character split over two messages)');
    ws.receive(1);
    assert.equal(h.outputs.length, 1, 'empty output is not written');
  });

  test('typing goes out as STDIN, big pastes in several messages', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.sent.length = 0;
    h.session.input('ls\r');
    h.session.input('x'.repeat(70_000));
    h.session.input('\x1b[M !!', true);
    const stdin = ws.frames().filter(f => f.channel === 0);
    assert.equal(text(stdin[0].payload), 'ls\r');
    assert.equal(stdin.length, 1 + 3 + 1);
    assert.ok(stdin.every(f => f.payload.length <= T.STDIN_CHUNK_BYTES));
    assert.deepEqual(bytes(stdin.at(-1).payload), [0x1b, 0x5b, 0x4d, 0x20, 0x21, 0x21]);
  });

  test('a resize is sent while connected, and not otherwise', async () => {
    const h = harness();
    const ws = await started(h);
    h.session.resize(80, 24);
    ws.open();
    h.session.resize(80, 24);
    assert.equal(ws.sent.length, 0);
    ws.confirm(false);
    h.session.resize(132, 50);
    assert.deepEqual(ws.resizes().at(-1), { width: 132, height: 50 });
  });
});

describe('heartbeat', () => {
  test('one every 30 s while the socket is open, and none after it closes', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    h.clock.advance(29_999);
    assert.equal(ws.heartbeats(), 0);
    h.clock.advance(1);
    assert.equal(ws.heartbeats(), 1);
    h.clock.advance(60_000);
    assert.equal(ws.heartbeats(), 3);
    assert.ok(ws.frames().filter(f => f.channel === 5).every(f => f.payload.length === 0), 'heartbeats are empty');
    ws.receive(5);
    assert.deepEqual(h.outputs, [], 'the echo is ignored');

    h.session.stop();
    ws.drop(1000);
    h.clock.advance(120_000);
    assert.equal(ws.heartbeats(), 3);
  });

  test('starts at open, before the shell is confirmed', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    h.clock.advance(30_000);
    assert.equal(ws.heartbeats(), 1);
  });
});

describe('reconnecting', () => {
  test('a dropped terminal reattaches to the same PTY: a fresh token, a repaint, no second Claude Code', async () => {
    const h = harness({ tokens: [TOKEN, NEW_TOKEN] });
    const first = await started(h);
    first.open();
    first.confirm(false);
    first.drop(1006);
    assert.equal(h.session.state, 'retrying');
    assert.equal(h.states.at(-1).inMs, 1000, 'a working terminal comes back quickly');

    h.session.input('typed while away');
    h.clock.advance(1000);
    await flush();
    assert.equal(h.sockets.length, 2);
    const second = h.last;
    assert.equal(second.url, first.url, 'the same shellId');
    assert.equal(Buffer.from(second.protocols[0].split('.').slice(1).join('.'), 'base64url').toString(), NEW_TOKEN, 'a fresh token');
    assert.equal(h.tokenCalls, 2);

    second.open();
    second.confirm(true);
    assert.equal(h.session.state, 'connected');
    assert.deepEqual(second.resizes(), [{ width: 100, height: 29 }, { width: 100, height: 30 }], 'one row away and back, so tmux repaints');
    assert.equal(second.stdin(), 'typed while away', 'no exec /usr/local/bin/devbox-claude on a reattach; what was typed meanwhile is sent');
    assert.deepEqual(h.connected.map(c => c.reconnected), [false, true]);
    assert.equal(h.session.failures, 0);
  });

  test('a one-row terminal is nudged down and back up', async () => {
    const h = harness();
    h.size = { cols: 40, rows: 1 };
    const ws = await started(h);
    ws.open();
    ws.confirm(true);
    assert.deepEqual(ws.resizes(), [{ width: 40, height: 2 }, { width: 40, height: 1 }]);
  });

  test('a new PTY after a drop (the box restarted) starts Claude Code again and drops stale typing', async () => {
    const h = harness();
    const first = await started(h);
    first.open();
    first.confirm(false);
    first.drop(1006);
    h.session.input('stale');
    h.clock.advance(1000);
    await flush();
    h.last.open();
    h.last.confirm(false);
    assert.equal(h.last.stdin(), 'exec /usr/local/bin/devbox-claude\r');
  });

  test('AgentCore\'s CLOSE and AgentCore\'s 1-hour cutoff on a working terminal reattach too', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.receive(0xff);
    ws.drop(1000);
    assert.equal(h.session.state, 'retrying');
    assert.equal(h.session.failures, 0);
    h.clock.advance(1000);
    await flush();
    assert.equal(h.sockets.length, 2);
  });

  test('five attempts that never get a shell stop with the reason; the token is refreshed once; Retry starts over', async () => {
    const h = harness();
    await started(h);
    const waits = [];
    for (let i = 0; i < 5; i++) {
      h.last.drop(1006);   // refused at the handshake: the browser never sees the HTTP status
      if (i < 4) {
        waits.push(h.states.at(-1).inMs);
        h.clock.advance(h.states.at(-1).inMs);
        await flush();
      }
    }
    assert.deepEqual(waits, [1000, 2000, 4000, 8000]);
    assert.equal(h.sockets.length, 5);
    assert.equal(h.session.state, 'failed');
    const failed = h.states.at(-1);
    assert.equal(failed.state, 'failed');
    assert.match(failed.reason, /did not accept the terminal connection \(code 1006\)/);
    assert.match(failed.reason, /InvokeAgentRuntimeCommandShell/);
    assert.equal(h.refreshes, 1, 'one forced refresh per run of failures');
    assert.equal(h.tokenCalls, 5, 'every attempt asks for a fresh token');
    h.clock.advance(600_000);
    await flush();
    assert.equal(h.sockets.length, 5, 'no more attempts on its own');
    h.session.input('x');
    assert.equal(h.sockets.length, 5, 'typing does not retry');

    h.session.retry();
    await flush();
    assert.equal(h.refreshes, 2, 'Retry refreshes the token first');
    assert.equal(h.sockets.length, 6);
    assert.equal(h.states.at(-1).attempt, 1, 'the count starts over');
    h.last.open();
    h.last.confirm(true);
    assert.equal(h.session.state, 'connected');
  });

  test('the reason says what AgentCore said before closing', async () => {
    const h = harness();
    await started(h);
    for (let i = 0; i < 5; i++) {
      h.last.open();
      h.last.receive(2, utf8('container is not running\n'));
      h.last.drop(1011, 'upstream');
      if (i < 4) {
        h.clock.advance(h.states.at(-1).inMs);
        await flush();
      }
    }
    assert.match(h.states.at(-1).reason, /closed before the shell started \(code 1011: upstream\)\. AgentCore said: container is not running$/);
    assert.equal(h.refreshes, 0, 'the handshake worked, so the token is fine');
  });

  test('a transient AgentCore error (InternalError, 500) is retried with backoff', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.status({ status: 'Failure', reason: 'InternalError', code: 500, message: 'try later' });
    assert.deepEqual(ws.closeCalls, [1000]);
    ws.drop(1000);
    assert.equal(h.session.state, 'retrying');
    assert.equal(h.session.failures, 1);
    assert.match(h.states.at(-1).reason, /temporary error \(try later\)/);
    assert.deepEqual(h.ended, [], 'not reported as the shell ending');
  });

  test('no confirmation within 60 s closes the attempt and counts it', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    h.clock.advance(T.CONNECT_TIMEOUT_MS - 1);
    assert.deepEqual(ws.closeCalls, []);
    h.clock.advance(1);
    assert.deepEqual(ws.closeCalls, [1000]);
    ws.drop(1006);
    assert.equal(h.session.failures, 1);
    assert.match(h.states.at(-1).reason, /within 60 s/);
  });

  test('no token: the attempt fails without opening a socket', async () => {
    const h = harness({ tokens: ['', TOKEN] });
    await started(h);
    assert.equal(h.sockets.length, 0);
    assert.equal(h.session.state, 'retrying');
    assert.match(h.states.at(-1).reason, /no access token/);
    h.clock.advance(1000);
    await flush();
    assert.equal(h.sockets.length, 1);
    assert.equal(h.refreshes, 1);
  });

  test('stop() closes the socket and nothing reconnects', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    h.session.stop();
    assert.deepEqual(ws.closeCalls, [1000]);
    ws.drop(1000);
    h.clock.advance(120_000);
    await flush();
    assert.equal(h.sockets.length, 1);
    assert.equal(h.session.state, 'stopped');
  });
});

describe('when the shell ends', () => {
  test('says so with the exit code, waits for Enter, then starts a new shell with Claude Code', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.status({ metadata: {}, status: 'Failure', reason: 'NonZeroExitCode', details: { causes: [{ reason: 'ExitCode', message: '1' }] } });
    assert.equal(h.session.state, 'ended');
    assert.deepEqual(h.ended, ['The terminal ended (exit 1). Press Enter to start a new one.']);
    assert.deepEqual(ws.closeCalls, [1000]);
    ws.drop(1000);
    h.clock.advance(600_000);
    await flush();
    assert.equal(h.sockets.length, 1, 'no reconnect on its own');

    h.session.input('q');
    await flush();
    assert.equal(h.sockets.length, 1, 'other keys do nothing');
    assert.equal(ws.stdin(), 'exec /usr/local/bin/devbox-claude\r', 'and are not sent anywhere');

    h.session.input('\r');
    await flush();
    assert.equal(h.sockets.length, 2);
    const next = h.last;
    assert.equal(next.url, ws.url, 'the same shellId gives a new shell once the old one has ended');
    next.open();
    next.confirm(false);
    assert.equal(next.stdin(), 'exec /usr/local/bin/devbox-claude\r');
  });

  test('a clean exit reads exit 0', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.status({ metadata: {}, status: 'Success' });
    assert.deepEqual(h.ended, ['The terminal ended (exit 0). Press Enter to start a new one.']);
  });

  test('Enter before the old socket has closed still starts cleanly', async () => {
    const h = harness();
    const ws = await started(h);
    ws.open();
    ws.confirm(false);
    ws.status({ status: 'Success' });
    h.session.input('\r');
    await flush();
    ws.drop(1000);   // the old socket's close arrives late
    assert.equal(h.session.state, 'connecting');
    assert.equal(h.sockets.length, 2);
  });
});
