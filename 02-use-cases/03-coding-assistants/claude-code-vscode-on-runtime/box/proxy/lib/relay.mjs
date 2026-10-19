// One browser WebSocket <-> one VS Code server WebSocket. Both directions carry VS Code's binary
// protocol, which both ends read as a byte stream.

import { WebSocket } from 'ws';
import { Pacer } from './pacer.mjs';

const UPSTREAM_MAX_MESSAGE = 64 * 1024 * 1024;
const EARLY_DATA_LIMIT = 16 * 1024 * 1024; // browser data that arrives before VS Code accepts
const PAUSE_ABOVE = 8 * 1024 * 1024;       // stop reading VS Code while this much waits for the browser
const RESUME_BELOW = 2 * 1024 * 1024;
// The VS Code server never answers a Close frame, so ws would keep its socket for its
// 30 s close timeout. The proxy ends it itself, after a moment for what's already queued to go out.
export const UPSTREAM_CLOSE_GRACE_MS = 250;

// Codes 1005, 1006 and 1015 describe a close; they can't be sent in one.
export function sendableCode(code) {
  if (code === 1000 || (code >= 1001 && code <= 1003) || (code >= 1007 && code <= 1014)) return code;
  if (code >= 3000 && code <= 4999) return code;
  return code === 1005 ? 1000 : 1011;
}

function shortReason(reason) {
  const buffer = Buffer.isBuffer(reason) ? reason : Buffer.from(String(reason ?? ''));
  return buffer.length <= 123 ? buffer : Buffer.alloc(0);
}

export function relay(browser, upstreamUrl, { busy, pacer: pacerOptions = {}, log = () => {} } = {}) {
  busy.opened();
  let finished = false;
  let paused = false;
  let upstreamClosed = null;
  let resumeTimer = null;
  let upstreamKill = null;
  const early = [];
  let earlyBytes = 0;

  const upstream = new WebSocket(upstreamUrl, {
    perMessageDeflate: false,
    maxPayload: UPSTREAM_MAX_MESSAGE,
    handshakeTimeout: 15_000,
  });

  const pacer = new Pacer((piece) => browser.send(piece, { binary: true }), {
    ...pacerOptions,
    onProgress: () => {
      checkBackpressure();
      if (upstreamClosed && pacer.pendingMessages === 0) closeBrowser(upstreamClosed.code, upstreamClosed.reason);
    },
  });

  function checkBackpressure() {
    const waiting = pacer.pendingBytes + browser.bufferedAmount;
    if (!paused && waiting > PAUSE_ABOVE) {
      paused = true;
      upstream.pause();
      // bufferedAmount drains without an event, so look again until we can resume.
      resumeTimer = setInterval(checkBackpressure, 100);
    } else if (paused && waiting < RESUME_BELOW) {
      paused = false;
      clearInterval(resumeTimer);
      resumeTimer = null;
      upstream.resume();
    }
  }

  function done() {
    if (finished) return;
    finished = true;
    if (resumeTimer) clearInterval(resumeTimer);
    pacer.stop();
    busy.closed();
  }

  function closeBrowser(code, reason) {
    if (browser.readyState === WebSocket.OPEN) browser.close(sendableCode(code), shortReason(reason));
    else if (browser.readyState === WebSocket.CONNECTING) browser.terminate();
  }

  // The Close frame is only politeness: VS Code ignores it, so the socket is destroyed a moment later.
  function closeUpstream(code, reason) {
    if (upstream.readyState === WebSocket.OPEN) {
      upstream.close(sendableCode(code), shortReason(reason));
      upstreamKill = setTimeout(() => upstream.terminate(), UPSTREAM_CLOSE_GRACE_MS);
      upstreamKill.unref();
    } else if (upstream.readyState === WebSocket.CONNECTING) {
      upstream.terminate();
    }
  }

  browser.on('message', (data, isBinary) => {
    if (upstream.readyState === WebSocket.OPEN) {
      upstream.send(data, { binary: isBinary });
    } else if (upstream.readyState === WebSocket.CONNECTING) {
      early.push([data, isBinary]);
      earlyBytes += data.length;
      if (earlyBytes > EARLY_DATA_LIMIT) {
        closeBrowser(1009, 'too much data before VS Code answered');
        closeUpstream(1001, '');
      }
    }
  });

  upstream.on('open', () => {
    for (const [data, isBinary] of early) upstream.send(data, { binary: isBinary });
    early.length = 0;
    earlyBytes = 0;
  });

  upstream.on('message', (data) => {
    pacer.push(Buffer.isBuffer(data) ? data : Buffer.from(data));
  });

  // Let what VS Code already sent reach the browser, then close with VS Code's code.
  upstream.on('close', (code, reason) => {
    if (upstreamKill) clearTimeout(upstreamKill);
    upstreamClosed = { code: code === 1006 ? 1011 : code, reason };
    if (pacer.pendingMessages === 0) closeBrowser(upstreamClosed.code, reason);
  });

  upstream.on('error', (err) => {
    log(`upstream websocket error: ${err.code ?? err.message}`);
  });

  browser.on('close', (code, reason) => {
    closeUpstream(code, reason);
    done();
  });

  browser.on('error', (err) => {
    log(`browser websocket error: ${err.code ?? err.message}`);
    closeUpstream(1011, '');
    browser.terminate();
    done();
  });

  return { upstream, pacer };
}
