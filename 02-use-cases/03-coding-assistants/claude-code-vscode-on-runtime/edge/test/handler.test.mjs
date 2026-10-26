import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { rm } from 'node:fs/promises';
import { join } from 'node:path';
import zlib from 'node:zlib';

import { chooseEncoding, createHandler, decodePath, parseAcceptEncoding, ConfigError } from '../src/handler.mjs';
import { MAX_RAW_BODY_BYTES } from '../src/limits.mjs';
import {
  CONFIG, SERVER_ROOT, STATIC_FILES, WEBVIEW_ORIGIN, WORKBENCH_ORIGIN, bodyBytes, envFor, event, makeFixtureDist,
} from './fixture.mjs';

const PRE = `${SERVER_ROOT}/static/out/vs/workbench/contrib/webview/browser/pre`;
const S = `${SERVER_ROOT}/static`;

let fixture;
let handler;
const logged = [];
const log = { error: (...a) => logged.push(a.join(' ')), warn: (...a) => logged.push(a.join(' ')), info: (...a) => logged.push(a.join(' ')), log: (...a) => logged.push(a.join(' ')) };

before(async () => {
  fixture = await makeFixtureDist();
  handler = createHandler({ env: envFor(), distDir: fixture.dist, log });
});
after(() => fixture.cleanup());

const get = (path, opts) => handler(event(path, opts));

describe('settings', () => {
  const tryEnv = env => () => createHandler({ env, distDir: fixture.dist, log });

  test('refuses to start without both origins', () => {
    assert.throws(tryEnv(envFor({ WORKBENCH_ORIGIN: undefined })), ConfigError);
    assert.throws(tryEnv(envFor({ WEBVIEW_ORIGIN: 'https://d222.cloudfront.net/path' })), ConfigError);
  });

  test('refuses a webview origin equal to the workbench origin', () => {
    const config = { ...CONFIG, webviewOrigin: WORKBENCH_ORIGIN };
    assert.throws(tryEnv(envFor({ WEBVIEW_ORIGIN: WORKBENCH_ORIGIN, DEVBOX_CONFIG_JSON: JSON.stringify(config) })), /must differ/);
  });

  test('refuses a config for another VS Code build, or with a different webview origin', () => {
    assert.throws(tryEnv(envFor({ DEVBOX_CONFIG_JSON: '{not json' })), /not valid JSON/);
    assert.throws(tryEnv(envFor({ DEVBOX_CONFIG_JSON: JSON.stringify({ ...CONFIG, serverRoot: '/stable-0000000000000000000000000000000000000000' }) })), /this image serves/);
    assert.throws(tryEnv(envFor({ DEVBOX_CONFIG_JSON: JSON.stringify({ ...CONFIG, webviewOrigin: 'https://d333.cloudfront.net' }) })), /webviewOrigin/);
    assert.throws(tryEnv(envFor({ DEVBOX_CONFIG_JSON: JSON.stringify({ ...CONFIG, okta: { issuer: 'not a url', clientId: 'x' } }) })), /okta.issuer/);
  });

  // The deployed config names nobody; the page asks the provisioner instead.
  test('takes a provision path instead of a boxes map, and refuses one that leaves the site', () => {
    const { boxes, ...rest } = CONFIG;
    const withProvision = p => envFor({ DEVBOX_CONFIG_JSON: JSON.stringify({ ...rest, provision: p }) });
    assert.doesNotThrow(tryEnv(withProvision({ path: '/api/box', header: 'X-Devbox-Token' })));
    assert.throws(tryEnv(withProvision({ path: 'https://evil.example/api/box', header: 'X-Devbox-Token' })), /same-origin/);
    assert.throws(tryEnv(withProvision({ path: '/api/box', header: 'Authorization' })), /X- header/);
    assert.throws(tryEnv(envFor({ DEVBOX_CONFIG_JSON: JSON.stringify(rest) })), /provision .* or boxes/);
  });
});

describe('workbench page', () => {
  for (const path of ['/', '/callback', '/terminal']) {
    test(`${path} is the workbench page with a fresh CSP nonce`, async () => {
      const res = await get(path);
      assert.equal(res.statusCode, 200);
      assert.equal(res.headers['content-type'], 'text/html; charset=utf-8');
      assert.equal(res.headers['cache-control'], 'no-store');
      assert.equal(res.headers['x-frame-options'], 'DENY');
      const csp = res.headers['content-security-policy'];
      const nonce = /'nonce-([A-Za-z0-9+/=]+)'/.exec(csp)[1];
      assert.equal(Buffer.from(nonce, 'base64').length, 16);
      const html = bodyBytes(res).toString('utf8');
      const tags = [...html.matchAll(/<script nonce="([^"]+)" src="([^"]+)"><\/script>/g)];
      assert.deepEqual(tags.map(m => m[2]), ['/devbox/shim.js', '/devbox/oidc.js', '/devbox/loader.js']);
      assert.ok(tags.every(m => m[1] === nonce));
      assert.ok(!html.includes('{{'), 'no unrendered placeholders');
      assert.ok(!/type="module"/.test(html), 'no module scripts in the page');
      assert.ok(html.includes(`${S}/resources/server/favicon.ico`));
    });
  }

  test('/terminal is the very same page as / (the loader picks the terminal from the path)', async () => {
    const strip = res => bodyBytes(res).toString('utf8').replace(/nonce="[^"]+"/g, 'nonce=""');
    const [home, terminal] = [await get('/'), await get('/terminal')];
    assert.equal(strip(terminal), strip(home));
    const cspOf = res => res.headers['content-security-policy'].replace(/'nonce-[^']+'/, '');
    assert.equal(cspOf(terminal), cspOf(home));
    assert.equal(terminal.headers['referrer-policy'], 'no-referrer');
  });

  test('the CSP matches the contract', async () => {
    const csp = (await get('/')).headers['content-security-policy'];
    const directives = Object.fromEntries(csp.split('; ').map(d => [d.split(' ')[0], d.split(' ').slice(1).join(' ')]));
    assert.equal(directives['default-src'], "'self'");
    assert.match(directives['script-src'], /^'self' 'unsafe-eval' 'nonce-[A-Za-z0-9+/=]+'$/);
    assert.equal(directives['style-src'], "'self' 'unsafe-inline'");
    assert.equal(directives['img-src'], "'self' data: blob:");
    assert.equal(directives['font-src'], "'self' data:");
    assert.equal(directives['connect-src'], "'self' https://bedrock-agentcore.us-east-1.amazonaws.com wss://bedrock-agentcore.us-east-1.amazonaws.com https://example.okta.com");
    assert.equal(directives['frame-src'], `'self' ${WEBVIEW_ORIGIN}`);
    assert.equal(directives['worker-src'], "'self' blob:");
    assert.equal(directives['frame-ancestors'], "'none'");
    assert.equal(directives['base-uri'], "'none'");
    assert.equal(directives['form-action'], "'self' https://example.okta.com");
  });

  test('every response gets its own nonce', async () => {
    const nonces = new Set();
    for (let i = 0; i < 50; i++) {
      nonces.add(/'nonce-([^']+)'/.exec((await get('/')).headers['content-security-policy'])[1]);
    }
    assert.equal(nonces.size, 50);
  });

  test('the local test config maps to http and ws sources', async () => {
    const local = { ...CONFIG, agentcoreBase: 'http://localhost:9401', okta: { ...CONFIG.okta, issuer: 'http://localhost:9400/oauth2/default' }, webviewOrigin: 'http://localhost:9403' };
    const h = createHandler({
      env: { WORKBENCH_ORIGIN: 'http://localhost:9402', WEBVIEW_ORIGIN: 'http://localhost:9403', DEVBOX_CONFIG_JSON: JSON.stringify(local) },
      distDir: fixture.dist,
      log,
    });
    const csp = (await h(event('/'))).headers['content-security-policy'];
    assert.match(csp, /connect-src 'self' http:\/\/localhost:9401 ws:\/\/localhost:9401 http:\/\/localhost:9400;/);
    assert.match(csp, /frame-src 'self' http:\/\/localhost:9403;/);
  });
});

describe('config, scripts and the Service Worker', () => {
  test('/devbox-config.json passes DEVBOX_CONFIG_JSON through unchanged', async () => {
    const raw = `${JSON.stringify(CONFIG, null, 2)}\n`;
    const h = createHandler({ env: envFor({ DEVBOX_CONFIG_JSON: raw }), distDir: fixture.dist, log });
    const res = await h(event('/devbox-config.json'));
    assert.equal(res.statusCode, 200);
    assert.equal(res.headers['cache-control'], 'no-store');
    assert.equal(res.headers['content-type'], 'application/json; charset=utf-8');
    assert.equal(bodyBytes(res).toString('utf8'), raw);
  });

  test('/sw.js may control the whole origin and can only talk to AgentCore', async () => {
    const res = await get('/sw.js');
    assert.equal(res.statusCode, 200);
    assert.equal(res.headers['service-worker-allowed'], '/');
    assert.equal(res.headers['cache-control'], 'no-cache');
    assert.equal(res.headers['content-type'], 'text/javascript; charset=utf-8');
    assert.equal(res.headers['content-security-policy'], "default-src 'none'; connect-src https://bedrock-agentcore.us-east-1.amazonaws.com");
  });

  test('/devbox/*.js serves only our scripts', async () => {
    for (const name of ['loader.js', 'oidc.js', 'shim.js', 'terminal.js']) {
      const res = await get(`/devbox/${name}`);
      assert.equal(res.statusCode, 200, name);
      assert.equal(res.headers['cache-control'], 'no-cache');
    }
    for (const path of ['/devbox/missing.js', '/devbox/index.html', '/devbox/../sw.js', '/devbox/%2e%2e/sw.js', '/devbox/', '/devbox/loader.js/x']) {
      assert.equal((await get(path)).statusCode, 404, path);
    }
  });
});

describe('static files', () => {
  test('picks br, then gzip, then identity from Accept-Encoding', async () => {
    const path = `${S}/out/vs/code/browser/workbench/workbench.js`;
    const original = STATIC_FILES['out/vs/code/browser/workbench/workbench.js'];
    const br = await get(path, { headers: { 'accept-encoding': 'gzip, deflate, br, zstd' } });
    assert.equal(br.statusCode, 200);
    assert.equal(br.headers['content-encoding'], 'br');
    assert.equal(br.headers.vary, 'Accept-Encoding');
    assert.equal(br.headers['cache-control'], 'public, max-age=31536000, immutable');
    assert.equal(br.isBase64Encoded, true);
    assert.deepEqual(zlib.brotliDecompressSync(bodyBytes(br)), original);

    const gz = await get(path, { headers: { 'accept-encoding': 'gzip' } });
    assert.equal(gz.headers['content-encoding'], 'gzip');
    assert.deepEqual(zlib.gunzipSync(bodyBytes(gz)), original);

    const plain = await get(path);
    assert.equal(plain.headers['content-encoding'], undefined);
    assert.equal(plain.headers.vary, 'Accept-Encoding');
    assert.deepEqual(bodyBytes(plain), original);

    assert.notEqual(br.headers.etag, gz.headers.etag);
    assert.notEqual(gz.headers.etag, plain.headers.etag);
  });

  test('honours q-values', async () => {
    const path = `${S}/out/vs/code/browser/workbench/workbench.css`;
    const enc = async ae => (await get(path, { headers: { 'accept-encoding': ae } })).headers['content-encoding'];
    assert.equal(await enc('br;q=0, gzip'), 'gzip');
    assert.equal(await enc('gzip;q=0.9, br;q=0.4'), 'gzip');
    assert.equal(await enc('gzip;q=0.5, br;q=0.8'), 'br');
    assert.equal(await enc('*;q=0.1'), 'br');
    assert.equal(await enc('identity'), undefined);
    assert.equal(await enc('gzip;q=0, br;q=0'), undefined);
  });

  test('never sends a body too big for a BUFFERED response', async () => {
    const path = `${S}/out/big.js`;
    const refused = await get(path);
    assert.equal(refused.statusCode, 406);
    const gz = await get(path, { headers: { 'accept-encoding': 'gzip' } });
    assert.equal(gz.statusCode, 200);
    assert.ok(bodyBytes(gz).length <= MAX_RAW_BODY_BYTES);
    // The whole function response must stay under Lambda's 6 MiB.
    assert.ok(JSON.stringify(gz).length < 6 * 1024 * 1024);
  });

  test('sets the right content types', async () => {
    const cases = {
      'node_modules/vscode-oniguruma/release/onig.wasm': 'application/wasm',
      'out/media/codicon.woff2': 'font/woff2',
      'extensions/theme/icon.svg': 'image/svg+xml',
      'out/vs/code/browser/workbench/workbench.css': 'text/css; charset=utf-8',
      'resources/server/manifest.json': 'application/json; charset=utf-8',
      'extensions/misc/blob.xyz': 'application/octet-stream',
      'extensions/javascript/syntaxes/Regular Expressions (JavaScript).tmLanguage': 'application/xml; charset=utf-8',
    };
    for (const [rel, type] of Object.entries(cases)) {
      const path = `${S}/${rel.split('/').map(encodeURIComponent).join('/')}`;
      const res = await get(path, { headers: { 'accept-encoding': 'br, gzip' } });
      assert.equal(res.statusCode, 200, rel);
      assert.equal(res.headers['content-type'], type, rel);
      assert.equal(res.headers['x-content-type-options'], 'nosniff');
    }
  });

  test('does not compress files that are already compressed', async () => {
    const res = await get(`${S}/out/media/codicon.woff2`, { headers: { 'accept-encoding': 'br, gzip' } });
    assert.equal(res.headers['content-encoding'], undefined);
  });

  test('answers If-None-Match with 304', async () => {
    const path = `${S}/out/vs/code/browser/workbench/workbench.js`;
    const first = await get(path, { headers: { 'accept-encoding': 'br' } });
    const again = await get(path, { headers: { 'accept-encoding': 'br', 'if-none-match': first.headers.etag } });
    assert.equal(again.statusCode, 304);
    assert.equal(again.body, '');
    assert.equal(again.headers.etag, first.headers.etag);
    const other = await get(path, { headers: { 'accept-encoding': 'gzip', 'if-none-match': first.headers.etag } });
    assert.equal(other.statusCode, 200, 'the br ETag does not match the gzip representation');
  });

  test('HEAD sends the headers without a body', async () => {
    const path = `${S}/out/vs/code/browser/workbench/workbench.js`;
    const head = await get(path, { method: 'HEAD', headers: { 'accept-encoding': 'br' } });
    const full = await get(path, { headers: { 'accept-encoding': 'br' } });
    assert.equal(head.statusCode, 200);
    assert.equal(head.body, '');
    assert.equal(head.headers['content-encoding'], 'br');
    assert.equal(head.headers['content-length'], String(bodyBytes(full).length));
    assert.equal((await get('/', { method: 'HEAD' })).body, '');
  });
});

describe('path safety', () => {
  const attempts = [
    `${S}/../web/index.html`,
    `${S}/out/../../web/sw.js`,
    `${S}/%2e%2e/web/sw.js`,
    `${S}/%2E%2E/%2E%2E/manifest.json`,
    `${S}/out%2fvs%2fcode%2fbrowser%2fworkbench%2fworkbench.js`,
    `${S}/out%2Fsmall.js`,
    `${S}/out/small.js%00.png`,
    `${S}/out/small.js\0`,
    `${S}/out\\small.js`,
    `${S}/out%5csmall.js`,
    `${S}/%252e%252e/web/sw.js`,
    `${S}/%c0%ae%c0%ae/web/sw.js`,
    `${S}/%e0%80%ae/web/sw.js`,
    `${S}/%`,
    `${S}/out/%zz`,
    `${S}//out/small.js`,
    `${S}/./out/small.js`,
    `${S}/out/`,
    `${S}/`,
    `${S}`,
    `/${SERVER_ROOT.slice(1)}/static/../../../../etc/passwd`,
    '/../../etc/passwd',
    '//etc/passwd',
    `${S}/out/nope.js`,
    '/manifest.json',
    '/static/out/small.js',
    '/web/index.html',
    '/index.html',
    '/callback/',
    '/terminal/',
    '/Terminal',
    '/terminal/x',
    `/stable-0000000000000000000000000000000000000000/static/out/small.js`,
  ];
  test('control: the file the attempts aim around is servable', async () => {
    assert.equal((await get(`${S}/out/small.js`)).statusCode, 200);
    assert.equal((await get(`${S}/out/%73mall.js`)).statusCode, 200, 'plain percent-encoding still resolves');
  });

  for (const path of attempts) {
    test(`404 for ${JSON.stringify(path)}`, async () => {
      const res = await get(path);
      assert.equal(res.statusCode, 404);
    });
  }

  test('decodePath rejects escapes and accepts ordinary names', () => {
    assert.deepEqual(decodePath('/a/b%20c/d.js'), ['a', 'b c', 'd.js']);
    assert.deepEqual(decodePath('/@xterm/xterm/lib/xterm.js'), ['@xterm', 'xterm', 'lib', 'xterm.js']);
    for (const bad of ['a/b', '/a/../b', '/a/%2e%2e/b', '/a/%2F', '/a//b', '/a/', '/a\\b', '/%00', '/%25', '/%c0%ae']) {
      assert.equal(decodePath(bad), null, bad);
    }
  });

  test('a file whose name needs escaping is found', async () => {
    const res = await get(`${S}/extensions/javascript/syntaxes/Regular%20Expressions%20(JavaScript).tmLanguage`);
    assert.equal(res.statusCode, 200);
  });
});

describe('routing', () => {
  test('only GET and HEAD', async () => {
    for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']) {
      const res = await get('/', { method });
      assert.equal(res.statusCode, 405, method);
      assert.equal(res.headers.allow, 'GET, HEAD');
    }
  });

  test('vscode-remote-resource is never proxied', async () => {
    for (const site of ['workbench', 'webview']) {
      const res = await get(`${SERVER_ROOT}/vscode-remote-resource`, { site, query: 'path=%2Fetc%2Fpasswd' });
      assert.equal(res.statusCode, 404, site);
    }
    assert.equal((await get(`${SERVER_ROOT}/version`)).statusCode, 404);
    assert.equal((await get(`${SERVER_ROOT}`)).statusCode, 404);
  });

  test('without a known site header nothing is served', async () => {
    for (const site of [null, '', 'WORKBENCH', 'other']) {
      assert.equal((await get('/', { site })).statusCode, 404, String(site));
      assert.equal((await get(`${PRE}/index.html`, { site })).statusCode, 404, String(site));
    }
  });

  test('the workbench site does not serve the webview shell', async () => {
    for (const name of ['index.html', 'fake.html', 'service-worker.js']) {
      assert.equal((await get(`${PRE}/${name}`)).statusCode, 404, name);
    }
  });

  test('the webview site serves only the patched webview shell', async () => {
    const index = await get(`${PRE}/index.html`, { site: 'webview' });
    assert.equal(index.statusCode, 200);
    assert.equal(index.headers['content-security-policy'], `frame-ancestors ${WORKBENCH_ORIGIN}`);
    assert.equal(index.headers['x-frame-options'], undefined);
    assert.equal(index.headers['cache-control'], 'public, max-age=3600');
    const html = bodyBytes(index).toString('utf8');
    assert.ok(html.includes(`<meta name="devbox-workbench-origin" content="${WORKBENCH_ORIGIN}">`));
    assert.ok(!html.includes('{{DEVBOX_WORKBENCH_ORIGIN}}'));

    const fake = await get(`${PRE}/fake.html`, { site: 'webview' });
    assert.equal(fake.statusCode, 200);
    assert.equal(fake.headers['content-security-policy'], `frame-ancestors 'self' ${WORKBENCH_ORIGIN}`);

    const sw = await get(`${PRE}/service-worker.js`, { site: 'webview', headers: { 'accept-encoding': 'br' } });
    assert.equal(sw.statusCode, 200);
    assert.equal(sw.headers['content-type'], 'text/javascript; charset=utf-8');
    assert.equal(sw.headers['x-frame-options'], undefined);

    for (const path of ['/', '/callback', '/terminal', '/sw.js', '/devbox-config.json', '/devbox/loader.js', '/devbox/terminal.js', `${S}/out/small.js`, `${PRE}/`, `${PRE}/other.html`, `${PRE}/../pre/index.html`, `${PRE}/%2e%2e/pre/index.html`]) {
      assert.equal((await get(path, { site: 'webview' })).statusCode, 404, path);
    }
  });

  test('the webview index ETag follows the rendered origin', async () => {
    const a = await get(`${PRE}/index.html`, { site: 'webview' });
    const other = createHandler({ env: envFor({ WORKBENCH_ORIGIN: 'https://d999999abcdef8.cloudfront.net' }), distDir: fixture.dist, log });
    const b = await other(event(`${PRE}/index.html`, { site: 'webview' }));
    assert.notEqual(a.headers.etag, b.headers.etag);
    const again = await get(`${PRE}/index.html`, { site: 'webview', headers: { 'if-none-match': a.headers.etag } });
    assert.equal(again.statusCode, 304);
  });
});

describe('logging', () => {
  test('never logs headers, cookies or query strings', async () => {
    const { dist } = await makeFixtureDist();
    const h = createHandler({ env: envFor(), distDir: dist, log });
    await rm(join(dist, 'static', 'out', 'small.js'));
    logged.length = 0;
    const secret = 'eyJhbGciOiJSUzI1NiJ9.SECRET-TOKEN.sig';
    const res = await h(event(`${S}/out/small.js`, {
      headers: { authorization: `Bearer ${secret}`, cookie: `session=${secret}`, 'x-other': secret },
      query: `token=${secret}`,
    }));
    assert.equal(res.statusCode, 500);
    assert.equal(logged.length, 1);
    assert.ok(logged[0].includes('/out/small.js'));
    assert.ok(!logged.join('\n').includes('SECRET-TOKEN'));
    await rm(dist, { recursive: true, force: true });
  });

  test('ordinary requests log nothing', async () => {
    logged.length = 0;
    await get('/', { headers: { authorization: 'Bearer x' }, query: 'code=abc' });
    await get('/nope');
    assert.equal(logged.length, 0);
  });
});

describe('encoding helpers', () => {
  test('parseAcceptEncoding', () => {
    const q = parseAcceptEncoding('gzip;q=0.8, br, *;q=0.1, identity;q=0');
    assert.equal(q.get('gzip'), 0.8);
    assert.equal(q.get('br'), 1);
    assert.equal(q.get('*'), 0.1);
    assert.equal(q.get('identity'), 0);
    assert.equal(parseAcceptEncoding(undefined).size, 0);
  });

  test('chooseEncoding falls back when a variant is too big', () => {
    const entry = { size: 100, variants: { br: { size: 60 }, gzip: { size: 70 } } };
    assert.equal(chooseEncoding('br, gzip', entry, 65), 'br');
    assert.equal(chooseEncoding('br, gzip', entry, 50), null);
    assert.equal(chooseEncoding('br, gzip', { ...entry, size: 40 }, 50), 'identity');
    // gzip (70) and identity (100) are both over the limit; br is not accepted.
    assert.equal(chooseEncoding('gzip', entry, 65), null);
  });
});

describe('the Lambda export', () => {
  test('handler builds itself from process.env on first use', async () => {
    const saved = { ...process.env };
    Object.assign(process.env, envFor(), { DEVBOX_DIST_DIR: fixture.dist });
    try {
      const { handler: lambda } = await import(`../src/handler.mjs?fresh=${Date.now()}`);
      const res = await lambda(event('/devbox-config.json'));
      assert.equal(res.statusCode, 200);
      assert.deepEqual(JSON.parse(bodyBytes(res)), CONFIG);
    } finally {
      for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
      Object.assign(process.env, saved);
    }
  });
});
