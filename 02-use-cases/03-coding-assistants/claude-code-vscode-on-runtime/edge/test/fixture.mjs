// A small dist/ built with the build's own emit(), so tests run without the 75 MB tarball.

import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { emit, renderWorkbenchTemplate } from '../build/build.mjs';
import { SERVER_ROOT, OVS } from '../build/pins.mjs';
import { MAX_RAW_BODY_BYTES } from '../src/limits.mjs';

export const EDGE = join(dirname(fileURLToPath(import.meta.url)), '..');
export { SERVER_ROOT };
export const COMMIT = OVS.commit;
export const WORKBENCH_ORIGIN = 'https://d111111abcdef8.cloudfront.net';
export const WEBVIEW_ORIGIN = 'https://d222222abcdef8.cloudfront.net';

export const CONFIG = {
  region: 'us-east-1',
  commit: COMMIT,
  serverRoot: SERVER_ROOT,
  agentcoreBase: 'https://bedrock-agentcore.us-east-1.amazonaws.com',
  okta: { issuer: 'https://example.okta.com/oauth2/default', clientId: '0oa-test', scopes: 'openid profile email offline_access devbox' },
  webviewOrigin: WEBVIEW_ORIGIN,
  boxes: { abc: { name: 'ada', runtimeArn: 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/devbox_ada-XYZ', generation: 1 } },
};

export function envFor(overrides = {}) {
  return {
    WORKBENCH_ORIGIN,
    WEBVIEW_ORIGIN,
    DEVBOX_CONFIG_JSON: JSON.stringify(CONFIG),
    ...overrides,
  };
}

// Deterministic bytes that don't compress, for a file that should get no compressed variant.
function noise(size, seed = 1) {
  const buf = Buffer.alloc(size);
  let x = seed;
  for (let i = 0; i < size; i++) {
    x = (x * 1103515245 + 12345) >>> 0;
    buf[i] = x >>> 24;
  }
  return buf;
}

export const STATIC_FILES = {
  'out/vs/code/browser/workbench/workbench.js': Buffer.from(`export const x = 1;\n${'console.log("workbench");\n'.repeat(400)}`),
  'out/vs/code/browser/workbench/workbench.css': Buffer.from(`.monaco-workbench { color: red; }\n${'.a{b:c}\n'.repeat(300)}`),
  'out/nls.messages.js': Buffer.from(`globalThis._VSCODE_NLS_MESSAGES=[${'"x",'.repeat(500)}];\n`),
  'node_modules/vscode-oniguruma/release/onig.wasm': Buffer.concat([Buffer.from([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0]), Buffer.alloc(4000, 7)]),
  'out/media/codicon.woff2': noise(3000),
  'resources/server/manifest.json': Buffer.from('{"name":"Code"}\n'),
  'extensions/theme/icon.svg': Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg">${'<g/>'.repeat(400)}</svg>`),
  'extensions/javascript/syntaxes/Regular Expressions (JavaScript).tmLanguage': Buffer.from(`<?xml version="1.0"?>${'<dict/>'.repeat(300)}`),
  'extensions/misc/blob.xyz': Buffer.from('opaque\n'),
  'out/small.js': Buffer.from('export {};\n'),
  // Too big to send uncompressed in a BUFFERED response; highly compressible.
  'out/big.js': Buffer.from('a'.repeat(MAX_RAW_BODY_BYTES + 1000)),
};

export const WEBVIEW_INDEX = `<!DOCTYPE html>
<html><head>
	<meta charset="UTF-8">
	<meta name="devbox-workbench-origin" content="{{DEVBOX_WORKBENCH_ORIGIN}}">
</head><body>webview shell${' '.repeat(2000)}</body></html>
`;

export async function makeFixtureDist() {
  const root = await mkdtemp(join(tmpdir(), 'devbox-edge-fixture-'));
  const dist = join(root, 'dist');
  const zdir = join(root, 'z');
  await mkdir(zdir, { recursive: true });
  const manifest = {
    version: 1,
    ovs: { version: OVS.version, quality: OVS.quality, commit: OVS.commit },
    serverRoot: SERVER_ROOT,
    sections: { static: {}, webview: {}, web: {} },
  };
  for (const [rel, buf] of Object.entries(STATIC_FILES)) {
    manifest.sections.static[rel] = await emit(join(dist, 'static'), rel, buf, { compress: true, zdir });
  }
  const webview = {
    'index.html': [Buffer.from(WEBVIEW_INDEX), true],
    'fake.html': [Buffer.from('<!DOCTYPE html><html><body></body></html>\n'), false],
    'service-worker.js': [Buffer.from(`self.addEventListener("fetch", () => {});\n${'// pad\n'.repeat(300)}`), false],
  };
  for (const [name, [buf, template]] of Object.entries(webview)) {
    manifest.sections.webview[name] = await emit(join(dist, 'webview'), name, buf, { compress: true, zdir, template });
  }
  const webSource = await readFile(join(EDGE, 'web', 'index.html'), 'utf8');
  const web = {
    'index.html': [Buffer.from(renderWorkbenchTemplate(webSource)), true],
    'sw.js': [await readFile(join(EDGE, 'web', 'sw.js')), false],
    'devbox/loader.js': [await readFile(join(EDGE, 'web', 'devbox', 'loader.js')), false],
    'devbox/oidc.js': [await readFile(join(EDGE, 'web', 'devbox', 'oidc.js')), false],
    'devbox/shim.js': [await readFile(join(EDGE, 'web', 'devbox', 'shim.js')), false],
    'devbox/terminal.js': [await readFile(join(EDGE, 'web', 'devbox', 'terminal.js')), false],
  };
  for (const [name, [buf, template]] of Object.entries(web)) {
    manifest.sections.web[name] = await emit(join(dist, 'web'), name, buf, { compress: true, zdir, template });
  }
  await writeFile(join(dist, 'manifest.json'), JSON.stringify(manifest));
  return { dist, manifest, cleanup: () => rm(root, { recursive: true, force: true }) };
}

// A function URL event (payload format 2.0).
export function event(path, { method = 'GET', site = 'workbench', headers = {}, query = '' } = {}) {
  return {
    version: '2.0',
    routeKey: '$default',
    rawPath: path,
    rawQueryString: query,
    headers: site ? { 'x-devbox-site': site, ...headers } : { ...headers },
    requestContext: { http: { method, path, protocol: 'HTTP/1.1', sourceIp: '192.0.2.1' } },
    isBase64Encoded: false,
  };
}

export function bodyBytes(res) {
  return Buffer.from(res.body ?? '', res.isBase64Encoded ? 'base64' : 'utf8');
}

