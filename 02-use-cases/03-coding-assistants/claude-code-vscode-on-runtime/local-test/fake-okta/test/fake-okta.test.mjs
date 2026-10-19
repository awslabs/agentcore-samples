import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { createFakeOkta } from '../server.mjs';
import { pkcePair, authorize, tokenRequest, signIn, decodeJwt } from '../../lib/oidc-client.mjs';

const PORT = 9410;
const ISSUER = `http://127.0.0.1:${PORT}/oauth2/default`;
const WB = 'http://localhost:9402';
const SPA = '0oadevboxspafake0001';
const OTHER = '0oaotherappfake00002';
const REDIRECT = `${WB}/callback`;
const SCOPE = 'openid profile email offline_access devbox';

let okta;
let clock = null; // when set, fake Okta's clock (seconds)

before(async () => {
  okta = createFakeOkta({
    issuer: ISSUER, workbenchOrigin: WB, refreshGraceSeconds: 5, log: () => {},
    now: () => clock ?? Math.floor(Date.now() / 1000),
  });
  await okta.listen(PORT);
});
after(() => okta.close());

function verifyWithJwks(token, jwks) {
  const [h, p, s] = token.split('.');
  const { kid, alg } = JSON.parse(Buffer.from(h, 'base64url'));
  assert.equal(alg, 'RS256');
  const jwk = jwks.keys.find((k) => k.kid === kid);
  assert.ok(jwk, 'the token kid is published in /v1/keys');
  const key = crypto.createPublicKey({ key: jwk, format: 'jwk' });
  return crypto.verify('sha256', Buffer.from(`${h}.${p}`), key, Buffer.from(s, 'base64url'));
}

describe('discovery and keys', () => {
  test('discovery document points at the issuer endpoints', async () => {
    const d = await (await fetch(`${ISSUER}/.well-known/openid-configuration`)).json();
    assert.equal(d.issuer, ISSUER);
    assert.equal(d.jwks_uri, `${ISSUER}/v1/keys`);
    assert.equal(d.token_endpoint, `${ISSUER}/v1/token`);
    assert.deepEqual(d.code_challenge_methods_supported, ['S256']);
    assert.ok(d.response_modes_supported.includes('fragment'));
  });

  test('keys are RS256 JWKs and rotation keeps the previous key published', async () => {
    const before = await (await fetch(`${ISSUER}/v1/keys`)).json();
    assert.equal(before.keys[0].kty, 'RSA');
    assert.equal(before.keys[0].alg, 'RS256');
    const oldToken = okta.mintAccessToken();
    okta.rotateKeys();
    const afterRotation = await (await fetch(`${ISSUER}/v1/keys`)).json();
    assert.equal(afterRotation.keys.length, 2);
    assert.ok(verifyWithJwks(oldToken, afterRotation));
    assert.ok(verifyWithJwks(okta.mintAccessToken(), afterRotation));
  });
});

describe('authorization code + PKCE', () => {
  test('happy path (fragment): access token carries the claims the runtime authorizer checks', async () => {
    const tokens = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    assert.equal(tokens.token_type, 'Bearer');
    assert.equal(tokens.expires_in, 3600);
    assert.equal(tokens.scope, SCOPE);
    assert.ok(tokens.refresh_token, 'offline_access gives a refresh token');
    const jwks = await (await fetch(`${ISSUER}/v1/keys`)).json();
    assert.ok(verifyWithJwks(tokens.access_token, jwks), 'signature verifies against /v1/keys');
    const { payload: at } = decodeJwt(tokens.access_token);
    assert.equal(at.iss, ISSUER);
    assert.equal(at.aud, 'api://default');
    assert.equal(at.uid, '00uadalovelace000001');
    assert.equal(at.sub, 'ada.lovelace@example.com');
    assert.equal(at.cid, SPA);
    assert.equal(at.client_id, SPA);
    assert.deepEqual(at.scp, SCOPE.split(' '));
    assert.deepEqual(at.groups, ['devbox-users'], 'groups filtered to the devbox prefix');
    assert.ok(at.exp - at.iat === 3600);
    const { payload: id } = decodeJwt(tokens.id_token);
    assert.equal(id.aud, SPA);
    assert.equal(id.sub, '00uadalovelace000001');
    assert.ok(id.nonce);
  });

  test('the code arrives in the fragment, never in the query', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge });
    assert.equal(a.status, 302);
    const u = new URL(a.location);
    assert.equal(u.search, '');
    assert.ok(new URLSearchParams(u.hash.slice(1)).get('code'));
    assert.equal(a.params.get('state'), a.state);
  });

  test('response_mode=query puts the code in the query', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge, responseMode: 'query' });
    assert.ok(new URL(a.location).searchParams.get('code'));
  });

  test('a wrong code_verifier is rejected, and the code is single use', async () => {
    const { verifier, challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge });
    const code = a.params.get('code');
    const wrong = await tokenRequest(ISSUER, { grant_type: 'authorization_code', client_id: SPA, code, redirect_uri: REDIRECT, code_verifier: pkcePair().verifier });
    assert.equal(wrong.status, 400);
    assert.equal(wrong.body.error, 'invalid_grant');
    const replay = await tokenRequest(ISSUER, { grant_type: 'authorization_code', client_id: SPA, code, redirect_uri: REDIRECT, code_verifier: verifier });
    assert.equal(replay.status, 400, 'a code is burnt after one attempt');
  });

  test('PKCE is required and must be S256', async () => {
    const noChallenge = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge: undefined, method: undefined });
    assert.equal(noChallenge.params.get('error'), 'invalid_request');
    const plain = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge: 'a'.repeat(43), method: 'plain' });
    assert.equal(plain.params.get('error'), 'invalid_request');
  });

  test('the token redirect_uri must match the authorize redirect_uri', async () => {
    const { verifier, challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge });
    const t = await tokenRequest(ISSUER, { grant_type: 'authorization_code', client_id: SPA, code: a.params.get('code'), redirect_uri: `${WB}/other`, code_verifier: verifier });
    assert.equal(t.body.error, 'invalid_grant');
  });

  test('an unregistered redirect_uri gets an error page, not a redirect', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: 'https://evil.example/callback', scope: SCOPE, challenge });
    assert.equal(a.status, 400);
    assert.equal(a.location, null);
  });

  test('codes expire', async () => {
    const { verifier, challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge });
    clock = Math.floor(Date.now() / 1000) + 61;
    try {
      const t = await tokenRequest(ISSUER, { grant_type: 'authorization_code', client_id: SPA, code: a.params.get('code'), redirect_uri: REDIRECT, code_verifier: verifier });
      assert.equal(t.body.error, 'invalid_grant');
    } finally { clock = null; }
  });
});

describe('scopes are an allowlist (access policy rule)', () => {
  test('a known scope outside the SPA rule fails policy evaluation', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: `${SCOPE} phone`, challenge });
    assert.equal(a.params.get('error'), 'access_denied');
  });

  test('an unknown scope is invalid_scope', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: 'openid admin', challenge });
    assert.equal(a.params.get('error'), 'invalid_scope');
  });

  test('another app on the same server can get a devbox-scoped token with its own client_id', async () => {
    const tokens = await signIn({ issuer: ISSUER, clientId: OTHER, redirectUri: 'http://localhost:9499/callback' });
    const { payload } = decodeJwt(tokens.access_token);
    assert.equal(payload.aud, 'api://default', 'same audience: allowedAudience alone would not tell them apart');
    assert.equal(payload.client_id, OTHER);
  });
});

describe('users and the Okta session', () => {
  test('login_hint picks mallory, whose token has no devbox group', async () => {
    const tokens = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, loginHint: 'mallory' });
    const { payload } = decodeJwt(tokens.access_token);
    assert.equal(payload.uid, '00umallory0000000003');
    assert.equal(payload.groups, undefined);
    assert.equal(payload.client_id, SPA);
  });

  test('prompt=none needs an Okta session (without one: a 400 page, no redirect, like real Okta); with the session cookie it signs in silently', async () => {
    const { challenge } = pkcePair();
    const noSession = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge, prompt: 'none' });
    assert.equal(noSession.status, 400);
    assert.equal(noSession.location, null, 'real Okta shows its own error page and never redirects back');

    const s = await fetch(`http://127.0.0.1:${PORT}/_fake/session?user=grace`);
    const cookie = s.headers.get('set-cookie').split(';')[0];
    assert.match(s.headers.get('set-cookie'), /Path=\/oauth2/, 'the fake Okta session cookie never reaches other localhost ports');
    const tokens = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, prompt: 'none', cookie });
    assert.equal(decodeJwt(tokens.access_token).payload.uid, '00ugracehopper0000002');
  });

  test('interactive sign-in without a session uses the configured default user and starts a session', async () => {
    const { challenge } = pkcePair();
    const a = await authorize({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge });
    assert.match(a.setCookie, /fake-okta-sid=/);
  });

  test('with assignment enforced, a user outside devbox-users is refused at /v1/authorize', async () => {
    const strict = createFakeOkta({ issuer: 'http://127.0.0.1:9411/oauth2/default', workbenchOrigin: WB, enforceAssignment: true, log: () => {} });
    await strict.listen(9411);
    try {
      const { challenge } = pkcePair();
      const a = await authorize({ issuer: 'http://127.0.0.1:9411/oauth2/default', clientId: SPA, redirectUri: REDIRECT, scope: SCOPE, challenge, loginHint: 'mallory' });
      assert.equal(a.params.get('error'), 'access_denied');
      await signIn({ issuer: 'http://127.0.0.1:9411/oauth2/default', clientId: SPA, redirectUri: REDIRECT, loginHint: 'ada' });
    } finally { await strict.close(); }
  });
});

describe('refresh tokens rotate', () => {
  const refresh = (rt, extra = {}) => tokenRequest(ISSUER, { grant_type: 'refresh_token', client_id: SPA, refresh_token: rt, ...extra });

  test('each refresh returns a new refresh token and a fresh access token', async () => {
    const first = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    const r1 = await refresh(first.refresh_token);
    assert.equal(r1.status, 200);
    assert.notEqual(r1.body.refresh_token, first.refresh_token);
    assert.notEqual(decodeJwt(r1.body.access_token).payload.jti, decodeJwt(first.access_token).payload.jti);
    assert.ok(r1.body.id_token);
    const r2 = await refresh(r1.body.refresh_token);
    assert.equal(r2.status, 200);
  });

  test('reuse within the grace period is allowed; after it the whole family is revoked', async () => {
    const first = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    const r1 = await refresh(first.refresh_token);
    const again = await refresh(first.refresh_token);
    assert.equal(again.status, 200, 'inside the 5 s grace');
    clock = Math.floor(Date.now() / 1000) + 10;
    try {
      const stale = await refresh(first.refresh_token);
      assert.equal(stale.status, 400);
      assert.equal(stale.body.error, 'invalid_grant');
      const newest = await refresh(r1.body.refresh_token);
      assert.equal(newest.status, 400, 'reuse detection revokes the newer tokens too');
    } finally { clock = null; }
  });

  test('a refresh may narrow scopes but not widen them', async () => {
    const first = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: 'openid offline_access devbox' });
    const wider = await refresh(first.refresh_token, { scope: 'openid offline_access devbox profile' });
    assert.equal(wider.body.error, 'invalid_scope');
  });

  test('revoking the refresh token ends the family', async () => {
    const first = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    const rv = await fetch(`${ISSUER}/v1/revoke`, {
      method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ token: first.refresh_token, token_type_hint: 'refresh_token', client_id: SPA }),
    });
    assert.equal(rv.status, 200);
    assert.equal((await refresh(first.refresh_token)).status, 400);
  });

  test('no offline_access, no refresh token', async () => {
    const t = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT, scope: 'openid devbox' });
    assert.equal(t.refresh_token, undefined);
  });
});

describe('CORS and logout', () => {
  test('the token endpoint answers CORS for the workbench origin only', async () => {
    const pre = await fetch(`${ISSUER}/v1/token`, { method: 'OPTIONS', headers: { origin: WB, 'access-control-request-method': 'POST', 'access-control-request-headers': 'content-type' } });
    assert.equal(pre.headers.get('access-control-allow-origin'), WB);
    const evil = await fetch(`${ISSUER}/v1/token`, { method: 'OPTIONS', headers: { origin: 'http://localhost:9403', 'access-control-request-method': 'POST' } });
    assert.equal(evil.headers.get('access-control-allow-origin'), null, 'the webview origin is not trusted');
    const t = await tokenRequest(ISSUER, { grant_type: 'refresh_token', client_id: SPA, refresh_token: 'nope' }, { origin: WB });
    assert.equal(t.headers.get('access-control-allow-origin'), WB);
  });

  test('logout needs a valid id_token_hint and a registered post-logout URI, and clears the session', async () => {
    const tokens = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    const ok = await fetch(`${ISSUER}/v1/logout?${new URLSearchParams({ id_token_hint: tokens.id_token, post_logout_redirect_uri: `${WB}/`, state: 's1' })}`, { redirect: 'manual' });
    assert.equal(ok.status, 302);
    assert.equal(ok.headers.get('location'), `${WB}/?state=s1`);
    assert.match(ok.headers.get('set-cookie'), /Max-Age=0/);
    const bad = await fetch(`${ISSUER}/v1/logout?${new URLSearchParams({ id_token_hint: tokens.id_token, post_logout_redirect_uri: 'https://evil.example/' })}`, { redirect: 'manual' });
    assert.equal(bad.status, 400);
    const noHint = await fetch(`${ISSUER}/v1/logout`, { redirect: 'manual' });
    assert.equal(noHint.status, 400);
  });

  test('logout also takes a form POST, so the ID token stays out of the URL', async () => {
    const tokens = await signIn({ issuer: ISSUER, clientId: SPA, redirectUri: REDIRECT });
    const post = (fields) => fetch(`${ISSUER}/v1/logout`, {
      method: 'POST', redirect: 'manual', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(fields),
    });
    const ok = await post({ id_token_hint: tokens.id_token, post_logout_redirect_uri: `${WB}/` });
    assert.equal(ok.status, 302);
    assert.equal(ok.headers.get('location'), `${WB}/`);
    assert.equal((await post({ post_logout_redirect_uri: `${WB}/` })).status, 400, 'still needs id_token_hint');
  });
});
