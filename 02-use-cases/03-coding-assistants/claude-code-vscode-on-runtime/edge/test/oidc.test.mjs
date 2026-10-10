import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { fakeTime, makeSandbox, runScript } from './sandbox.mjs';

const ISSUER = 'https://example.okta.com/oauth2/default';
const ORIGIN = 'https://d111111abcdef8.cloudfront.net';

const plain = value => JSON.parse(JSON.stringify(value));

function jwt(payload) {
  const enc = obj => Buffer.from(JSON.stringify(obj)).toString('base64url');
  return `${enc({ alg: 'RS256', kid: 'k' })}.${enc(payload)}.c2lnbmF0dXJl`;
}

class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

let Oidc;
let env;

beforeEach(() => {
  const ctx = makeSandbox({});
  runScript(ctx, 'devbox/oidc.js');
  Oidc = ctx.DevboxOidc;
  const clock = fakeTime(1_700_000_000_000);
  env = {
    clock,
    storage: new MemoryStorage(),
    assigned: [],
    replaced: [],
    requests: [],
    responses: [],
    forms: [],
    sessionEnded: [],
    location: { pathname: '/', hash: '', assign(url) { env.assigned.push(url); } },
  };
});

function makeClient(overrides = {}) {
  return Oidc.create({
    issuer: ISSUER,
    clientId: '0oa-client',
    scopes: 'openid profile email offline_access devbox',
    redirectUri: `${ORIGIN}/callback`,
    postLogoutRedirectUri: `${ORIGIN}/`,
    crypto: globalThis.crypto,
    location: env.location,
    history: { replaceState: (_s, _t, url) => env.replaced.push(url) },
    storage: env.storage,
    now: env.clock.now,
    setTimeout: env.clock.setTimeout,
    clearTimeout: env.clock.clearTimeout,
    fetch: async (url, init) => {
      env.requests.push({ url, init, form: Object.fromEntries(new URLSearchParams(init.body)) });
      const next = env.responses.shift();
      if (next instanceof Error) throw next;
      return new Response(JSON.stringify(next.body ?? {}), { status: next.status ?? 200 });
    },
    document: {
      body: { appendChild: el => env.forms.push(el) },
      createElement: tag => ({ tag, children: [], appendChild(c) { this.children.push(c); }, submit() { this.submitted = true; } }),
    },
    onSessionEnded: err => env.sessionEnded.push(err),
    ...overrides,
  });
}

async function startAndReturn(client, { promptNone = false, returnTo } = {}) {
  await client.signIn({ promptNone, returnTo });
  const url = new URL(env.assigned.at(-1));
  const state = url.searchParams.get('state');
  env.location.pathname = '/callback';
  return { url, state };
}

describe('encoding helpers', () => {
  test('base64url has no padding and uses - and _', () => {
    assert.equal(Oidc.base64Url(new Uint8Array([0xfb, 0xff, 0xfe])), '-__-');
    assert.equal(Oidc.base64Url(new Uint8Array([1])), 'AQ');
    assert.deepEqual(Array.from(Oidc.base64UrlDecode('-__-')), [0xfb, 0xff, 0xfe]);
  });

  test('PKCE S256 matches RFC 7636 appendix B', async () => {
    const challenge = await Oidc.pkceChallenge(globalThis.crypto, 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk');
    assert.equal(challenge, 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
  });

  test('decodes JWT payloads, including UTF-8', () => {
    assert.deepEqual(plain(Oidc.decodeJwtPayload(jwt({ uid: '00u1', name: 'Ada Lovelace ✓' }))), { uid: '00u1', name: 'Ada Lovelace ✓' });
    assert.throws(() => Oidc.decodeJwtPayload('not-a-jwt'));
  });
});

describe('sign-in', () => {
  test('redirects to /v1/authorize with PKCE S256 and response_mode=fragment', async () => {
    const client = makeClient();
    await client.signIn({ promptNone: false });
    const url = new URL(env.assigned[0]);
    assert.equal(url.origin + url.pathname, `${ISSUER}/v1/authorize`);
    const p = url.searchParams;
    assert.equal(p.get('client_id'), '0oa-client');
    assert.equal(p.get('response_type'), 'code');
    assert.equal(p.get('response_mode'), 'fragment');
    assert.equal(p.get('scope'), 'openid profile email offline_access devbox');
    assert.equal(p.get('redirect_uri'), `${ORIGIN}/callback`);
    assert.equal(p.get('code_challenge_method'), 'S256');
    assert.equal(p.get('prompt'), null);
    const txn = JSON.parse(env.storage.getItem('devbox-oidc-txn'));
    assert.equal(txn.verifier.length, 43);
    assert.match(txn.verifier, /^[A-Za-z0-9_-]+$/);
    assert.equal(p.get('code_challenge'), await Oidc.pkceChallenge(globalThis.crypto, txn.verifier));
    assert.equal(p.get('state'), txn.state);
    assert.equal(p.get('nonce'), txn.nonce);
  });

  test('prompt=none on a reload', async () => {
    await makeClient().signIn({ promptNone: true });
    assert.equal(new URL(env.assigned[0]).searchParams.get('prompt'), 'none');
  });

  test('a page load that is not a callback is left alone', async () => {
    const client = makeClient();
    assert.equal(await client.handleRedirect(), null);
    env.location.pathname = '/callback';
    assert.equal(await client.handleRedirect(), null, 'no code or error in the fragment');
    assert.equal(env.replaced.length, 0);
  });

  test('completes the code exchange and keeps tokens in memory only', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client);
    const txn = JSON.parse(env.storage.getItem('devbox-oidc-txn'));
    const access = jwt({ uid: '00u-ada', scp: ['devbox'] });
    const id = jwt({ nonce: txn.nonce, sub: '00u-ada' });
    env.location.hash = `#code=the-code&state=${state}`;
    env.responses.push({ body: { access_token: access, id_token: id, refresh_token: 'rt-1', expires_in: 3600, token_type: 'Bearer' } });

    const result = await client.handleRedirect();
    assert.equal(result.ok, true);
    assert.equal(env.requests[0].url, `${ISSUER}/v1/token`);
    assert.equal(env.requests[0].init.method, 'POST');
    assert.deepEqual(env.requests[0].form, {
      grant_type: 'authorization_code', client_id: '0oa-client', code: 'the-code', redirect_uri: `${ORIGIN}/callback`, code_verifier: txn.verifier,
    });
    assert.deepEqual(env.replaced, ['/'], 'the code is dropped from the address bar');
    assert.equal(env.storage.getItem('devbox-oidc-txn'), null, 'the transaction is single-use');
    assert.equal(client.getAccessToken(), access);
    assert.equal(client.accessTokenClaims().uid, '00u-ada');
    for (const value of env.storage.map.values()) {
      for (const secret of [access, id, 'rt-1']) assert.ok(!value.includes(secret), 'no token in sessionStorage');
    }
  });

  test('comes back to the path the sign-in started from (the terminal), not to /', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client, { returnTo: '/terminal' });
    const txn = JSON.parse(env.storage.getItem('devbox-oidc-txn'));
    assert.equal(txn.returnTo, '/terminal');
    assert.equal(new URL(env.assigned[0]).searchParams.get('redirect_uri'), `${ORIGIN}/callback`, 'Okta still returns to the one registered URI');
    env.location.hash = `#code=the-code&state=${state}`;
    env.responses.push({ body: { access_token: jwt({ uid: '00u-ada' }), id_token: jwt({ nonce: txn.nonce }), expires_in: 3600 } });
    const result = await client.handleRedirect();
    assert.equal(result.ok, true);
    assert.equal(result.returnTo, '/terminal');
    assert.deepEqual(env.replaced, ['/terminal']);
  });

  test('only a single-segment path of this site is a return path', async () => {
    for (const bad of ['//evil.example', 'https://evil.example/', '/a/b', '/terminal?x=1', '/terminal#x', '/Terminal', 'terminal', '/%2e%2e', null]) {
      env.replaced.length = 0;
      env.location.pathname = '/';
      const client = makeClient();
      const { state } = await startAndReturn(client, { returnTo: bad });
      assert.equal(JSON.parse(env.storage.getItem('devbox-oidc-txn')).returnTo, '/', String(bad));
      env.location.hash = `#error=access_denied&state=${state}`;
      const result = await client.handleRedirect();
      assert.equal(result.returnTo, '/', String(bad));
      assert.deepEqual(env.replaced, ['/'], String(bad));
    }
  });

  test('a tampered transaction still comes back to /', async () => {
    env.location.pathname = '/callback';
    env.location.hash = '#code=c&state=s';
    env.storage.setItem('devbox-oidc-txn', JSON.stringify({ state: 's', returnTo: '//evil.example' }));
    env.responses.push({ status: 400, body: { error: 'invalid_grant' } });
    await makeClient().handleRedirect().catch(() => {});
    assert.deepEqual(env.replaced, ['/']);
    env.replaced.length = 0;
    env.storage.setItem('devbox-oidc-txn', '{not json');
    assert.equal((await makeClient().handleRedirect()).error, 'no_transaction');
    assert.deepEqual(env.replaced, ['/']);
  });

  test('an interactive retry keeps the return path', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client, { promptNone: true, returnTo: '/terminal' });
    env.location.hash = `#error=login_required&state=${state}`;
    const result = await client.handleRedirect();
    assert.equal(result.needsInteraction, true);
    assert.equal(result.returnTo, '/terminal');
    assert.deepEqual(env.replaced, ['/terminal']);
  });

  test('rejects a mismatched state or nonce', async () => {
    let client = makeClient();
    await startAndReturn(client);
    env.location.hash = '#code=c&state=forged';
    assert.equal((await client.handleRedirect()).error, 'state_mismatch');
    assert.equal(env.requests.length, 0);

    client = makeClient();
    const { state } = await startAndReturn(client);
    env.location.hash = `#code=c&state=${state}`;
    env.responses.push({ body: { access_token: jwt({ uid: 'x' }), id_token: jwt({ nonce: 'other' }), expires_in: 3600 } });
    assert.equal((await client.handleRedirect()).error, 'nonce_mismatch');
    assert.equal(client.getAccessToken(), '');
  });

  test('a callback without a transaction from this tab is refused', async () => {
    env.location.pathname = '/callback';
    env.location.hash = '#code=c&state=s';
    assert.equal((await makeClient().handleRedirect()).error, 'no_transaction');
  });

  test('login_required after prompt=none asks for an interactive sign-in', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client, { promptNone: true });
    env.location.hash = `#error=login_required&error_description=Login+required&state=${state}`;
    const result = await client.handleRedirect();
    assert.equal(result.error, 'login_required');
    assert.equal(result.needsInteraction, true);
  });

  test('other errors are reported, not retried', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client, { promptNone: false });
    env.location.hash = `#error=access_denied&error_description=Not+assigned&state=${state}`;
    const result = await client.handleRedirect();
    assert.equal(result.needsInteraction, false);
    assert.equal(result.description, 'Not assigned');
  });

  test('a failed code exchange throws with the Okta error', async () => {
    const client = makeClient();
    const { state } = await startAndReturn(client);
    env.location.hash = `#code=c&state=${state}`;
    env.responses.push({ status: 400, body: { error: 'invalid_grant', error_description: 'The authorization code is invalid' } });
    await assert.rejects(client.handleRedirect(), /authorization code is invalid/);
  });
});

async function signedInClient(expiresIn = 3600) {
  const client = makeClient();
  const { state } = await startAndReturn(client);
  const txn = JSON.parse(env.storage.getItem('devbox-oidc-txn'));
  env.location.hash = `#code=c&state=${state}`;
  env.responses.push({ body: { access_token: jwt({ uid: 'u', n: 1 }), id_token: jwt({ nonce: txn.nonce }), refresh_token: 'rt-1', expires_in: expiresIn } });
  await client.handleRedirect();
  env.requests = [];
  return client;
}

describe('refresh', () => {
  test('refreshes at 75% of the token lifetime and rotates the refresh token', async () => {
    const client = await signedInClient(3600);
    const notified = [];
    client.onTokens(() => notified.push(client.getAccessToken()));
    const next = jwt({ uid: 'u', n: 2 });
    env.responses.push({ body: { access_token: next, refresh_token: 'rt-2', expires_in: 3600 } });
    env.clock.advance(2700 * 1000 - 1);
    assert.equal(env.requests.length, 0);
    env.clock.advance(1);
    await new Promise(r => setImmediate(r));
    assert.equal(env.requests.length, 1);
    assert.deepEqual(env.requests[0].form, { grant_type: 'refresh_token', client_id: '0oa-client', refresh_token: 'rt-1' });
    assert.equal(client.getAccessToken(), next);
    assert.deepEqual(notified, [next]);

    env.responses.push({ body: { access_token: jwt({ uid: 'u', n: 3 }), refresh_token: 'rt-3', expires_in: 3600 } });
    env.clock.advance(2700 * 1000);
    await new Promise(r => setImmediate(r));
    assert.equal(env.requests[1].form.refresh_token, 'rt-2', 'uses the rotated token');
  });

  test('an invalid_grant ends the session', async () => {
    const client = await signedInClient();
    env.responses.push({ status: 400, body: { error: 'invalid_grant' } });
    await assert.rejects(client.refresh());
    assert.equal(env.sessionEnded.length, 1);
    await assert.rejects(client.refresh(), /no refresh token/);
  });

  test('a network failure is retried in 30 s while the token is still valid', async () => {
    const client = await signedInClient();
    env.responses.push(new TypeError('offline'));
    await assert.rejects(client.refresh());
    assert.equal(env.sessionEnded.length, 0);
    env.responses.push({ body: { access_token: jwt({ uid: 'u', n: 9 }), refresh_token: 'rt-9', expires_in: 3600 } });
    env.clock.advance(30000);
    await new Promise(r => setImmediate(r));
    assert.equal(client.accessTokenClaims().n, 9);
  });

  test('concurrent refreshes share one request', async () => {
    const client = await signedInClient();
    env.responses.push({ body: { access_token: jwt({ uid: 'u', n: 4 }), refresh_token: 'rt-4', expires_in: 3600 } });
    await Promise.all([client.refresh(), client.refresh(), client.ensureFresh(10 * 3600 * 1000)]);
    assert.equal(env.requests.length, 1);
  });

  test('ensureFresh only refreshes when the token is close to expiry', async () => {
    const client = await signedInClient(3600);
    await client.ensureFresh(5 * 60 * 1000);
    assert.equal(env.requests.length, 0, 'an hour left: nothing to do');
    env.responses.push({ body: { access_token: jwt({ uid: 'u', n: 5 }), refresh_token: 'rt-5', expires_in: 3600 } });
    await client.ensureFresh(3600 * 1000 + 1);
    assert.equal(env.requests.length, 1, 'expires within the window: refreshed');
    assert.equal(client.accessTokenClaims().n, 5);
  });
});

describe('sign-out', () => {
  test('revokes the refresh token, then POSTs to /v1/logout (no token in a URL)', async () => {
    const client = await signedInClient();
    env.responses.push({ status: 200, body: {} });
    await client.signOut();
    assert.equal(env.requests[0].url, `${ISSUER}/v1/revoke`);
    assert.deepEqual(env.requests[0].form, { token: 'rt-1', token_type_hint: 'refresh_token', client_id: '0oa-client' });
    const form = env.forms[0];
    assert.equal(form.method, 'POST');
    assert.equal(form.action, `${ISSUER}/v1/logout`);
    assert.equal(form.submitted, true);
    const fields = Object.fromEntries(form.children.map(c => [c.name, c.value]));
    assert.deepEqual(Object.keys(fields).sort(), ['id_token_hint', 'post_logout_redirect_uri']);
    assert.equal(fields.post_logout_redirect_uri, `${ORIGIN}/`);
    assert.ok(form.children.every(c => c.type === 'hidden'));
    assert.equal(client.getAccessToken(), '');
    assert.ok(env.assigned.every(url => !url.includes(fields.id_token_hint)));
    assert.equal(client.consumeSignedOut(), true);
    assert.equal(client.consumeSignedOut(), false, 'the flag is read once');
  });

  test('still signs out of Okta when revocation fails', async () => {
    const client = await signedInClient();
    env.responses.push(new TypeError('offline'));
    await client.signOut();
    assert.equal(env.forms[0].submitted, true);
  });
});
