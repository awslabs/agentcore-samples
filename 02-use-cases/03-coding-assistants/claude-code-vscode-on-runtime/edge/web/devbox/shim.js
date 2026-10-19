// Dev box WebSocket shim. Runs before any VS Code script and replaces window.WebSocket.
//
// VS Code opens its remote connections to wss://<this host>/stable-<commit>?reconnectionToken=...
// (openvscode-server forces remoteAuthority to location.host). The shim sends exactly those to the box's
// AgentCore WebSocket instead, with the Okta access token in the subprotocol (browsers can't set
// headers on a WebSocket), and splits and paces what VS Code sends so it fits AgentCore's limits:
// at most 32000 bytes per message and 200 messages per second (AgentCore closes at 250). VS Code reads
// the connection as a byte stream, so splitting and merging binary messages is safe. Every other
// WebSocket passes through untouched.
(function (root) {
  'use strict';

  const NativeWebSocket = root.WebSocket;
  if (typeof NativeWebSocket !== 'function' || NativeWebSocket.devboxInternals) return;

  const MAX_MESSAGE_BYTES = 32000;
  const MAX_MESSAGES_PER_WINDOW = 200;
  const WINDOW_MS = 1000;
  const CONNECTING = 0;
  const OPEN = 1;
  const CLOSING = 2;

  const toString = Object.prototype.toString;

  function base64Url(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return root.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function base64UrlOfString(text) {
    return base64Url(new TextEncoder().encode(text));
  }

  // Where a VS Code WebSocket really goes, or null to leave the URL alone.
  function devboxTarget(rawUrl) {
    const devbox = root.__devbox;
    if (!devbox || typeof devbox.getToken !== 'function' || !devbox.serverRoot) return null;
    let url;
    try {
      url = new URL(String(rawUrl), root.location.href);
    } catch (e) {
      return null;
    }
    if (url.protocol !== 'ws:' && url.protocol !== 'wss:') return null;
    if (url.host !== root.location.host) return null;
    if (url.pathname !== devbox.serverRoot && url.pathname.indexOf(devbox.serverRoot + '/') !== 0) return null;

    const base = new URL(devbox.agentcoreBase);
    const wsBase = (base.protocol === 'https:' ? 'wss://' : 'ws://') + base.host + base.pathname.replace(/\/+$/, '');
    const query = new URLSearchParams();
    query.set('qualifier', 'DEFAULT');
    query.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', devbox.sessionId);
    query.set('X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath', base64UrlOfString(url.pathname + '?' + url.search.slice(1)));
    return {
      url: wsBase + '/runtimes/' + encodeURIComponent(devbox.runtimeArn) + '/ws?' + query.toString(),
      protocols: ['base64UrlBearerAuthorization.' + base64UrlOfString(devbox.getToken() || ''), 'base64UrlBearerAuthorization'],
    };
  }

  function isArrayBuffer(value) {
    return value instanceof ArrayBuffer || toString.call(value) === '[object ArrayBuffer]';
  }

  function isBlob(value) {
    return typeof Blob !== 'undefined' && (value instanceof Blob || toString.call(value) === '[object Blob]');
  }

  // Copies the caller's data at send() time, as the native send() does, because it may be queued.
  function toQueueItem(data) {
    if (typeof data === 'string') return { kind: 'text', data: data, size: new TextEncoder().encode(data).length };
    if (isArrayBuffer(data)) return { kind: 'bytes', data: new Uint8Array(data.slice(0)), size: data.byteLength };
    if (ArrayBuffer.isView(data)) {
      const copy = new Uint8Array(data.byteLength);
      copy.set(new Uint8Array(data.buffer, data.byteOffset, data.byteLength));
      return { kind: 'bytes', data: copy, size: copy.byteLength };
    }
    if (isBlob(data)) return { kind: 'blob', data: data, size: data.size };
    const text = String(data);
    return { kind: 'text', data: text, size: new TextEncoder().encode(text).length };
  }

  // Takes the next message off the queue: up to MAX_MESSAGE_BYTES of consecutive binary data (merging
  // small pieces and splitting big ones), or one text message as it is.
  function takeMessage(queue) {
    const head = queue[0];
    if (head.kind === 'text') {
      queue.shift();
      return { message: head.data, size: head.size };
    }
    const parts = [];
    let size = 0;
    let hasBlob = false;
    while (queue.length && queue[0].kind !== 'text' && size < MAX_MESSAGE_BYTES) {
      const item = queue[0];
      const take = Math.min(MAX_MESSAGE_BYTES - size, item.size);
      if (item.kind === 'blob') {
        hasBlob = true;
        parts.push(take === item.size ? item.data : item.data.slice(0, take));
        if (take < item.size) item.data = item.data.slice(take);
      } else {
        parts.push(item.data.subarray(0, take));
        item.data = item.data.subarray(take);
      }
      item.size -= take;
      size += take;
      if (item.size === 0) queue.shift();
    }
    let message;
    if (hasBlob) {
      message = new Blob(parts);
    } else if (parts.length === 1) {
      message = parts[0];
    } else {
      message = new Uint8Array(size);
      let offset = 0;
      for (const part of parts) {
        message.set(part, offset);
        offset += part.byteLength;
      }
    }
    return { message: message, size: size };
  }

  class DevboxWebSocket extends NativeWebSocket {
    #s = null;

    constructor(url, protocols) {
      const target = devboxTarget(url);
      if (target) super(target.url, target.protocols);
      else if (protocols === undefined) super(url);
      else super(url, protocols);
      if (!target) return;
      this.#s = { url: String(url), queue: [], queuedBytes: 0, sentAt: [], timer: null, closeArgs: null };
      super.addEventListener('open', () => this.#pump());
      super.addEventListener('close', () => {
        const s = this.#s;
        if (s.timer !== null) root.clearTimeout(s.timer);
        s.timer = null;
        s.queue = [];
        s.queuedBytes = 0;
      });
    }

    get url() {
      return this.#s ? this.#s.url : super.url;
    }

    // The subprotocols were ours (they carry the token), not the caller's.
    get protocol() {
      return this.#s ? '' : super.protocol;
    }

    get bufferedAmount() {
      return super.bufferedAmount + (this.#s ? this.#s.queuedBytes : 0);
    }

    get readyState() {
      const state = super.readyState;
      return this.#s && this.#s.closeArgs && state === OPEN ? CLOSING : state;
    }

    send(data) {
      const s = this.#s;
      if (!s) return super.send(data);
      const state = super.readyState;
      if (state === CONNECTING) {
        throw new DOMException("Failed to execute 'send' on 'WebSocket': Still in CONNECTING state.", 'InvalidStateError');
      }
      // Like the browser, drop data once closing has started.
      if (state !== OPEN || s.closeArgs) return undefined;
      const item = toQueueItem(data);
      s.queue.push(item);
      s.queuedBytes += item.size;
      this.#pump();
      return undefined;
    }

    // Queued data is still sent before the close frame, as with a native socket's buffer.
    close(code, reason) {
      const s = this.#s;
      if (!s || !s.queue.length || super.readyState !== OPEN) {
        if (code === undefined) return super.close();
        return super.close(code, reason);
      }
      if (code !== undefined && code !== 1000 && (code < 3000 || code > 4999)) {
        throw new DOMException("Failed to execute 'close' on 'WebSocket': The code must be either 1000, or between 3000 and 4999.", 'InvalidAccessError');
      }
      if (!s.closeArgs) s.closeArgs = [code, reason];
      return undefined;
    }

    #pump() {
      const s = this.#s;
      if (!s || s.timer !== null) return;
      while (s.queue.length && super.readyState === OPEN) {
        const now = Date.now();
        while (s.sentAt.length && now - s.sentAt[0] >= WINDOW_MS) s.sentAt.shift();
        if (s.sentAt.length >= MAX_MESSAGES_PER_WINDOW) {
          s.timer = root.setTimeout(() => {
            s.timer = null;
            this.#pump();
          }, WINDOW_MS - (now - s.sentAt[0]));
          return;
        }
        const next = takeMessage(s.queue);
        s.queuedBytes -= next.size;
        s.sentAt.push(now);
        super.send(next.message);
      }
      if (!s.queue.length && s.closeArgs && super.readyState === OPEN) {
        const args = s.closeArgs;
        if (args[0] === undefined) super.close();
        else super.close(args[0], args[1]);
      }
    }
  }

  Object.defineProperty(DevboxWebSocket, 'devboxInternals', {
    value: Object.freeze({
      devboxTarget: devboxTarget,
      takeMessage: takeMessage,
      toQueueItem: toQueueItem,
      base64UrlOfString: base64UrlOfString,
      MAX_MESSAGE_BYTES: MAX_MESSAGE_BYTES,
      MAX_MESSAGES_PER_WINDOW: MAX_MESSAGES_PER_WINDOW,
      NativeWebSocket: NativeWebSocket,
    }),
  });
  root.WebSocket = DevboxWebSocket;
})(typeof window !== 'undefined' ? window : globalThis);
