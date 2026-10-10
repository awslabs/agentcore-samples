// Dev box terminal: Claude Code in the box, in the browser, without VS Code (the /terminal page).
//
// /terminal is the same page as /: the loader signs the person in, finds their box and waits until it is
// up, then calls DevboxTerminal.start() instead of booting VS Code. The terminal is AgentCore's own
// (InvokeAgentRuntimeCommandShell): a WebSocket to
//   wss://<agentcore>/runtimes/<arn>/ws/shells?qualifier=DEFAULT&shellId=<id>&X-Amzn-Bedrock-AgentCore-Runtime-Session-Id=<sid>
// with the Okta access token in the subprotocol, like every other call from this page (never in the URL).
// It is drawn by the xterm.js that ships in the pinned VS Code bundle (build/pins.mjs XTERM).
//
// Every message is binary: one channel byte, then the payload.
//   0x00 STDIN      page -> shell, raw bytes (UTF-8 text), each message under 64 KB
//   0x01 STDOUT     shell -> page, raw PTY output
//   0x02 STDERR     shell -> page, text from AgentCore (shown dimmed)
//   0x03 STATUS     shell -> page, a Kubernetes metav1.Status as JSON. The first one on a connection has
//                   metadata.shellId: connected, and metadata.reconnected says whether it is the PTY from
//                   before. One without shellId means the shell ended (Success, or Failure with
//                   details.causes ExitCode or Signal); reason InternalError (code 500) is a transient
//                   AgentCore error.
//   0x04 RESIZE     page -> shell, JSON {"width": cols, "height": rows}
//   0x05 HEARTBEAT  both ways, empty. The page sends one every 30 s, which keeps AgentCore's ~15 minute idle
//                   timer from ending the connection; the server echoes it.
//   0xFF CLOSE      from the server: AgentCore is shutting the terminal down. The page never sends it (it
//                   asks for the shell to end, and a reload should find the same shell again).
//
// The same shellId reattaches to the same PTY, with up to 256 KB of output buffered meanwhile, so the page
// always uses "claude-<generation>": a reload, a dropped connection or AgentCore's 1-hour limit all come
// back to the same Claude Code. A browser never learns why a WebSocket handshake failed (it only sees close
// code 1006), so the page reasons from STATUS messages and from retries.
(function (root) {
  'use strict';

  const CHANNEL = Object.freeze({ STDIN: 0x00, STDOUT: 0x01, STDERR: 0x02, STATUS: 0x03, RESIZE: 0x04, HEARTBEAT: 0x05, CLOSE: 0xff });
  const MAX_FRAME_BYTES = 64 * 1024;
  // Well under the 64 KB message limit. Chunks are cut only between UTF-8 characters.
  const STDIN_CHUNK_BYTES = 32 * 1024;
  const HEARTBEAT_MS = 30000;
  const CONNECT_TIMEOUT_MS = 60000;
  const MAX_FAILURES = 5;
  const MAX_ENCODED_TOKEN = 4096;
  // Typing while the page reattaches is kept (up to this much) and sent once the same shell is back.
  const MAX_PENDING_BYTES = 64 * 1024;
  // By its full path: AgentCore's shell doesn't get the box's PATH (or any of its environment), so
  // the bare name would depend on whatever PATH AgentCore gives it. devbox-claude builds the rest itself.
  const START_COMMAND = 'exec /usr/local/bin/devbox-claude\r';
  const PROTOCOL = 'base64UrlBearerAuthorization';
  // AgentCore takes 1-128 characters without ? # &; ours are always claude-<n>.
  const SHELL_ID = /^[A-Za-z0-9._-]{1,128}$/;
  // xterm.js reserves this much for its vertical scrollbar (overviewRuler width, default 14).
  const SCROLLBAR_PX = 14;
  const MIN_COLS = 2;
  const MIN_ROWS = 1;
  const FONT_FAMILY = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", monospace';
  const FONT_SIZE = 14;
  const DIM = '\x1b[2m';
  const UNDIM = '\x1b[22m';

  const encoder = new TextEncoder();
  const toString = Object.prototype.toString;

  class TerminalError extends Error {}

  // ---- the wire format (pure; unit-tested) ----

  function base64Url(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return root.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function shellIdFor(generation) {
    if (typeof generation !== 'number' || !Number.isSafeInteger(generation) || generation < 1) {
      throw new TerminalError('The dev box settings have no valid generation for this box.');
    }
    return 'claude-' + generation;
  }

  function shellUrl(opts) {
    if (typeof opts.shellId !== 'string' || !SHELL_ID.test(opts.shellId)) throw new TerminalError('Invalid shell id.');
    const base = new URL(opts.agentcoreBase);
    const wsBase = (base.protocol === 'https:' ? 'wss://' : 'ws://') + base.host + base.pathname.replace(/\/+$/, '');
    const query = new URLSearchParams();
    query.set('qualifier', 'DEFAULT');
    query.set('shellId', opts.shellId);
    query.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', opts.sessionId);
    return wsBase + '/runtimes/' + encodeURIComponent(opts.runtimeArn) + '/ws/shells?' + query.toString();
  }

  // Browsers can't set headers on a WebSocket, so the token goes in the subprotocol, as on /ws.
  function shellProtocols(token) {
    if (typeof token !== 'string' || !token) throw new TerminalError('There is no access token (signed out?).');
    const encoded = base64Url(encoder.encode(token));
    if (encoded.length > MAX_ENCODED_TOKEN) {
      throw new TerminalError('The access token is too long for AgentCore\'s terminal (' + encoded.length + ' characters encoded, at most ' + MAX_ENCODED_TOKEN + ').');
    }
    return [PROTOCOL + '.' + encoded, PROTOCOL];
  }

  function isArrayBuffer(value) {
    return value instanceof ArrayBuffer || toString.call(value) === '[object ArrayBuffer]';
  }

  function toBytes(data) {
    if (typeof data === 'string') return encoder.encode(data);
    if (isArrayBuffer(data)) return new Uint8Array(data);
    if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    return null;
  }

  function encodeFrame(channel, payload) {
    const body = payload || new Uint8Array(0);
    if (body.length + 1 > MAX_FRAME_BYTES) throw new RangeError('a terminal message is at most ' + MAX_FRAME_BYTES + ' bytes');
    const frame = new Uint8Array(body.length + 1);
    frame[0] = channel;
    frame.set(body, 1);
    return frame;
  }

  function resizeFrame(cols, rows) {
    return encodeFrame(CHANNEL.RESIZE, encoder.encode(JSON.stringify({ width: cols, height: rows })));
  }

  // xterm's onBinary gives "binary strings": one byte per character (mouse reports, for example).
  function binaryStringBytes(text) {
    const out = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) out[i] = text.charCodeAt(i) & 0xff;
    return out;
  }

  // Splits typed or pasted input into STDIN messages.
  function stdinFrames(data, chunkBytes) {
    const bytes = toBytes(data);
    const size = chunkBytes || STDIN_CHUNK_BYTES;
    const frames = [];
    let start = 0;
    while (bytes && start < bytes.length) {
      let end = Math.min(bytes.length, start + size);
      if (end < bytes.length) {
        // Continuation bytes are 10xxxxxx: step back to the start of the character.
        let cut = end;
        while (cut > start && (bytes[cut] & 0xc0) === 0x80) cut--;
        if (cut > start) end = cut;
      }
      frames.push(encodeFrame(CHANNEL.STDIN, bytes.subarray(start, end)));
      start = end;
    }
    return frames;
  }

  function decodeFrame(data) {
    const bytes = toBytes(data);
    if (!bytes || bytes.length === 0) return null;
    return { channel: bytes[0], payload: bytes.subarray(1) };
  }

  function parseStatus(payload) {
    try {
      const value = JSON.parse(new TextDecoder().decode(payload));
      return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
    } catch (e) {
      return null;
    }
  }

  function exitOf(status) {
    const causes = status.details && Array.isArray(status.details.causes) ? status.details.causes : [];
    const cause = name => {
      const found = causes.find(c => c && c.reason === name);
      return found && found.message !== undefined && found.message !== null ? String(found.message).trim() : null;
    };
    const code = cause('ExitCode');
    const signal = cause('Signal');
    if (code !== null && /^-?\d+$/.test(code)) return { code: Number(code), signal: null, message: '' };
    if (signal) return { code: null, signal: signal.slice(0, 32), message: '' };
    if (status.status === 'Success') return { code: 0, signal: null, message: '' };
    return { code: null, signal: null, message: typeof status.message === 'string' ? status.message.slice(0, 300) : '' };
  }

  // What a STATUS message means for the page.
  function classifyStatus(status) {
    const meta = status.metadata && typeof status.metadata === 'object' ? status.metadata : {};
    if (typeof meta.shellId === 'string' && meta.shellId) {
      return { kind: 'connected', shellId: meta.shellId, reconnected: meta.reconnected === true };
    }
    if (status.reason === 'InternalError' || status.code === 500) {
      return { kind: 'transient', message: typeof status.message === 'string' ? status.message.slice(0, 300) : '' };
    }
    return { kind: 'ended', exit: exitOf(status) };
  }

  function endedText(exit) {
    const how = exit.signal ? 'signal ' + exit.signal : exit.code !== null ? 'exit ' + exit.code : 'exit unknown';
    return 'The terminal ended (' + how + ')' + (exit.message ? ': ' + exit.message : '') + '. Press Enter to start a new one.';
  }

  // Exponential backoff with jitter, as in the loader: about 1 s, 2 s, 4 s, 8 s, then 15 s.
  function backoffMs(failures, random) {
    const cap = Math.min(15000, 1000 * Math.pow(2, failures));
    return Math.round(cap / 2 + (random || Math.random)() * (cap / 2));
  }

  // cols/rows that fit a box of `space` pixels with cells of `cell` pixels, leaving xterm's scrollbar room.
  function fitSize(space, cell, scrollbarPx) {
    if (!space || !cell || !(cell.width > 0) || !(cell.height > 0) || !(space.width > 0) || !(space.height > 0)) return null;
    const bar = scrollbarPx === undefined ? SCROLLBAR_PX : scrollbarPx;
    return {
      cols: Math.max(MIN_COLS, Math.floor((space.width - bar) / cell.width)),
      rows: Math.max(MIN_ROWS, Math.floor(space.height / cell.height)),
    };
  }

  // ---- the connection (a state machine; unit-tested with a fake WebSocket and fake timers) ----
  //
  // States: idle, connecting, connected, retrying, ended (the shell exited: Enter starts a new one),
  // failed (MAX_FAILURES attempts in a row did not get a shell: retry() tries again), stopped.
  //
  // deps: WebSocket, url() -> string, getToken() -> Promise<string> (fresh), refresh() -> Promise,
  //   size() -> {cols, rows}, setTimeout, clearTimeout, random, and callbacks onOutput(bytes),
  //   onErrorText(text), onConnected({reconnected, shellId}), onEnded(text), onState({state, ...}).
  function createShellSession(deps) {
    const setTimer = deps.setTimeout || ((fn, ms) => root.setTimeout(fn, ms));
    const clearTimer = deps.clearTimeout || (id => root.clearTimeout(id));
    const random = deps.random || Math.random;
    const notify = (name, value) => {
      if (typeof deps[name] === 'function') deps[name](value);
    };

    let state = 'idle';
    let conn = null;
    let epoch = 0;
    let failures = 0;
    let refreshed = false;
    let refreshNext = false;
    let retryTimer = null;
    let pending = [];
    let pendingBytes = 0;

    function setState(next, detail) {
      state = next;
      notify('onState', Object.assign({ state: next, failures: failures }, detail || {}));
    }

    function send(c, frame) {
      if (!c || c !== conn || !c.socket || c.socket.readyState !== 1) return false;
      c.socket.send(frame);
      return true;
    }

    function stopTimers(c) {
      clearTimer(c.heartbeat);
      clearTimer(c.timeout);
      c.heartbeat = null;
      c.timeout = null;
    }

    function closeSocket(c) {
      try {
        c.socket.close(1000);
      } catch (e) {
        // already closed
      }
    }

    function schedule(ms, reason) {
      clearTimer(retryTimer);
      setState('retrying', { inMs: ms, attempt: failures + 1, reason: reason || '' });
      retryTimer = setTimer(() => {
        retryTimer = null;
        connect();
      }, ms);
    }

    // An attempt that got no shell. c is null when it failed before a socket existed.
    function attemptFailed(c, reason) {
      failures++;
      // A handshake that never opened may be a turned-down token: refresh it once per run of failures.
      if ((!c || !c.opened) && !refreshed) refreshNext = true;
      if (failures >= MAX_FAILURES) {
        setState('failed', { reason: reason });
        return;
      }
      schedule(backoffMs(failures - 1, random), reason);
    }

    function describeFailure(c, ev) {
      const code = ev && typeof ev.code === 'number' ? ev.code : 1006;
      const why = ev && ev.reason ? ': ' + String(ev.reason).slice(0, 120) : '';
      let text = c.opened
        ? 'The connection closed before the shell started (code ' + code + why + ').'
        : 'AgentCore did not accept the terminal connection (code ' + code + why + '). The browser isn\'t told why; '
          + 'usually the runtime\'s resource policy does not allow bedrock-agentcore:InvokeAgentRuntimeCommandShell, '
          + 'AgentCore turned down the sign-in, or the box is not running.';
      const lines = c.stderr.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      if (lines.length) text += ' AgentCore said: ' + lines[lines.length - 1].slice(0, 200);
      return text;
    }

    function beat(c) {
      c.heartbeat = setTimer(() => {
        if (c !== conn) return;
        send(c, encodeFrame(CHANNEL.HEARTBEAT));
        beat(c);
      }, HEARTBEAT_MS);
    }

    function flushPending(c) {
      const frames = pending;
      pending = [];
      pendingBytes = 0;
      for (const frame of frames) send(c, frame);
    }

    function dropPending() {
      pending = [];
      pendingBytes = 0;
    }

    function onStatus(c, status) {
      const s = classifyStatus(status);
      if (s.kind === 'connected') {
        if (c.confirmed) return;
        c.confirmed = true;
        clearTimer(c.timeout);
        c.timeout = null;
        failures = 0;
        refreshed = false;
        refreshNext = false;
        notify('onConnected', { reconnected: s.reconnected, shellId: s.shellId });
        setState('connected', { reconnected: s.reconnected });
        const size = deps.size ? deps.size() : null;
        if (s.reconnected) {
          // The PTY already has this size, and a resize to the same size changes nothing (no SIGWINCH), so
          // step one row away and back: tmux (and Claude Code in it) repaints the whole screen.
          if (size) {
            send(c, resizeFrame(size.cols, size.rows > MIN_ROWS ? size.rows - 1 : size.rows + 1));
            send(c, resizeFrame(size.cols, size.rows));
          }
          flushPending(c);
        } else {
          // A new shell: size it first, then start Claude Code in it (once per shell).
          if (size) send(c, resizeFrame(size.cols, size.rows));
          dropPending();
          for (const frame of stdinFrames(START_COMMAND)) send(c, frame);
        }
        return;
      }
      if (s.kind === 'transient') {
        c.transient = true;
        c.reason = 'AgentCore reported a temporary error' + (s.message ? ' (' + s.message + ')' : '') + '.';
        closeSocket(c);
        return;
      }
      c.ended = true;
      const text = endedText(s.exit);
      dropPending();
      setState('ended', { exit: s.exit, text: text });
      notify('onEnded', text);
      closeSocket(c);
    }

    function onMessage(c, ev) {
      if (c !== conn) return;
      const frame = decodeFrame(typeof ev.data === 'string' ? encoder.encode(ev.data) : ev.data);
      if (!frame) return;
      switch (frame.channel) {
        case CHANNEL.STDOUT:
          if (frame.payload.length) notify('onOutput', frame.payload);
          break;
        case CHANNEL.STDERR: {
          const text = c.decoder.decode(frame.payload, { stream: true });
          if (text) {
            c.stderr = (c.stderr + text).slice(-400);
            notify('onErrorText', text);
          }
          break;
        }
        case CHANNEL.STATUS: {
          const status = parseStatus(frame.payload);
          if (status) onStatus(c, status);
          break;
        }
        case CHANNEL.CLOSE:
          c.reason = c.reason || 'AgentCore shut the terminal down.';
          break;
        default:
          // HEARTBEAT (the server's echo of ours), and channels this page doesn't know.
          break;
      }
    }

    function onClose(c, ev) {
      if (c !== conn) return;
      conn = null;
      stopTimers(c);
      if (state === 'stopped' || c.ended) return;
      if (c.confirmed && !c.transient) {
        // A working terminal dropped (AgentCore's 1-hour limit, a network change, AgentCore): reattach.
        const code = ev && typeof ev.code === 'number' ? ev.code : 1006;
        schedule(backoffMs(0, random), c.reason || 'The connection dropped (code ' + code + ').');
        return;
      }
      attemptFailed(c, c.reason || describeFailure(c, ev));
    }

    async function connect() {
      const my = ++epoch;
      clearTimer(retryTimer);
      retryTimer = null;
      if (conn) {
        const old = conn;
        conn = null;
        stopTimers(old);
        closeSocket(old);
      }
      setState('connecting', { attempt: failures + 1 });
      let url;
      let protocols;
      try {
        if (refreshNext) {
          refreshNext = false;
          refreshed = true;
          try {
            await deps.refresh();
          } catch (e) {
            // The attempt below reports what happens with the current token.
          }
        }
        const token = await deps.getToken();
        if (my !== epoch) return;
        url = deps.url();
        protocols = shellProtocols(token);
      } catch (err) {
        if (my !== epoch) return;
        attemptFailed(null, (err && err.message) || String(err));
        return;
      }
      const c = { socket: null, opened: false, confirmed: false, ended: false, transient: false, reason: '', stderr: '', decoder: new TextDecoder(), heartbeat: null, timeout: null };
      try {
        c.socket = new deps.WebSocket(url, protocols);
      } catch (err) {
        attemptFailed(null, 'The browser would not open the terminal connection: ' + ((err && err.message) || String(err)));
        return;
      }
      conn = c;
      c.socket.binaryType = 'arraybuffer';
      c.socket.addEventListener('open', () => {
        if (c !== conn) return;
        c.opened = true;
        beat(c);
      });
      c.socket.addEventListener('message', ev => onMessage(c, ev));
      c.socket.addEventListener('close', ev => onClose(c, ev));
      c.timeout = setTimer(() => {
        if (c !== conn || c.confirmed) return;
        c.reason = 'No answer from AgentCore\'s terminal within ' + Math.round(CONNECT_TIMEOUT_MS / 1000) + ' s.';
        closeSocket(c);
      }, CONNECT_TIMEOUT_MS);
    }

    function restart() {
      failures = 0;
      refreshed = false;
      return connect();
    }

    return {
      start: () => connect(),
      // Typed or pasted text (binary: an xterm binary string).
      input(data, binary) {
        if (state === 'ended') {
          if (typeof data === 'string' && data.indexOf('\r') >= 0) restart();
          return;
        }
        if (state === 'failed' || state === 'stopped' || state === 'idle') return;
        const frames = stdinFrames(binary ? binaryStringBytes(String(data)) : data);
        if (state === 'connected' && conn && conn.confirmed) {
          for (const frame of frames) send(conn, frame);
          return;
        }
        for (const frame of frames) {
          if (pendingBytes + frame.length > MAX_PENDING_BYTES) break;
          pending.push(frame);
          pendingBytes += frame.length;
        }
      },
      resize(cols, rows) {
        if (state === 'connected' && conn && conn.confirmed) send(conn, resizeFrame(cols, rows));
      },
      // After `failed`: the Retry button. Refreshes the token first.
      retry() {
        if (state !== 'failed') return undefined;
        refreshNext = true;
        return restart();
      },
      stop() {
        epoch++;
        clearTimer(retryTimer);
        retryTimer = null;
        const c = conn;
        conn = null;
        state = 'stopped';
        if (c) {
          stopTimers(c);
          closeSocket(c);
        }
      },
      get state() {
        return state;
      },
      get failures() {
        return failures;
      },
    };
  }

  // ---- the page ----

  // The socket must be the browser's own: the loader's shim (shim.js) wraps window.WebSocket for VS Code.
  // It would pass this URL through untouched anyway (another host, and not under SERVER_ROOT).
  function nativeWebSocket() {
    const current = root.WebSocket;
    return (current && current.devboxInternals && current.devboxInternals.NativeWebSocket) || current;
  }

  // The cell size xterm.js measured for its renderer, or, failing that, our own measurement of its font.
  function measureCell(term, doc) {
    const dims = term.dimensions;
    if (dims && dims.css && dims.css.cell && dims.css.cell.width > 0 && dims.css.cell.height > 0) {
      return { width: dims.css.cell.width, height: dims.css.cell.height };
    }
    const probe = doc.createElement('span');
    probe.textContent = 'W'.repeat(32);
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;visibility:hidden;white-space:pre;line-height:normal';
    probe.style.fontFamily = term.options.fontFamily;
    probe.style.fontSize = term.options.fontSize + 'px';
    (term.element || doc.body).appendChild(probe);
    const rect = probe.getBoundingClientRect();
    probe.remove();
    return { width: rect.width / 32, height: Math.ceil(rect.height * (term.options.lineHeight || 1)) };
  }

  function contentBox(el) {
    const style = root.getComputedStyle(el);
    const px = value => parseFloat(value) || 0;
    return {
      width: el.clientWidth - px(style.paddingLeft) - px(style.paddingRight),
      height: el.clientHeight - px(style.paddingTop) - px(style.paddingBottom),
    };
  }

  const STATE_TEXT = {
    idle: () => '',
    connecting: s => (s.attempt > 1 ? 'Connecting (attempt ' + s.attempt + ' of ' + MAX_FAILURES + ')…' : 'Connecting…'),
    connected: () => 'Connected',
    retrying: s => 'Reconnecting in ' + Math.max(1, Math.round(s.inMs / 1000)) + ' s…',
    ended: () => 'Ended. Press Enter in the terminal to start a new one.',
    failed: () => 'Not connected',
    stopped: () => 'Closed',
  };

  function buildPage(doc, opts) {
    const page = doc.createElement('div');
    page.id = 'devbox-term';
    page.className = 'devbox-term';
    const bar = doc.createElement('header');
    bar.className = 'devbox-term-bar';
    const title = doc.createElement('span');
    title.className = 'devbox-term-title';
    title.textContent = 'Claude Code' + (opts.boxName ? ' · ' + opts.boxName + '’s dev box' : '');
    const status = doc.createElement('span');
    status.id = 'devbox-term-state';
    status.className = 'devbox-term-state';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    bar.append(title, status);
    if (opts.modeSwitch) bar.appendChild(opts.modeSwitch);
    if (opts.signOut) {
      const button = doc.createElement('button');
      button.type = 'button';
      button.id = 'devbox-term-signout';
      button.textContent = 'Sign out';
      button.addEventListener('click', opts.signOut);
      bar.appendChild(button);
    }
    const screen = doc.createElement('div');
    screen.id = 'devbox-term-screen';
    screen.className = 'devbox-term-screen';
    page.append(bar, screen);
    doc.body.appendChild(page);
    return { page: page, screen: screen, status: status };
  }

  // opts: devbox (window.__devbox), generation, boxName, getToken, refresh, banner (the loader's
  // view.banner), modeSwitch (an element for the bar), signOut.
  function start(opts) {
    const doc = root.document;
    const Terminal = root.Terminal;
    if (typeof Terminal !== 'function') throw new TerminalError('xterm.js did not load.');
    const shellId = shellIdFor(opts.generation);
    const ui = buildPage(doc, opts);
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: FONT_FAMILY,
      fontSize: FONT_SIZE,
      scrollback: 5000,
      theme: { background: '#1e1e1e', foreground: '#cccccc', cursor: '#cccccc', selectionBackground: '#264f78' },
    });
    term.open(ui.screen);

    function fit() {
      const target = fitSize(contentBox(ui.screen), measureCell(term, doc));
      if (target && (target.cols !== term.cols || target.rows !== term.rows)) term.resize(target.cols, target.rows);
    }
    fit();

    let failedBanner = null;
    let hadShell = false;
    const session = createShellSession({
      WebSocket: nativeWebSocket(),
      url: () => shellUrl({ agentcoreBase: opts.devbox.agentcoreBase, runtimeArn: opts.devbox.runtimeArn, sessionId: opts.devbox.sessionId, shellId: shellId }),
      getToken: opts.getToken,
      refresh: opts.refresh,
      size: () => ({ cols: term.cols, rows: term.rows }),
      onOutput: bytes => term.write(bytes),
      onErrorText: text => term.write(DIM + text.replace(/\r?\n/g, '\r\n') + UNDIM),
      onConnected: info => {
        // A new shell (after the last one ended, or the box restarted): start from a clean screen.
        if (!info.reconnected && hadShell) term.reset();
        hadShell = true;
      },
      onEnded: text => term.write('\r\n' + DIM + text + UNDIM + '\r\n'),
      onState: s => {
        ui.status.textContent = (STATE_TEXT[s.state] || String)(s);
        if (failedBanner && s.state !== 'failed') {
          failedBanner.bar.remove();
          failedBanner = null;
        }
        if (s.state === 'failed' && opts.banner) {
          failedBanner = opts.banner('The terminal could not connect after ' + MAX_FAILURES + ' attempts. ' + s.reason, [
            { label: 'Retry', run: () => session.retry() },
          ], { id: 'devbox-term-failed' });
        }
      },
    });
    term.onData(data => session.input(data));
    term.onBinary(data => session.input(data, true));
    term.onResize(size => session.resize(size.cols, size.rows));
    let refit = null;
    root.addEventListener('resize', () => {
      root.clearTimeout(refit);
      refit = root.setTimeout(fit, 50);
    });
    session.start();
    term.focus();
    return { term: term, session: session, fit: fit };
  }

  root.DevboxTerminal = Object.freeze({
    CHANNEL: CHANNEL,
    MAX_FRAME_BYTES: MAX_FRAME_BYTES,
    STDIN_CHUNK_BYTES: STDIN_CHUNK_BYTES,
    HEARTBEAT_MS: HEARTBEAT_MS,
    CONNECT_TIMEOUT_MS: CONNECT_TIMEOUT_MS,
    MAX_FAILURES: MAX_FAILURES,
    START_COMMAND: START_COMMAND,
    shellIdFor: shellIdFor,
    shellUrl: shellUrl,
    shellProtocols: shellProtocols,
    encodeFrame: encodeFrame,
    resizeFrame: resizeFrame,
    stdinFrames: stdinFrames,
    binaryStringBytes: binaryStringBytes,
    decodeFrame: decodeFrame,
    parseStatus: parseStatus,
    classifyStatus: classifyStatus,
    endedText: endedText,
    backoffMs: backoffMs,
    fitSize: fitSize,
    createShellSession: createShellSession,
    start: start,
  });
})(typeof window !== 'undefined' ? window : globalThis);
