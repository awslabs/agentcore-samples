// POST /invocations and the other HTTP operations: the authorizer, the session id, header forwarding,
// 424 masking, CORS, cold start, and the resource policy. Ports 9420-9427.
import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createFakeOkta } from '../../fake-okta/server.mjs';
import { OTHER_CLIENT_ID, USERS } from '../../config/stack.mjs';
import { startStack, tokenFor, invoke, invocationsUrl, ARN, ADA_SESSION, quiet, WB, sleep, randomSession } from './helpers.mjs';

let stack;
let ada;

before(async () => {
  stack = await startStack({ oktaPort: 9420, boxPort: 9421, acPort: 9422 });
  ada = await tokenFor(stack.okta, 'ada');
});
after(() => stack.close());

const lastInvocation = () => stack.box.seen.invocations.at(-1);

describe('the runtime authorizer', () => {
  test("ada's token from the Dev Box SPA is accepted and reaches the box", async () => {
    const before = stack.box.seen.invocations.length;
    const r = await invoke(stack.base, { token: ada });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json.echo.op, 'status');
    assert.equal(stack.box.seen.invocations.length, before + 1);
  });

  test('no token: 401 with a WWW-Authenticate resource_metadata pointer', async () => {
    const r = await invoke(stack.base, {});
    assert.equal(r.status, 401);
    assert.equal(r.headers.get('x-amzn-errortype'), 'UnauthorizedException');
    assert.match(r.headers.get('www-authenticate'), /^Bearer resource_metadata=".*\/invocations\/\.well-known\/oauth-protected-resource\?qualifier=DEFAULT"$/);
  });

  const rejected = async (token, why) => {
    const before = stack.box.seen.invocations.length;
    const r = await invoke(stack.base, { token });
    assert.equal(r.status, 401, `${why}: expected 401, got ${r.status} ${r.text}`);
    assert.equal(r.headers.get('x-amzn-errortype'), 'UnauthorizedException');
    assert.equal(stack.box.seen.invocations.length, before, `${why}: the box must not be reached`);
    return stack.logs.at(-1);
  };

  test("grace is in devbox-users but is not the owner: rejected by uid EQUALS", async () => {
    const log = await rejected(await tokenFor(stack.okta, 'grace'), 'wrong uid');
    assert.match(log, /custom claim uid did not match/);
  });

  test('mallory is not in devbox-users: rejected by groups CONTAINS_ANY', async () => {
    const log = await rejected(await tokenFor(stack.okta, 'mallory'), 'missing group');
    assert.match(log, /custom claim groups did not match/);
  });

  test("a token another app minted for ada (same aud, devbox scope) is rejected by allowedClients", async () => {
    const other = await tokenFor(stack.okta, 'ada', OTHER_CLIENT_ID, 'http://localhost:9499/callback');
    const log = await rejected(other, 'other client');
    assert.match(log, /client_id claim not allowed/);
  });

  test('allowedClients reads client_id only: an Okta token with just cid is rejected', async () => {
    const log = await rejected(stack.okta.mintAccessToken({ user: 'ada', omit: ['client_id'] }), 'no client_id claim');
    assert.match(log, /client_id claim not allowed/);
  });

  test('without the devbox scope the token is rejected by allowedScopes', async () => {
    const log = await rejected(stack.okta.mintAccessToken({ user: 'ada', scopes: ['openid', 'profile'], claims: { client_id: '0oadevboxspafake0001', groups: ['devbox-users'] } }), 'no scope');
    assert.match(log, /no allowed scope/);
  });

  test('expired, wrong audience, wrong issuer, bad signature and alg none are all rejected', async () => {
    const now = Math.floor(Date.now() / 1000);
    assert.match(await rejected(stack.okta.mintAccessToken({ user: 'ada', claims: { exp: now - 1 } }), 'expired'), /token expired/);
    assert.match(await rejected(stack.okta.mintAccessToken({ user: 'ada', claims: { aud: 'api://other' } }), 'aud'), /audience not allowed/);
    assert.match(await rejected(stack.okta.mintAccessToken({ user: 'ada', claims: { iss: 'http://evil.example/oauth2/default' } }), 'iss'), /issuer/);
    const stranger = createFakeOkta({ issuer: stack.okta.issuer, workbenchOrigin: WB, log: quiet });
    assert.match(await rejected(stranger.mintAccessToken({ user: 'ada' }), 'foreign key'), /unknown signing key/);
    const [h, p] = ada.split('.');
    const forgedPayload = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(p, 'base64url')), uid: USERS.grace.uid })).toString('base64url');
    assert.match(await rejected(`${h}.${forgedPayload}.${ada.split('.')[2]}`, 'tampered'), /bad signature/);
    const none = `${Buffer.from('{"alg":"none"}').toString('base64url')}.${p}.`;
    assert.match(await rejected(none, 'alg none'), /unsupported alg/);
  });

  test('a token signed with a freshly rotated key verifies (JWKS refetch on unknown kid)', async () => {
    stack.okta.rotateKeys();
    await sleep(1100); // the fake refetches JWKS at most once a second
    const r = await invoke(stack.base, { token: stack.okta.mintAccessToken({ user: 'ada' }) });
    assert.equal(r.status, 200, r.text);
  });

  test('a SigV4 Authorization header on a JWT runtime is an authorization method mismatch', async () => {
    const r = await invoke(stack.base, { headers: { authorization: 'AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20260928/us-east-1/bedrock-agentcore/aws4_request' } });
    assert.equal(r.status, 403);
    assert.match(r.json.message, /Authorization method mismatch/);
  });
});

describe('addressing the runtime', () => {
  test('unknown runtime: 403 Missing Authentication Token without a token, 404 with one', async () => {
    const other = ARN.replace('T3stRuntim', 'N0SuchRunt');
    assert.equal((await invoke(stack.base, { arn: other })).json.message, 'Missing Authentication Token');
    const r = await invoke(stack.base, { arn: other, token: ada });
    assert.equal(r.status, 404);
    assert.equal(r.headers.get('x-amzn-errortype'), 'ResourceNotFoundException');
  });

  test('the raw ARN in the path and agent id + accountId both resolve', async () => {
    const raw = await invoke(stack.base, { token: ada, url: `${stack.base}/runtimes/${ARN}/invocations?qualifier=DEFAULT` });
    assert.equal(raw.status, 200);
    const byId = await invoke(stack.base, { token: ada, url: `${stack.base}/runtimes/devbox_ada-T3stRuntim/invocations?qualifier=DEFAULT&accountId=111122223333` });
    assert.equal(byId.status, 200);
  });

  test('only the DEFAULT qualifier exists', async () => {
    const r = await invoke(stack.base, { token: ada, url: invocationsUrl(stack.base, ARN, 'qualifier=beta') });
    assert.equal(r.status, 404);
  });

  test('/invocations takes POST only', async () => {
    const r = await fetch(invocationsUrl(stack.base), { headers: { authorization: `Bearer ${ada}` } });
    assert.equal(r.status, 405);
    assert.equal(r.headers.get('allow'), 'POST');
  });
});

describe('the session id is required and validated', () => {
  test('missing: 400 (the real service would silently create a new box)', async () => {
    const r = await invoke(stack.base, { token: ada, sessionId: null });
    assert.equal(r.status, 400);
    assert.equal(r.headers.get('x-amzn-errortype'), 'ValidationException');
  });

  for (const [label, id] of [['32 chars', 'a'.repeat(32)], ['101 chars', 'a'.repeat(101)], ['leading dash', `-${'a'.repeat(40)}`], ['bad charset', `${'a'.repeat(40)}.x`]]) {
    test(`rejects a session id with ${label}`, async () => {
      assert.equal((await invoke(stack.base, { token: ada, sessionId: id })).status, 400);
    });
  }

  test('accepts 33 and 100 characters', async () => {
    assert.equal((await invoke(stack.base, { token: ada, sessionId: `a${'b'.repeat(32)}` })).status, 200);
    assert.equal((await invoke(stack.base, { token: ada, sessionId: `a${'b'.repeat(99)}` })).status, 200);
  });
});

describe('what the container receives, and what comes back', () => {
  test('forwards the session id, a request id and allowlisted headers only', async () => {
    await invoke(stack.base, {
      token: ada,
      headers: {
        origin: WB, cookie: 'a=b', 'accept-encoding': 'gzip, br', 'user-agent': 'test', referer: `${WB}/`,
        'x-amzn-bedrock-agentcore-runtime-custom-vscodepath': 'L3ZlcnNpb24',
        'x-amzn-bedrock-agentcore-runtime-custom-other': 'not-allowlisted',
        'x-not-allowlisted': '1',
      },
    });
    const h = lastInvocation().headers;
    assert.equal(h['x-amzn-bedrock-agentcore-runtime-session-id'], ADA_SESSION);
    assert.match(h['x-amzn-bedrock-agentcore-runtime-request-id'], /^[0-9a-f-]{36}$/);
    assert.equal(h.authorization, `Bearer ${ada}`, 'Authorization is allowlisted, so it is forwarded');
    assert.equal(h['x-amzn-bedrock-agentcore-runtime-custom-vscodepath'], 'L3ZlcnNpb24');
    assert.equal(h['content-type'], 'application/json');
    for (const name of ['origin', 'cookie', 'accept-encoding', 'user-agent', 'referer', 'x-amzn-bedrock-agentcore-runtime-custom-other', 'x-not-allowlisted']) {
      assert.equal(h[name], undefined, `${name} must not reach the box`);
    }
    assert.equal(lastInvocation().url, '/invocations', 'no query string reaches the box');
    assert.match(stack.logs.at(-1), /dropped: x-amzn-bedrock-agentcore-runtime-custom-other/);
  });

  test('response headers other than Content-Type are dropped; the session id and CORS * are added', async () => {
    const r = await invoke(stack.base, { token: ada });
    assert.equal(r.headers.get('content-type'), 'application/json');
    assert.equal(r.headers.get('etag'), null);
    assert.equal(r.headers.get('set-cookie'), null);
    assert.equal(r.headers.get('cache-control'), null);
    assert.equal(r.headers.get('x-amzn-bedrock-agentcore-runtime-session-id'), ADA_SESSION);
    assert.equal(r.headers.get('access-control-allow-origin'), '*');
    assert.match(r.headers.get('access-control-expose-headers'), /x-amzn-ErrorType/);
  });

  for (const code of [400, 404, 413, 500, 503]) {
    test(`a box ${code} becomes an opaque 424 RuntimeClientError`, async () => {
      const r = await invoke(stack.base, { token: ada, body: { respond: code } });
      assert.equal(r.status, 424);
      assert.equal(r.headers.get('x-amzn-errortype'), 'RuntimeClientError');
      assert.equal(r.json.message, `Received error (${code}) from runtime. Please check your CloudWatch logs for more information.`);
      assert.equal(r.headers.get('x-box-detail'), null);
      assert.doesNotMatch(r.text, /must not reach/);
    });
  }

  test('without Authorization in the allowlist, the box gets no token', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9423, runtime: { allowlist: ['X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath'] } });
    try {
      assert.equal((await invoke(s.base, { token: ada })).status, 200);
      assert.equal(lastInvocation().headers.authorization, undefined);
    } finally { await s.close(); }
  });

  test('a token-like value in a URL query is flagged', async () => {
    const before = stack.ac.snapshot().counters.tokenInUrl;
    await invoke(stack.base, { token: ada, url: invocationsUrl(stack.base, ARN, `qualifier=DEFAULT&t=${ada}`) });
    assert.equal(stack.ac.snapshot().counters.tokenInUrl, before + 1);
    assert.ok(stack.logs.every((l) => !l.includes(ada.split('.')[2])), 'the token itself is never logged');
  });
});

describe('CORS', () => {
  test('preflight from any origin: ACAO *, echoed headers and methods, 2-day max-age', async () => {
    const r = await fetch(invocationsUrl(stack.base), {
      method: 'OPTIONS',
      headers: { origin: 'https://anything.example', 'access-control-request-method': 'POST', 'access-control-request-headers': 'authorization,content-type,x-amzn-bedrock-agentcore-runtime-session-id' },
    });
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('access-control-allow-origin'), '*');
    assert.equal(r.headers.get('access-control-allow-headers'), 'authorization,content-type,x-amzn-bedrock-agentcore-runtime-session-id');
    assert.equal(r.headers.get('access-control-allow-methods'), 'POST');
    assert.equal(r.headers.get('access-control-max-age'), '172800');
  });

  test('errors carry ACAO * too, so the browser can read them', async () => {
    const r = await invoke(stack.base, {});
    assert.equal(r.headers.get('access-control-allow-origin'), '*');
  });
});

describe('cold start', () => {
  test('the first call waits FAKE_AC_COLD_SECONDS; concurrent calls get 409 RetryableConflictException', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9424, settings: { coldSeconds: 1.5 } });
    try {
      const session = randomSession();
      const t0 = Date.now();
      const first = invoke(s.base, { token: ada, sessionId: session });
      await sleep(200);
      const second = await invoke(s.base, { token: ada, sessionId: session });
      assert.equal(second.status, 409);
      assert.equal(second.headers.get('x-amzn-errortype'), 'RetryableConflictException');
      assert.equal(second.json.message, 'Session operation in progress, please retry');
      const other = await invoke(s.base, { token: ada, sessionId: randomSession() }).then((r) => r.status);
      assert.equal((await first).status, 200);
      assert.ok(Date.now() - t0 >= 1500, 'the first call held for the cold start');
      assert.equal(other, 200, 'another session provisions on its own');
      const t1 = Date.now();
      assert.equal((await invoke(s.base, { token: ada, sessionId: session })).status, 200);
      assert.ok(Date.now() - t1 < 1000, 'warm calls are immediate');
      const snap = s.ac.snapshot().sessions.find((x) => x.sessionId === session);
      assert.equal(snap.coldStarts, 1);
      assert.equal(snap.conflicts, 1);
    } finally { await s.close(); }
  });

  test('a rejected token never triggers provisioning', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9425, settings: { coldSeconds: 5 } });
    try {
      const t0 = Date.now();
      assert.equal((await invoke(s.base, { token: await tokenFor(stack.okta, 'grace') })).status, 401);
      assert.ok(Date.now() - t0 < 1000);
      assert.equal(s.ac.snapshot().sessions.length, 0);
    } finally { await s.close(); }
  });
});

describe('resource-based policy', () => {
  const post = (base, op, token = ada) => fetch(`${base}/runtimes/${encodeURIComponent(ARN)}/${op}?qualifier=DEFAULT`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', 'x-amzn-bedrock-agentcore-runtime-session-id': ADA_SESSION },
    body: JSON.stringify({ command: 'id' }),
  });

  test('InvokeAgentRuntimeCommand and StopRuntimeSession are denied even for the owner', async () => {
    for (const op of ['commands', 'stopruntimesession']) {
      const r = await post(stack.base, op);
      assert.equal(r.status, 403, op);
      assert.equal(r.headers.get('x-amzn-errortype'), 'AccessDeniedException');
      assert.match((await r.json()).message, /explicit deny/);
    }
  });

  test('with no policy, stop works and commands are refused as not emulated', async () => {
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9426, settings: { resourcePolicyMode: 'none' } });
    try {
      assert.equal((await post(s.base, 'commands')).status, 501);
      const stop = await post(s.base, 'stopruntimesession');
      assert.equal(stop.status, 200);
      assert.equal((await stop.json()).runtimeSessionId, ADA_SESSION);
    } finally { await s.close(); }
  });

  test('a policy without an Allow for InvokeAgentRuntime denies it implicitly', async () => {
    const policy = { Version: '2012-10-17', Statement: [{ Effect: 'Deny', Principal: '*', Action: 'bedrock-agentcore:InvokeAgentRuntimeCommand', Resource: '*' }] };
    const s = await startStack({ okta: stack.okta, box: stack.box, acPort: 9427, runtime: { resourcePolicy: policy } });
    try {
      assert.equal((await invoke(s.base, { token: ada })).status, 403);
    } finally { await s.close(); }
  });
});
