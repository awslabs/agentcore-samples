// Browserless smoke check of a running stack (./run.sh smoke runs it against the stub box; it also works
// against the real box). Signs in through fake Okta with PKCE, calls op:status through fake AgentCore,
// opens the /ws the way the shim does, and checks the authorizer and the resource policy refuse what they must.
import assert from 'node:assert/strict';
import { signIn } from '../lib/oidc-client.mjs';
import { sessionIdFor, bearerSubprotocols, base64url } from '../lib/ids.mjs';
import { ISSUER, SPA_CLIENT_ID, ORIGINS, BOXES, USERS, SERVER_ROOT } from '../config/stack.mjs';

const box = BOXES[0];
const sessionId = sessionIdFor(USERS[box.name].uid, box.generation);
const base = `${ORIGINS.agentcore}/runtimes/${encodeURIComponent(box.runtimeArn)}`;
const redirectUri = `${ORIGINS.workbench}/callback`;

async function call(token, body, { op = 'invocations', session = sessionId } = {}) {
  const res = await fetch(`${base}/${op}?qualifier=DEFAULT`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', accept: 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': session },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(120_000),
  });
  return { status: res.status, type: res.headers.get('x-amzn-errortype'), json: await res.json().catch(() => null) };
}

async function statusWhenWarm(token) {
  const t0 = Date.now();
  for (;;) {
    const r = await call(token, { v: 1, op: 'status' });
    if (r.status !== 409) return r;
    if (Date.now() - t0 > 600_000) throw new Error('still 409 after 10 minutes');
    await new Promise((res) => setTimeout(res, 1000));
  }
}

function openWs(token) {
  const target = `${SERVER_ROOT}?reconnectionToken=${crypto.randomUUID()}&reconnection=false&skipWebSocketFrames=false`;
  const q = new URLSearchParams({
    qualifier: 'DEFAULT',
    'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionId,
    'X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath': base64url(target),
  });
  return new Promise((resolve) => {
    const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws?${q}`, bearerSubprotocols(token));
    ws.onopen = () => resolve({ ws, protocol: ws.protocol });
    ws.onclose = (e) => resolve({ closed: e.code });
  });
}

const step = async (name, fn) => {
  process.stdout.write(`- ${name} ... `);
  await fn();
  console.log('ok');
};

let ada;
await step('ada signs in with PKCE', async () => {
  ada = (await signIn({ issuer: ISSUER, clientId: SPA_CLIENT_ID, redirectUri, loginHint: 'ada' })).access_token;
});
await step("op:status on ada's runtime answers 200 (waiting out the cold start)", async () => {
  const r = await statusWhenWarm(ada);
  assert.equal(r.status, 200, JSON.stringify(r));
  assert.ok(r.json, 'a JSON envelope');
});
await step('the /ws handshake opens with the bearer subprotocol answered', async () => {
  const r = await openWs(ada);
  assert.equal(r.protocol, 'base64UrlBearerAuthorization', `closed with ${r.closed}`);
  r.ws.close();
});
await step('mallory (not in devbox-users) and grace (not the owner) are rejected by the authorizer', async () => {
  for (const user of ['mallory', 'grace']) {
    const token = (await signIn({ issuer: ISSUER, clientId: SPA_CLIENT_ID, redirectUri, loginHint: user })).access_token;
    const r = await call(token, { v: 1, op: 'status' });
    assert.equal(r.status, 401, `${user}: ${JSON.stringify(r)}`);
  }
});
await step('the AgentCore command API is denied by the resource policy', async () => {
  const r = await call(ada, { command: 'id' }, { op: 'commands' });
  assert.equal(r.status, 403);
  assert.equal(r.type, 'AccessDeniedException');
});
console.log('smoke: all checks passed');
