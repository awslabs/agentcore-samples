// A small stand-in for one CloudFront distribution in front of the edge Lambda's function URL,
// used when edge/src/local-server.mjs is not available. It turns each viewer request into a Lambda
// Function URL (payload 2.0) event and forwards what CloudFront would:
// - /stable-*/static/* (CachingOptimized, no origin request policy): no cookies, no query string, and only
//   a normalized Accept-Encoding;
// - everything else (CachingDisabled + AllViewerExceptHostHeader): all viewer headers except Host.
// Both add the distribution's origin custom header x-devbox-site. The workbench site also gets the
// managed SecurityHeadersPolicy headers. GET/HEAD only, like the distributions.
import http from 'node:http';
import crypto from 'node:crypto';
import { Writable } from 'node:stream';

const STATIC_BEHAVIOR = /^\/stable-[^/]*\/static\//;
const SECURITY_HEADERS = {
  'strict-transport-security': 'max-age=31536000',
  'x-frame-options': 'SAMEORIGIN',
  'referrer-policy': 'strict-origin-when-cross-origin',
  'x-xss-protection': '1; mode=block',
};
const STREAMING = Symbol.for('aws.lambda.runtime.handler.streaming');

// Minimal awslambda globals so a RESPONSE_STREAM handler (awslambda.streamifyResponse) also runs locally.
export function installLambdaGlobals() {
  if (globalThis.awslambda) return;
  globalThis.awslambda = {
    streamifyResponse(fn) { fn[STREAMING] = true; return fn; },
    HttpResponseStream: {
      from(stream, prelude) { stream.setPrelude?.(prelude); return stream; },
    },
  };
}

function normalizeAcceptEncoding(value = '') {
  const v = value.toLowerCase();
  const out = [];
  if (/\bbr\b/.test(v)) out.push('br');
  if (/\bgzip\b/.test(v)) out.push('gzip');
  return out.join(',');
}

function looksLikeJwt(value) {
  return /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/.test(value);
}

export function toFunctionUrlEvent(req, { site, domainName }) {
  const url = new URL(req.url, 'http://edge.local');
  const isStatic = STATIC_BEHAVIOR.test(url.pathname);
  const headers = {};
  let cookies;
  if (isStatic) {
    const ae = normalizeAcceptEncoding(req.headers['accept-encoding']);
    if (ae) headers['accept-encoding'] = ae;
    headers['user-agent'] = 'Amazon CloudFront';
  } else {
    for (const [name, value] of Object.entries(req.headers)) {
      if (name === 'host' || name === 'cookie') continue;
      headers[name] = Array.isArray(value) ? value.join(',') : value;
    }
    if (req.headers.cookie) cookies = req.headers.cookie.split(';').map((c) => c.trim()).filter(Boolean);
  }
  headers.host = domainName;
  headers['x-devbox-site'] = site;
  headers['x-forwarded-for'] = req.socket.remoteAddress ?? '127.0.0.1';
  // Added by the function URL itself.
  headers['x-forwarded-proto'] = 'https';
  headers['x-forwarded-port'] = '443';
  headers['x-amzn-trace-id'] = `Root=1-${Math.floor(Date.now() / 1000).toString(16)}-${crypto.randomBytes(12).toString('hex')}`;
  headers.via = '2.0 local.cloudfront.net (CloudFront)';
  headers['x-amz-cf-id'] = crypto.randomBytes(21).toString('base64url');
  const rawQueryString = isStatic ? '' : url.search.slice(1);
  const now = new Date();
  return {
    version: '2.0',
    routeKey: '$default',
    rawPath: url.pathname,
    rawQueryString,
    ...(cookies ? { cookies } : {}),
    headers,
    ...(rawQueryString ? { queryStringParameters: Object.fromEntries(new URLSearchParams(rawQueryString)) } : {}),
    requestContext: {
      accountId: 'anonymous',
      apiId: 'localedge',
      domainName,
      domainPrefix: domainName.split('.')[0],
      http: { method: req.method, path: url.pathname, protocol: 'HTTP/1.1', sourceIp: headers['x-forwarded-for'], userAgent: req.headers['user-agent'] ?? '' },
      requestId: crypto.randomUUID(),
      routeKey: '$default',
      stage: '$default',
      time: now.toUTCString(),
      timeEpoch: now.getTime(),
    },
    isBase64Encoded: false,
  };
}

// Collects a streamed response: prelude (status/headers) then body chunks.
class CollectingStream extends Writable {
  constructor() { super(); this.chunks = []; this.prelude = null; }
  setPrelude(p) { this.prelude = p; }
  _write(chunk, _enc, cb) { this.chunks.push(Buffer.from(chunk)); cb(); }
}

async function runHandler(handler, event) {
  if (handler[STREAMING]) {
    const stream = new CollectingStream();
    const done = new Promise((resolve, reject) => { stream.on('finish', resolve); stream.on('error', reject); });
    await handler(event, stream, {});
    await done;
    return { statusCode: stream.prelude?.statusCode ?? 200, headers: stream.prelude?.headers ?? {}, cookies: stream.prelude?.cookies, bodyBuffer: Buffer.concat(stream.chunks) };
  }
  const r = await handler(event, {});
  if (typeof r === 'string') return { statusCode: 200, headers: { 'content-type': 'application/json' }, bodyBuffer: Buffer.from(r) };
  const bodyBuffer = r.body === undefined ? Buffer.alloc(0) : Buffer.from(r.body, r.isBase64Encoded ? 'base64' : 'utf8');
  return { statusCode: r.statusCode ?? 200, headers: r.headers ?? {}, cookies: r.cookies, bodyBuffer };
}

export function createDistribution({ site, handler, domainName = `${site}.local.cloudfront.net`, log = console.log }) {
  return http.createServer(async (req, res) => {
    const started = Date.now();
    const url = new URL(req.url, 'http://edge.local');
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      // The distributions allow GET/HEAD only; CloudFront answers other methods itself.
      res.writeHead(403, { 'content-type': 'text/plain' });
      res.end('This distribution is not configured to allow the HTTP request method that was used for this request.');
      log(`[edge-local:${site}] ${req.method} ${url.pathname} -> 403 (method not allowed by the distribution)`);
      return;
    }
    const suspicious = [req.headers.authorization, req.headers.cookie, url.search].filter((v) => v && looksLikeJwt(v));
    if (req.headers.authorization || suspicious.length) {
      log(`[edge-local:${site}] WARN a request to ${url.pathname} carried ${req.headers.authorization ? 'an Authorization header' : 'a token-like value'}: the edge must never receive tokens`);
    }
    try {
      const out = await runHandler(handler, toFunctionUrlEvent(req, { site, domainName }));
      const headers = Object.fromEntries(Object.entries(out.headers).map(([k, v]) => [k.toLowerCase(), v]));
      if (site === 'workbench') {
        for (const [k, v] of Object.entries(SECURITY_HEADERS)) if (!(k in headers)) headers[k] = v;
        headers['x-content-type-options'] = 'nosniff';
      }
      if (out.cookies?.length) headers['set-cookie'] = out.cookies;
      headers['content-length'] = String(out.bodyBuffer.length);
      res.writeHead(out.statusCode, headers);
      res.end(req.method === 'HEAD' ? undefined : out.bodyBuffer);
      log(`[edge-local:${site}] ${req.method} ${url.pathname} -> ${out.statusCode} ${out.bodyBuffer.length}B ${Date.now() - started}ms`);
    } catch (err) {
      // CloudFront shows a generic 502 when the origin fails.
      res.writeHead(502, { 'content-type': 'text/plain' });
      res.end('502 Bad Gateway (the edge Lambda threw)');
      log(`[edge-local:${site}] ${req.method} ${url.pathname} -> 502 handler error: ${err.stack ?? err.message}`);
    }
  });
}
