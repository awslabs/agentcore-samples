// Serves the edge handler over plain HTTP for local runs and tests: one server per site, standing in
// for one CloudFront distribution. The site is fixed per server (like CloudFront's origin custom
// header), so a client can't pick the other site by sending x-devbox-site itself.
//
//   node src/local-server.mjs --site workbench --port 9402 [--host 127.0.0.1]
//   node src/local-server.mjs --site webview   --port 9403
//
// Environment: WORKBENCH_ORIGIN, WEBVIEW_ORIGIN, and DEVBOX_CONFIG_JSON (or DEVBOX_CONFIG_FILE, a path
// to the same JSON), optionally DEVBOX_DIST_DIR.

import http from 'node:http';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createHandler } from './handler.mjs';

const SITES = new Set(['workbench', 'webview']);

// Turns a Node request into the function URL event (payload format 2.0) the Lambda receives.
export function toFunctionUrlEvent(req, site) {
  const url = req.url ?? '/';
  const q = url.indexOf('?');
  const rawPath = q < 0 ? url : url.slice(0, q);
  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (name === 'x-devbox-site') continue;
    headers[name] = Array.isArray(value) ? value.join(',') : value;
  }
  headers['x-devbox-site'] = site;
  return {
    version: '2.0',
    routeKey: '$default',
    rawPath,
    rawQueryString: q < 0 ? '' : url.slice(q + 1),
    headers,
    requestContext: {
      http: { method: req.method, path: rawPath, protocol: `HTTP/${req.httpVersion}`, sourceIp: req.socket.remoteAddress ?? '' },
    },
    isBase64Encoded: false,
  };
}

export function createLocalServer({ site, handler }) {
  if (!SITES.has(site)) throw new Error(`site must be workbench or webview, got ${site}`);
  return http.createServer(async (req, res) => {
    try {
      const out = await handler(toFunctionUrlEvent(req, site));
      const body = out.body
        ? Buffer.from(out.body, out.isBase64Encoded ? 'base64' : 'utf8')
        : Buffer.alloc(0);
      const headers = { ...out.headers };
      if (req.method !== 'HEAD') headers['content-length'] = String(body.length);
      res.writeHead(out.statusCode, headers);
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch (err) {
      console.error(`edge-local: handler failed: ${err?.message ?? err}`);
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('edge-local: handler error\n');
    }
  });
}

// Starts one site. Returns { server, url, close }.
export async function startLocalServer({ site, port, host = '127.0.0.1', env = process.env, distDir }) {
  const handler = createHandler({ env, ...(distDir ? { distDir } : {}) });
  const server = createLocalServer({ site, handler });
  await new Promise((resolveListen, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.off('error', reject);
      resolveListen();
    });
  });
  const { port: bound } = server.address();
  return {
    server,
    url: `http://${host.includes(':') ? `[${host}]` : host}:${bound}`,
    close: () => new Promise(done => server.close(() => done())),
  };
}

function parseArgs(argv) {
  const args = { host: '127.0.0.1' };
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    const value = argv[i + 1];
    if (!['--site', '--port', '--host'].includes(key) || value === undefined) {
      throw new Error('usage: local-server.mjs --site workbench|webview --port <n> [--host <addr>]');
    }
    args[key.slice(2)] = value;
  }
  if (!SITES.has(args.site) || !/^\d+$/.test(args.port ?? '')) {
    throw new Error('usage: local-server.mjs --site workbench|webview --port <n> [--host <addr>]');
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const env = { ...process.env };
  if (!env.DEVBOX_CONFIG_JSON && env.DEVBOX_CONFIG_FILE) {
    env.DEVBOX_CONFIG_JSON = readFileSync(resolve(env.DEVBOX_CONFIG_FILE), 'utf8');
  }
  const { url, close } = await startLocalServer({ site: args.site, port: Number(args.port), host: args.host, env });
  console.log(`edge-local: ${args.site} site on ${url}`);
  for (const signal of ['SIGINT', 'SIGTERM']) {
    process.on(signal, () => close().then(() => process.exit(0)));
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(err => {
    console.error(`edge-local: ${err.message}`);
    process.exit(1);
  });
}
