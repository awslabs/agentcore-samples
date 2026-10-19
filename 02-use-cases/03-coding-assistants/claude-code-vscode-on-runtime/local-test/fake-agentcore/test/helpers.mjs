import crypto from 'node:crypto';
import WebSocket from 'ws';
import { createFakeOkta } from '../../fake-okta/server.mjs';
import { createFakeAgentCore } from '../server.mjs';
import { startFakeBox } from './fake-box.mjs';
import { runtimeEntry, SPA_CLIENT_ID, USERS } from '../../config/stack.mjs';
import { sessionIdFor, bearerSubprotocols, base64url } from '../../lib/ids.mjs';
import { signIn } from '../../lib/oidc-client.mjs';

export const ARN = 'arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/devbox_ada-T3stRuntim';
export const ADA_SESSION = sessionIdFor(USERS.ada.uid, 1);
export const WB = 'http://localhost:9402';
export const REDIRECT = `${WB}/callback`;
export const quiet = () => {};

// Fake Okta + a fake box + fake AgentCore wired together on the given ports.
export async function startStack({ oktaPort, boxPort, acPort, settings = {}, runtime = {}, okta: sharedOkta, box: sharedBox }) {
  const okta = sharedOkta ?? createFakeOkta({ issuer: `http://127.0.0.1:${oktaPort}/oauth2/default`, workbenchOrigin: WB, log: quiet });
  if (!sharedOkta) await okta.listen(oktaPort);
  const box = sharedBox ?? await startFakeBox(boxPort);
  const config = {
    runtimes: [runtimeEntry({ runtimeArn: ARN, boxUrl: box.url, ownerUid: USERS.ada.uid, issuer: okta.issuer, clientId: SPA_CLIENT_ID, ...runtime })],
  };
  const logs = [];
  const ac = createFakeAgentCore({ config, settings: { publicBase: `http://127.0.0.1:${acPort}`, ...settings }, log: (l) => logs.push(l) });
  await ac.listen(acPort);
  const base = `http://127.0.0.1:${acPort}`;
  return {
    okta, box, ac, base, logs,
    async close() {
      await ac.close();
      if (!sharedBox) await box.close();
      if (!sharedOkta) await okta.close();
    },
  };
}

export async function tokenFor(okta, user, clientId = SPA_CLIENT_ID, redirectUri = REDIRECT) {
  return (await signIn({ issuer: okta.issuer, clientId, redirectUri, loginHint: user })).access_token;
}

export function invocationsUrl(base, arn = ARN, query = 'qualifier=DEFAULT') {
  return `${base}/runtimes/${encodeURIComponent(arn)}/invocations${query ? `?${query}` : ''}`;
}

export async function invoke(base, { token, sessionId = ADA_SESSION, body = { v: 1, op: 'status' }, headers = {}, arn = ARN, url } = {}) {
  const h = { 'content-type': 'application/json', accept: 'application/json', ...headers };
  if (token) h.authorization = `Bearer ${token}`;
  if (sessionId) h['x-amzn-bedrock-agentcore-runtime-session-id'] = sessionId;
  const res = await fetch(url ?? invocationsUrl(base, arn), { method: 'POST', headers: h, body: typeof body === 'string' ? body : JSON.stringify(body) });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: res.status, headers: res.headers, text, json };
}

export function vscodePath(pathAndQuery) {
  return base64url(pathAndQuery);
}

export function wsUrl(base, { arn = ARN, sessionId = ADA_SESSION, custom = {}, extra = {} } = {}) {
  const q = new URLSearchParams({ qualifier: 'DEFAULT' });
  if (sessionId) q.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sessionId);
  for (const [k, v] of Object.entries(custom)) q.set(`X-Amzn-Bedrock-AgentCore-Runtime-Custom-${k}`, v);
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  return `${base.replace(/^http/, 'ws')}/runtimes/${encodeURIComponent(arn)}/ws?${q}`;
}

// Resolves { ws, protocol } when open, or { status, errorType } when the upgrade is refused.
export function openWs(url, { token, protocols, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const offered = protocols ?? (token ? bearerSubprotocols(token) : undefined);
    const ws = new WebSocket(url, offered, { headers, perMessageDeflate: false });
    ws.once('open', () => resolve({ ws, protocol: ws.protocol }));
    ws.once('unexpected-response', (_req, res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, errorType: res.headers['x-amzn-errortype'], body: Buffer.concat(chunks).toString() }));
    });
    ws.once('error', (err) => { if (ws.readyState !== WebSocket.CLOSED) reject(err); });
  });
}

export function nextClose(ws) {
  return new Promise((resolve) => ws.once('close', (code, reason) => resolve({ code, reason: reason.toString() })));
}

export function nextMessage(ws) {
  return new Promise((resolve) => ws.once('message', (data, isBinary) => resolve({ data, isBinary })));
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const randomSession = () => `test-${crypto.randomBytes(20).toString('hex')}`;
