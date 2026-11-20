// Sign-in for the CloudFront site (Lambda@Edge, viewer request, /auth/* and /signed-out only).
// Dependency-free.
//
// Every other request is checked by a CloudFront Function (infra/iac/roots/web/gate.js), so this
// function runs a few times per sign-in, not once per file. Two realms keep two independent sessions
// in one browser: the operator console (/, /api/*) and the customer portal (/portal/*, /api/portal/*).
// Logins go through Cognito's Hosted UI with PKCE; each login first ends the Hosted UI session so a
// different persona can sign in. On success it sets the access-token cookie plus a session cookie
// "<realm>.<exp>.<hmac>" that the CloudFront Function verifies with the shared key. Lambda@Edge has no
// environment variables, so configuration is read once from SSM in us-east-1. Missing config fails closed.
//
// Persona switch: the "presenter" persona signs in once through the Hosted UI like anyone else. Its
// verified access token (cookie scoped to /auth) lets /auth/switch sign in as alice, bob or carol
// server-side (AdminInitiateAuth, passwords from Secrets Manager), so every downstream check still sees
// that persona's real Cognito token. Nobody else can switch, and cross-site requests cannot trigger it.
import crypto from "node:crypto";
import { SSMClient, GetParameterCommand } from "@aws-sdk/client-ssm";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
import { CognitoIdentityProviderClient, AdminInitiateAuthCommand, InitiateAuthCommand } from "@aws-sdk/client-cognito-identity-provider";

const SSM_PATH = "__SSM_PATH__"; // replaced at build time with /<project>/<env>/edge/
const ssm = new SSMClient({ region: "us-east-1" });
const secrets = new SecretsManagerClient({ region: "us-east-1" });
let idp;
let config;
let jwks;

async function getConfig() {
  if (config) return config;
  const keys = ["client_id", "domain", "issuer", "session_key", "personas"];
  const values = await Promise.all(
    keys.map((k) =>
      ssm.send(new GetParameterCommand({ Name: `${SSM_PATH}${k}`, WithDecryption: k === "session_key" })).then((r) => r.Parameter.Value),
    ),
  );
  const c = Object.fromEntries(keys.map((k, i) => [k, values[i]]));
  if (!c.client_id || !c.domain || !c.issuer || !c.session_key || !c.personas) throw new Error("edge auth config missing");
  c.personas = JSON.parse(c.personas);
  const [, region, pool] = c.issuer.match(/^https:\/\/cognito-idp\.([a-z0-9-]+)\.amazonaws\.com\/([\w-]+)$/) || [];
  if (!pool) throw new Error("edge auth issuer malformed");
  c.region = region;
  c.pool = pool;
  idp = new CognitoIdentityProviderClient({ region });
  config = c;
  return config;
}

async function getKeys(issuer) {
  if (jwks) return jwks;
  const res = await fetch(`${issuer}/.well-known/jwks.json`);
  if (!res.ok) throw new Error("jwks unavailable");
  jwks = Object.fromEntries((await res.json()).keys.map((k) => [k.kid, crypto.createPublicKey({ key: k, format: "jwk" })]));
  return jwks;
}

const b64url = (buf) => Buffer.from(buf).toString("base64url");

async function verify(token, cfg) {
  const [h, p, s] = (token || "").split(".");
  if (!s) return null;
  const header = JSON.parse(Buffer.from(h, "base64url"));
  const claims = JSON.parse(Buffer.from(p, "base64url"));
  const key = (await getKeys(cfg.issuer))[header.kid];
  if (!key || header.alg !== "RS256") return null;
  const ok = crypto.verify("RSA-SHA256", Buffer.from(`${h}.${p}`), key, Buffer.from(s, "base64url"));
  const now = Math.floor(Date.now() / 1000);
  if (!ok || claims.iss !== cfg.issuer || claims.token_use !== "access" || claims.client_id !== cfg.client_id || claims.exp <= now) return null;
  return claims;
}

function cookies(headers) {
  const out = {};
  for (const { value } of headers.cookie || []) {
    for (const part of value.split(";")) {
      const i = part.indexOf("=");
      if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
    }
  }
  return out;
}

const cookieName = (realm) => `ticketops_${realm}`;
const sessionName = (realm) => `ticketops_s_${realm}`;
// session cookie checked by the CloudFront Function: HMAC over realm, expiry and the token's hash
function session(cfg, realm, exp, token) {
  const th = crypto.createHash("sha256").update(token).digest("hex");
  const sig = crypto.createHmac("sha256", cfg.session_key).update(`${realm}.${exp}.${th}`).digest("hex");
  return `${realm}.${exp}.${sig}`;
}
const setCookie = (name, value, maxAge, path = "/") => ({
  key: "Set-Cookie",
  value: `${name}=${value}; Path=${path}; Secure; HttpOnly; SameSite=Lax; Max-Age=${maxAge}`,
});
const realmOf = (role) => (role === "operator" ? "op" : role === "customer" ? "portal" : null);
// the access-token cookie the API reads plus the session cookie the CloudFront Function checks
const sessionCookies = (cfg, realm, token, claims, ttl) => [
  setCookie(cookieName(realm), token, ttl),
  setCookie(sessionName(realm), session(cfg, realm, claims.exp, token), ttl),
  setCookie(`ticketops_as_${realm}`, encodeURIComponent(claims.username || ""), ttl),
];

// Presenter persona switch
const PRESENTER = "ticketops_presenter"; // access token, sent only to /auth/*
const PRESENTER_R = "ticketops_presenter_r"; // refresh token, keeps the presenter signed in for the day
const presenterCookies = (access, refresh, ttl) => [
  setCookie(PRESENTER, access, ttl, "/auth"),
  ...(refresh ? [setCookie(PRESENTER_R, refresh, 86400, "/auth")] : []),
];

// The presenter's verified claims (refreshing an expired access token), or null.
async function presenter(cfg, jar) {
  let claims = await verify(jar[PRESENTER], cfg);
  const cookies = [];
  if (!claims && jar[PRESENTER_R]) {
    try {
      const r = await idp.send(
        new InitiateAuthCommand({ ClientId: cfg.client_id, AuthFlow: "REFRESH_TOKEN_AUTH", AuthParameters: { REFRESH_TOKEN: jar[PRESENTER_R] } }),
      );
      const t = r.AuthenticationResult;
      claims = t && (await verify(t.AccessToken, cfg));
      if (claims) cookies.push(...presenterCookies(t.AccessToken, null, t.ExpiresIn || 3600));
    } catch (err) {
      console.warn("presenter refresh failed", err?.name);
      claims = null;
    }
  }
  return claims && claims.role === "presenter" ? { claims, cookies } : null;
}

// Sign in as one listed persona with its own credentials; returns its realm and cookies, or null.
async function signInAs(cfg, user) {
  const p = Object.hasOwn(cfg.personas, user) ? cfg.personas[user] : null;
  if (!p) return null;
  const secret = JSON.parse((await secrets.send(new GetSecretValueCommand({ SecretId: p.secret }))).SecretString);
  const r = await idp.send(
    new AdminInitiateAuthCommand({
      UserPoolId: cfg.pool,
      ClientId: cfg.client_id,
      AuthFlow: "ADMIN_USER_PASSWORD_AUTH",
      AuthParameters: { USERNAME: secret.username, PASSWORD: secret.password },
    }),
  );
  const t = r.AuthenticationResult; // a challenge (e.g. new password) means no tokens: refuse
  const claims = t && (await verify(t.AccessToken, cfg));
  const realm = claims && realmOf(claims.role);
  if (!realm || claims.username !== user) return null;
  return { realm, cookies: sessionCookies(cfg, realm, t.AccessToken, claims, t.ExpiresIn || 3600) };
}

const firstOf = (cfg, realm) => Object.keys(cfg.personas).find((u) => realmOf(cfg.personas[u].role) === realm);
// Browsers mark cross-site requests; a switch only follows a same-origin click or a typed URL.
const crossSite = (headers) => ["cross-site", "same-site"].includes(headers["sec-fetch-site"]?.[0]?.value);

// Return destinations are browser URLs, not string prefixes: WHATWG treats /\\host as //host.
// Revalidate the PKCE cookie on callback too; the cookie is not an authority to redirect.
function returnPath(value, host) {
  if (typeof value !== "string" || !value.startsWith("/") || /[\\\u0000-\u001f\u007f]/.test(value)) return "/";
  try {
    const origin = new URL(`https://${host}`);
    const url = new URL(value, origin);
    if (url.origin !== origin.origin || !url.pathname.startsWith("/") ||
        /%(?:2f|5c|25|0[0-9a-f]|1[0-9a-f]|7f)/i.test(url.pathname)) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

async function switchTo(cfg, host, query, headers, jar) {
  if (crossSite(headers)) return deny(403, "cross-site persona switch refused");
  const q = new URLSearchParams(query);
  const user = q.get("u") || "";
  const who = await presenter(cfg, jar);
  if (!who) return redirect(`/auth/start?r=op&b=${encodeURIComponent("/")}`);
  const as = await signInAs(cfg, user);
  if (!as) return page(400, "Unknown persona", "That persona cannot be switched to.", "Back to the console", "/");
  console.log(JSON.stringify({ event: "persona_switch", presenter: who.claims.sub, as: user, realm: as.realm }));
  let back = returnPath(q.get("b") || "/", host);
  if (back.startsWith("/auth")) back = "/";
  if (as.realm === "portal") back = "/portal/";
  else if (back.startsWith("/portal")) back = "/";
  return redirect(back, [...who.cookies, ...as.cookies]);
}

async function me(cfg, jar) {
  const who = await presenter(cfg, jar);
  const body = who
    ? {
        presenter: true,
        acting_as: { op: decodeURIComponent(jar.ticketops_as_op || ""), portal: decodeURIComponent(jar.ticketops_as_portal || "") },
        personas: Object.entries(cfg.personas).map(([u, p]) => ({ username: u, role: p.role, customer_id: p.customer_id })),
      }
    : { presenter: false };
  return {
    status: "200",
    headers: {
      "content-type": [{ key: "Content-Type", value: "application/json" }],
      "cache-control": [{ key: "Cache-Control", value: "no-store" }],
      ...(who?.cookies.length ? { "set-cookie": who.cookies } : {}),
    },
    body: JSON.stringify(body),
  };
}

function redirect(location, extraCookies = []) {
  return {
    status: "302",
    statusDescription: "Found",
    headers: {
      location: [{ key: "Location", value: location }],
      "cache-control": [{ key: "Cache-Control", value: "no-store" }],
      ...(extraCookies.length ? { "set-cookie": extraCookies } : {}),
    },
  };
}

const deny = (status, message) => ({
  status: String(status),
  headers: {
    "content-type": [{ key: "Content-Type", value: "application/json" }],
    "cache-control": [{ key: "Cache-Control", value: "no-store" }],
  },
  body: JSON.stringify({ error: message }),
});

// Browser-facing outcome pages (sign-out, wrong persona). Self-contained: no fonts or images, since
// the visitor has no session to fetch site assets with. API paths keep JSON errors.
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const page = (status, title, message, action, href) => ({
  status: String(status),
  headers: {
    "content-type": [{ key: "Content-Type", value: "text/html; charset=utf-8" }],
    "cache-control": [{ key: "Cache-Control", value: "no-store" }],
  },
  body: `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title><style>
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;font:450 17px/1.5 ui-sans-serif,-apple-system,"Segoe UI",sans-serif;color:#d9d6e6;
background:radial-gradient(80% 90% at 0% 100%,#8c1a4a 0%,transparent 55%),radial-gradient(70% 80% at 90% 10%,#3b3fe0 0%,transparent 60%),linear-gradient(110deg,#4c1a7a,#241a62 60%,#07070f)}
main{width:min(460px,100%);padding:32px;border:1px solid rgba(245,243,239,.28);border-radius:16px;background:#0d0d18}
small{display:block;margin-bottom:14px;color:#a7a3bd;font-size:13px;font-weight:600;letter-spacing:.12em}h1{margin:0 0 10px;color:#fff;font-size:28px;line-height:1.15}
p{margin:0 0 24px}a{display:inline-block;padding:12px 24px;border-radius:999px;background:#ff6200;color:#0a0a14;font-weight:650;text-decoration:none}a:hover{background:#ff7f33}a:focus-visible{outline:2px solid #fff;outline-offset:3px}
</style></head><body><main><small>CUSTOMER TICKET OPERATIONS</small><h1>${esc(title)}</h1><p>${esc(message)}</p><a href="${esc(href)}">${esc(action)}</a></main></body></html>`,
});

async function startLogin(cfg, host, uri, realm, jar) {
  realm = realm === "portal" ? "portal" : "op";
  uri = returnPath(uri, host);
  // A signed-in presenter whose persona session expired gets that persona back without a login page.
  const who = await presenter(cfg, jar);
  if (who) {
    const last = decodeURIComponent(jar[`ticketops_as_${realm}`] || "");
    const as = await signInAs(cfg, realmOf(cfg.personas[last]?.role) === realm ? last : firstOf(cfg, realm));
    if (as) return redirect(realm === "portal" ? "/portal/" : uri.startsWith("/api") ? "/" : uri, [...who.cookies, ...as.cookies]);
  }
  // End any Hosted UI session first so the next persona can sign in; then /auth/login authorizes.
  const verifier = b64url(crypto.randomBytes(32));
  const nonce = b64url(crypto.randomBytes(16));
  const back = realm === "portal" ? "/portal/" : uri.startsWith("/api") ? "/" : uri;
  const pkce = b64url(JSON.stringify({ v: verifier, n: nonce, r: realm, b: back }));
  const logout = new URL(`https://${cfg.domain}/logout`);
  logout.searchParams.set("client_id", cfg.client_id);
  logout.searchParams.set("logout_uri", `https://${host}/auth/login`);
  // "ticketops_pkce" is the attempt /auth/login authorizes next; the per-attempt copy lets the callback
  // find its own verifier even if another tab started a login in between.
  return redirect(logout.toString(), [setCookie("ticketops_pkce", pkce, 600), setCookie(`ticketops_pkce_${nonce}`, pkce, 600)]);
}

function authorize(cfg, host, jar) {
  let state;
  try {
    state = JSON.parse(Buffer.from(jar.ticketops_pkce || "", "base64url"));
  } catch {
    return redirect("/");
  }
  const url = new URL(`https://${cfg.domain}/oauth2/authorize`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", cfg.client_id);
  url.searchParams.set("redirect_uri", `https://${host}/auth/callback`);
  url.searchParams.set("scope", "openid");
  url.searchParams.set("state", state.n);
  url.searchParams.set("code_challenge", b64url(crypto.createHash("sha256").update(state.v).digest()));
  url.searchParams.set("code_challenge_method", "S256");
  return redirect(url.toString());
}

async function callback(cfg, host, query, jar) {
  const params = new URLSearchParams(query);
  const nonce = params.get("state") || "";
  let state;
  try {
    state = /^[A-Za-z0-9_-]{16,64}$/.test(nonce) ? JSON.parse(Buffer.from(jar[`ticketops_pkce_${nonce}`] || "", "base64url")) : null;
  } catch {
    state = null;
  }
  // Unknown or expired attempt (e.g. a login page left open for over 10 minutes): start again.
  if (!state || state.n !== nonce || !params.get("code")) return redirect("/", [setCookie(`ticketops_pkce_${nonce}`, "", 0)]);
  const res = await fetch(`https://${cfg.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: cfg.client_id,
      code: params.get("code"),
      redirect_uri: `https://${host}/auth/callback`,
      code_verifier: state.v,
    }),
  });
  if (!res.ok) return deny(401, "login failed");
  const tokens = await res.json();
  const claims = await verify(tokens.access_token, cfg);
  if (!claims) return deny(401, "login failed");
  const ttl = tokens.expires_in || 3600;
  if (claims.role === "presenter") {
    // one sign-in: start as the first operator (console) and the first customer (portal)
    const [op, portal] = await Promise.all([signInAs(cfg, firstOf(cfg, "op")), signInAs(cfg, firstOf(cfg, "portal"))]);
    if (!op || !portal) return deny(503, "persona sign-in unavailable");
    console.log(JSON.stringify({ event: "presenter_signed_in", presenter: claims.sub }));
    return redirect(state.r === "portal" ? "/portal/" : returnPath(state.b, host), [
      ...presenterCookies(tokens.access_token, tokens.refresh_token, ttl),
      ...op.cookies,
      ...portal.cookies,
      setCookie(`ticketops_pkce_${nonce}`, "", 0),
    ]);
  }
  if (state.r === "op" && claims.role !== "operator")
    return page(403, "Operators only", "The operator console needs an operator persona such as alice or bob.", "Sign in as another user", "/auth/logout");
  if (state.r === "portal" && claims.role !== "customer")
    return page(403, "Customers only", "The Ticket Portal needs a customer persona such as carol.", "Sign in as another user", "/auth/logout");
  return redirect(returnPath(state.b, host), [...sessionCookies(cfg, state.r, tokens.access_token, claims, ttl), setCookie(`ticketops_pkce_${nonce}`, "", 0)]);
}

export const handler = async (event) => {
  try {
    return await route(event);
  } catch (err) {
    console.error("sign-in failed", err?.name, err?.message);
    return deny(503, "sign-in unavailable");
  }
};

async function route(event) {
  const request = event.Records[0].cf.request;
  const host = request.headers.host[0].value;
  const uri = request.uri;
  delete request.headers["x-ticketops-access-token"]; // never trust a client-supplied value
  let cfg;
  try {
    cfg = await getConfig();
  } catch (err) {
    console.error("edge config unavailable", err?.name, err?.message);
    return deny(503, "authentication unavailable");
  }
  const jar = cookies(request.headers);
  if (uri === "/auth/start") {
    const q = new URLSearchParams(request.querystring);
    return startLogin(cfg, host, q.get("b") || "/", q.get("r"), jar);
  }
  if (uri === "/auth/switch") return switchTo(cfg, host, request.querystring, request.headers, jar);
  if (uri === "/auth/me") return me(cfg, jar);
  if (uri === "/auth/login") return authorize(cfg, host, jar);
  if (uri === "/auth/callback") return callback(cfg, host, request.querystring, jar);
  if (uri === "/auth/logout") {
    return redirect(`https://${cfg.domain}/logout?client_id=${cfg.client_id}&logout_uri=${encodeURIComponent(`https://${host}/signed-out`)}`, [
      setCookie(cookieName("op"), "", 0),
      setCookie(cookieName("portal"), "", 0),
      setCookie(sessionName("op"), "", 0),
      setCookie(sessionName("portal"), "", 0),
      setCookie("ticketops_as_op", "", 0),
      setCookie("ticketops_as_portal", "", 0),
      setCookie(PRESENTER, "", 0, "/auth"),
      setCookie(PRESENTER_R, "", 0, "/auth"),
    ]);
  }
  if (uri === "/signed-out") return page(200, "Signed out", "Both the operator console and the Ticket Portal sessions have ended.", "Sign in again", "/");
  return deny(404, "not found");
}
