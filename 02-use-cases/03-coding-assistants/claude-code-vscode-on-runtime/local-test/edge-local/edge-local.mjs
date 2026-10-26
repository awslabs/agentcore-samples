// Runs the edge Lambda as the two local sites: the workbench on :9402 and the webview shell on :9403.
//
// EDGE_LOCAL_MODE=auto (default): use the edge's own edge/src/local-server.mjs when it exports
//   startLocalServer({ site, port, host }) (called once per site), else fall back to cloudfront-sim.
// EDGE_LOCAL_MODE=local-server | cloudfront-sim forces one of them.
// Both get the Lambda's env first: DEVBOX_CONFIG_JSON (from EDGE_LOCAL_CONFIG), WORKBENCH_ORIGIN, WEBVIEW_ORIGIN.
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createDistribution, installLambdaGlobals } from './cloudfront-sim.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EDGE_DIR = process.env.EDGE_DIR || path.resolve(HERE, '..', '..', 'edge');
const CONFIG = process.env.EDGE_LOCAL_CONFIG || path.resolve(HERE, '..', 'generated', 'devbox-config.json');
const MODE = process.env.EDGE_LOCAL_MODE || 'auto';
const HOST = process.env.EDGE_LOCAL_HOST || '127.0.0.1';
const SITES = [
  { site: 'workbench', port: Number(process.env.EDGE_LOCAL_WORKBENCH_PORT || 9402) },
  { site: 'webview', port: Number(process.env.EDGE_LOCAL_WEBVIEW_PORT || 9403) },
];

const config = JSON.parse(readFileSync(CONFIG, 'utf8'));
process.env.DEVBOX_CONFIG_JSON ||= JSON.stringify(config);
process.env.WORKBENCH_ORIGIN ||= `http://localhost:${SITES[0].port}`;
process.env.WEBVIEW_ORIGIN ||= config.webviewOrigin;

async function localServerStarter() {
  const file = path.join(EDGE_DIR, 'src', 'local-server.mjs');
  if (!existsSync(file)) return null;
  const mod = await import(pathToFileURL(file).href);
  return mod.startLocalServer ?? mod.default?.startLocalServer ?? null;
}

async function startWithSim() {
  installLambdaGlobals();
  const mod = await import(pathToFileURL(path.join(EDGE_DIR, 'src', 'handler.mjs')).href);
  const handler = mod.handler ?? mod.default;
  if (typeof handler !== 'function') throw new Error(`${EDGE_DIR}/src/handler.mjs exports no handler`);
  for (const { site, port } of SITES) {
    const server = createDistribution({ site, handler });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, HOST, resolve); });
  }
}

let used = MODE;
const start = MODE === 'cloudfront-sim' ? null : await localServerStarter();
if (start) {
  for (const { site, port } of SITES) await start({ site, port, host: HOST });
  used = 'local-server';
} else if (MODE === 'local-server') {
  throw new Error(`${EDGE_DIR}/src/local-server.mjs is missing or does not export startLocalServer({ site, port, host })`);
} else {
  await startWithSim();
  used = 'cloudfront-sim';
}
console.log(`[edge-local] ${used}: workbench http://localhost:${SITES[0].port}, webview http://localhost:${SITES[1].port} (listening on ${HOST})`);
