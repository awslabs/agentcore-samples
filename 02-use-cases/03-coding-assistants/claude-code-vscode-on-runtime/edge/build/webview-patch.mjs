// Patches the VS Code webview shell (out/vs/workbench/contrib/webview/browser/pre/) so it runs from
// one fixed host (the webview CloudFront distribution, or localhost:9403 in the local test) instead of
// the per-webview '<hash>.vscode-cdn.net' hosts upstream expects.
//
// Every edit is anchored on exact upstream text and fails the build if the anchor is missing, so a
// VS Code bump can't silently ship an unpatched (broken) or half-patched shell.

import { createHash } from 'node:crypto';

export const WORKBENCH_ORIGIN_PLACEHOLDER = '{{DEVBOX_WORKBENCH_ORIGIN}}';

const SCRIPT_OPEN = '<script async type="module">';
const SCRIPT_CLOSE = '</script>';
const UPSTREAM_SCRIPT_HASH = "'sha256-TaWGDzV7c9rUH2q/5ygOyYUHSyHIqBMYfucPh3lnKvU='";

const META_ANCHOR = '\t<meta charset="UTF-8">\n';
const META_TAG = `\t<meta name="devbox-workbench-origin" content="${WORKBENCH_ORIGIN_PLACEHOLDER}">\n`;

// signalReady(): from the hostname lookup to the final throw is the upstream hash check.
const CHECK_START = '\t\t\t\tconst hostname = location.hostname;\n';
const CHECK_END = "\t\t\t\tthrow new Error(`Expected '${parentOriginHash}' as hostname or subdomain!`);\n";
const CHECK_REPLACEMENT = [
  '\t\t\t\t// devbox: every webview is served from one fixed host, so the upstream check that this',
  "\t\t\t\t// hostname is '<sha256(parentOrigin, webview id)>.' can never pass. Accept exactly one parent:",
  '\t\t\t\t// the workbench origin the edge writes into the devbox-workbench-origin meta tag. Never run on',
  '\t\t\t\t// the workbench origin itself.',
  "\t\t\t\tconst devboxWorkbenchOrigin = document.querySelector('meta[name=\"devbox-workbench-origin\"]')?.getAttribute('content') ?? '';",
  '\t\t\t\tif (!devboxWorkbenchOrigin || parentOrigin !== devboxWorkbenchOrigin || location.origin === devboxWorkbenchOrigin) {',
  "\t\t\t\t\tthrow new Error(`Webview parent '${parentOrigin}' is not the dev box workbench.`);",
  '\t\t\t\t}',
  '\t\t\t\treturn start(parentOrigin);',
  '',
].join('\n');

const SOURCE_MAP_TAIL = /\n*(?:\/\/# sourceMappingURL=https:\/\/main\.vscode-cdn\.net\/[^\n]*|\/\*# sourceMappingURL=https:\/\/main\.vscode-cdn\.net\/[^\n]*?\*\/)\s*$/;

// Removes the trailing source-map pointer to Microsoft's CDN (only fetched when devtools are open, but
// the demo promises no Microsoft endpoints).
export function stripVscodeCdnSourceMap(text) {
  return text.replace(SOURCE_MAP_TAIL, '\n');
}

function replaceOnce(text, anchor, replacement, what) {
  const at = text.indexOf(anchor);
  if (at < 0 || text.indexOf(anchor, at + anchor.length) >= 0) {
    throw new Error(`webview patch: anchor for ${what} not found exactly once`);
  }
  return text.slice(0, at) + replacement + text.slice(at + anchor.length);
}

export function inlineScriptOf(html) {
  const open = html.indexOf(SCRIPT_OPEN);
  const close = html.indexOf(SCRIPT_CLOSE, open);
  if (open < 0 || close < 0 || html.indexOf(SCRIPT_OPEN, open + 1) >= 0) {
    throw new Error('webview patch: expected exactly one inline module script');
  }
  return html.slice(open + SCRIPT_OPEN.length, close);
}

export function cspHashOf(script) {
  return `'sha256-${createHash('sha256').update(script, 'utf8').digest('base64')}'`;
}

export function patchWebviewIndex(html) {
  if (cspHashOf(inlineScriptOf(html)) !== UPSTREAM_SCRIPT_HASH) {
    throw new Error('webview patch: upstream inline script changed; re-check the patch');
  }
  let out = replaceOnce(html, META_ANCHOR, META_ANCHOR + META_TAG, 'the meta tag');

  const start = out.indexOf(CHECK_START);
  const end = out.indexOf(CHECK_END, start);
  if (start < 0 || end < 0 || out.indexOf(CHECK_START, start + 1) >= 0) {
    throw new Error('webview patch: anchor for the hostname check not found exactly once');
  }
  out = out.slice(0, start) + CHECK_REPLACEMENT + out.slice(end + CHECK_END.length);

  const hash = cspHashOf(inlineScriptOf(out));
  out = replaceOnce(out, `script-src ${UPSTREAM_SCRIPT_HASH} 'self'`, `script-src ${hash} 'self'`, 'the CSP hash');
  return out;
}

// The webview service worker needs no functional change: it routes every request by the webview id in
// the requesting client's URL, and it already ignores requests to its own origin (the code-server
// same-host patch was upstreamed). These guards are what make one shared host work, so the build checks
// they are still there before shipping the file.
const SW_REQUIRED = [
  'sw.addEventListener("fetch"',
  't.origin!==sw.origin&&t.host===remoteAuthority',
  'function getWebviewIdForClient(e){return new URL(e.url).searchParams.get("id")}',
  'r.searchParams.get("id")===e',
];

export function patchWebviewServiceWorker(js) {
  for (const needle of SW_REQUIRED) {
    if (!js.includes(needle)) throw new Error(`webview patch: service-worker.js no longer contains ${needle}`);
  }
  return stripVscodeCdnSourceMap(js);
}
