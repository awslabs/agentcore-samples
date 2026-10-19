import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { assertSafeRelPath, isNativeBinary, renderWorkbenchTemplate } from '../build/build.mjs';
import { OVS, SERVER_ROOT, WEBVIEW_PRE } from '../build/pins.mjs';
import {
  cspHashOf, inlineScriptOf, patchWebviewIndex, patchWebviewServiceWorker, stripVscodeCdnSourceMap,
} from '../build/webview-patch.mjs';
import { isCompressible, mimeFor } from '../src/mime.mjs';
import { EDGE } from './fixture.mjs';

// The extracted upstream tree from build.sh's cache, if a build has run on this machine.
function upstreamRoot() {
  const cache = join(EDGE, '.cache');
  if (!existsSync(cache)) return null;
  for (const dir of readdirSync(cache)) {
    if (!dir.startsWith('ovs-')) continue;
    for (const inner of readdirSync(join(cache, dir))) {
      if (existsSync(join(cache, dir, inner, 'product.json'))) return join(cache, dir, inner);
    }
  }
  return null;
}
const upstream = upstreamRoot();
const needsUpstream = { skip: upstream ? false : 'run build/build.sh first (needs the extracted tarball in .cache/)' };

describe('webview shell patch (on the real 1.109.5 files)', () => {
  test('replaces the hostname-hash check with a strict parent-origin check and re-pins the CSP hash', needsUpstream, () => {
    const original = readFileSync(join(upstream, WEBVIEW_PRE, 'index.html'), 'utf8');
    const patched = patchWebviewIndex(original);

    const script = inlineScriptOf(patched);
    const meta = /script-src ('sha256-[^']+') 'self'/.exec(patched)[1];
    assert.equal(meta, cspHashOf(script), 'the meta CSP pins the patched inline script');
    assert.notEqual(meta, cspHashOf(inlineScriptOf(original)));

    assert.ok(patched.includes('<meta name="devbox-workbench-origin" content="{{DEVBOX_WORKBENCH_ORIGIN}}">'));
    assert.ok(script.includes("if (!devboxWorkbenchOrigin || parentOrigin !== devboxWorkbenchOrigin || location.origin === devboxWorkbenchOrigin) {"));
    assert.ok(!script.includes('as hostname or subdomain'), 'the upstream check is gone');
    assert.ok(!script.includes('const hostname = location.hostname'));
    // Everything else is untouched.
    const withoutPatch = patched
      .replace(/\t<meta name="devbox-workbench-origin"[^\n]*\n/, '')
      .replace(/'sha256-[^']+'/, "'sha256-TaWGDzV7c9rUH2q/5ygOyYUHSyHIqBMYfucPh3lnKvU='");
    const before = original.slice(0, original.indexOf('\t\t\t\tconst hostname = location.hostname;'));
    assert.ok(withoutPatch.startsWith(before));
    const endOfCheck = "throw new Error(`Expected '${parentOriginHash}' as hostname or subdomain!`);\n";
    const tail = original.slice(original.indexOf(endOfCheck) + endOfCheck.length);
    assert.ok(withoutPatch.endsWith(tail));
  });

  test('refuses an upstream file that has changed', needsUpstream, () => {
    const original = readFileSync(join(upstream, WEBVIEW_PRE, 'index.html'), 'utf8');
    assert.throws(() => patchWebviewIndex(original.replace('perfMark(\'signalingReady\')', 'perfMark("x")')), /upstream inline script changed/);
    assert.throws(() => patchWebviewIndex('<html></html>'), /exactly one inline module script/);
  });

  test('the service worker keeps the routing guards a shared host relies on', needsUpstream, () => {
    const original = readFileSync(join(upstream, WEBVIEW_PRE, 'service-worker.js'), 'utf8');
    const patched = patchWebviewServiceWorker(original);
    assert.ok(!patched.includes('vscode-cdn.net/sourcemaps'));
    assert.equal(patched.trimEnd(), original.slice(0, original.indexOf('\n\n//# sourceMappingURL')).trimEnd());
    assert.throws(() => patchWebviewServiceWorker(original.replace('t.origin!==sw.origin&&', '')), /no longer contains/);
  });

  test('the pinned upstream files are the ones in the tarball', needsUpstream, async () => {
    const { createHash } = await import('node:crypto');
    for (const [rel, sha] of Object.entries(OVS.upstream)) {
      assert.equal(createHash('sha256').update(readFileSync(join(upstream, rel))).digest('hex'), sha, rel);
    }
    const product = JSON.parse(readFileSync(join(upstream, 'product.json'), 'utf8'));
    assert.equal(`/${product.quality}-${product.commit}`, SERVER_ROOT);
  });
});

describe('workbench page template', () => {
  const source = readFileSync(join(EDGE, 'web', 'index.html'), 'utf8');

  test('our template renders', () => {
    const html = renderWorkbenchTemplate(source);
    assert.ok(html.includes(`href="${SERVER_ROOT}/static/resources/server/favicon.ico"`));
    assert.ok(!html.includes('{{WORKBENCH_WEB_BASE_URL}}'));
  });

  test('it is derived from the pinned upstream template', needsUpstream, () => {
    const upstreamHtml = readFileSync(join(upstream, 'out/vs/code/browser/workbench/workbench.html'), 'utf8');
    for (const kept of ['apple-mobile-web-app-title', 'user-scalable=no', '/resources/server/manifest.json', 'crossorigin="use-credentials"']) {
      assert.ok(upstreamHtml.includes(kept) && source.includes(kept), kept);
    }
  });

  test('rejects module scripts, a late shim or a missing nonce', () => {
    const shim = '<script nonce="{{CSP_NONCE}}" src="/devbox/shim.js"></script>';
    const oidc = '<script nonce="{{CSP_NONCE}}" src="/devbox/oidc.js"></script>';
    const loader = '<script nonce="{{CSP_NONCE}}" src="/devbox/loader.js"></script>';
    assert.throws(() => renderWorkbenchTemplate(`${oidc}${shim}${loader}`), /in that order/);
    assert.throws(() => renderWorkbenchTemplate(`${shim}${oidc}${loader}<script type="module" src="/x.js"></script>`), /in that order/);
    assert.throws(() => renderWorkbenchTemplate(`${shim}${oidc}<script src="/devbox/loader.js"></script>`), /nonce/);
    assert.throws(() => renderWorkbenchTemplate(`${shim}${oidc}${loader}<script>alert(1)</script>`), /in that order/);
    assert.doesNotThrow(() => renderWorkbenchTemplate(`<!-- <script src="/x.js"></script> -->${shim}${oidc}${loader}`));
  });
});

describe('build helpers', () => {
  test('strips only the Microsoft CDN source-map comment', () => {
    assert.equal(stripVscodeCdnSourceMap('a();\n//# sourceMappingURL=https://main.vscode-cdn.net/sourcemaps/x/a.js.map\n'), 'a();\n');
    assert.equal(stripVscodeCdnSourceMap('.a{}\n\n/*# sourceMappingURL=https://main.vscode-cdn.net/sourcemaps/x/a.css.map */'), '.a{}\n');
    assert.equal(stripVscodeCdnSourceMap('a();\n//# sourceMappingURL=a.js.map\n'), 'a();\n//# sourceMappingURL=a.js.map\n');
  });

  test('recognises native binaries by name and by magic', () => {
    const elf = Buffer.from([0x7f, 0x45, 0x4c, 0x46]);
    const macho = Buffer.from([0xcf, 0xfa, 0xed, 0xfe]);
    assert.equal(isNativeBinary('a/b.node', Buffer.from('text')), true);
    assert.equal(isNativeBinary('lib/libfoo.so.1', Buffer.from('text')), true);
    assert.equal(isNativeBinary('bin/rg', elf), true);
    assert.equal(isNativeBinary('bin/tool', macho), true);
    assert.equal(isNativeBinary('a/b.js', Buffer.from('cons')), false);
    assert.equal(isNativeBinary('a/b.wasm', Buffer.from([0, 0x61, 0x73, 0x6d])), false);
  });

  test('refuses ambiguous paths from the tarball', () => {
    for (const bad of ['', '/abs', 'a/../b', 'a//b', 'a/./b', 'a\\b', 'a%2fb', 'a\0b']) {
      assert.throws(() => assertSafeRelPath(bad), /unusual file path/, JSON.stringify(bad));
    }
    assert.doesNotThrow(() => assertSafeRelPath('extensions/javascript/syntaxes/Regular Expressions (JavaScript).tmLanguage'));
  });

  test('content types and what gets compressed', () => {
    assert.equal(mimeFor('a/onig.wasm'), 'application/wasm');
    assert.equal(mimeFor('a/workbench.js'), 'text/javascript; charset=utf-8');
    assert.equal(mimeFor('a/x.MJS'), 'text/javascript; charset=utf-8');
    assert.equal(mimeFor('a/LICENSE'), 'text/plain; charset=utf-8');
    assert.equal(mimeFor('a/noext'), 'application/octet-stream');
    assert.equal(mimeFor('a/.npmrc'), 'application/octet-stream');
    assert.equal(isCompressible('text/javascript; charset=utf-8'), true);
    assert.equal(isCompressible('application/wasm'), true);
    assert.equal(isCompressible('font/woff2'), false);
    assert.equal(isCompressible('image/png'), false);
  });
});
