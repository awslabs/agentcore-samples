// Run actual Lambda@Edge route with fake AWS boundaries and locally signed Cognito tokens.
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import vm from "node:vm";

const host = "site.invalid";
const issuer = "https://cognito-idp.us-east-1.amazonaws.com/lab";
const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = publicKey.export({ format: "jwk" });
jwk.kid = "lab";
const enc = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");
function signed(username, role) {
  const h = enc({ alg: "RS256", kid: "lab" });
  const p = enc({ username, sub: `${username}-sub`, role, customer_id: "customer-a", exp: Math.floor(Date.now() / 1000) + 3600, iss: issuer, client_id: "lab", token_use: "access" });
  return `${h}.${p}.${crypto.sign("RSA-SHA256", Buffer.from(`${h}.${p}`), privateKey).toString("base64url")}`;
}
let loginToken = signed("alice", "operator");
const cfg = { client_id: "lab", domain: "auth.invalid", issuer, session_key: "secret", personas: JSON.stringify({ alice: { role: "operator", secret: "alice" }, carol: { role: "customer", secret: "carol" } }) };
class Command { constructor(input) { this.input = input; } }
class SSMClient { async send({ input }) { return { Parameter: { Value: cfg[Object.keys(cfg).find((k) => input.Name.endsWith(k))] } }; } }
const modules = {
  "node:crypto": { default: crypto },
  "@aws-sdk/client-ssm": { SSMClient, GetParameterCommand: Command },
  "@aws-sdk/client-secrets-manager": { SecretsManagerClient: class { async send({ input }) { return { SecretString: JSON.stringify({ username: input.SecretId, password: "unused" }) }; } }, GetSecretValueCommand: Command },
  "@aws-sdk/client-cognito-identity-provider": { CognitoIdentityProviderClient: class { async send({ input }) { const u = input.AuthParameters.USERNAME; return { AuthenticationResult: { AccessToken: signed(u, u === "carol" ? "customer" : "operator"), ExpiresIn: 3600 } }; } }, AdminInitiateAuthCommand: Command, InitiateAuthCommand: Command },
};
const context = vm.createContext({ crypto, Buffer, URL, URLSearchParams, Date, console,
  fetch: async (url) => {
    if (url === `${issuer}/.well-known/jwks.json`) return { ok: true, json: async () => ({ keys: [jwk] }) };
    if (url === "https://auth.invalid/oauth2/token") return { ok: true, json: async () => ({ access_token: loginToken, expires_in: 3600 }) };
    throw Error(`unexpected fetch ${url}`);
  },
});
const file = `${process.cwd()}/functions/edge_auth/index.mjs`;
const edge = new vm.SourceTextModule(fs.readFileSync(file, "utf8"), { context });
await edge.link((id) => {
  assert(modules[id], `unmocked import ${id}`);
  return new vm.SyntheticModule(Object.keys(modules[id]), function () {
    for (const [key, value] of Object.entries(modules[id])) this.setExport(key, value);
  }, { context });
});
await edge.evaluate();
const handler = edge.namespace.handler;
function req(uri, query = "", jar = {}) {
  return { Records: [{ cf: { request: { uri, querystring: query, headers: { host: [{ value: host }], cookie: [{ value: Object.entries(jar).map(([k,v]) => `${k}=${v}`).join("; ") }] } } } }] };
}
function location(reply) { return reply.headers.location[0].value; }
function jarFrom(reply) {
  const jar = {};
  for (const { value } of reply.headers["set-cookie"] || []) {
    const [pair] = value.split(";");
    const eq = pair.indexOf("=");
    jar[pair.slice(0, eq)] = pair.slice(eq + 1);
  }
  return jar;
}
for (const [returnTo, expected] of [
  ["/console?next=1#main", "/console?next=1#main"],
  ["/\\evil.invalid", "/"], ["//evil.invalid", "/"],
  ["/%5cevil.invalid", "/"], ["/%2fevil.invalid", "/"],
  ["/%252fevil.invalid", "/"], ["https://evil.invalid/", "/"],
  ["/\u0000evil", "/"], ["/%0aevil", "/"],
]) {
  const start = await handler(req("/auth/start", new URLSearchParams({ r: "op", b: returnTo }).toString()));
  const jar = jarFrom(start);
  const auth = await handler(req("/auth/login", "", jar));
  const nonce = new URL(location(auth)).searchParams.get("state");
  const callback = await handler(req("/auth/callback", new URLSearchParams({ code: "code", state: nonce }).toString(), jar));
  const back = location(callback);
  assert.equal(back, expected, `callback for ${returnTo}`);
  assert.equal(new URL(back, `https://${host}`).origin, `https://${host}`);
}
// Presenter bootstrap callback, already-signed presenter start, and switch share the validator.
loginToken = signed("presenter", "presenter");
const start = await handler(req("/auth/start", new URLSearchParams({ r: "op", b: "/\\evil.invalid" }).toString()));
const jar = jarFrom(start);
const auth = await handler(req("/auth/login", "", jar));
const nonce = new URL(location(auth)).searchParams.get("state");
const callback = await handler(req("/auth/callback", new URLSearchParams({ code: "code", state: nonce }).toString(), jar));
assert.equal(location(callback), "/");
const presenterJar = { ticketops_presenter: loginToken, ticketops_as_op: "alice" };
const auto = await handler(req("/auth/start", new URLSearchParams({ b: "/\\evil.invalid" }).toString(), presenterJar));
assert.equal(location(auto), "/");
for (const [back, expected] of [["/\\evil.invalid", "/"], ["//evil.invalid", "/"],
                                ["/console?ok=1#top", "/console?ok=1#top"]]) {
  const switched = await handler(req("/auth/switch", new URLSearchParams({ u: "alice", b: back }).toString(), presenterJar));
  assert.equal(location(switched), expected);
  assert.equal(new URL(location(switched), `https://${host}`).origin, `https://${host}`);
}
console.log("edge login, presenter and switch: same-origin attack/positive cases passed");
