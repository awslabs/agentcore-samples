#!/usr/bin/env node
// Container smoke test for the dev box image. Runs the image the way AgentCore would (a volume at
// /mnt/workspace owned root:2000 mode 2775, DEVBOX_* runtime env, a container credential endpoint
// in the env), then checks it from the host through the proxy and from inside with docker exec.
// Then a second container stands in for a capability-less microVM on an EFS access point (single-
// user mode, a volume owned 1000:1000). In both, devbox-claude is started the way AgentCore's
// terminal would start it. Needs Docker. Removes its containers and volumes at the end
// (unless --keep).
//   node test/smoke/smoke.mjs [--build] [--keep]

import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { connect } from './vscode-client.mjs';

const BOX = fileURLToPath(new URL('../..', import.meta.url));
const IMAGE = 'devbox-box:dev';
const PORT = 9461;
const NAME = `devbox-smoke-${process.pid}`;
const VOLUME = `devbox-smoke-ws-${process.pid}`;
const COMMIT = '072586267e68ece9a47aa43f8c108e0dcbf44622';
const SERVER_ROOT = `/stable-${COMMIT}`;
const SESSION = `dbx-${createHash('sha256').update('00u-smoke:1').digest('hex')}`;
const MOUNT_DELAY = 6;
const ENV = {
  DEVBOX_OWNER: 'ada',
  DEVBOX_OWNER_UID: '00u-smoke',
  DEVBOX_SESSION_ID: SESSION,
  DEVBOX_TIER: 'power',
  DEVBOX_SSO_ROLE: 'ClaudeCode-Power',
  DEVBOX_ACCOUNT_ID: '111122223333',
  DEVBOX_SSO_START_URL: 'https://d-1234567890.awsapps.com/start',
  DEVBOX_SSO_REGION: 'us-east-1',
  DEVBOX_MODELS: JSON.stringify({ opus: 'us.anthropic.claude-opus-4-6-v1', sonnet: 'us.anthropic.claude-sonnet-4-6',
    haiku: 'us.anthropic.claude-haiku-4-5-20251001-v1:0' }),
  DEVBOX_TOOLS_GATEWAY_URL: 'https://devbox-tools-smoke.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp',
  DEVBOX_TEST_MOUNT_DELAY: String(MOUNT_DELAY),
  // What AgentCore gives the container; none of it may reach the person's processes.
  AWS_CONTAINER_CREDENTIALS_FULL_URI: 'http://169.254.170.23/v1/credentials',
  AWS_EC2_METADATA_SERVICE_ENDPOINT: 'http://100.88.0.1:1338',
};

const args = new Set(process.argv.slice(2));
const results = [];
const facts = {};

function check(name, ok, detail = '') {
  results.push({ name, ok: Boolean(ok) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  return ok;
}

function docker(argv, { input, allowFail = false, timeout = 120_000 } = {}) {
  const res = spawnSync('docker', argv, { input, encoding: 'utf8', timeout, maxBuffer: 64 * 1024 * 1024 });
  if (res.status !== 0 && !allowFail) throw new Error(`docker ${argv.join(' ')} failed: ${res.stderr || res.error}`);
  return { code: res.status, out: (res.stdout || '').trim(), err: (res.stderr || '').trim() };
}

const inContainer = (name, user, script, opts = {}) =>
  docker(['exec', '-i', '-u', user, '-e', 'HOME=/mnt/workspace/home', name, 'bash', '-c', script], { allowFail: true, ...opts });
const inBox = (user, script, opts = {}) => inContainer(NAME, user, script, opts);
const envArgs = (env) => Object.entries(env).flatMap(([k, v]) => ['-e', `${k}=${v}`]);
const cleanups = [];

// docker() blocks the event loop (spawnSync), so fetch can't notice that the proxy closed an idle
// keep-alive connection (Node's 5 s timeout) and may send on it. That request never reached the
// proxy, so one retry on a fresh connection is safe.
async function fetchBox(url, init) {
  try {
    return await fetch(url, init);
  } catch (err) {
    if (!['UND_ERR_SOCKET', 'ECONNRESET'].includes(err?.cause?.code)) throw err;
    return fetch(url, init);
  }
}

async function invoke(body, session = SESSION, port = PORT) {
  const res = await fetchBox(`http://127.0.0.1:${port}/invocations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': session },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60_000),
  });
  return { status: res.status, json: await res.json() };
}

async function ping() {
  const res = await fetchBox(`http://127.0.0.1:${PORT}/ping`, { signal: AbortSignal.timeout(2_000) });
  return { status: res.status, json: await res.json() };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function until(what, fn, timeoutMs, stepMs = 250) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch { /* not yet */ }
    if (Date.now() > end) throw new Error(`timed out: ${what}`);
    await sleep(stepMs);
  }
}

function maxPerSecond(times) {
  let best = 0;
  for (let i = 0, j = 0; i < times.length; i++) {
    while (times[i] - times[j] >= 1000) j++;
    best = Math.max(best, i - j + 1);
  }
  return best;
}

async function main() {
  if (args.has('--build')) {
    console.log('building the image...');
    execFileSync('docker', ['buildx', 'build', '--platform', 'linux/arm64', '--load', '-t', IMAGE, BOX], { stdio: 'inherit' });
  }

  // -- image --------------------------------------------------------------------------------
  const size = Number(docker(['image', 'inspect', IMAGE, '--format', '{{.Size}}']).out);
  const layers = Number(docker(['image', 'inspect', IMAGE, '--format', '{{len .RootFS.Layers}}']).out);
  // AgentCore doesn't say whether its 2 GB limit is compressed or not, so check both.
  const unpacked = Number(docker(['run', '--rm', '--entrypoint', '/bin/sh', IMAGE, '-c', 'du -sxb / 2>/dev/null | cut -f1']).out);
  facts.imageBytes = { compressed: size, uncompressed: unpacked };
  facts.layers = layers;
  check('image is well under 2 GB, compressed and uncompressed', size < 1.6e9 && unpacked < 1.6e9,
    `${(size / 1e9).toFixed(2)} GB compressed, ${(unpacked / 1e9).toFixed(2)} GB on disk`);
  check('image has fewer than 53 layers', layers < 53, `${layers} layers`);
  const user = docker(['image', 'inspect', IMAGE, '--format', '{{.Config.User}}']).out;
  check('no USER in the image (the entrypoint drops privileges)', user === '', `User="${user}"`);

  // -- start like AgentCore -------------------------------------------------------------------
  cleanups.push(() => docker(['rm', '-f', NAME], { allowFail: true }), () => docker(['volume', 'rm', '-f', VOLUME], { allowFail: true }));
  docker(['volume', 'create', VOLUME]);
  docker(['run', '--rm', '--entrypoint', '/bin/sh', '-v', `${VOLUME}:/v`, IMAGE, '-c', 'chown 0:2000 /v && chmod 2775 /v']);
  const t0 = Date.now();
  docker(['run', '-d', '--name', NAME, '-p', `127.0.0.1:${PORT}:8080`, '-v', `${VOLUME}:/mnt/workspace`, ...envArgs(ENV), IMAGE]);

  const first = await until('/ping', ping, 15_000, 50);
  facts.pingAfterMs = Date.now() - t0;
  check('/ping answers right after start', first.status === 200 && first.json.status === 'Healthy', `${facts.pingAfterMs} ms`);

  const early = await invoke({ v: 1, op: 'status' });
  check('status before the volume counts as mounted: volume waiting, vscode waiting',
    early.status === 200 && early.json.volume === 'waiting' && early.json.vscode === 'waiting', JSON.stringify(early.json));
  const wrong = await invoke({ v: 1, op: 'status' }, 'dbx-not-this-box-000000000000000000000000000000');
  check('a different session id is refused (HTTP 200, ok:false)', wrong.status === 200 && wrong.json.error === 'wrong session');

  const seen = [];
  const ready = await until('vscode ready', async () => {
    const { json } = await invoke({ v: 1, op: 'status' });
    const key = `${json.volume}/${json.vscode}`;
    if (seen.at(-1) !== key) seen.push(key);
    return json.vscode === 'ready' ? json : null;
  }, 180_000);
  facts.readyAfterMs = Date.now() - t0;
  facts.transitions = seen;
  check('status goes volume waiting -> mounted and vscode starting -> ready',
    seen[0] === 'waiting/waiting' && seen.includes('mounted/starting') && seen.at(-1) === 'mounted/ready',
    `${seen.join(' -> ')} in ${(facts.readyAfterMs / 1000).toFixed(1)} s`);
  check('status carries owner, session, commit and a serverStartId',
    ready.owner === 'ada' && ready.sessionId === SESSION && ready.commit === COMMIT && /^[0-9a-f]{16}$/.test(ready.serverStartId));

  // -- op: http -------------------------------------------------------------------------------
  const version = await invoke({ v: 1, op: 'http', method: 'GET', path: '/version', query: '' });
  check('op http GET /version returns the commit', version.json.status === 200
    && Buffer.from(version.json.bodyB64, 'base64').toString() === COMMIT);
  const resource = await invoke({ v: 1, op: 'http', method: 'GET', path: `${SERVER_ROOT}/vscode-remote-resource`,
    query: `path=${encodeURIComponent('/mnt/workspace/projects/.vscode/tasks.json')}` });
  check('op http vscode-remote-resource reads the first-boot tasks.json', resource.json.status === 200
    && Buffer.from(resource.json.bodyB64, 'base64').toString().includes('/usr/local/bin/devbox-claude'),
    `status ${resource.json.status}, headers ${JSON.stringify(resource.json.headers)}`);
  const root = await invoke({ v: 1, op: 'http', method: 'GET', path: '/', query: '' });
  check('op http outside the allowlist is 404', root.json.ok === true && root.json.status === 404);

  // -- /ws through the proxy ------------------------------------------------------------------
  const client = await connect({ port: PORT, sessionId: SESSION, serverRoot: SERVER_ROOT, commit: COMMIT });
  check('VS Code handshake through the proxy /ws (auth, sign, connectionType, ok, Initialize)', true);
  const busy = await ping();
  check('/ping is HealthyBusy while the WebSocket is open', busy.json.status === 'HealthyBusy');

  const bigFile = '/opt/openvscode-server/out/vs/code/browser/workbench/workbench.js';
  const bigSize = Number(inBox('root', `stat -c %s ${bigFile}`).out);
  client.sizes.length = 0;
  client.arrivals.length = 0;
  const started = Date.now();
  const content = await client.call('remoteFilesystem', 'readFile',
    [{ $mid: 1, scheme: 'vscode-remote', authority: 'smoke', path: bigFile }, {}]);
  const took = Date.now() - started;
  const maxMsg = Math.max(...client.sizes);
  facts.readFile = { bytes: content.length, messages: client.sizes.length, maxMessage: maxMsg, ms: took,
    maxPerSecond: maxPerSecond(client.arrivals) };
  check('a 13 MB readFile arrives whole', content.length === bigSize, `${content.length} of ${bigSize} bytes in ${took} ms`);
  check('every message the browser side got is at most 32000 bytes', maxMsg <= 32_000,
    `${client.sizes.length} messages, largest ${maxMsg}`);
  check('messages arrive at about 200 a second or fewer', facts.readFile.maxPerSecond <= 210,
    `max ${facts.readFile.maxPerSecond} in any 1 s window (receive side)`);

  const extensions = await client.call('remoteExtensionsScanner', 'scanExtensions', ['en']);
  const claudeExt = extensions.find((e) => String(e.identifier?.value ?? e.identifier).toLowerCase() === 'anthropic.claude-code');
  facts.claudeExtension = claudeExt && { version: claudeExt.version, isBuiltin: claudeExt.isBuiltin,
    location: claudeExt.extensionLocation?.path };
  check('the server lists the Claude Code system extension (remoteExtensionsScanner)',
    claudeExt && claudeExt.version === '2.1.277' && claudeExt.extensionLocation?.path === '/opt/openvscode-server/extensions/anthropic.claude-code',
    JSON.stringify(facts.claudeExtension));

  // "Install from VSIX" in the workbench takes this path: the running server installs into its
  // --extensions-dir, which is root-owned and read-only.
  inBox('dev', `python3 - <<'EOF'
import zipfile
with zipfile.ZipFile("/tmp/sideload.vsix", "w") as z:
    z.writestr("extension/package.json", '{"name":"sideload","publisher":"smoke","version":"0.0.1","engines":{"vscode":"*"}}')
    z.writestr("extension.vsixmanifest", '<?xml version="1.0"?><PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata><Identity Id="sideload" Version="0.0.1" Publisher="smoke"/></Metadata></PackageManifest>')
    z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="json" ContentType="application/json"/></Types>')
EOF`);
  const vsixUri = { $mid: 1, scheme: 'vscode-remote', authority: 'smoke', path: '/tmp/sideload.vsix' };
  const installed = await Promise.race([
    client.call('extensions', 'install', [vsixUri, {}]).then((r) => `INSTALLED ${JSON.stringify(r)?.slice(0, 200)}`, (e) => `refused: ${e.message}`),
    sleep(60_000).then(() => 'no answer in 60 s'),
  ]);
  check('the running server cannot install a VSIX (its extensions folder is read-only)', installed.startsWith('refused'),
    installed.slice(0, 200));
  const sideloadCli = inBox('dev', '/opt/openvscode-server/bin/openvscode-server --server-data-dir /mnt/workspace/home/.openvscode-server --install-extension /tmp/sideload.vsix 2>&1');
  const after = await client.call('remoteExtensionsScanner', 'scanExtensions', ['en']);
  const outside = after.map((e) => e.extensionLocation?.path ?? '').filter((p) => !p.startsWith('/opt/openvscode-server/extensions/'));
  facts.extensionCount = after.length;
  check("a VSIX side-loaded into dev's own folder is not loaded by the server; every extension is the image's",
    outside.length === 0 && after.length === extensions.length, `${after.length} extensions; CLI said: ${sideloadCli.out.split('\n').at(-1)}; outside: ${outside.join(', ')}`);
  client.close();

  // -- inside the box -------------------------------------------------------------------------
  const id = inBox('root', 'id dev; id devboxproxy; command -v sudo || echo no-sudo; dpkg -s sudo >/dev/null 2>&1 && echo sudo-installed || echo sudo-absent');
  facts.ids = id.out;
  check('dev is uid 1000 with no sudo (binary and package absent)', /uid=1000\(dev\) gid=1000\(dev\)/.test(id.out)
    && /uid=1001\(devboxproxy\) gid=1001\(devboxproxy\)/.test(id.out)
    && !/\bsudo\b.*groups|\(sudo\)/.test(id.out) && id.out.includes('no-sudo') && id.out.includes('sudo-absent'), id.out.replace(/\n/g, '; '));
  const suid = inBox('root', 'find / -xdev -type f -perm /6000 2>/dev/null');
  check('no setuid or setgid files in the image', suid.out === '', suid.out.split('\n').slice(0, 3).join(', '));

  const perms = inBox('root', 'stat -c "%U:%G %a %n" /etc/claude-code /etc/claude-code/managed-settings.json /etc/claude-code/managed-mcp.json /etc/claude-code/managed-settings.d/*.json /etc/claude-code/hooks/* /etc/devbox /etc/devbox/aws-config');
  const permLines = perms.out.split('\n');
  check('/etc/claude-code and /etc/devbox files are root:root 0644 (dirs and hooks 0755)',
    permLines.every((l) => /^root:root (644|755) /.test(l)) && permLines.filter((l) => l.endsWith('.json')).every((l) => l.includes(' 644 ')),
    permLines.join('; '));
  const writes = inBox('dev', [
    '/etc/claude-code/managed-settings.json', '/etc/claude-code/managed-settings.d/99-mine.json', '/etc/claude-code/managed-mcp.json',
    '/etc/devbox/aws-config', '/opt/devbox/proxy/server.mjs', '/opt/openvscode-server/product.json', '/opt/new-file',
    '/usr/local/bin/devbox-claude', '/opt/devbox/extensions/extensions.json', '/opt/devbox/extensions/new-file',
    '/opt/openvscode-server/extensions/new-file', '/etc/tmux.conf',
  ].map((f) => `(echo x >> ${f}) 2>/dev/null && echo "WROTE ${f}"`).join('; ') + '; true');
  check('dev cannot write /etc/claude-code, /etc/devbox, /opt or the proxy', writes.out === '', writes.out);

  const product = inBox('dev', "jq -c '[has(\"extensionsGallery\"), .commit, .quality]' /opt/openvscode-server/product.json");
  check('product.json has no extensionsGallery; commit and quality are intact',
    product.out === `[false,"${COMMIT}","stable"]`, product.out);
  const extDir = inBox('root', 'stat -c "%U:%G %a %n" /opt/devbox/extensions /opt/devbox/extensions/extensions.json; cat /opt/devbox/extensions/extensions.json');
  check('the server extensions folder is root-owned and read-only, with a build-time extensions.json of []',
    /^root:root 555 \/opt\/devbox\/extensions\nroot:root 444 \/opt\/devbox\/extensions\/extensions\.json\n\[\]$/.test(extDir.out), extDir.out.replace(/\n/g, '; '));
  const gallery = inBox('dev', 'timeout 60 /opt/openvscode-server/bin/openvscode-server --server-data-dir /mnt/workspace/home/.openvscode-server --install-extension redhat.vscode-yaml 2>&1');
  check('installing from the gallery fails without trying open-vsx.org', gallery.code !== 0
    && /No extension gallery service configured/.test(gallery.out) && !/open-vsx|getaddrinfo/i.test(gallery.out),
  gallery.out.split('\n').filter(Boolean).at(-1));

  const version2 = inBox('dev', 'claude --version');
  check("'claude --version' works as dev", /^2\.1\.277 \(Claude Code\)/.test(version2.out), version2.out || version2.err);
  const mcpAdd = inBox('dev', 'cd /mnt/workspace/projects && timeout 60 claude mcp add --transport http test https://example.com/mcp 2>&1');
  check('managed-mcp.json has exclusive control (claude mcp add is refused)', mcpAdd.code !== 0 && /enterprise MCP/i.test(mcpAdd.out),
    mcpAdd.out.split('\n')[0]);
  // Enforced before any network call, so it proves managed-settings.json itself is loaded.
  const market = inBox('dev', 'cd /mnt/workspace/projects && timeout 60 claude plugin marketplace add https://github.com/example/none 2>&1');
  check('managed-settings.json is enforced (strictKnownMarketplaces [] blocks marketplace add)',
    market.code !== 0 && /polic|managed|not allowed|blocked/i.test(market.out), market.out.split('\n').filter(Boolean)[0]);
  const locate = inBox('dev', '/opt/openvscode-server/bin/openvscode-server --server-data-dir /mnt/workspace/home/.openvscode-server --locate-extension anthropic.claude-code 2>&1; echo "user extensions: $(/opt/openvscode-server/bin/openvscode-server --server-data-dir /mnt/workspace/home/.openvscode-server --list-extensions --show-versions 2>&1 | tr "\\n" " ")"');
  check('the server CLI locates the system extension', locate.out.startsWith('/opt/openvscode-server/extensions/anthropic.claude-code'),
    locate.out.replace(/\n/g, ' | '));
  const mcpList = inBox('dev', 'cd /mnt/workspace/projects && timeout 90 claude mcp list 2>&1');
  check('claude mcp list shows only the managed web-search server', /web-search/.test(mcpList.out),
    mcpList.out.split('\n').filter(Boolean).slice(0, 3).join(' | '));

  const listen = inBox('root', `python3 - <<'EOF'
for name in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(name).read().splitlines()[1:]:
        f = line.split()
        if f[3] == "0A":
            addr, port = f[1].rsplit(":", 1)
            print(name[-4:].strip("/"), addr, int(port, 16))
EOF`);
  const listeners = listen.out.split('\n').filter(Boolean);
  facts.listeners = listeners;
  const on3000 = listeners.filter((l) => l.endsWith(' 3000'));
  check('openvscode-server listens on 127.0.0.1:3000 only', on3000.length === 1 && on3000[0].includes('0100007F'), on3000.join(', '));
  const on8080 = listeners.filter((l) => l.endsWith(' 8080'));
  check('the proxy owns :8080 with one dual-stack socket on [::] (IPv4 and IPv6)',
    on8080.length === 1 && on8080[0] === `tcp6 ${'0'.repeat(32)} 8080`, on8080.join(', '));

  const procs = inBox('root', 'ps -eo user:14,pid,args --no-headers');
  facts.processes = procs.out.split('\n').map((l) => l.replace(/\s+/g, ' ').slice(0, 140));
  check('the proxy runs as devboxproxy with the bundled node',
    /devboxproxy\s+\d+ \/opt\/openvscode-server\/node \/opt\/devbox\/proxy\/server\.mjs/.test(procs.out));
  check('openvscode-server runs as dev', /\ndev\s+\d+ \/opt\/openvscode-server\/node .*out\/server-main\.js/.test(`\n${procs.out}`));

  const diag = await invoke({ v: 1, op: 'diag' });
  facts.diag = diag.json;
  const vp = diag.json.vscodeProcess;
  check('op diag: proxy is 1001, VS Code is 1000 with the volume group 2000',
    diag.json.proxy?.uid === 1001 && vp?.uid === 1000 && vp?.gid === 1000 && vp?.groups?.includes(2000), JSON.stringify({ proxy: diag.json.proxy, vp }));
  check('op diag: /mnt/workspace stat and mount info', diag.json.workspace?.mode === '2775' && diag.json.workspace?.gid === 2000
    && diag.json.workspace?.mount, JSON.stringify(diag.json.workspace));
  check('op diag: header names seen and the user-namespace probe',
    diag.json.headersSeen?.ws?.names?.includes('x-amzn-bedrock-agentcore-runtime-custom-vscodepath') && diag.json.userNamespace?.unshareUser,
    JSON.stringify(diag.json.userNamespace?.unshareUser));
  check('op diag: the address family the caller came from (IPv4 through the dual-stack socket)',
    diag.json.headersSeen?.ws?.peer === 'IPv4' && diag.json.headersSeen?.invocations?.peer === 'IPv4',
    JSON.stringify({ ws: diag.json.headersSeen?.ws?.peer, invocations: diag.json.headersSeen?.invocations?.peer }));

  const vscodePid = inBox('root', "pgrep -u dev -f 'out/server-main.js' | head -1").out;
  // As dev: root here has no CAP_SYS_PTRACE, so it can't read another user's environ.
  const nnp = inBox('dev', `grep -E '^(NoNewPrivs|Groups)' /proc/${vscodePid}/status; tr '\\0' '\\n' < /proc/${vscodePid}/environ | grep -E '^(AWS_|HOME=|DEVBOX_)' | sort`);
  facts.vscodeEnv = nnp.out.split('\n');
  check('VS Code runs with no_new_privs', /NoNewPrivs:\s+1/.test(nnp.out));
  check("AgentCore's credential endpoint never reaches the person's processes",
    !/AWS_CONTAINER_CREDENTIALS|AWS_EC2_METADATA_SERVICE_ENDPOINT|DEVBOX_SESSION_ID/.test(nnp.out) && /AWS_PROFILE=devbox/.test(nnp.out), facts.vscodeEnv.join(' '));

  const vol = inBox('root', 'stat -c "%u:%g %a %n" /mnt/workspace /mnt/workspace/home /mnt/workspace/projects /mnt/workspace/projects/.vscode/tasks.json');
  check('home and projects are dev-owned, keeping the volume group', /1000:2000 700 \/mnt\/workspace\/home/.test(vol.out)
    && /1000:2000 700 \/mnt\/workspace\/projects/.test(vol.out) && /1000:\d+ \d+ \/mnt\/workspace\/projects\/\.vscode\/tasks\.json/.test(vol.out),
    vol.out.replace(/\n/g, '; '));

  const files = inBox('root', 'cat /etc/devbox/aws-config; echo ---; cat /etc/claude-code/managed-settings.d/20-tier.json; echo ---; cat /etc/claude-code/managed-mcp.json');
  facts.renderedFiles = files.out;
  check('aws-config names the devbox profile and the tier role', /\[profile devbox\][\s\S]*sso_role_name = ClaudeCode-Power/.test(files.out));
  check('20-tier.json and managed-mcp.json are written from the env', files.out.includes('"ANTHROPIC_DEFAULT_OPUS_MODEL"')
    && files.out.includes(ENV.DEVBOX_TOOLS_GATEWAY_URL) && !files.out.includes('${'));
  const parse = inBox('dev', 'for f in /etc/claude-code/*.json /etc/claude-code/managed-settings.d/*.json; do python3 -m json.tool "$f" >/dev/null || echo "BAD $f"; done');
  check('every managed JSON file parses', parse.out === '' && parse.code === 0, parse.out);

  const guard = inBox('dev', `echo '{"tool_name":"Bash","tool_input":{"command":"cat /etc/devbox/aws-config"}}' | /usr/bin/python3 -I /etc/claude-code/hooks/block-direct-access.py`);
  check('the installed Bash guard denies reading /etc/devbox', /"permissionDecision": "deny"/.test(guard.out));

  // -- pointer hook, sign-in state, devbox-claude ---------------------------------------------
  const event = JSON.stringify({ hook_event_name: 'SessionStart', source: 'startup', session_id: 'smoke-session-1',
    cwd: '/mnt/workspace/projects', transcript_path: '/mnt/workspace/home/.claude/projects/-mnt-workspace-projects/smoke-session-1.jsonl' });
  inBox('dev', '/usr/bin/python3 -I /etc/claude-code/hooks/last-session.py', { input: event });
  const pointed = await until('lastSession in status', async () => {
    const { json } = await invoke({ v: 1, op: 'status' });
    return json.lastSession?.sessionId === 'smoke-session-1' ? json.lastSession : null;
  }, 15_000).catch(() => null);
  check('op status reports the pointer after a SessionStart hook run', pointed, JSON.stringify(pointed));

  // The agent-busy hook: a turn under way keeps the box busy with no browser attached.
  const turn = (name) => JSON.stringify({ hook_event_name: name, session_id: 'smoke-turn-1', cwd: '/mnt/workspace/projects',
    transcript_path: '/mnt/workspace/home/.claude/projects/-mnt-workspace-projects/smoke-turn-1.jsonl' });
  const agentBusy = async () => (await invoke({ v: 1, op: 'diag' })).json.websockets?.agentBusy;
  const quiet = await agentBusy();
  const prompt = inBox('dev', '/usr/bin/python3 -I /etc/claude-code/hooks/agent-busy.py', { input: turn('UserPromptSubmit') });
  const working = await until('agentBusy', async () => (await agentBusy()) === true, 15_000).catch(() => false);
  inBox('dev', '/usr/bin/python3 -I /etc/claude-code/hooks/agent-busy.py', { input: turn('Stop') });
  const idle = await until('agentBusy false', async () => (await agentBusy()) === false, 15_000).catch(() => false);
  check('a Claude turn (UserPromptSubmit hook) makes the box agent-busy until its Stop hook',
    quiet === false && prompt.code === 0 && prompt.out === '' && working && idle, JSON.stringify({ quiet, working, idle }));

  const seed = inBox('root', 'stat -c "%u %a" /mnt/workspace/home/.claude.json && cat /mnt/workspace/home/.claude.json');
  const [seedOwner, ...seedJson] = seed.out.split('\n');
  let seeded = null;
  try { seeded = JSON.parse(seedJson.join('\n')); } catch { /* reported below */ }
  check('first boot seeds ~/.claude.json (dev, 0600): onboarding done, the projects folder trusted',
    seedOwner === '1000 600' && seeded?.hasCompletedOnboarding === true && seeded?.lastOnboardingVersion === '2.1.277'
    && seeded?.projects?.['/mnt/workspace/projects']?.hasTrustDialogAccepted === true, seed.out.replace(/\n/g, ' ').slice(0, 200));
  // Claude itself, in a tmux pane like devbox-claude's, in a throwaway container with no network:
  // first a new home (the control: the first-run screen the probe must be able to see), then the seeded one.
  const tui = docker(['run', '--rm', '-i', '--network', 'none', '--entrypoint', '/bin/bash', IMAGE, '-c', `set -u
mkdir -p /mnt/workspace/projects /mnt/workspace/home /tmp/fresh && cat > /mnt/workspace/home/.claude.json
chown -R 1000:1000 /mnt/workspace /tmp/fresh && chmod 600 /mnt/workspace/home/.claude.json
probe() {
  local as="setpriv --reuid 1000 --regid 1000 --clear-groups --no-new-privs env -i HOME=$1 PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=xterm-256color"
  $as tmux -L probe new -d -s probe -x 200 -y 50 'cd /mnt/workspace/projects && claude'
  for _ in $(seq 1 80); do
    sleep 0.5
    $as tmux -L probe capture-pane -p -t probe | grep -qE '[?] for shortcuts|Choose the text style|Quick safety check' && break
  done
  sleep 1; $as tmux -L probe capture-pane -p -t probe; $as tmux -L probe kill-server
}
echo '=== fresh'; probe /tmp/fresh
echo '=== seeded'; probe /mnt/workspace/home`], { input: seedJson.join('\n'), allowFail: true, timeout: 120_000 });
  const [, fresh = '', seededScreen = ''] = tui.out.split(/^=== (?:fresh|seeded)$/m);
  const firstRun = /Choose the text style|Quick safety check/;
  facts.claudeScreens = { fresh: fresh.split('\n').filter((l) => firstRun.test(l)).join(' | '),
    seeded: seededScreen.split('\n').filter((l) => /for shortcuts/.test(l)).join(' | ') };
  check('Claude Code opens at its prompt: no theme picker or folder-trust question (a new home shows them)',
    firstRun.test(fresh) && /\? for shortcuts/.test(seededScreen) && !firstRun.test(seededScreen),
    JSON.stringify(facts.claudeScreens));

  const cache = `/mnt/workspace/home/.aws/sso/cache/${createHash('sha1').update('devbox').digest('hex')}.json`;
  const future = new Date(Date.now() + 3_600_000).toISOString();
  inBox('dev', `mkdir -p "$(dirname ${cache})" && umask 077 && echo '{"accessToken":"not-a-real-token","expiresAt":"${future}"}' > ${cache}`);
  const signedIn = await until('signedIn', async () => (await invoke({ v: 1, op: 'status' })).json.signedIn === true, 15_000).catch(() => false);
  const statusText = JSON.stringify((await invoke({ v: 1, op: 'status' })).json);
  check('signedIn turns true for an unexpired IdC token cache, without exposing it', signedIn && !statusText.includes('not-a-real-token'));

  // AgentCore's terminal is root here (the container has Docker's default capabilities), so
  // devbox-claude must drop to dev and land in dev's tmux server, the one VS Code's task uses.
  // devbox-claude resumes a session only when Claude left a transcript for it.
  inBox('dev', 'mkdir -p /mnt/workspace/home/.claude/projects/-mnt-workspace-projects && echo "{}" > /mnt/workspace/home/.claude/projects/-mnt-workspace-projects/smoke-session-1.jsonl');
  await checkDevboxClaude(NAME, { sessionUser: 'dev', uid: 1000, secondFrom: 'dev', resume: 'smoke-session-1' });

  // -- supervision ----------------------------------------------------------------------------
  const before = (await invoke({ v: 1, op: 'status' })).json.serverStartId;
  inBox('root', "pkill -KILL -f 'out/server-main.js'");
  const restarted = await until('vscode restarted', async () => {
    const { json } = await invoke({ v: 1, op: 'status' });
    return json.vscode === 'ready' && json.serverStartId !== before ? json : null;
  }, 90_000).catch(() => null);
  check('a killed openvscode-server is restarted with a new serverStartId', restarted, restarted && `${before} -> ${restarted.serverStartId}`);
  // Keep trying to bind :8080 as dev on every address and family, while the proxy runs and then
  // through the restart window after it is killed.
  const squatScript = `python3 - <<'EOF'
import socket, time
tries = [(socket.AF_INET, "0.0.0.0", None), (socket.AF_INET, "127.0.0.1", None), (socket.AF_INET6, "::", 0),
         (socket.AF_INET6, "::", 1), (socket.AF_INET6, "::1", 1)]
result = "refused"
for _ in range(40):
    for family, host, v6only in tries:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        if v6only is not None:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
        try:
            s.bind((host, 8080)); s.listen(); result = f"BOUND [{host}]:8080 v6only={v6only}"; break
        except OSError as e:
            pass
        finally:
            s.close()
    if result != "refused":
        break
    time.sleep(0.05)
print(result)
EOF`;
  const squatRunning = inBox('dev', squatScript);
  check('dev cannot listen on :8080 on any address, IPv4 or IPv6', squatRunning.out === 'refused', squatRunning.out);
  inBox('root', "pkill -KILL -f '/opt/devbox/proxy/server.mjs'");
  const squat = inBox('dev', squatScript);
  check('while the proxy restarts, dev cannot take port 8080 (IPv4 or IPv6)', squat.out === 'refused', squat.out);
  const back = await until('proxy back', ping, 15_000, 100).catch(() => null);
  check('a killed proxy is restarted', back?.status === 200);

  // -- the Python unit tests, again, on the image's Python ------------------------------------
  const py = docker(['run', '--rm', '-u', '1000', '-e', 'HOME=/tmp', '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', '/usr/bin/python3',
    '-v', `${BOX}:/src:ro`, '-w', '/src', IMAGE, '-m', 'unittest', 'discover', '-s', 'test', '-p', 'test_*.py'], { allowFail: true });
  check("Python unit tests pass on the image's python3", py.code === 0, py.err.split('\n').filter((l) => /^(Ran|OK|FAILED)/.test(l)).join(' '));

  const logs = docker(['logs', NAME], { allowFail: true });
  facts.containerLog = `${logs.out}\n${logs.err}`.split('\n').filter((l) => l.startsWith('[devbox]') || l.startsWith('[proxy]')).slice(0, 30);
  check('no token or auth header value in the container log', !/not-a-real-token|Bearer /.test(`${logs.out}${logs.err}`));

  await microVm();
}

// devbox-claude started the way AgentCore's terminal starts its shell: a process execed
// into the container as root, carrying the container's own env (the credential endpoint included)
// and none of what the supervisor gives VS Code. `script` gives it a terminal, as AgentCore's PTY
// does. Then a second devbox-claude, like VS Code's folder-open task, must join the same session.
async function checkDevboxClaude(name, { sessionUser, uid, secondFrom, resume }) {
  const label = `devbox-claude (${sessionUser === 'dev' ? 'two-user' : 'single-user'})`;
  const tmux = (argv) => inContainer(name, sessionUser, `tmux ${argv}`);
  const start = (user) => docker(['exec', '-d', '-u', user, name, 'script', '-qfec', '/usr/local/bin/devbox-claude', '/dev/null']);
  start('root');
  // Claude is the pane's process, or (while `claude --resume X` runs) the pane shell's child.
  const pane = await until('the tmux session "claude" runs claude', async () => {
    const r = tmux(`list-panes -s -t claude -F '#{pane_pid}@@#{pane_current_path}@@#{pane_start_command}'`);
    if (r.code !== 0) return null;
    const [panePid, path, startCommand] = r.out.split('\n')[0].split('@@');
    const claude = inContainer(name, 'root', 'ps -eo pid=,ppid=,comm=').out.split('\n').map((l) => l.trim().split(/\s+/))
      .find(([p, parent, comm]) => comm === 'claude' && (p === panePid || parent === panePid));
    return claude ? { panePid, pid: claude[0], path, startCommand } : null;
  }, 45_000).catch(() => null);
  const who = pane && inContainer(name, sessionUser, `grep -E '^(Uid|NoNewPrivs)' /proc/${pane.pid}/status; tr '\\0' '\\n' < /proc/${pane.pid}/environ | sort`);
  const lines = who ? who.out.split('\n') : [];
  facts[`${label} pane`] = { ...pane, status: lines.filter((l) => /^(Uid|NoNewPrivs):/.test(l)) };
  check(`${label} from AgentCore's terminal, as root lands in the tmux session "claude" running claude, as uid ${uid}`,
    pane && lines.some((l) => new RegExp(`^Uid:\\s+${uid}\\s`).test(l)) && lines.some((l) => /^NoNewPrivs:\s+1/.test(l)),
    JSON.stringify(facts[`${label} pane`]));
  const want = ['HOME=/mnt/workspace/home', 'USER=dev', 'SHELL=/bin/bash', 'PATH=/usr/local/bin:/usr/bin:/bin', 'LANG=C.UTF-8',
    'AWS_PROFILE=devbox', 'AWS_CONFIG_FILE=/etc/devbox/aws-config', 'AWS_REGION=us-east-1', 'AWS_EC2_METADATA_DISABLED=true',
    'DEVBOX_OWNER=ada', 'DEVBOX_TIER=power'];
  const leaked = lines.filter((l) => /^(AWS_CONTAINER_|AWS_EC2_METADATA_SERVICE_ENDPOINT|DEVBOX_SESSION_ID|DEVBOX_SSO_)/.test(l));
  check(`${label} gives Claude the person's environment (dev_env), not the terminal's (no credential endpoint)`,
    want.every((w) => lines.includes(w)) && leaked.length === 0,
    `missing ${JSON.stringify(want.filter((w) => !lines.includes(w)))}, leaked ${JSON.stringify(leaked)}`);
  const clientTerm = tmux("list-clients -t claude -F '#{client_termname}'").out;
  check(`${label}: the terminal type defaults to xterm-256color`, clientTerm === 'xterm-256color', clientTerm);
  const expected = resume ? `claude --resume ${resume} || exec claude` : 'exec claude';
  check(`${label} starts \`${expected}\` in the last session's folder`,
    pane && pane.startCommand.includes(expected) && pane.path === '/mnt/workspace/projects', JSON.stringify(pane));
  // A window of its own in the session, so it runs with the session's environment.
  const out = `/tmp/devbox-claude-version-${process.pid}`;
  tmux(`new-window -d -t claude: 'claude --version > ${out} 2>&1'`);
  const version = await until('claude --version in the session',
    async () => { const r = inContainer(name, sessionUser, `cat ${out}`).out; return /Claude Code/.test(r) ? r : null; }, 30_000)
    .catch(() => inContainer(name, sessionUser, `cat ${out}`).out);
  check(`${label}: claude --version works in the session`, /^2\.1\.277 \(Claude Code\)/.test(version), version);
  start(secondFrom);
  const joined = await until('a second client', async () => (tmux("list-clients -t claude -F '#{client_pid}'").out.split('\n').filter(Boolean).length === 2), 20_000)
    .catch(() => false);
  const sessions = tmux("list-sessions -F '#{session_name}'").out;
  check(`${label} again (as ${secondFrom}, like VS Code's task) attaches to the same session`, joined && sessions === 'claude', `sessions: ${sessions}`);
  tmux('kill-server');
}

// A microVM may start the container as root with no capabilities (single-user mode), on an EFS
// access point that makes every file 1000:1000 (PosixUser). A docker volume can't make the kernel
// hand every request to uid 1000 the way EFS does, so this stands in for it with a mount root owned
// 1000:1000 that anyone may write (EFS decides as 1000 whoever asks), and a home an earlier boot
// left, owned by 1000: the supervisor's chmod of it is refused with EPERM, as it is on EFS.
async function microVm() {
  const name = `${NAME}-microvm`;
  const volume = `${VOLUME}-efs`;
  const port = PORT + 1;
  cleanups.push(() => docker(['rm', '-f', name], { allowFail: true }), () => docker(['volume', 'rm', '-f', volume], { allowFail: true }));
  docker(['volume', 'create', volume]);
  docker(['run', '--rm', '--entrypoint', '/bin/sh', '-v', `${volume}:/v`, IMAGE, '-c',
    'chown 1000:1000 /v && chmod 0777 /v && mkdir /v/home && chown 1000:1000 /v/home && chmod 0777 /v/home']);
  const { DEVBOX_TEST_MOUNT_DELAY, ...env } = ENV;   // the volume is there at start, as on a microVM
  docker(['run', '-d', '--name', name, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '-p', `127.0.0.1:${port}:8080`,
    '-v', `${volume}:/mnt/workspace`, ...envArgs(env), IMAGE]);
  const t0 = Date.now();
  const ready = await until('microVM box: vscode ready', async () => {
    const { json } = await invoke({ v: 1, op: 'status' }, SESSION, port);
    return json.vscode === 'ready' ? json : null;
  }, 180_000).catch(() => null);
  const logs = () => { const l = docker(['logs', name], { allowFail: true }); return `${l.out}\n${l.err}`; };
  check('microVM stand-in (--cap-drop ALL, no-new-privileges, EFS-like 1000:1000 volume): VS Code reaches ready',
    ready, ready ? `${((Date.now() - t0) / 1000).toFixed(1)} s` : logs().split('\n').filter((l) => l.startsWith('[devbox]')).slice(-8).join(' | '));
  if (!ready) return;
  const client = await connect({ port, sessionId: SESSION, serverRoot: SERVER_ROOT, commit: COMMIT });
  const home = await client.call('remoteFilesystem', 'stat', [{ $mid: 1, scheme: 'vscode-remote', authority: 'smoke', path: '/mnt/workspace/home' }]);
  client.close();
  check('microVM stand-in: the VS Code handshake through /ws works and the server sees the workspace', home && home.type === 2, JSON.stringify(home));
  const diag = (await invoke({ v: 1, op: 'diag' }, SESSION, port)).json;
  facts.microVm = { vscodeProcess: diag.vscodeProcess, workspace: diag.workspace };
  check('microVM stand-in: single-user mode (VS Code runs as this uid 0) on a volume owned 1000:1000',
    diag.vscodeProcess?.uid === 0 && diag.workspace?.uid === 1000 && diag.workspace?.gid === 1000, JSON.stringify(facts.microVm));
  const log = logs();
  const refused = log.split('\n').filter((l) => /can't ch(own|mod)/.test(l));
  facts.microVm.refused = refused;
  check("microVM stand-in: the supervisor logs the refused chmod of home (EPERM) and carries on",
    /single-user mode/.test(log) && refused.some((l) => /can't chmod \/mnt\/workspace\/home to 700 \(EPERM/.test(l)), refused.join(' | '));
  await checkDevboxClaude(name, { sessionUser: 'root', uid: 0, secondFrom: 'root', resume: null });
  check('microVM stand-in: no token or auth header value in the container log', !/not-a-real-token|Bearer /.test(logs()));
}

let failed = false;
try {
  await main();
} catch (err) {
  failed = true;
  console.log(`FAIL  smoke test aborted: ${err.stack || err.message}`);
} finally {
  if (!args.has('--keep')) {
    for (const clean of cleanups) clean();
  } else {
    console.log(`kept containers ${NAME}, ${NAME}-microvm and volumes ${VOLUME}, ${VOLUME}-efs`);
  }
}
console.log('\nfacts:', JSON.stringify(facts, null, 2));
const bad = results.filter((r) => !r.ok);
console.log(`\n${results.length - bad.length}/${results.length} checks passed${failed ? ' (aborted early)' : ''}`);
process.exit(bad.length || failed ? 1 : 0);
