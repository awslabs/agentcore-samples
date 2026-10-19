// Edge Lambda: the dev box's static web front door. It serves our loader page and scripts, the pinned
// VS Code web assets and the patched webview shell, and nothing else. It never proxies to the box,
// never sees a token and holds no per-user data.
//
// Function URL, payload format 2.0, BUFFERED responses (binary bodies base64). Both CloudFront
// distributions share this function; each sets the origin header x-devbox-site to say which site
// the request is for.

import { randomBytes, createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { MAX_RAW_BODY_BYTES } from './limits.mjs';

const WEBVIEW_PRE = 'out/vs/workbench/contrib/webview/browser/pre';
const WORKBENCH_ORIGIN_PLACEHOLDER = '{{DEVBOX_WORKBENCH_ORIGIN}}';
const NONCE_PLACEHOLDER = '{{CSP_NONCE}}';
const FILE_CACHE_BUDGET = 256 * 1024 * 1024;

const IMMUTABLE = 'public, max-age=31536000, immutable';
// Patched and templated files keep a short lifetime: their content can change without the commit in
// the URL changing.
const WEBVIEW_CACHE = 'public, max-age=3600';

const ORIGIN = /^https?:\/\/[a-z0-9.-]+(:\d{1,5})?$/;

export class ConfigError extends Error {}

function defaultDistDir() {
  const here = fileURLToPath(new URL('.', import.meta.url));
  for (const candidate of [join(here, 'dist'), join(here, '..', 'dist')]) {
    if (existsSync(join(candidate, 'manifest.json'))) return candidate;
  }
  return join(here, 'dist');
}

function requireOrigin(name, value) {
  if (typeof value !== 'string' || !ORIGIN.test(value)) {
    throw new ConfigError(`${name} must be a bare origin such as https://d111111abcdef8.cloudfront.net`);
  }
  return value;
}

function requireUrl(name, value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new ConfigError(`${name} must be an absolute URL`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new ConfigError(`${name} must be http(s)`);
  return url;
}

// Reads and cross-checks the deploy-time settings. Anything wrong here is a deployment mistake, so the
// function refuses to start rather than serve a page with a wrong CSP or a mismatched VS Code.
export function loadSettings(env, manifest) {
  const workbenchOrigin = requireOrigin('WORKBENCH_ORIGIN', env.WORKBENCH_ORIGIN);
  const webviewOrigin = requireOrigin('WEBVIEW_ORIGIN', env.WEBVIEW_ORIGIN);
  if (workbenchOrigin === webviewOrigin) {
    throw new ConfigError('WEBVIEW_ORIGIN must differ from WORKBENCH_ORIGIN (webviews must not share the workbench origin)');
  }
  const configJson = env.DEVBOX_CONFIG_JSON;
  let config;
  try {
    config = JSON.parse(configJson);
  } catch {
    throw new ConfigError('DEVBOX_CONFIG_JSON is not valid JSON');
  }
  if (!config || typeof config !== 'object') throw new ConfigError('DEVBOX_CONFIG_JSON must be an object');
  if (config.serverRoot !== manifest.serverRoot || config.commit !== manifest.ovs.commit) {
    throw new ConfigError(`DEVBOX_CONFIG_JSON is for ${config.serverRoot}, but this image serves ${manifest.serverRoot}`);
  }
  if (config.webviewOrigin !== webviewOrigin) {
    throw new ConfigError('DEVBOX_CONFIG_JSON webviewOrigin must equal WEBVIEW_ORIGIN');
  }
  const agentcore = requireUrl('agentcoreBase', config.agentcoreBase);
  const issuer = requireUrl('okta.issuer', config.okta?.issuer);
  if (typeof config.okta?.clientId !== 'string' || !config.okta.clientId) {
    throw new ConfigError('DEVBOX_CONFIG_JSON okta.clientId is missing');
  }
  // The deployed config names nobody; the page asks the provisioner (a same-origin path, so 'self' in the CSP
  // covers it). The laptop test still uses a static boxes map.
  const provision = config.provision;
  if (provision !== undefined) {
    if (!provision || typeof provision.path !== 'string' || !/^\/api\/[a-z]+$/.test(provision.path)) {
      throw new ConfigError('DEVBOX_CONFIG_JSON provision.path must be a same-origin /api/<name> path');
    }
    if (typeof provision.header !== 'string' || !/^X-[A-Za-z-]+$/.test(provision.header)) {
      throw new ConfigError('DEVBOX_CONFIG_JSON provision.header must be an X- header name');
    }
  } else if (!config.boxes || typeof config.boxes !== 'object') {
    throw new ConfigError('DEVBOX_CONFIG_JSON needs provision (the provisioner) or boxes (a static map)');
  }
  return { workbenchOrigin, webviewOrigin, configJson, agentcore, issuer };
}

export function workbenchCsp({ agentcore, issuer, webviewOrigin }) {
  const agentcoreWs = `${agentcore.protocol === 'https:' ? 'wss:' : 'ws:'}//${agentcore.host}`;
  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-eval' 'nonce-${NONCE_PLACEHOLDER}'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${agentcore.origin} ${agentcoreWs} ${issuer.origin}`,
    // 'self' is the web worker extension host iframe (same origin in openvscode-server).
    `frame-src 'self' ${webviewOrigin}`,
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    // Sign-out is a form POST to Okta (so the ID token stays out of URLs); Okta then redirects back here.
    `form-action 'self' ${issuer.origin}`,
  ].join('; ');
}

export function parseAcceptEncoding(header) {
  const q = new Map();
  if (typeof header !== 'string') return q;
  for (const part of header.split(',')) {
    const [name, ...params] = part.trim().toLowerCase().split(';');
    if (!name) continue;
    let weight = 1;
    for (const p of params) {
      const m = /^\s*q\s*=\s*([0-9.]+)\s*$/.exec(p);
      if (m) weight = Math.min(1, Number(m[1]) || 0);
    }
    q.set(name.trim(), weight);
  }
  return q;
}

// Picks the representation to send: the most preferred pre-compressed variant the client accepts
// (br wins ties), else the identity bytes. null means nothing acceptable fits a BUFFERED response.
export function chooseEncoding(acceptEncoding, entry, max = MAX_RAW_BODY_BYTES) {
  const q = parseAcceptEncoding(acceptEncoding);
  const weight = name => (q.has(name) ? q.get(name) : (q.has('*') ? q.get('*') : 0));
  let best = null;
  for (const name of ['br', 'gzip']) {
    const variant = entry.variants?.[name];
    if (!variant || variant.size > max || weight(name) <= 0) continue;
    if (!best || weight(name) > weight(best)) best = name;
  }
  if (best) return best;
  return entry.size <= max ? 'identity' : null;
}

// Decodes a request path into segments, or returns null for anything that is malformed or could
// step outside the baked tree: NUL, backslashes, encoded slashes, '.', '..', empty segments, and
// double encoding. Files are only ever found by exact manifest lookup, never by joining paths.
export function decodePath(rawPath) {
  if (typeof rawPath !== 'string' || !rawPath.startsWith('/') || rawPath.length > 2048) return null;
  if (/[\0\\]/.test(rawPath) || /%(00|2f|5c)/i.test(rawPath)) return null;
  const segments = [];
  for (const raw of rawPath.slice(1).split('/')) {
    let seg;
    try {
      seg = decodeURIComponent(raw);
    } catch {
      return null;
    }
    if (seg === '' || seg === '.' || seg === '..' || /[\0/\\%]/.test(seg)) return null;
    segments.push(seg);
  }
  return segments;
}

function etagMatches(ifNoneMatch, etag) {
  if (typeof ifNoneMatch !== 'string') return false;
  return ifNoneMatch.split(',').some(tag => {
    const t = tag.trim();
    return t === '*' || t === etag || t === `W/${etag}`;
  });
}

const BASE_HEADERS = { 'x-content-type-options': 'nosniff' };

function plain(statusCode, text, extra = {}) {
  return {
    statusCode,
    headers: { ...BASE_HEADERS, 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...extra },
    body: `${text}\n`,
    isBase64Encoded: false,
  };
}

const notFound = () => plain(404, 'Not found');

function lowercaseHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers ?? {})) out[k.toLowerCase()] = Array.isArray(v) ? v.join(',') : String(v);
  return out;
}

function bodyLength(res) {
  if (!res.body) return 0;
  return res.isBase64Encoded ? Buffer.from(res.body, 'base64').length : Buffer.byteLength(res.body, 'utf8');
}

export function createHandler({ env = process.env, distDir = env.DEVBOX_DIST_DIR || defaultDistDir(), log = console } = {}) {
  const manifest = JSON.parse(readFileSync(join(distDir, 'manifest.json'), 'utf8'));
  const settings = loadSettings(env, manifest);
  const { static: staticFiles, webview: webviewFiles, web: webFiles } = manifest.sections;
  const serverRootName = manifest.serverRoot.slice(1);
  const webviewPrefix = `${manifest.serverRoot}/static/${WEBVIEW_PRE}/`;
  const csp = workbenchCsp(settings);
  const swCsp = `default-src 'none'; connect-src ${settings.agentcore.origin}`;

  const fileCache = new Map();
  let cachedBytes = 0;
  function readVariant(section, rel, encoding) {
    const suffix = encoding === 'br' ? '.br' : encoding === 'gzip' ? '.gz' : '';
    const key = `${section}/${rel}${suffix}`;
    let buf = fileCache.get(key);
    if (!buf) {
      buf = readFileSync(join(distDir, section, ...rel.split('/')) + suffix);
      if (cachedBytes + buf.length > FILE_CACHE_BUDGET) {
        fileCache.clear();
        cachedBytes = 0;
      }
      fileCache.set(key, buf);
      cachedBytes += buf.length;
    }
    return buf;
  }

  // The page gets its nonce per response; the webview shell gets the workbench origin once per cold
  // start, and its ETag follows the rendered bytes.
  const workbenchHtml = readVariant('web', 'index.html', 'identity').toString('utf8');
  const webviewIndexBuf = Buffer.from(readVariant('webview', 'index.html', 'identity').toString('utf8')
    .split(WORKBENCH_ORIGIN_PLACEHOLDER).join(settings.workbenchOrigin), 'utf8');
  const webviewIndex = {
    buf: webviewIndexBuf,
    etag: `"${createHash('sha256').update(webviewIndexBuf).digest('hex').slice(0, 32)}"`,
  };

  function serveFile(section, entries, rel, request, extraHeaders) {
    if (!Object.hasOwn(entries, rel)) return notFound();
    const entry = entries[rel];
    let encoding;
    let buf;
    let etag;
    if (section === 'webview' && rel === 'index.html') {
      encoding = 'identity';
      buf = webviewIndex.buf;
      etag = webviewIndex.etag;
    } else {
      encoding = chooseEncoding(request.headers['accept-encoding'], entry);
      if (!encoding) return plain(406, 'This file is only served compressed (br or gzip).', { vary: 'Accept-Encoding' });
      const variant = encoding === 'identity' ? entry : entry.variants[encoding];
      etag = `"${variant.sha256.slice(0, 32)}"`;
      buf = null;
    }
    const headers = {
      ...BASE_HEADERS,
      'content-type': entry.mime,
      etag,
      vary: 'Accept-Encoding',
      ...extraHeaders,
    };
    if (encoding !== 'identity') headers['content-encoding'] = encoding;
    if (etagMatches(request.headers['if-none-match'], etag)) {
      return { statusCode: 304, headers, body: '', isBase64Encoded: false };
    }
    buf ??= readVariant(section, rel, encoding);
    return { statusCode: 200, headers, body: buf.toString('base64'), isBase64Encoded: true };
  }

  function page() {
    const nonce = randomBytes(16).toString('base64');
    return {
      statusCode: 200,
      headers: {
        ...BASE_HEADERS,
        'content-type': 'text/html; charset=utf-8',
        'cache-control': 'no-store',
        'content-security-policy': csp.split(NONCE_PLACEHOLDER).join(nonce),
        'referrer-policy': 'no-referrer',
        'x-frame-options': 'DENY',
      },
      body: workbenchHtml.split(NONCE_PLACEHOLDER).join(nonce),
      isBase64Encoded: false,
    };
  }

  function workbench(request) {
    const { rawPath } = request;
    // One page: the loader boots VS Code on / and a Claude Code terminal on /terminal; /callback is Okta's
    // return, from which the loader goes back to whichever of the two started the sign-in.
    if (rawPath === '/' || rawPath === '/callback' || rawPath === '/terminal') return page();
    if (rawPath === '/devbox-config.json') {
      return {
        statusCode: 200,
        headers: { ...BASE_HEADERS, 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
        body: settings.configJson,
        isBase64Encoded: false,
      };
    }
    if (rawPath === '/sw.js') {
      return serveFile('web', webFiles, 'sw.js', request, {
        'cache-control': 'no-cache',
        'service-worker-allowed': '/',
        'content-security-policy': swCsp,
      });
    }
    const segments = decodePath(rawPath);
    if (!segments) return notFound();
    if (segments.length === 2 && segments[0] === 'devbox' && segments[1].endsWith('.js')) {
      return serveFile('web', webFiles, `devbox/${segments[1]}`, request, { 'cache-control': 'no-cache' });
    }
    if (segments[0] === serverRootName && segments[1] === 'static' && segments.length > 2) {
      const rel = segments.slice(2).join('/');
      // The webview shell lives on the webview site only.
      if (rel.startsWith(`${WEBVIEW_PRE}/`)) return notFound();
      return serveFile('static', staticFiles, rel, request, { 'cache-control': IMMUTABLE });
    }
    // Includes SERVER_ROOT/vscode-remote-resource: the Service Worker answers those in the browser.
    return notFound();
  }

  function webview(request) {
    const { rawPath } = request;
    if (!rawPath.startsWith(webviewPrefix)) return notFound();
    const name = rawPath.slice(webviewPrefix.length);
    if (!Object.hasOwn(webviewFiles, name)) return notFound();
    // fake.html is framed by the webview's own index.html, which is itself framed by the workbench.
    const ancestors = name === 'fake.html' ? `'self' ${settings.workbenchOrigin}` : settings.workbenchOrigin;
    return serveFile('webview', webviewFiles, name, request, {
      'cache-control': WEBVIEW_CACHE,
      'content-security-policy': `frame-ancestors ${ancestors}`,
    });
  }

  return async function handler(event) {
    const method = String(event?.requestContext?.http?.method ?? 'GET').toUpperCase();
    const request = { rawPath: typeof event?.rawPath === 'string' ? event.rawPath : '/', headers: lowercaseHeaders(event?.headers) };
    const site = request.headers['x-devbox-site'];
    let res;
    try {
      if (method !== 'GET' && method !== 'HEAD') res = plain(405, 'Method not allowed', { allow: 'GET, HEAD' });
      else if (site === 'workbench') res = workbench(request);
      else if (site === 'webview') res = webview(request);
      else res = notFound();
    } catch (err) {
      // Paths only: never headers, cookies or query strings.
      log.error(JSON.stringify({ msg: 'edge error', site, method, path: request.rawPath.slice(0, 300), error: String(err?.message ?? err) }));
      res = plain(500, 'Internal error');
    }
    if (method === 'HEAD') {
      res = { ...res, headers: { ...res.headers, 'content-length': String(bodyLength(res)) }, body: '', isBase64Encoded: false };
    }
    return res;
  };
}

let defaultHandler;
export async function handler(event) {
  defaultHandler ??= createHandler();
  return defaultHandler(event);
}
