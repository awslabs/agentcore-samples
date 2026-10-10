// Fake Okta: an OIDC provider that behaves like an Okta custom authorization server ("default")
// for the Dev Box SPA. Local tests only. RS256 keys are generated at start and never written anywhere.
//
// What it mirrors from real Okta (see /tmp/remote-research/okta-jwt.md):
// - access tokens carry ver, jti, iss, aud, iat, exp, cid, uid, scp (array), auth_time, sub (the login email),
//   plus the custom claims client_id and groups (filtered "starts with devbox"), both only for scope devbox;
// - /v1/authorize needs PKCE S256 and state, supports response_mode query|fragment and prompt none|login;
// - access-policy rules are allowlists: every requested scope must be in the client's rule;
// - SPA refresh tokens rotate, with a grace period, and reusing an old one after the grace revokes the family;
// - CORS only for trusted origins.
// Test knobs: login_hint on /v1/authorize, a fake Okta session cookie set by /_fake/session?user=,
// and FAKE_OKTA_USER (who "signs in" when there is no session).
import http from 'node:http';
import crypto from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SESSION_COOKIE = 'fake-okta-sid';
const MAX_BODY_BYTES = 64 * 1024;

const b64url = (data) => Buffer.from(data).toString('base64url');
const randomToken = (bytes = 32) => crypto.randomBytes(bytes).toString('base64url');

export function loadDirectory(file, vars) {
  const text = readFileSync(file, 'utf8').replace(/\$\{(\w+)\}/g, (_, name) => vars[name] ?? '');
  return JSON.parse(text);
}

function newSigningKey() {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const { n, e } = publicKey.export({ format: 'jwk' });
  // RFC 7638 thumbprint as the kid, like most providers.
  const kid = b64url(crypto.createHash('sha256').update(JSON.stringify({ e, kty: 'RSA', n })).digest());
  return { kid, privateKey, jwk: { kty: 'RSA', alg: 'RS256', kid, use: 'sig', e, n } };
}

export function decodeJwtPayload(token) {
  return JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8'));
}

export function createFakeOkta(options = {}) {
  const opts = {
    issuer: 'http://localhost:9400/oauth2/default',
    workbenchOrigin: 'http://localhost:9402',
    defaultUser: 'ada',
    accessTtlSeconds: 3600,
    idTtlSeconds: 3600,
    codeTtlSeconds: 60,
    refreshTtlSeconds: 12 * 3600,
    refreshIdleSeconds: 2 * 3600,
    refreshGraceSeconds: 30,
    enforceAssignment: false,
    directoryPath: path.join(HERE, 'directory.json'),
    now: () => Math.floor(Date.now() / 1000),
    log: (line) => console.log(`[fake-okta] ${line}`),
    ...options,
  };
  const issuerUrl = new URL(opts.issuer);
  const basePath = issuerUrl.pathname.replace(/\/$/, '');
  const directory = opts.directory ?? loadDirectory(opts.directoryPath, { WORKBENCH_ORIGIN: opts.workbenchOrigin });
  const authServer = directory.authorizationServer;
  const trustedOrigins = new Set(directory.trustedOrigins);

  let keys = [newSigningKey()];
  const codes = new Map();          // code -> grant
  const refreshTokens = new Map();  // token -> { familyId, clientId, userKey, scopes, authTime, idleExpiresAt, usedAt }
  const families = new Map();       // familyId -> { expiresAt, revoked }
  const sessions = new Map();       // fake Okta session cookie -> { userKey, authTime }
  const revokedAccessJti = new Set();

  function findUser(hint) {
    if (!hint) return null;
    const h = String(hint).toLowerCase();
    for (const [key, user] of Object.entries(directory.users)) {
      if (key === h || user.login.toLowerCase() === h || user.uid.toLowerCase() === h) return { ...user, key };
    }
    return null;
  }

  function sign(payload) {
    const key = keys[0];
    const input = `${b64url(JSON.stringify({ kid: key.kid, alg: 'RS256' }))}.${b64url(JSON.stringify(payload))}`;
    return `${input}.${b64url(crypto.sign('sha256', Buffer.from(input), key.privateKey))}`;
  }

  function verifyOwnToken(token) {
    try {
      const [h, p, s] = String(token).split('.');
      if (!s) return null;
      const header = JSON.parse(Buffer.from(h, 'base64url').toString('utf8'));
      const key = keys.find((k) => k.kid === header.kid);
      if (!key) return null;
      const pub = crypto.createPublicKey({ key: key.jwk, format: 'jwk' });
      if (!crypto.verify('sha256', Buffer.from(`${h}.${p}`), pub, Buffer.from(s, 'base64url'))) return null;
      return JSON.parse(Buffer.from(p, 'base64url').toString('utf8'));
    } catch {
      return null;
    }
  }

  function accessTokenClaims({ user, clientId, scopes, authTime, ttlSeconds }) {
    const t = opts.now();
    const claims = {
      ver: 1,
      jti: `AT.${randomToken(24)}`,
      iss: opts.issuer,
      aud: authServer.audience,
      iat: t,
      exp: t + (ttlSeconds ?? opts.accessTtlSeconds),
      cid: clientId,
      uid: user.uid,
      scp: scopes,
      auth_time: authTime,
      sub: user.login,
    };
    if (scopes.includes(authServer.clientIdClaim.onlyForScope)) claims.client_id = clientId;
    if (scopes.includes(authServer.groupsClaim.onlyForScope)) {
      const groups = user.groups.filter((g) => g.startsWith(authServer.groupsClaim.filterStartsWith));
      if (groups.length) claims.groups = groups;
    }
    return claims;
  }

  // For tests: sign an access token directly, optionally overriding claims (expired, wrong aud, ...).
  function mintAccessToken({ user = opts.defaultUser, clientId = firstSpaClientId(), scopes, ttlSeconds, claims = {}, omit = [] } = {}) {
    const u = typeof user === 'string' ? findUser(user) : user;
    if (!u) throw new Error(`unknown user ${user}`);
    const ruleScopes = directory.clients[clientId]?.ruleScopes.filter((s) => s !== '*') ?? [];
    const granted = scopes ?? (ruleScopes.length ? ruleScopes : ['openid', 'profile', 'email', 'offline_access', 'devbox']);
    const payload = { ...accessTokenClaims({ user: u, clientId, scopes: granted, authTime: opts.now(), ttlSeconds }), ...claims };
    for (const name of omit) delete payload[name];
    return sign(payload);
  }

  function firstSpaClientId() {
    return Object.entries(directory.clients).find(([, c]) => c.type === 'spa')?.[0];
  }

  function idToken({ user, clientId, nonce, authTime, accessToken }) {
    const t = opts.now();
    const atHash = b64url(crypto.createHash('sha256').update(accessToken).digest().subarray(0, 16));
    const claims = {
      sub: user.uid,
      name: user.name,
      email: user.login,
      ver: 1,
      iss: opts.issuer,
      aud: clientId,
      iat: t,
      exp: t + opts.idTtlSeconds,
      jti: `ID.${randomToken(24)}`,
      amr: ['pwd'],
      idp: '00ofakeidentityprov1',
      preferred_username: user.login,
      auth_time: authTime,
      at_hash: atHash,
    };
    if (nonce) claims.nonce = nonce;
    return sign(claims);
  }

  function issueTokens({ user, clientId, scopes, authTime, nonce, familyId }) {
    const accessToken = sign(accessTokenClaims({ user, clientId, scopes, authTime }));
    const body = {
      token_type: 'Bearer',
      expires_in: opts.accessTtlSeconds,
      access_token: accessToken,
      scope: scopes.join(' '),
    };
    const client = directory.clients[clientId];
    if (scopes.includes('offline_access') && client.grantTypes.includes('refresh_token')) {
      let fam = familyId;
      if (!fam) {
        fam = randomToken(12);
        families.set(fam, { expiresAt: opts.now() + opts.refreshTtlSeconds, revoked: false });
      }
      const refreshToken = randomToken(32);
      refreshTokens.set(refreshToken, {
        familyId: fam, clientId, userKey: user.key, scopes, authTime,
        idleExpiresAt: opts.now() + opts.refreshIdleSeconds, usedAt: null,
      });
      body.refresh_token = refreshToken;
    }
    if (scopes.includes('openid')) body.id_token = idToken({ user, clientId, nonce, authTime, accessToken });
    return body;
  }

  function scopeAllowedByRule(client, scopes) {
    return client.ruleScopes.includes('*') || scopes.every((s) => client.ruleScopes.includes(s));
  }

  function assignmentAllows(client, user) {
    if (!opts.enforceAssignment || !client.assignedGroups.length) return true;
    return user.groups.some((g) => client.assignedGroups.includes(g));
  }

  // ---- HTTP plumbing ----

  function corsHeaders(req, preflight = false) {
    const origin = req.headers.origin;
    if (!origin || !trustedOrigins.has(origin)) return {};
    const h = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true', Vary: 'Origin' };
    if (preflight) {
      h['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS';
      h['Access-Control-Allow-Headers'] = req.headers['access-control-request-headers'] || 'accept, content-type, authorization';
      h['Access-Control-Max-Age'] = '600';
    }
    return h;
  }

  function send(res, status, body, headers = {}) {
    const isJson = typeof body === 'object' && body !== null;
    const payload = isJson ? JSON.stringify(body) : String(body ?? '');
    res.writeHead(status, {
      'Content-Type': isJson ? 'application/json' : 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
      Pragma: 'no-cache',
      ...headers,
    });
    res.end(payload);
  }

  function readForm(req) {
    return new Promise((resolve, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (c) => {
        size += c.length;
        if (size > MAX_BODY_BYTES) { reject(new Error('body too large')); req.destroy(); return; }
        chunks.push(c);
      });
      req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
      req.on('error', reject);
    });
  }

  function cookies(req) {
    const out = {};
    for (const part of (req.headers.cookie || '').split(';')) {
      const i = part.indexOf('=');
      if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
    }
    return out;
  }

  function sessionCookie(value, maxAge) {
    // Path-scoped to /oauth2 so the browser never sends it to the workbench site on another localhost port.
    return `${SESSION_COOKIE}=${value}; Path=/oauth2; HttpOnly; SameSite=Lax; Max-Age=${maxAge}`;
  }

  function startSession(res, user) {
    const sid = randomToken(24);
    const authTime = opts.now();
    sessions.set(sid, { userKey: user.key, authTime });
    res.setHeader('Set-Cookie', sessionCookie(sid, 7200));
    return authTime;
  }

  function redirectWith(res, redirectUri, mode, params) {
    const qs = new URLSearchParams(params).toString();
    const sep = mode === 'fragment' ? '#' : (redirectUri.includes('?') ? '&' : '?');
    res.writeHead(302, { Location: `${redirectUri}${sep}${qs}`, 'Cache-Control': 'no-store' });
    res.end();
  }

  function errorPage(res, status, summary) {
    send(res, status, `<!doctype html><title>Fake Okta error</title><h1>${summary}</h1>`);
  }

  function authorize(req, res, url) {
    const q = url.searchParams;
    const clientId = q.get('client_id');
    const client = directory.clients[clientId];
    if (!client) return errorPage(res, 400, 'Invalid client_id'), 'invalid_client';
    const redirectUri = q.get('redirect_uri');
    if (!client.redirectUris.includes(redirectUri)) {
      return errorPage(res, 400, "The 'redirect_uri' parameter must be a Login redirect URI in the client app settings"), 'bad_redirect_uri';
    }
    const mode = q.get('response_mode') || 'query';
    const state = q.get('state');
    const fail = (error, description) => {
      const params = { error, error_description: description };
      if (state) params.state = state;
      redirectWith(res, redirectUri, mode === 'fragment' ? 'fragment' : 'query', params);
      return error;
    };
    if (!['query', 'fragment'].includes(mode)) return fail('invalid_request', `Unsupported response_mode '${mode}' in this fake.`);
    if (q.get('response_type') !== 'code') return fail('unsupported_response_type', 'The response type is not supported by the authorization server. Configured response types: [code].');
    if (!state) return fail('invalid_request', "The 'state' parameter is required.");
    const challenge = q.get('code_challenge');
    if (!challenge) return fail('invalid_request', 'PKCE code challenge is required when the token endpoint authentication method is \'NONE\'.');
    if (q.get('code_challenge_method') !== 'S256') return fail('invalid_request', "Only 'S256' is supported as the code_challenge_method.");
    if (!/^[A-Za-z0-9_-]{43}$/.test(challenge)) return fail('invalid_request', 'The code_challenge is not a base64url SHA-256 hash.');
    const scopes = (q.get('scope') || '').split(' ').filter(Boolean);
    if (!scopes.length) return fail('invalid_scope', 'The authorization request is missing the scope parameter.');
    const unknown = scopes.filter((s) => !authServer.scopes.includes(s));
    if (unknown.length) return fail('invalid_scope', `One or more scopes are not configured for the authorization server resource: ${unknown.join(' ')}`);
    if (!scopeAllowedByRule(client, scopes)) {
      return fail('access_denied', 'Policy evaluation failed for this request, please check the policy configurations.');
    }

    const prompt = q.get('prompt') || '';
    const session = sessions.get(cookies(req)[SESSION_COOKIE]);
    let user = findUser(q.get('login_hint'));
    let authTime = session && user && session.userKey === user.key ? session.authTime : null;
    if (!user && session) { user = findUser(session.userKey); authTime = session.authTime; }
    if (prompt === 'none') {
      // Real Okta (seen live) doesn't redirect back with login_required here: it shows its own 400 page.
      if (!session || !user || session.userKey !== user.key) {
        res.writeHead(400, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        res.end('<!doctype html><title>400 Bad Request</title><h1>400 Bad Request</h1><p>The client specified not to prompt, but the user is not logged in.</p><p>Error Code: login_required</p>');
        return 'login_required (error page)';
      }
    } else {
      // Interactive sign-in is auto-approved: the session user, the login_hint user, or the configured default.
      user = user || findUser(opts.defaultUser);
      if (!user) return fail('access_denied', `Fake Okta has no user '${opts.defaultUser}'.`);
      if (prompt === 'login' || !authTime) authTime = startSession(res, user);
    }
    if (!assignmentAllows(client, user)) return fail('access_denied', 'User is not assigned to the client application.');

    const code = randomToken(32);
    codes.set(code, {
      clientId, redirectUri, userKey: user.key, scopes, challenge, nonce: q.get('nonce'), authTime,
      expiresAt: opts.now() + opts.codeTtlSeconds,
    });
    redirectWith(res, redirectUri, mode, { code, state });
    return `code for ${user.key}`;
  }

  function tokenError(res, req, status, error, description) {
    send(res, status, { error, error_description: description }, corsHeaders(req));
    return error;
  }

  async function token(req, res) {
    const type = (req.headers['content-type'] || '').split(';')[0].trim();
    if (type !== 'application/x-www-form-urlencoded') {
      return tokenError(res, req, 400, 'invalid_request', 'The token endpoint only accepts application/x-www-form-urlencoded.');
    }
    const form = new URLSearchParams(await readForm(req));
    if (req.headers.authorization) return tokenError(res, req, 401, 'invalid_client', 'Client authentication is not supported by this public client.');
    const clientId = form.get('client_id');
    const client = directory.clients[clientId];
    if (!client) return tokenError(res, req, 401, 'invalid_client', 'The client_id is invalid.');
    const grantType = form.get('grant_type');
    if (!client.grantTypes.includes(grantType)) return tokenError(res, req, 400, 'unsupported_grant_type', `The grant type '${grantType}' is not supported for this client.`);

    if (grantType === 'authorization_code') {
      const grant = codes.get(form.get('code'));
      if (!grant) return tokenError(res, req, 400, 'invalid_grant', 'The authorization code is invalid or has expired.');
      codes.delete(form.get('code'));
      if (grant.clientId !== clientId || opts.now() > grant.expiresAt) {
        return tokenError(res, req, 400, 'invalid_grant', 'The authorization code is invalid or has expired.');
      }
      if (form.get('redirect_uri') !== grant.redirectUri) {
        return tokenError(res, req, 400, 'invalid_grant', "The 'redirect_uri' does not match the redirection URI used in the authorization request.");
      }
      const verifier = form.get('code_verifier') || '';
      if (!/^[A-Za-z0-9._~-]{43,128}$/.test(verifier)) return tokenError(res, req, 400, 'invalid_request', 'The code_verifier is missing or malformed.');
      const computed = b64url(crypto.createHash('sha256').update(verifier).digest());
      if (computed !== grant.challenge) return tokenError(res, req, 400, 'invalid_grant', 'PKCE verification failed.');
      const user = findUser(grant.userKey);
      send(res, 200, issueTokens({ user, clientId, scopes: grant.scopes, authTime: grant.authTime, nonce: grant.nonce }), corsHeaders(req));
      return `tokens for ${user.key} (code)`;
    }

    // refresh_token
    const presented = form.get('refresh_token');
    const rec = refreshTokens.get(presented);
    const t = opts.now();
    if (!rec || rec.clientId !== clientId) return tokenError(res, req, 400, 'invalid_grant', 'The refresh token is invalid or expired.');
    const family = families.get(rec.familyId);
    if (family.revoked || t > family.expiresAt || t > rec.idleExpiresAt) {
      return tokenError(res, req, 400, 'invalid_grant', 'The refresh token is invalid or expired.');
    }
    if (rec.usedAt !== null && t - rec.usedAt > opts.refreshGraceSeconds) {
      family.revoked = true;
      opts.log('refresh token reused after the grace period: token family revoked');
      return tokenError(res, req, 400, 'invalid_grant', 'The refresh token is invalid or expired.');
    }
    rec.usedAt ??= t;
    let scopes = rec.scopes;
    if (form.get('scope')) {
      const asked = form.get('scope').split(' ').filter(Boolean);
      if (!asked.every((s) => rec.scopes.includes(s))) return tokenError(res, req, 400, 'invalid_scope', 'The requested scope exceeds the original grant.');
      scopes = asked;
    }
    const user = findUser(rec.userKey);
    if (!assignmentAllows(client, user)) return tokenError(res, req, 400, 'invalid_grant', 'User is not assigned to the client application.');
    send(res, 200, issueTokens({ user, clientId, scopes, authTime: rec.authTime, familyId: rec.familyId }), corsHeaders(req));
    return `tokens for ${user.key} (refresh)`;
  }

  async function revoke(req, res) {
    const form = new URLSearchParams(await readForm(req));
    const client = directory.clients[form.get('client_id')];
    if (!client) return tokenError(res, req, 401, 'invalid_client', 'The client_id is invalid.');
    const value = form.get('token') || '';
    const rec = refreshTokens.get(value);
    if (rec && rec.clientId === form.get('client_id')) families.get(rec.familyId).revoked = true;
    const claims = verifyOwnToken(value);
    if (claims?.jti) revokedAccessJti.add(claims.jti);
    send(res, 200, '', corsHeaders(req));
    return rec ? 'refresh token family revoked' : 'ok';
  }

  // GET with a query, or a form POST (Okta's logoutCustomASWithPost), which keeps the ID token out of the URL.
  async function logout(req, res, url) {
    const q = req.method === 'POST' ? new URLSearchParams(await readForm(req)) : url.searchParams;
    const hint = q.get('id_token_hint');
    const claims = hint ? verifyOwnToken(hint) : null;
    if (!claims) return errorPage(res, 400, "The 'id_token_hint' parameter is missing or invalid."), 'bad_id_token_hint';
    const client = directory.clients[claims.aud];
    const target = q.get('post_logout_redirect_uri');
    const sid = cookies(req)[SESSION_COOKIE];
    if (sid) sessions.delete(sid);
    res.setHeader('Set-Cookie', sessionCookie('', 0));
    if (!target) return send(res, 200, '<!doctype html><title>Signed out</title><p>Signed out of Fake Okta.</p>'), 'signed out';
    if (!client?.postLogoutRedirectUris.includes(target)) {
      return errorPage(res, 400, "The 'post_logout_redirect_uri' parameter must be a Logout redirect URI in the client app settings"), 'bad_post_logout_uri';
    }
    const state = q.get('state');
    res.writeHead(302, { Location: state ? `${target}${target.includes('?') ? '&' : '?'}state=${encodeURIComponent(state)}` : target });
    res.end();
    return 'signed out';
  }

  function userinfo(req, res) {
    const bearer = /^Bearer (.+)$/.exec(req.headers.authorization || '')?.[1];
    const claims = bearer ? verifyOwnToken(bearer) : null;
    if (!claims || claims.exp <= opts.now() || revokedAccessJti.has(claims.jti)) {
      send(res, 401, { error: 'invalid_token' }, { ...corsHeaders(req), 'WWW-Authenticate': 'Bearer error="invalid_token"' });
      return 'invalid_token';
    }
    const user = findUser(claims.uid);
    send(res, 200, { sub: user.uid, name: user.name, email: user.login, preferred_username: user.login }, corsHeaders(req));
    return 'ok';
  }

  function discovery() {
    const e = (p) => `${opts.issuer}${p}`;
    return {
      issuer: opts.issuer,
      authorization_endpoint: e('/v1/authorize'),
      token_endpoint: e('/v1/token'),
      userinfo_endpoint: e('/v1/userinfo'),
      jwks_uri: e('/v1/keys'),
      response_types_supported: ['code'],
      response_modes_supported: ['query', 'fragment'],
      grant_types_supported: ['authorization_code', 'refresh_token'],
      subject_types_supported: ['public'],
      scopes_supported: authServer.scopes,
      token_endpoint_auth_methods_supported: ['none'],
      claims_supported: ['ver', 'jti', 'iss', 'aud', 'iat', 'exp', 'cid', 'uid', 'scp', 'auth_time', 'sub', 'client_id', 'groups', 'name', 'email', 'nonce', 'at_hash'],
      code_challenge_methods_supported: ['S256'],
      revocation_endpoint: e('/v1/revoke'),
      end_session_endpoint: e('/v1/logout'),
      id_token_signing_alg_values_supported: ['RS256'],
    };
  }

  // The fake Okta session knob: set who is "signed in to Okta" in this browser.
  function fakeSession(req, res, url) {
    const user = findUser(url.searchParams.get('user'));
    if (!user) return send(res, 400, { error: 'unknown user' }), 'unknown user';
    startSession(res, user);
    const back = url.searchParams.get('return_to');
    if (back && trustedOrigins.has(new URL(back).origin)) {
      res.writeHead(302, { Location: back });
      res.end();
    } else {
      send(res, 200, { ok: true, user: user.key });
    }
    return `session for ${user.key}`;
  }

  async function handler(req, res) {
    const url = new URL(req.url, 'http://fake-okta.local');
    const p = url.pathname;
    let note = '';
    try {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, corsHeaders(req, true));
        res.end();
        note = 'preflight';
      } else if (req.method === 'GET' && (p === `${basePath}/.well-known/openid-configuration` || p === `${basePath}/.well-known/oauth-authorization-server`)) {
        send(res, 200, discovery(), corsHeaders(req));
      } else if (req.method === 'GET' && p === `${basePath}/v1/keys`) {
        send(res, 200, { keys: keys.map((k) => k.jwk) }, { ...corsHeaders(req), 'Cache-Control': 'max-age=60' });
      } else if (req.method === 'GET' && p === `${basePath}/v1/authorize`) {
        note = authorize(req, res, url);
      } else if (req.method === 'POST' && p === `${basePath}/v1/token`) {
        note = await token(req, res);
      } else if (req.method === 'POST' && p === `${basePath}/v1/revoke`) {
        note = await revoke(req, res);
      } else if ((req.method === 'GET' || req.method === 'POST') && p === `${basePath}/v1/logout`) {
        note = await logout(req, res, url);
      } else if ((req.method === 'GET' || req.method === 'POST') && p === `${basePath}/v1/userinfo`) {
        note = userinfo(req, res);
      } else if (req.method === 'GET' && p === '/_fake/health') {
        send(res, 200, { ok: true, issuer: opts.issuer });
      } else if (req.method === 'GET' && p === '/_fake/session') {
        note = fakeSession(req, res, url);
      } else {
        send(res, 404, { errorCode: 'E0000022', errorSummary: 'The endpoint does not support the provided HTTP method' });
      }
    } catch (err) {
      note = `error: ${err.message}`;
      if (!res.headersSent) send(res, 400, { error: 'invalid_request', error_description: 'Malformed request.' });
    }
    // Paths and parameter names only: never codes, tokens or verifiers.
    const params = [...url.searchParams.keys()].join(',');
    opts.log(`${req.method} ${p}${params ? ` [${params}]` : ''} -> ${res.statusCode}${note ? ` ${note}` : ''}`);
  }

  const server = http.createServer((req, res) => { handler(req, res); });

  return {
    issuer: opts.issuer,
    directory,
    server,
    handler,
    mintAccessToken,
    findUser,
    jwks: () => ({ keys: keys.map((k) => k.jwk) }),
    // Add a new signing key; the old one stays published so tokens already issued keep verifying.
    rotateKeys() { keys = [newSigningKey(), ...keys].slice(0, 2); return keys[0].kid; },
    listen(port, host = '127.0.0.1') {
      return new Promise((resolve) => server.listen(port, host, () => resolve(server.address().port)));
    },
    close() { return new Promise((resolve) => { server.closeAllConnections?.(); server.close(() => resolve()); }); },
  };
}

function envInt(name, fallback) {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : Number.parseInt(v, 10);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const port = envInt('FAKE_OKTA_PORT', 9400);
  const okta = createFakeOkta({
    issuer: process.env.FAKE_OKTA_ISSUER || `http://localhost:${port}/oauth2/default`,
    workbenchOrigin: process.env.FAKE_OKTA_WORKBENCH_ORIGIN || 'http://localhost:9402',
    defaultUser: process.env.FAKE_OKTA_USER || 'ada',
    accessTtlSeconds: envInt('FAKE_OKTA_ACCESS_TTL_SECONDS', 3600),
    refreshGraceSeconds: envInt('FAKE_OKTA_REFRESH_GRACE_SECONDS', 30),
    enforceAssignment: process.env.FAKE_OKTA_ENFORCE_ASSIGNMENT === '1',
  });
  const host = process.env.FAKE_OKTA_HOST || '127.0.0.1';
  await okta.listen(port, host);
  console.log(`[fake-okta] issuer ${okta.issuer} listening on ${host}:${port} (default user ${process.env.FAKE_OKTA_USER || 'ada'})`);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => okta.close().then(() => process.exit(0)));
}
