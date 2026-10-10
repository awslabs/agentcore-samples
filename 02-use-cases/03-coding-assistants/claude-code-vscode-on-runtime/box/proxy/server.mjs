// Dev box proxy entry point. Started by devbox-entrypoint as uid 1001 with openvscode-server's
// bundled node. Everything it knows about the box comes from the root supervisor's state file.

import fs from 'node:fs';
import { createApp, readJson } from './lib/app.mjs';

const OVS_ROOT = process.env.DEVBOX_OVS_ROOT || '/opt/openvscode-server';
const product = readJson(`${OVS_ROOT}/product.json`, {});
const commit = product.commit || '072586267e68ece9a47aa43f8c108e0dcbf44622';
const quality = product.quality || 'stable';

const config = {
  owner: process.env.DEVBOX_OWNER || '',
  sessionId: process.env.DEVBOX_SESSION_ID || '',
  commit,
  serverRoot: `/${quality}-${commit}`,
  stateFile: process.env.DEVBOX_STATE_FILE || '/run/devbox/state.json',
  upstream: { host: '127.0.0.1', port: Number(process.env.DEVBOX_VSCODE_PORT || 3000) },
};
const buildInfo = readJson(process.env.DEVBOX_BUILD_INFO || '/opt/devbox/build-info.json', null);
const workspace = process.env.DEVBOX_WORKSPACE || '/mnt/workspace';

function workspaceStat() {
  try {
    const st = fs.statSync(workspace);
    const mount = fs.readFileSync('/proc/self/mountinfo', 'utf8').split('\n')
      .map((line) => line.split(' '))
      .find((f) => f[4] === workspace);
    const dash = mount ? mount.indexOf('-') : -1;
    return {
      path: workspace,
      mode: (st.mode & 0o7777).toString(8),
      uid: st.uid,
      gid: st.gid,
      mount: mount ? { fsType: mount[dash + 1], source: mount[dash + 2], options: mount[5] } : null,
    };
  } catch (err) {
    return { path: workspace, error: err.code ?? err.message };
  }
}

const { server } = createApp(config, {
  diagExtra: (state) => ({
    vscodeProcess: state.vscodeProcess ?? null,
    workspace: workspaceStat(),
    userNamespace: state.userNamespace ?? null,
    supervisor: state.supervisor ?? null,
    build: buildInfo,
  }),
});

// In the box the root supervisor owns the listening socket and passes it in (DEVBOX_LISTEN_FD).
const listenFd = process.env.DEVBOX_LISTEN_FD;
const port = Number(process.env.DEVBOX_PROXY_PORT || 8080);
const onListening = () => console.log(`[proxy] listening (${listenFd ? `fd ${listenFd}` : `0.0.0.0:${port}`}), server root ${config.serverRoot}`);
if (listenFd) server.listen({ fd: Number(listenFd) }, onListening);
else server.listen(port, '0.0.0.0', onListening);

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => {
    server.close();
    process.exit(0);
  });
}
