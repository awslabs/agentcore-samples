// Runs devbox-edge:dev under the Lambda Runtime Interface Emulator that ships in the base image, and
// invokes it with function URL (payload 2.0) events. Needs Docker and a built image:
//
//   build/build.sh && docker buildx build --platform linux/arm64 --load -t devbox-edge:dev .
//   node test/image/rie-smoke.mjs
//
// Uses host port 9481 and removes its container when done.

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import zlib from 'node:zlib';

import { CONFIG, EDGE, SERVER_ROOT, WEBVIEW_ORIGIN, WORKBENCH_ORIGIN, event } from '../fixture.mjs';

const IMAGE = process.env.DEVBOX_EDGE_IMAGE || 'devbox-edge:dev';
const PORT = 9481;
const INVOKE = `http://127.0.0.1:${PORT}/2015-03-31/functions/function/invocations`;
const manifest = JSON.parse(readFileSync(join(EDGE, 'dist', 'manifest.json'), 'utf8'));

function docker(args, env) {
  const res = spawnSync('docker', args, { encoding: 'utf8', env: { ...process.env, ...env } });
  if (res.status !== 0) throw new Error(`docker ${args[0]} failed: ${res.stderr}`);
  return res.stdout.trim();
}

async function invoke(ev) {
  const res = await fetch(INVOKE, { method: 'POST', body: JSON.stringify(ev) });
  const text = await res.text();
  return { raw: text, out: JSON.parse(text) };
}

const checks = [];
function check(name, fn) {
  checks.push([name, fn]);
}

const S = `${SERVER_ROOT}/static`;

check('/ is the workbench page with a nonce CSP', async () => {
  const { out } = await invoke(event('/'));
  assert.equal(out.statusCode, 200);
  assert.match(out.headers['content-security-policy'], /script-src 'self' 'unsafe-eval' 'nonce-[A-Za-z0-9+/=]{24}'/);
  const nonce = /'nonce-([^']+)'/.exec(out.headers['content-security-policy'])[1];
  assert.ok(out.body.includes(`<script nonce="${nonce}" src="/devbox/shim.js"></script>`));
});

check('/callback is the same page', async () => {
  const { out } = await invoke(event('/callback'));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['cache-control'], 'no-store');
});

check('/terminal is the same page, and its scripts are in the image', async () => {
  const { out } = await invoke(event('/terminal'));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['cache-control'], 'no-store');
  assert.match(out.headers['content-security-policy'], /connect-src 'self' https:\/\/bedrock-agentcore\.us-east-1\.amazonaws\.com wss:\/\/bedrock-agentcore\.us-east-1\.amazonaws\.com /);
  assert.ok(out.body.includes('src="/devbox/loader.js"'));
  const terminal = (await invoke(event('/devbox/terminal.js'))).out;
  assert.equal(terminal.statusCode, 200);
  assert.equal(terminal.headers['cache-control'], 'no-cache');
  for (const rel of ['node_modules/@xterm/xterm/lib/xterm.js', 'node_modules/@xterm/xterm/css/xterm.css']) {
    const { out: file } = await invoke(event(`${S}/${rel}`, { headers: { 'accept-encoding': 'br' } }));
    assert.equal(file.statusCode, 200, rel);
    assert.equal(file.headers['cache-control'], 'public, max-age=31536000, immutable', rel);
  }
  assert.equal((await invoke(event('/terminal/'))).out.statusCode, 404);
});

check('/sw.js may control the origin', async () => {
  const { out } = await invoke(event('/sw.js', { headers: { 'accept-encoding': 'br' } }));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['service-worker-allowed'], '/');
  assert.equal(out.headers['cache-control'], 'no-cache');
  const js = zlib.brotliDecompressSync(Buffer.from(out.body, 'base64')).toString('utf8');
  assert.ok(js.includes("self.addEventListener('fetch'"));
});

check('/devbox-config.json passes the config through', async () => {
  const { out } = await invoke(event('/devbox-config.json'));
  assert.deepEqual(JSON.parse(out.body), CONFIG);
});

check('workbench.js arrives as brotli within the 6 MiB response limit', async () => {
  const { raw, out } = await invoke(event(`${S}/out/vs/code/browser/workbench/workbench.js`, {
    headers: { 'accept-encoding': 'gzip, deflate, br, zstd' },
  }));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['content-encoding'], 'br');
  assert.equal(out.isBase64Encoded, true);
  assert.ok(raw.length < 6 * 1024 * 1024, `response is ${raw.length} bytes`);
  const js = zlib.brotliDecompressSync(Buffer.from(out.body, 'base64'));
  assert.equal(createHash('sha256').update(js).digest('hex'), manifest.sections.static['out/vs/code/browser/workbench/workbench.js'].sha256);
  return `${raw.length} byte response`;
});

check('onig.wasm is application/wasm', async () => {
  const { out } = await invoke(event(`${S}/node_modules/vscode-oniguruma/release/onig.wasm`, { headers: { 'accept-encoding': 'br' } }));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['content-type'], 'application/wasm');
});

check('the webview site serves the patched shell with frame-ancestors', async () => {
  const { out } = await invoke(event(`${S}/out/vs/workbench/contrib/webview/browser/pre/index.html`, { site: 'webview' }));
  assert.equal(out.statusCode, 200);
  assert.equal(out.headers['content-security-policy'], `frame-ancestors ${WORKBENCH_ORIGIN}`);
  assert.ok(Buffer.from(out.body, 'base64').toString('utf8').includes(`content="${WORKBENCH_ORIGIN}"`));
});

const MARKER = 'tok-marker-7f3a91';

check('requests carrying a token in headers and query leave no trace in the logs', async () => {
  const headers = { authorization: `Bearer ${MARKER}`, cookie: `a=${MARKER}` };
  await invoke(event('/', { headers, query: `code=${MARKER}` }));
  await invoke(event(`${S}/out/nope.js`, { headers, query: `t=${MARKER}` }));
});

check('vscode-remote-resource is 404, POST is 405, traversal is 404', async () => {
  assert.equal((await invoke(event(`${SERVER_ROOT}/vscode-remote-resource`, { query: 'path=/etc/passwd' }))).out.statusCode, 404);
  assert.equal((await invoke(event('/', { method: 'POST' }))).out.statusCode, 405);
  assert.equal((await invoke(event(`${S}/%2e%2e/%2e%2e/manifest.json`))).out.statusCode, 404);
});

async function main() {
  const env = { WORKBENCH_ORIGIN, WEBVIEW_ORIGIN, DEVBOX_CONFIG_JSON: JSON.stringify(CONFIG) };
  const id = docker(['run', '-d', '--rm', '--platform', 'linux/arm64', '-p', `127.0.0.1:${PORT}:8080`,
    '-e', 'WORKBENCH_ORIGIN', '-e', 'WEBVIEW_ORIGIN', '-e', 'DEVBOX_CONFIG_JSON', IMAGE], env);
  let failed = 0;
  try {
    for (let i = 0; ; i++) {
      try {
        await fetch(`http://127.0.0.1:${PORT}/`, { method: 'GET' });
        break;
      } catch {
        if (i > 50) throw new Error('the emulator did not come up');
        await new Promise(r => setTimeout(r, 200));
      }
    }
    for (const [name, fn] of checks) {
      try {
        const note = await fn();
        console.log(`ok   ${name}${note ? ` (${note})` : ''}`);
      } catch (err) {
        failed++;
        console.log(`FAIL ${name}: ${err.message}`);
      }
    }
    const logs = spawnSync('docker', ['logs', id], { encoding: 'utf8' });
    const text = `${logs.stdout}\n${logs.stderr}`;
    const init = /INIT REPORT\(durationMs: ([\d.]+)\)/.exec(text);
    if (init) console.log(`init (emulator on this laptop, not Lambda): ${Math.round(Number(init[1]))} ms`);
    if (text.includes(MARKER)) {
      failed++;
      console.log('FAIL the token marker appears in the function logs');
    } else {
      console.log('ok   the token marker is not in the function logs');
    }
  } finally {
    spawnSync('docker', ['rm', '-f', id], { stdio: 'ignore' });
  }
  console.log(failed ? `${failed} of ${checks.length} checks failed` : `all ${checks.length} checks passed`);
  process.exit(failed ? 1 : 0);
}

main().catch(err => {
  console.error(err.message);
  process.exit(1);
});
