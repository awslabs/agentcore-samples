// The runtime's customJWTAuthorizer, as documented for AgentCore (inbound-jwt-authorizer):
// the token is validated locally against the discovery document's JWKS (no introspection), then every
// configured check is ANDed: allowedAudience (aud), allowedClients (the client_id claim; Okta's cid is
// NOT consulted), allowedScopes (at least one of scope/scp), customClaims (STRING EQUALS,
// STRING_ARRAY CONTAINS / CONTAINS_ANY).
import crypto from 'node:crypto';

// botocore's claim-name pattern, copied as-is: ".-:" is a character range, so a literal "-" is not allowed.
const CLAIM_NAME = /^[A-Za-z0-9_.-:]+$/;
const MATCH_VALUE = /^[A-Za-z0-9_.-]{1,255}$/;
const SCOPE_VALUE = /^[\x21\x23-\x5B\x5D-\x7E]{1,255}$/;
const RESERVED_CLAIMS = new Set(['client_id']);
const JWKS_REFETCH_MIN_MS = 1000;

export function validateAuthorizerConfig(cfg) {
  const a = cfg?.customJWTAuthorizer;
  if (!a) throw new Error('only customJWTAuthorizer is emulated');
  if (!/.+\/\.well-known\/openid-configuration$/.test(a.discoveryUrl || '')) {
    throw new Error('discoveryUrl must end with /.well-known/openid-configuration');
  }
  const present = ['allowedAudience', 'allowedClients', 'allowedScopes', 'customClaims'].filter((k) => a[k]?.length);
  if (!present.length) throw new Error('at least one of allowedAudience, allowedClients, allowedScopes, customClaims is required');
  for (const s of a.allowedScopes ?? []) if (!SCOPE_VALUE.test(s)) throw new Error(`invalid allowedScopes value ${JSON.stringify(s)}`);
  for (const c of a.customClaims ?? []) {
    const name = c.inboundTokenClaimName;
    if (!CLAIM_NAME.test(name || '')) throw new Error(`invalid inboundTokenClaimName ${JSON.stringify(name)}`);
    if (RESERVED_CLAIMS.has(name)) throw new Error(`claim name ${name} is reserved`);
    const op = c.authorizingClaimMatchValue?.claimMatchOperator;
    const v = c.authorizingClaimMatchValue?.claimMatchValue ?? {};
    const type = c.inboundTokenClaimValueType;
    if (type === 'STRING') {
      if (op !== 'EQUALS' || typeof v.matchValueString !== 'string') throw new Error(`${name}: STRING supports only EQUALS with matchValueString`);
    } else if (type === 'STRING_ARRAY') {
      if (op === 'CONTAINS' && typeof v.matchValueString !== 'string') {
        // AWS's own tools disagree on CONTAINS (CDK: one string, CLI: a list); the fake accepts the CDK form only.
        throw new Error(`${name}: CONTAINS takes exactly one matchValueString (use CONTAINS_ANY for a list)`);
      }
      if (op === 'CONTAINS_ANY' && !Array.isArray(v.matchValueStringList)) throw new Error(`${name}: CONTAINS_ANY needs matchValueStringList`);
      if (op !== 'CONTAINS' && op !== 'CONTAINS_ANY') throw new Error(`${name}: STRING_ARRAY supports CONTAINS or CONTAINS_ANY`);
    } else {
      throw new Error(`${name}: inboundTokenClaimValueType must be STRING or STRING_ARRAY`);
    }
    for (const value of [v.matchValueString, ...(v.matchValueStringList ?? [])].filter((x) => x !== undefined)) {
      if (!MATCH_VALUE.test(value)) throw new Error(`${name}: match value ${JSON.stringify(value)} does not match [A-Za-z0-9_.-]+`);
    }
  }
  return a;
}

function scopesOf(claims) {
  const out = [];
  for (const v of [claims.scope, claims.scp]) {
    if (typeof v === 'string') out.push(...v.split(' ').filter(Boolean));
    else if (Array.isArray(v)) out.push(...v.filter((x) => typeof x === 'string'));
  }
  return out;
}

function claimMatches(rule, claims) {
  const value = claims[rule.inboundTokenClaimName];
  const { claimMatchOperator: op, claimMatchValue: v } = rule.authorizingClaimMatchValue;
  if (rule.inboundTokenClaimValueType === 'STRING') return typeof value === 'string' && value === v.matchValueString;
  if (!Array.isArray(value) || !value.every((x) => typeof x === 'string')) return false;
  if (op === 'CONTAINS') return value.includes(v.matchValueString);
  return v.matchValueStringList.some((m) => value.includes(m));
}

function decodePart(part) {
  return JSON.parse(Buffer.from(part, 'base64url').toString('utf8'));
}

// fetchJson(url) -> parsed JSON. rewriteUrl lets a container reach the IdP by another host name while the
// token's iss (and the discovery issuer) keep the public URL the browser uses.
export function createAuthorizer(authorizerConfiguration, { fetchJson, rewriteUrl = (u) => u, now = () => Date.now() / 1000, clockSkewSeconds = 0 } = {}) {
  const cfg = validateAuthorizerConfig(authorizerConfiguration);
  let discovery = null;
  let keys = new Map();
  let keysFetchedAt = 0;

  async function loadDiscovery() {
    if (!discovery) {
      const doc = await fetchJson(rewriteUrl(cfg.discoveryUrl));
      if (!doc?.issuer || !doc?.jwks_uri) throw new Error('discovery document lacks issuer or jwks_uri');
      discovery = doc;
    }
    return discovery;
  }

  async function refreshKeys() {
    const d = await loadDiscovery();
    const jwks = await fetchJson(rewriteUrl(d.jwks_uri));
    keys = new Map((jwks.keys ?? []).filter((k) => k.kty === 'RSA').map((k) => [k.kid, crypto.createPublicKey({ key: k, format: 'jwk' })]));
    keysFetchedAt = Date.now();
  }

  async function keyFor(kid) {
    if (!keys.has(kid) && Date.now() - keysFetchedAt >= JWKS_REFETCH_MIN_MS) await refreshKeys();
    return keys.get(kid);
  }

  // Returns { ok: true, claims } or { ok: false, reason } (the reason is for logs; it never holds token content).
  async function authorize(token) {
    const parts = String(token || '').split('.');
    if (parts.length !== 3) return { ok: false, reason: 'malformed token' };
    let header;
    let claims;
    try {
      header = decodePart(parts[0]);
      claims = decodePart(parts[1]);
    } catch {
      return { ok: false, reason: 'malformed token' };
    }
    if (header.alg !== 'RS256') return { ok: false, reason: `unsupported alg ${String(header.alg).slice(0, 10)}` };
    let key;
    try {
      key = await keyFor(header.kid);
    } catch (err) {
      return { ok: false, reason: `cannot load JWKS: ${err.message}`, unavailable: true };
    }
    if (!key) return { ok: false, reason: 'unknown signing key' };
    const signed = Buffer.from(`${parts[0]}.${parts[1]}`);
    if (!crypto.verify('sha256', signed, key, Buffer.from(parts[2], 'base64url'))) return { ok: false, reason: 'bad signature' };
    const t = now();
    if (typeof claims.exp !== 'number' || t >= claims.exp + clockSkewSeconds) return { ok: false, reason: 'token expired' };
    if (typeof claims.nbf === 'number' && t < claims.nbf - clockSkewSeconds) return { ok: false, reason: 'token not yet valid' };
    if (claims.iss !== discovery.issuer) return { ok: false, reason: 'issuer does not match the discovery document' };
    if (cfg.allowedAudience?.length) {
      const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
      if (!aud.some((x) => cfg.allowedAudience.includes(x))) return { ok: false, reason: 'audience not allowed' };
    }
    if (cfg.allowedClients?.length && !cfg.allowedClients.includes(claims.client_id)) {
      return { ok: false, reason: 'client_id claim not allowed' };
    }
    if (cfg.allowedScopes?.length && !scopesOf(claims).some((s) => cfg.allowedScopes.includes(s))) {
      return { ok: false, reason: 'no allowed scope in token' };
    }
    for (const rule of cfg.customClaims ?? []) {
      if (!claimMatches(rule, claims)) return { ok: false, reason: `custom claim ${rule.inboundTokenClaimName} did not match` };
    }
    return { ok: true, claims };
  }

  return { authorize, config: cfg };
}
