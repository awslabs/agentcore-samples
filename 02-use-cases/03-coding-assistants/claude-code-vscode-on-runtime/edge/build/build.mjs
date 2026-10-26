// Builds edge/dist/ from the pinned openvscode-server tarball and edge/web/.
//
//   dist/manifest.json   every servable file: size, content type, sha256, pre-compressed variants
//   dist/static/...      the browser part of the install, served under SERVER_ROOT/static/
//   dist/webview/...     the patched webview shell, served by the webview site only
//   dist/web/...         our workbench page template, loader scripts and Service Worker
//
// Usage: node build/build.mjs [--tarball <file>] [--arch arm64|x64] [--out <dir>] [--cache <dir>]
// (build.sh is the normal entry point; it sets the thread pool size for parallel compression.)

import { createHash } from 'node:crypto';
import { createWriteStream, existsSync } from 'node:fs';
import { mkdir, open, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { availableParallelism } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import zlib from 'node:zlib';

import { OVS, SERVER_ROOT, STATIC_ROOTS, WEB_NODE_MODULES, WEBVIEW_PRE, XTERM } from './pins.mjs';
import { patchWebviewIndex, patchWebviewServiceWorker, stripVscodeCdnSourceMap } from './webview-patch.mjs';
import { isCompressible, mimeFor } from '../src/mime.mjs';
import { MAX_RAW_BODY_BYTES } from '../src/limits.mjs';

const EDGE = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const brotli = promisify(zlib.brotliCompress);
const gzip = promisify(zlib.gzip);

// Only keep a compressed variant when it is worth a separate file.
const MIN_COMPRESS_BYTES = 1024;
const MIN_SAVING = 0.9;

export const NATIVE_EXTENSIONS = ['.node', '.so', '.dylib', '.dll', '.exe', '.pdb'];

export function sha256(buf) {
  return createHash('sha256').update(buf).digest('hex');
}

export function isNativeBinary(path, head) {
  const lower = path.toLowerCase();
  if (NATIVE_EXTENSIONS.some(ext => lower.endsWith(ext)) || /\.so\.\d/.test(lower)) return true;
  if (head.length < 4) return false;
  const magic = head.readUInt32BE(0);
  // ELF, and the Mach-O / fat-binary magics in both byte orders.
  return magic === 0x7f454c46
    || [0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca].includes(magic);
}

// Manifest keys are looked up with decoded URL paths, so keep them unambiguous.
export function assertSafeRelPath(rel) {
  if (!rel || rel.startsWith('/') || rel.includes('\\') || rel.includes('\0') || rel.includes('%')
      || rel.split('/').some(seg => seg === '' || seg === '.' || seg === '..')) {
    throw new Error(`refusing unusual file path in the tarball: ${JSON.stringify(rel)}`);
  }
}

async function walk(root, rel, out) {
  const dir = join(root, rel);
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const childRel = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) throw new Error(`symlink in the static tree: ${childRel}`);
    if (entry.isDirectory()) {
      if (childRel === WEBVIEW_PRE) continue;
      await walk(root, childRel, out);
    } else if (entry.isFile()) {
      out.push(childRel);
    }
  }
}

export async function listStaticFiles(ovsRoot) {
  const files = [];
  const missing = [];
  for (const top of STATIC_ROOTS) await walk(ovsRoot, top, files);
  for (const pkg of WEB_NODE_MODULES) {
    const rel = `node_modules/${pkg}`;
    if (existsSync(join(ovsRoot, rel))) await walk(ovsRoot, rel, files);
    else missing.push(pkg);
  }
  return { files: files.sort(), missing };
}

async function readHead(path) {
  const fh = await open(path, 'r');
  try {
    const buf = Buffer.alloc(4);
    const { bytesRead } = await fh.read(buf, 0, 4, 0);
    return buf.subarray(0, bytesRead);
  } finally {
    await fh.close();
  }
}

async function runPool(items, limit, fn) {
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      await fn(items[i], i);
    }
  });
  await Promise.all(workers);
}

async function compressCached(buf, digest, mime, zdir) {
  const brPath = join(zdir, `${digest}.br`);
  const gzPath = join(zdir, `${digest}.gz`);
  const [br, gz] = await Promise.all([
    existsSync(brPath) ? readFile(brPath) : brotli(buf, {
      params: {
        [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
        [zlib.constants.BROTLI_PARAM_LGWIN]: 24,
        [zlib.constants.BROTLI_PARAM_SIZE_HINT]: buf.length,
        [zlib.constants.BROTLI_PARAM_MODE]: mime.startsWith('font/') || mime === 'application/wasm'
          ? zlib.constants.BROTLI_MODE_GENERIC : zlib.constants.BROTLI_MODE_TEXT,
      },
    }).then(async out => { await writeFile(brPath, out); return out; }),
    existsSync(gzPath) ? readFile(gzPath) : gzip(buf, { level: 9 })
      .then(async out => { await writeFile(gzPath, out); return out; }),
  ]);
  return { br, gzip: gz };
}

// Writes one servable file (and its sidecars) and returns its manifest entry.
export async function emit(outDir, rel, buf, { compress, zdir, template = false }) {
  const mime = mimeFor(rel);
  const entry = { size: buf.length, mime, sha256: sha256(buf) };
  if (template) entry.template = true;
  const dest = join(outDir, ...rel.split('/'));
  await mkdir(dirname(dest), { recursive: true });
  await writeFile(dest, buf);
  if (compress && !template && isCompressible(mime) && buf.length >= MIN_COMPRESS_BYTES) {
    const variants = await compressCached(buf, entry.sha256, mime, zdir);
    for (const [name, data] of Object.entries(variants)) {
      if (data.length > buf.length * MIN_SAVING) continue;
      await writeFile(`${dest}.${name === 'gzip' ? 'gz' : 'br'}`, data);
      entry.variants ??= {};
      entry.variants[name] = { size: data.length, sha256: sha256(data) };
    }
  }
  return entry;
}

async function checkUpstream(ovsRoot) {
  const product = JSON.parse(await readFile(join(ovsRoot, 'product.json'), 'utf8'));
  if (product.commit !== OVS.commit || product.quality !== OVS.quality || product.version !== OVS.version) {
    throw new Error(`product.json is ${product.quality}-${product.commit} ${product.version}, expected ${SERVER_ROOT} ${OVS.version}`);
  }
  for (const [rel, expected] of Object.entries(OVS.upstream)) {
    const actual = sha256(await readFile(join(ovsRoot, rel)));
    if (actual !== expected) {
      throw new Error(`${rel} changed upstream (sha256 ${actual}); re-derive our copy before building`);
    }
  }
  const xterm = JSON.parse(await readFile(join(ovsRoot, 'node_modules/@xterm/xterm/package.json'), 'utf8'));
  if (xterm.version !== XTERM.version) {
    throw new Error(`@xterm/xterm is ${xterm.version}, but web/devbox/terminal.js was checked against ${XTERM.version}; re-check the terminal and update XTERM in build/pins.mjs`);
  }
  for (const rel of [XTERM.script, XTERM.css]) {
    if (!existsSync(join(ovsRoot, rel))) throw new Error(`${rel} is missing; the /terminal page loads it`);
  }
}

export function renderWorkbenchTemplate(source) {
  const html = source.split('{{WORKBENCH_WEB_BASE_URL}}').join(`${SERVER_ROOT}/static`);
  const scripts = [...html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<script\b([^>]*)>/g)].map(m => m[1]);
  const expected = ['/devbox/shim.js', '/devbox/oidc.js', '/devbox/loader.js'];
  const srcs = scripts.map(attrs => /\bsrc="([^"]+)"/.exec(attrs)?.[1] ?? '(inline)');
  if (srcs.join(' ') !== expected.join(' ')) {
    throw new Error(`web/index.html must load exactly ${expected.join(', ')} in that order (found ${srcs.join(', ')})`);
  }
  if (scripts.some(attrs => !attrs.includes('nonce="{{CSP_NONCE}}"') || /type=/.test(attrs))) {
    throw new Error('web/index.html scripts must be classic scripts carrying nonce="{{CSP_NONCE}}"');
  }
  return html;
}

export async function buildDist({ ovsRoot, webDir = join(EDGE, 'web'), outDir, zdir, log = console.log }) {
  const started = Date.now();
  await checkUpstream(ovsRoot);

  // The quick checks first (webview patch, page template), so a mistake fails before compression.
  const pre = join(ovsRoot, ...WEBVIEW_PRE.split('/'));
  const webview = {
    'index.html': [Buffer.from(patchWebviewIndex(await readFile(join(pre, 'index.html'), 'utf8'))), true],
    'service-worker.js': [Buffer.from(patchWebviewServiceWorker(await readFile(join(pre, 'service-worker.js'), 'utf8'))), false],
    'fake.html': [await readFile(join(pre, 'fake.html')), false],
  };
  const web = {
    'index.html': [Buffer.from(renderWorkbenchTemplate(await readFile(join(webDir, 'index.html'), 'utf8'))), true],
    'sw.js': [await readFile(join(webDir, 'sw.js')), false],
  };
  for (const name of (await readdir(join(webDir, 'devbox'))).sort()) {
    if (name.endsWith('.js')) web[`devbox/${name}`] = [await readFile(join(webDir, 'devbox', name)), false];
  }

  const manifest = {
    version: 1,
    ovs: { version: OVS.version, quality: OVS.quality, commit: OVS.commit },
    serverRoot: SERVER_ROOT,
    sections: { static: {}, webview: {}, web: {} },
  };
  const skipped = [];
  const tmp = `${outDir}.tmp-${process.pid}`;
  await rm(tmp, { recursive: true, force: true });
  await mkdir(zdir, { recursive: true });
  try {
    const { files, missing } = await listStaticFiles(ovsRoot);
    if (missing.length) log(`note: not in this build (bundled into workbench.js instead): ${missing.join(', ')}`);
    await runPool(files, Math.max(2, availableParallelism()), async rel => {
      assertSafeRelPath(rel);
      const abs = join(ovsRoot, ...rel.split('/'));
      if (isNativeBinary(rel, await readHead(abs))) { skipped.push(rel); return; }
      let buf = await readFile(abs);
      if (/\.(m?js|css)$/.test(rel) && buf.includes('sourceMappingURL=https://main.vscode-cdn.net/')) {
        buf = Buffer.from(stripVscodeCdnSourceMap(buf.toString('utf8')), 'utf8');
      }
      manifest.sections.static[rel] = await emit(join(tmp, 'static'), rel, buf, { compress: true, zdir });
    });
    for (const [section, files] of [['webview', webview], ['web', web]]) {
      for (const [name, [buf, template]] of Object.entries(files)) {
        manifest.sections[section][name] = await emit(join(tmp, section), name, buf, { compress: true, zdir, template });
      }
    }

    // Every file must be servable within the BUFFERED response limit by at least one encoding browsers
    // always accept (gzip); big ones are then only ever sent compressed.
    for (const [section, entries] of Object.entries(manifest.sections)) {
      for (const [rel, entry] of Object.entries(entries)) {
        if (entry.size <= MAX_RAW_BODY_BYTES) continue;
        if (!(entry.variants?.gzip?.size <= MAX_RAW_BODY_BYTES)) {
          throw new Error(`${section}/${rel} (${entry.size} bytes) has no variant under the Lambda response limit`);
        }
      }
    }

    for (const [section, entries] of Object.entries(manifest.sections)) {
      manifest.sections[section] = Object.fromEntries(Object.keys(entries).sort().map(k => [k, entries[k]]));
    }
    await writeFile(join(tmp, 'manifest.json'), `${JSON.stringify(manifest, null, 1)}\n`);
    await rm(outDir, { recursive: true, force: true });
    await rename(tmp, outDir);
  } catch (err) {
    await rm(tmp, { recursive: true, force: true });
    throw err;
  }

  const stats = summarize(manifest);
  log(`static: ${stats.static.files} files, ${mb(stats.static.bytes)} (br ${mb(stats.static.br)} for the ${stats.static.brFiles} compressible ones)`);
  log(`skipped native binaries: ${skipped.length ? skipped.sort().join(', ') : 'none'}`);
  const wb = manifest.sections.static['out/vs/code/browser/workbench/workbench.js'];
  log(`workbench.js: ${wb.size} raw, br ${wb.variants?.br?.size}, gzip ${wb.variants?.gzip?.size}`);
  log(`webview: ${Object.keys(manifest.sections.webview).join(', ')}; web: ${Object.keys(manifest.sections.web).join(', ')}`);
  log(`dist written to ${outDir} in ${((Date.now() - started) / 1000).toFixed(1)} s`);
  return manifest;
}

function summarize(manifest) {
  const out = {};
  for (const [section, entries] of Object.entries(manifest.sections)) {
    const s = { files: 0, bytes: 0, br: 0, brFiles: 0 };
    for (const e of Object.values(entries)) {
      s.files++; s.bytes += e.size;
      if (e.variants?.br) { s.br += e.variants.br.size; s.brFiles++; }
    }
    out[section] = s;
  }
  return out;
}

const mb = n => `${(n / 1e6).toFixed(1)} MB`;

async function fileSha256(path) {
  const hash = createHash('sha256');
  const fh = await open(path, 'r');
  try {
    for await (const chunk of fh.createReadStream()) hash.update(chunk);
  } finally {
    await fh.close();
  }
  return hash.digest('hex');
}

async function download(url, dest) {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok || !res.body) throw new Error(`download failed: ${res.status} ${url}`);
  const part = `${dest}.part`;
  await pipeline(Readable.fromWeb(res.body), createWriteStream(part));
  await rename(part, dest);
}

export async function fetchVerified({ arch, tarball, cacheDir, log = console.log }) {
  const pin = OVS.tarballs[arch];
  if (!pin) throw new Error(`unknown arch ${arch}`);
  let path = tarball;
  if (!path) {
    await mkdir(cacheDir, { recursive: true });
    path = join(cacheDir, basename(new URL(pin.url).pathname));
    if (!existsSync(path)) {
      log(`downloading ${pin.url}`);
      await download(pin.url, path);
    }
  }
  const actual = await fileSha256(path);
  if (actual !== pin.sha256) {
    throw new Error(`sha256 mismatch for ${path}: got ${actual}, pinned ${pin.sha256}`);
  }
  log(`verified ${basename(path)} sha256 ${actual}`);
  return path;
}

export async function extract(tarball, cacheDir, digest) {
  const target = join(cacheDir, `ovs-${digest.slice(0, 16)}`);
  const marker = join(target, '.extracted');
  if (!existsSync(marker)) {
    const tmp = `${target}.tmp-${process.pid}`;
    await rm(tmp, { recursive: true, force: true });
    await mkdir(tmp, { recursive: true });
    const res = spawnSync('tar', ['-xzf', tarball, '-C', tmp], { stdio: 'inherit' });
    if (res.status !== 0) throw new Error(`tar failed with status ${res.status}`);
    await rm(target, { recursive: true, force: true });
    await rename(tmp, target);
    await writeFile(marker, `${digest}\n`);
  }
  const dirs = (await readdir(target, { withFileTypes: true })).filter(d => d.isDirectory());
  if (dirs.length !== 1) throw new Error(`expected one top-level directory in the tarball, found ${dirs.length}`);
  return join(target, dirs[0].name);
}

function parseArgs(argv) {
  const args = { arch: process.env.DEVBOX_OVS_ARCH || 'arm64', tarball: process.env.DEVBOX_OVS_TARBALL || '' };
  for (let i = 0; i < argv.length; i++) {
    const [key, value] = [argv[i], argv[i + 1]];
    if (!['--tarball', '--arch', '--out', '--cache'].includes(key) || value === undefined) {
      throw new Error(`usage: build.mjs [--tarball file] [--arch arm64|x64] [--out dir] [--cache dir] (bad: ${key})`);
    }
    args[key.slice(2)] = value;
    i++;
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const cacheDir = resolve(args.cache ?? join(EDGE, '.cache'));
  const outDir = resolve(args.out ?? join(EDGE, 'dist'));
  const tarball = await fetchVerified({ arch: args.arch, tarball: args.tarball ? resolve(args.tarball) : '', cacheDir });
  const ovsRoot = await extract(tarball, cacheDir, OVS.tarballs[args.arch].sha256);
  await buildDist({ ovsRoot, outDir, zdir: join(cacheDir, 'z-v1') });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(err => {
    console.error(`build failed: ${err.message}`);
    process.exit(1);
  });
}

