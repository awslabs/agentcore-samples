// Dev box Service Worker (scope /). VS Code loads workspace and extension files (icons, images, fonts,
// web extension code) from same-origin SERVER_ROOT/vscode-remote-resource URLs, as plain <img>, CSS
// and fetch requests that can't carry an Authorization header. This worker answers exactly those
// requests by asking the box through AgentCore (op: http) with the token the page handed over.
// Nothing else is intercepted, and nothing is cached: CloudFront and the Lambda never see these files.
'use strict';

const REMOTE_RESOURCE_PATH = /^\/[a-z]+-[0-9a-f]{40}\/vscode-remote-resource$/;
const TOKEN_WAIT_MS = 5000;
const FETCH_TIMEOUT_MS = 60000;
const MAX_ATTEMPTS = 3;
const RETRYABLE = new Set([408, 409, 424, 429, 500, 502, 503, 504]);
// A workspace file must never become a page on the workbench origin (it could read the page's token).
const REFUSED_DESTINATIONS = new Set(['document', 'iframe', 'frame', 'embed', 'object']);

let auth = null;
let waiters = [];

function validAuth(data) {
  const ok = typeof data.token === 'string' && data.token.length > 0
    && typeof data.sessionId === 'string' && /^dbx-[0-9a-f]{64}$/.test(data.sessionId)
    && typeof data.runtimeArn === 'string' && data.runtimeArn.startsWith('arn:')
    && typeof data.agentcoreBase === 'string' && /^https?:\/\//.test(data.agentcoreBase)
    && typeof data.serverRoot === 'string' && /^\/[a-z]+-[0-9a-f]{40}$/.test(data.serverRoot);
  if (!ok) return null;
  return {
    token: data.token,
    sessionId: data.sessionId,
    runtimeArn: data.runtimeArn,
    agentcoreBase: data.agentcoreBase.replace(/\/+$/, ''),
    serverRoot: data.serverRoot,
  };
}

self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('message', event => {
  const data = event.data;
  // Only our own pages (same-origin windows) may hand over a token.
  if (!data || typeof data !== 'object' || !event.source || event.source.type !== 'window') return;
  if (data.type === 'devbox-token') {
    const next = validAuth(data);
    if (!next) return;
    auth = next;
    const ready = waiters;
    waiters = [];
    for (const resolve of ready) resolve(auth);
  } else if (data.type === 'devbox-claim') {
    event.waitUntil(self.clients.claim());
  } else if (data.type === 'devbox-signed-out') {
    auth = null;
  }
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' && request.method !== 'HEAD') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !REMOTE_RESOURCE_PATH.test(url.pathname)) return;
  if (auth && url.pathname !== auth.serverRoot + '/vscode-remote-resource') return;
  event.respondWith(remoteResource(request, url));
});

function plain(status, text) {
  return new Response(text + '\n', {
    status,
    headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' },
  });
}

// The token, or null if no page answers within TOKEN_WAIT_MS (the worker's memory is dropped whenever
// the browser stops an idle worker, so it asks the pages again).
async function currentAuth() {
  if (auth) return auth;
  const wait = new Promise(resolve => {
    waiters.push(resolve);
    setTimeout(() => resolve(null), TOKEN_WAIT_MS);
  });
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const client of windows) client.postMessage({ type: 'devbox-need-token' });
  return wait;
}

function invoke(current, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  return fetch(current.agentcoreBase + '/runtimes/' + encodeURIComponent(current.runtimeArn) + '/invocations?qualifier=DEFAULT', {
    method: 'POST',
    mode: 'cors',
    credentials: 'omit',
    cache: 'no-store',
    redirect: 'error',
    signal: controller.signal,
    headers: {
      Authorization: 'Bearer ' + current.token,
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': current.sessionId,
    },
    body: JSON.stringify(body),
  }).finally(() => clearTimeout(timer));
}

function decodeBase64(text) {
  if (typeof Uint8Array.fromBase64 === 'function') return Uint8Array.fromBase64(text);
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function headerOf(headers, name) {
  if (!headers || typeof headers !== 'object') return '';
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === name && typeof headers[key] === 'string') return headers[key];
  }
  return '';
}

function fromEnvelope(envelope, method) {
  if (!envelope || envelope.v !== 1 || envelope.ok !== true || !Number.isInteger(envelope.status)
      || envelope.status < 200 || envelope.status > 599) {
    return plain(502, 'The dev box sent a malformed reply.');
  }
  const headers = new Headers({
    'content-type': headerOf(envelope.headers, 'content-type') || 'application/octet-stream',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    // Belt and braces for the navigation refusal above: if a workspace HTML file is ever rendered as a
    // document, it gets an opaque origin instead of the workbench's.
    'content-security-policy': 'sandbox',
  });
  const etag = headerOf(envelope.headers, 'etag');
  if (etag) headers.set('etag', etag);
  const noBody = method === 'HEAD' || envelope.status === 204 || envelope.status === 205 || envelope.status === 304;
  let body = null;
  if (!noBody) {
    try {
      body = decodeBase64(typeof envelope.bodyB64 === 'string' ? envelope.bodyB64 : '');
    } catch (e) {
      return plain(502, 'The dev box sent a malformed body.');
    }
  }
  return new Response(body, { status: envelope.status, headers });
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function remoteResource(request, url) {
  if (request.mode === 'navigate' || REFUSED_DESTINATIONS.has(request.destination)) {
    return plain(403, 'Workspace files are not opened as pages here.');
  }
  let current = await currentAuth();
  if (!current) return plain(503, 'The dev box page has not handed over a sign-in yet.');
  if (url.pathname !== current.serverRoot + '/vscode-remote-resource') return plain(404, 'Not found');

  const body = { v: 1, op: 'http', method: 'GET', path: url.pathname, query: url.search.slice(1) };
  const ifNoneMatch = request.headers.get('if-none-match');
  if (ifNoneMatch) body.headers = { 'if-none-match': ifNoneMatch };

  let askedForNewToken = false;
  for (let attempt = 1; ; attempt++) {
    let res;
    try {
      res = await invoke(current, body);
    } catch (e) {
      if (attempt < MAX_ATTEMPTS) {
        await sleep(500 * 3 ** (attempt - 1));
        continue;
      }
      return plain(504, 'The dev box did not answer.');
    }
    if (res.status === 200) {
      let envelope = null;
      try {
        envelope = await res.json();
      } catch (e) {
        envelope = null;
      }
      return fromEnvelope(envelope, request.method);
    }
    if ((res.status === 401 || res.status === 403) && !askedForNewToken) {
      // The token may have been refreshed by the page since it was handed over.
      askedForNewToken = true;
      auth = null;
      current = await currentAuth();
      if (!current) return plain(503, 'The dev box page has not handed over a sign-in yet.');
      continue;
    }
    if (RETRYABLE.has(res.status) && attempt < MAX_ATTEMPTS) {
      await sleep(500 * 3 ** (attempt - 1));
      continue;
    }
    return plain(502, 'The dev box answered ' + res.status + '.');
  }
}
