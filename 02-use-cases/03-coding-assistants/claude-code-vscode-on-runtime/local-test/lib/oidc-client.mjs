// A headless OIDC client (Authorization Code + PKCE S256) for tests: drives /v1/authorize without a
// browser, catches the redirect, and exchanges the code at /v1/token. Works against fake Okta only
// because fake Okta auto-approves the sign-in.
import crypto from 'node:crypto';

export function pkcePair() {
  const verifier = crypto.randomBytes(32).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

export function readRedirectParams(location, mode = 'fragment') {
  const u = new URL(location);
  return new URLSearchParams(mode === 'fragment' ? u.hash.slice(1) : u.search);
}

// Returns { status, location, params, setCookie } for one /v1/authorize request.
export async function authorize({ issuer, clientId, redirectUri, scope, challenge, state = 'st-' + crypto.randomUUID(),
  responseMode = 'fragment', prompt, loginHint, cookie, nonce = 'n-' + crypto.randomUUID(), method = 'S256' }) {
  const q = new URLSearchParams({ client_id: clientId, response_type: 'code', response_mode: responseMode, scope, redirect_uri: redirectUri, state, nonce });
  if (challenge !== undefined) q.set('code_challenge', challenge);
  if (method) q.set('code_challenge_method', method);
  if (prompt) q.set('prompt', prompt);
  if (loginHint) q.set('login_hint', loginHint);
  const res = await fetch(`${issuer}/v1/authorize?${q}`, { redirect: 'manual', headers: cookie ? { cookie } : {} });
  const location = res.headers.get('location');
  const setCookie = res.headers.get('set-cookie');
  return { status: res.status, location, params: location ? readRedirectParams(location, responseMode) : null, setCookie, state, nonce };
}

export async function tokenRequest(issuer, form, headers = {}) {
  const res = await fetch(`${issuer}/v1/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json', ...headers },
    body: new URLSearchParams(form).toString(),
  });
  return { status: res.status, headers: res.headers, body: await res.json().catch(() => null) };
}

// Full sign-in: returns the token response body (access_token, id_token, refresh_token, ...).
export async function signIn({ issuer, clientId, redirectUri, scope = 'openid profile email offline_access devbox', loginHint, prompt, cookie }) {
  const { verifier, challenge } = pkcePair();
  const a = await authorize({ issuer, clientId, redirectUri, scope, challenge, loginHint, prompt, cookie });
  if (a.status !== 302 || !a.params?.get('code')) {
    throw new Error(`authorize failed: status ${a.status} error ${a.params?.get('error')} ${a.params?.get('error_description') ?? ''}`);
  }
  const t = await tokenRequest(issuer, { grant_type: 'authorization_code', client_id: clientId, code: a.params.get('code'), redirect_uri: redirectUri, code_verifier: verifier });
  if (t.status !== 200) throw new Error(`token exchange failed: ${t.status} ${JSON.stringify(t.body)}`);
  return t.body;
}

export function decodeJwt(token) {
  const [h, p] = token.split('.');
  return { header: JSON.parse(Buffer.from(h, 'base64url')), payload: JSON.parse(Buffer.from(p, 'base64url')) };
}
