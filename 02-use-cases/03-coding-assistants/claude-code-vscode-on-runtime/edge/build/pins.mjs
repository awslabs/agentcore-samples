// Everything the edge build takes from outside this repo, pinned. Bumping VS Code means changing these
// values AND re-deriving the files listed in `upstream` (the build refuses to run until you do).

export const OVS = {
  version: '1.109.5',
  quality: 'stable',
  commit: '072586267e68ece9a47aa43f8c108e0dcbf44622',
  tarballs: {
    arm64: {
      url: 'https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v1.109.5/openvscode-server-v1.109.5-linux-arm64.tar.gz',
      sha256: '36d9c14036489b63de84ebace837fcacf7e60e669a0dc715802c5443684ea4dc',
    },
    x64: {
      url: 'https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v1.109.5/openvscode-server-v1.109.5-linux-x64.tar.gz',
      sha256: 'b433bf4f0227321a7014d8460d10a8f958adc0f45aa79bd889e84e65e8f88363',
    },
  },
  // The upstream files our derived copies are based on. If one changes, re-derive web/index.html
  // (from workbench.html) or re-check the webview patch anchors (build/webview-patch.mjs).
  upstream: {
    'out/vs/code/browser/workbench/workbench.html': '7391cd46d65fe1729650ee6b5b173d31aaa807f408b9bfcbf2c78f68b7a5eaa7',
    'out/vs/workbench/contrib/webview/browser/pre/index.html': '8b2e27b411b4fa493fe003c5312378ca0c7164fee99ed288c6e4f47c43dbca1f',
    'out/vs/workbench/contrib/webview/browser/pre/service-worker.js': '589fdd5794bba23c8dfcd9edbf9b3872cb310aee5f7118d12a88bb99faf183c2',
    'out/vs/workbench/contrib/webview/browser/pre/fake.html': 'dfa91b189b7df71b814d1f3ef98ce5d561309de2c069cd23c49d45c76bd947d5',
  },
};

export const SERVER_ROOT = `/${OVS.quality}-${OVS.commit}`;

// The browser-side npm packages: remote/web/package.json at the pinned commit, plus their transitive
// dependencies from remote/web/package-lock.json. The workbench loads them at runtime from
// /static/node_modules (amdX importAMDNodeModule, onig.wasm, tree-sitter wasm). Everything else in the
// server's node_modules is server-only (node-pty, ripgrep, spdlog, ...) and native.
export const WEB_NODE_MODULES = [
  '@microsoft/1ds-core-js',
  '@microsoft/1ds-post-js',
  '@microsoft/applicationinsights-core-js',
  '@microsoft/applicationinsights-shims',
  '@microsoft/dynamicproto-js',
  '@vscode/codicons',
  '@vscode/iconv-lite-umd',
  '@vscode/tree-sitter-wasm',
  '@vscode/vscode-languagedetection',
  '@xterm/addon-clipboard',
  '@xterm/addon-image',
  '@xterm/addon-ligatures',
  '@xterm/addon-progress',
  '@xterm/addon-search',
  '@xterm/addon-serialize',
  '@xterm/addon-unicode11',
  '@xterm/addon-webgl',
  '@xterm/xterm',
  'commander',
  'js-base64',
  'jschardet',
  'katex',
  'lru-cache',
  'opentype.js',
  'tas-client',
  'tiny-inflate',
  'vscode-oniguruma',
  'vscode-textmate',
  'yallist',
];

// The /terminal page (web/devbox/terminal.js) uses this bundle's xterm.js directly, as a classic script
// that defines window.Terminal, with its stylesheet. Its API use (new Terminal, open, write, resize, reset,
// onData/onBinary/onResize, dimensions.css.cell) was checked against this version, and loader.js has
// these two paths. The build refuses another version until someone re-checks the terminal.
export const XTERM = {
  version: '6.1.0-beta.109',
  script: 'node_modules/@xterm/xterm/lib/xterm.js',
  css: 'node_modules/@xterm/xterm/css/xterm.css',
};

// Top-level parts of the install that the browser loads from SERVER_ROOT/static.
export const STATIC_ROOTS = ['out', 'resources/server', 'extensions'];

// Served patched from the webview site only; never from the workbench site.
export const WEBVIEW_PRE = 'out/vs/workbench/contrib/webview/browser/pre';
