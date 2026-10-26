// Unit tests for the pure parts of the proxy: target decoding, /ping transitions, chunking, pacing and
// the peer address family.

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BUSY_TAIL_MS, BusyTracker } from '../proxy/lib/busy.mjs';
import { MAX_CHUNK, Pacer, chunks } from '../proxy/lib/pacer.mjs';
import { peerFamily } from '../proxy/lib/app.mjs';
import { sendableCode } from '../proxy/lib/relay.mjs';
import { base64UrlDecode, parseTarget } from '../proxy/lib/target.mjs';
import { FakeClock, SERVER_ROOT, b64url } from './helpers.mjs';

test('target: accepts the server root with the three VS Code keys and re-serializes them', () => {
  const t = parseTarget(b64url(`${SERVER_ROOT}?reconnectionToken=abc-123&reconnection=true&skipWebSocketFrames=false`), SERVER_ROOT);
  assert.deepEqual(t, { ok: true, path: SERVER_ROOT, query: 'reconnectionToken=abc-123&reconnection=true&skipWebSocketFrames=false' });
  assert.deepEqual(parseTarget(b64url(SERVER_ROOT), SERVER_ROOT), { ok: true, path: SERVER_ROOT, query: '' });
});

test('target: rejects any other path, key, repeated key or value', () => {
  const bad = [
    '/',
    `${SERVER_ROOT}/`,
    `${SERVER_ROOT}/vscode-remote-resource?path=/etc/passwd`,
    `/stable-other?reconnectionToken=a`,
    `${SERVER_ROOT}/../x`,
    `${SERVER_ROOT}?reconnectionToken=a&tkn=secret`,
    `${SERVER_ROOT}?reconnectionToken=a&reconnectionToken=b`,
    `${SERVER_ROOT}?reconnection=maybe`,
    `${SERVER_ROOT}?skipWebSocketFrames=true`,
    `${SERVER_ROOT}?reconnectionToken=a%20b`,
    `${SERVER_ROOT}?reconnectionToken=${'a'.repeat(129)}`,
    `${SERVER_ROOT} ?reconnection=true`,
  ];
  for (const text of bad) assert.equal(parseTarget(b64url(text), SERVER_ROOT).ok, false, text);
  assert.equal(parseTarget('not base64url!', SERVER_ROOT).ok, false);
  assert.equal(parseTarget('', SERVER_ROOT).ok, false);
  assert.equal(parseTarget(undefined, SERVER_ROOT).ok, false);
  assert.equal(base64UrlDecode('a'.repeat(5000)), null);
});

test('ping: Healthy at start, HealthyBusy while a socket is open and for 120 s after the last closes', () => {
  const clock = new FakeClock(1_700_000_000_000);
  const busy = new BusyTracker(clock.now);
  const start = busy.ping();
  assert.deepEqual(start, { status: 'Healthy', time_of_last_update: 1_700_000_000 });

  clock.advance(5_000);
  assert.deepEqual(busy.ping(), start, 'time_of_last_update must not move without a status change');

  busy.opened();
  assert.deepEqual(busy.ping(), { status: 'HealthyBusy', time_of_last_update: 1_700_000_005 });
  clock.advance(1_000);
  busy.opened();                        // a second socket doesn't change anything
  busy.closed();
  clock.advance(60_000);
  assert.deepEqual(busy.ping(), { status: 'HealthyBusy', time_of_last_update: 1_700_000_005 });

  busy.closed();                        // last one closes at t0 + 66 s
  clock.advance(BUSY_TAIL_MS - 1);
  assert.equal(busy.ping().status, 'HealthyBusy');
  clock.advance(1);
  assert.deepEqual(busy.ping(), { status: 'Healthy', time_of_last_update: 1_700_000_066 + 120 });

  clock.advance(3_600_000);             // the change time is the end of the tail, not when we looked
  assert.deepEqual(busy.ping(), { status: 'Healthy', time_of_last_update: 1_700_000_186 });
});

test('ping: first look long after the tail still reports when the tail ended', () => {
  const clock = new FakeClock(1_700_000_000_000);
  const busy = new BusyTracker(clock.now);
  busy.opened();
  clock.advance(1_000);
  busy.closed();
  clock.advance(170_000);
  assert.deepEqual(busy.ping(), { status: 'Healthy', time_of_last_update: 1_700_000_001 + 120 });
});

test('ping: a reconnect inside the tail keeps HealthyBusy and its original timestamp', () => {
  const clock = new FakeClock(1_700_000_000_000);
  const busy = new BusyTracker(clock.now);
  busy.opened();
  clock.advance(10_000);
  busy.closed();
  clock.advance(100_000);
  busy.opened();
  clock.advance(200_000);
  assert.deepEqual(busy.ping(), { status: 'HealthyBusy', time_of_last_update: 1_700_000_000 });
  busy.closed();
  busy.closed();                        // an extra close never goes negative
  assert.equal(busy.open, 0);
});

test('ping: a Claude turn counts like an open socket, and its end starts the same tail', () => {
  const clock = new FakeClock(1_700_000_000_000);
  const busy = new BusyTracker(clock.now);
  busy.opened();
  clock.advance(1_000);
  busy.agent(true);                     // a turn starts while the tab is open
  busy.closed();                        // then the laptop closes
  clock.advance(10 * 3_600_000);
  assert.deepEqual(busy.ping(), { status: 'HealthyBusy', time_of_last_update: 1_700_000_000 });
  busy.agent(true);                     // told again on every ping: nothing changes
  busy.agent(false);                    // the turn ends ten hours later
  clock.advance(BUSY_TAIL_MS - 1);
  assert.equal(busy.ping().status, 'HealthyBusy');
  clock.advance(1);
  assert.deepEqual(busy.ping(), { status: 'Healthy', time_of_last_update: 1_700_036_001 + 120 });

  busy.opened();                        // a turn ending while a tab is open leaves it to the socket
  busy.agent(true);
  busy.agent(false);
  clock.advance(BUSY_TAIL_MS * 2);
  assert.equal(busy.ping().status, 'HealthyBusy');
  assert.equal(busy.agentBusy, false);
});

test('peer family: IPv4, IPv4 through the dual-stack listener, IPv6', () => {
  assert.equal(peerFamily('100.88.0.1'), 'IPv4');
  assert.equal(peerFamily('::ffff:172.17.0.1'), 'IPv4');
  assert.equal(peerFamily('::1'), 'IPv6');
  assert.equal(peerFamily('fd00:ec2::254'), 'IPv6');
  assert.equal(peerFamily(undefined), null);
});

test('chunks: pieces are at most 32000 bytes and rebuild the message', () => {
  const message = Buffer.alloc(262_144, 7);
  const pieces = [...chunks(message)];
  assert.equal(MAX_CHUNK, 32_000);
  assert.equal(pieces.length, Math.ceil(262_144 / 32_000));
  assert.ok(pieces.every((p) => p.length <= 32_000));
  assert.deepEqual(Buffer.concat(pieces), message);
  assert.deepEqual([...chunks(Buffer.alloc(0))].map((p) => p.length), [0]);
  assert.deepEqual([...chunks(Buffer.alloc(32_000))].map((p) => p.length), [32_000]);
  assert.deepEqual([...chunks(Buffer.alloc(32_001))].map((p) => p.length), [32_000, 1]);
});

test('pacer: never more than 200 sends in any 1-second window, and nothing is lost', () => {
  const clock = new FakeClock(0);
  const sentAt = [];
  const out = [];
  const pacer = new Pacer((piece) => { sentAt.push(clock.t); out.push(piece); },
    { now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  const message = Buffer.alloc(1000 * 32_000);
  for (let i = 0; i < message.length; i++) message[i] = i % 251;
  pacer.push(message);
  assert.equal(sentAt.length, 200, 'first burst stops at the limit');
  for (let i = 0; i < 20 && pacer.pendingMessages > 0; i++) {
    clock.advance(137);                 // odd steps so window edges don't line up with timers
    pacer.push(Buffer.alloc(0));        // pushes can arrive while throttled
  }
  clock.advance(10_000);
  assert.equal(pacer.pendingMessages, 0);
  const data = out.filter((p) => p.length > 0);
  assert.deepEqual(Buffer.concat(data), message);
  for (let i = 0; i < sentAt.length; i++) {
    let inWindow = 0;
    for (let j = i; j < sentAt.length && sentAt[j] - sentAt[i] < 1000; j++) inWindow++;
    assert.ok(inWindow <= 200, `window starting at ${sentAt[i]} ms has ${inWindow} sends`);
  }
  assert.ok(sentAt.at(-1) >= 5000, 'about 1020 messages need at least 5 full windows');
});

test('pacer: stop drops the queue and cancels the timer', () => {
  const clock = new FakeClock(0);
  let sent = 0;
  const pacer = new Pacer(() => { sent++; }, { now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  pacer.push(Buffer.alloc(300 * 32_000));
  pacer.stop();
  clock.advance(5_000);
  assert.equal(sent, 200);
  assert.equal(pacer.pendingBytes, 0);
});

test('close codes: reserved codes are mapped to ones that can be sent', () => {
  assert.equal(sendableCode(1000), 1000);
  assert.equal(sendableCode(1008), 1008);
  assert.equal(sendableCode(4001), 4001);
  assert.equal(sendableCode(1005), 1000);
  assert.equal(sendableCode(1006), 1011);
  assert.equal(sendableCode(1015), 1011);
  assert.equal(sendableCode(1004), 1011);
});
