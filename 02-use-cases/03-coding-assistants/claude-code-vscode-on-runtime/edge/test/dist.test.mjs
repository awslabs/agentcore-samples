// Checks the real dist/ produced by build/build.sh (skipped until a build has run).

import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import zlib from 'node:zlib';

import { SERVER_ROOT, WEBVIEW_PRE, XTERM } from '../build/pins.mjs';
import { renderWorkbenchTemplate } from '../build/build.mjs';
import { cspHashOf, inlineScriptOf } from '../build/webview-patch.mjs';
import { createHandler } from '../src/handler.mjs';
import { MAX_RAW_BODY_BYTES } from '../src/limits.mjs';
import { EDGE, bodyBytes, envFor, event } from './fixture.mjs';

const DIST = join(EDGE, 'dist');
const built = existsSync(join(DIST, 'manifest.json'));
const opts = { skip: built ? false : 'run build/build.sh first' };
const manifest = built ? JSON.parse(readFileSync(join(DIST, 'manifest.json'), 'utf8')) : null;
const sha = buf => createHash('sha256').update(buf).digest('hex');

describe('dist/', () => {
  test('is for the pinned server root', opts, () => {
    assert.equal(manifest.serverRoot, SERVER_ROOT);
    assert.equal(manifest.ovs.version, '1.109.5');
  });

  test('every file matches its manifest entry, sidecars included', opts, () => {
    for (const [section, entries] of Object.entries(manifest.sections)) {
      for (const [rel, entry] of Object.entries(entries)) {
        const path = join(DIST, section, ...rel.split('/'));
        const buf = readFileSync(path);
        assert.equal(buf.length, entry.size, `${section}/${rel}`);
        assert.equal(sha(buf), entry.sha256, `${section}/${rel}`);
        for (const [name, variant] of Object.entries(entry.variants ?? {})) {
          const side = readFileSync(`${path}.${name === 'gzip' ? 'gz' : 'br'}`);
          assert.equal(side.length, variant.size, `${section}/${rel} ${name}`);
          assert.equal(sha(side), variant.sha256, `${section}/${rel} ${name}`);
        }
      }
    }
  });

  test('is up to date with web/ (rebuild after editing the page or scripts)', opts, () => {
    for (const [rel, entry] of Object.entries(manifest.sections.web)) {
      const source = readFileSync(join(EDGE, 'web', ...rel.split('/')));
      const expected = rel === 'index.html' ? Buffer.from(renderWorkbenchTemplate(source.toString('utf8'))) : source;
      assert.equal(sha(expected), entry.sha256, `dist/web/${rel} is stale: run build/build.sh`);
    }
  });

  test('has what the browser needs and nothing it must not have', opts, () => {
    const files = Object.keys(manifest.sections.static);
    for (const need of [
      'out/vs/code/browser/workbench/workbench.js',
      'out/vs/code/browser/workbench/workbench.css',
      'out/nls.messages.js',
      'out/vs/workbench/services/extensions/worker/webWorkerExtensionHostIframe.html',
      'out/vs/workbench/api/worker/extensionHostWorkerMain.js',
      'out/vs/editor/common/services/editorWebWorkerMain.js',
      'resources/server/manifest.json',
      'resources/server/favicon.ico',
      'node_modules/vscode-oniguruma/release/onig.wasm',
      XTERM.script,
      XTERM.css,
      'node_modules/@vscode/tree-sitter-wasm/wasm/tree-sitter.js',
      'extensions/theme-defaults/package.json',
    ]) {
      assert.ok(files.includes(need), need);
    }
    assert.ok(!files.some(f => f.endsWith('.node') || /\.so(\.|$)/.test(f)), 'no native binaries');
    assert.ok(!files.some(f => f.startsWith(`${WEBVIEW_PRE}/`)), 'the webview shell is not on the workbench site');
    assert.ok(!files.some(f => f.startsWith('node_modules/node-pty/') || f.startsWith('node_modules/@vscode/ripgrep/')), 'no server-only modules');
    assert.ok(!files.includes('node'), 'no node binary');
    // The workbench probes for Microsoft's vsda signing module on every connect. openvscode-server doesn't
    // ship it, so both sides skip signing; the 404s are expected (README, Known limits). Don't stub it.
    assert.ok(!files.some(f => f.startsWith('node_modules/vsda/')), 'no vsda');
    assert.equal(manifest.sections.static['node_modules/vscode-oniguruma/release/onig.wasm'].mime, 'application/wasm');
  });

  test('the terminal page has its scripts and the xterm.js it was checked against', opts, () => {
    assert.ok(manifest.sections.web['devbox/terminal.js'], 'devbox/terminal.js');
    assert.equal(manifest.sections.static[XTERM.script].mime, 'text/javascript; charset=utf-8');
    assert.equal(manifest.sections.static[XTERM.css].mime, 'text/css; charset=utf-8');
    const pkg = JSON.parse(readFileSync(join(DIST, 'static', 'node_modules', '@xterm', 'xterm', 'package.json'), 'utf8'));
    assert.equal(pkg.version, XTERM.version);
    // A classic script that defines window.Terminal without an AMD loader, and nothing CSP would refuse.
    const js = readFileSync(join(DIST, 'static', ...XTERM.script.split('/')), 'utf8');
    assert.match(js.slice(0, 300), /^!function\(e,t\)\{if\("object"==typeof exports/);
    for (const needle of ['eval(', 'new Function', 'new Worker', 'createObjectURL', 'importScripts(']) {
      assert.ok(!js.includes(needle), `xterm.js has no ${needle}`);
    }
    const css = readFileSync(join(DIST, 'static', ...XTERM.css.split('/')), 'utf8');
    assert.ok(!/url\(/.test(css), 'xterm.css loads nothing');
  });

  test('no file mentions the Microsoft source-map CDN any more', opts, () => {
    for (const [rel, entry] of Object.entries(manifest.sections.static)) {
      if (!/\.(m?js|css)$/.test(rel) || entry.size > 20_000_000) continue;
      const text = readFileSync(join(DIST, 'static', ...rel.split('/')), 'utf8');
      assert.ok(!text.includes('sourceMappingURL=https://main.vscode-cdn.net'), rel);
    }
  });

  test('every big file has a variant that fits a BUFFERED response', opts, () => {
    for (const [rel, entry] of Object.entries(manifest.sections.static)) {
      if (entry.size <= MAX_RAW_BODY_BYTES) continue;
      assert.ok(entry.variants.br.size <= MAX_RAW_BODY_BYTES && entry.variants.gzip.size <= MAX_RAW_BODY_BYTES, rel);
    }
    const wb = manifest.sections.static['out/vs/code/browser/workbench/workbench.js'];
    assert.ok(wb.variants.br.size < 3_000_000, `workbench.js br is ${wb.variants.br.size}`);
  });

  test('the patched webview shell pins its own inline script', opts, () => {
    const html = readFileSync(join(DIST, 'webview', 'index.html'), 'utf8');
    assert.equal(/script-src ('sha256-[^']+') 'self'/.exec(html)[1], cspHashOf(inlineScriptOf(html)));
    assert.ok(html.includes('{{DEVBOX_WORKBENCH_ORIGIN}}'));
    assert.equal(manifest.sections.webview['index.html'].template, true);
  });

  test('the real workbench.js goes through the handler within Lambda limits', opts, () => {
    const handler = createHandler({ env: envFor(), distDir: DIST, log: console });
    return (async () => {
      const res = await handler(event(`${SERVER_ROOT}/static/out/vs/code/browser/workbench/workbench.js`, { headers: { 'accept-encoding': 'gzip, deflate, br' } }));
      assert.equal(res.statusCode, 200);
      assert.equal(res.headers['content-encoding'], 'br');
      assert.ok(JSON.stringify(res).length < 6 * 1024 * 1024, 'fits the 6 MiB BUFFERED limit');
      const js = zlib.brotliDecompressSync(bodyBytes(res));
      assert.equal(sha(js), manifest.sections.static['out/vs/code/browser/workbench/workbench.js'].sha256);
      assert.match(js.toString('utf8', js.length - 2000), /export\{[\w$]+ as LocalStorageSecretStorageProvider\}/, 'it is the ES module build');
      const gz = await handler(event(`${SERVER_ROOT}/static/out/vs/code/browser/workbench/workbench.js`, { headers: { 'accept-encoding': 'gzip' } }));
      assert.ok(JSON.stringify(gz).length < 6 * 1024 * 1024);
      const none = await handler(event(`${SERVER_ROOT}/static/out/vs/code/browser/workbench/workbench.js`));
      assert.equal(none.statusCode, 406);
    })();
  });

  test('dist stays well inside the Lambda image limit', opts, () => {
    let bytes = 0;
    for (const entries of Object.values(manifest.sections)) {
      for (const e of Object.values(entries)) bytes += e.size + (e.variants?.br?.size ?? 0) + (e.variants?.gzip?.size ?? 0);
    }
    assert.ok(bytes < 500_000_000, `${bytes} bytes`);
    assert.ok(statSync(join(DIST, 'manifest.json')).size < 2_000_000);
  });
});
