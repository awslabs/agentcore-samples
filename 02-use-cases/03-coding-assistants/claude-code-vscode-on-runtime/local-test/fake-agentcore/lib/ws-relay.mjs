// Pipes a caller WebSocket to the container WebSocket the way the AgentCore edge does: it terminates both
// connections, enforces the per-connection frame size and frame rate, caps the connection duration, and
// closes both sides together. ws hands us whole messages, so each message counts as one frame (a
// fragmented message would be several frames at the real edge; browsers and ws never fragment ours).

// Codes that can't be sent in a close frame get a sendable stand-in.
function sendableCode(code) {
  if (code === 1005) return 1000;
  if (code === 1006 || code === 1015) return 1011;
  if ((code >= 1000 && code <= 1003) || (code >= 1007 && code <= 1014) || (code >= 3000 && code <= 4999)) return code;
  return 1011;
}

function rateWindow(limit) {
  const stamps = [];
  return () => {
    const t = Date.now();
    while (stamps.length && t - stamps[0] >= 1000) stamps.shift();
    stamps.push(t);
    return stamps.length > limit;
  };
}

// limits: { maxFrameBytes, maxFramesPerSecond, rateScope: 'direction'|'connection', maxSeconds }
// onEvent(kind, detail) is called for 'violation' and 'closed'.
export function relay(caller, box, limits, onEvent = () => {}) {
  const stats = { callerToBox: 0, boxToCaller: 0, openedAt: Date.now() };
  const overRate = limits.rateScope === 'connection'
    ? (() => { const shared = rateWindow(limits.maxFramesPerSecond); return { c2b: shared, b2c: shared }; })()
    : { c2b: rateWindow(limits.maxFramesPerSecond), b2c: rateWindow(limits.maxFramesPerSecond) };
  let closing = false;

  function closeBoth(code, reason, cause) {
    if (closing) return;
    closing = true;
    clearTimeout(maxTimer);
    const c = sendableCode(code);
    for (const ws of [caller, box]) {
      try { ws.close(c, reason); } catch { ws.terminate(); }
    }
    // Don't wait forever for a close handshake from a peer that has gone quiet.
    setTimeout(() => { caller.terminate(); box.terminate(); }, 2000).unref();
    onEvent('closed', { code: c, reason, cause, seconds: (Date.now() - stats.openedAt) / 1000, ...stats });
  }

  function forward(from, to, direction, data, isBinary) {
    if (closing) return;
    const size = data.length ?? data.byteLength;
    if (size > limits.maxFrameBytes) {
      onEvent('violation', { direction, kind: 'frame-size', size });
      return closeBoth(1009, 'Message too big', `${direction} frame of ${size} bytes`);
    }
    if (overRate[direction === 'caller->box' ? 'c2b' : 'b2c']()) {
      onEvent('violation', { direction, kind: 'frame-rate' });
      return closeBoth(1008, 'Policy violation: frame rate limit exceeded', `${direction} over ${limits.maxFramesPerSecond} frames/s`);
    }
    if (direction === 'caller->box') stats.callerToBox += 1; else stats.boxToCaller += 1;
    to.send(data, { binary: isBinary });
  }

  caller.on('message', (data, isBinary) => forward(caller, box, 'caller->box', data, isBinary));
  box.on('message', (data, isBinary) => forward(box, caller, 'box->caller', data, isBinary));
  caller.on('close', (code, reason) => closeBoth(code, reason.toString(), 'caller closed'));
  box.on('close', (code, reason) => closeBoth(code, reason.toString(), 'box closed'));
  caller.on('error', () => closeBoth(1011, 'Server error', 'caller socket error'));
  box.on('error', () => closeBoth(1011, 'Server error', 'box socket error'));
  const maxTimer = setTimeout(() => closeBoth(1008, 'Maximum connection duration exceeded', `after ${limits.maxSeconds}s`), limits.maxSeconds * 1000);
  maxTimer.unref();

  return { stats, close: (code = 1001, reason = 'Going away') => closeBoth(code, reason, 'fake-agentcore') };
}
