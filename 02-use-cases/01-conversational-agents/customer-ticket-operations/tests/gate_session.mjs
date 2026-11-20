// The shipped CloudFront Function (infra/iac/roots/web/gate.js) with a stub KeyValueStore: a session
// is refused five minutes before its token expires, so no request carries a token that AgentCore
// Runtime or Gateway would refuse in its last minute.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import vm from 'node:vm';

const KEY = 'test-session-key';
const src = fs.readFileSync(new URL('../infra/iac/roots/web/gate.js', import.meta.url), 'utf8')
  .replace("import cf from 'cloudfront';", '')
  .replace("import crypto from 'crypto';", '');
let now = 1_800_000_000;
const ctx = vm.createContext({
  cf: { kvs: () => ({ get: async () => KEY }) },
  crypto,
  Date: { now: () => now * 1000 },
  Math, parseInt, encodeURIComponent,
});
vm.runInContext(`${src}\nthis.handler = handler;`, ctx);

const token = 'header.payload.signature';
const session = (realm, exp) => {
  const th = crypto.createHash('sha256').update(token).digest('hex');
  return `${realm}.${exp}.${crypto.createHmac('sha256', KEY).update(`${realm}.${exp}.${th}`).digest('hex')}`;
};
const call = (uri, exp, realm = 'op') => ctx.handler({ request: { uri, headers: {},
  cookies: { [`ticketops_${realm}`]: { value: token }, [`ticketops_s_${realm}`]: { value: session(realm, exp) } } } });

// well inside the token's life: the request passes with the token attached
let r = await call('/api/run', now + 3600);
assert.equal(r.headers['x-ticketops-access-token'].value, token);
r = await call('/api/run', now + 301);
assert.equal(r.headers['x-ticketops-access-token'].value, token);
// five minutes or less left: the API call is refused, a page is sent to sign in again
r = await call('/api/run', now + 300);
assert.equal(r.statusCode, 401);
r = await call('/api/run', now + 45);
assert.equal(r.statusCode, 401);
r = await call('/', now + 45);
assert.equal(r.statusCode, 302);
assert.match(r.headers.location.value, /^\/auth\/start\?r=op&b=%2F$/);
r = await call('/portal/', now + 45, 'portal');
assert.match(r.headers.location.value, /^\/auth\/start\?r=portal/);
// a forged expiry does not buy time
const forged = session('op', now + 45).replace(`.${now + 45}.`, `.${now + 3600}.`);
r = await ctx.handler({ request: { uri: '/api/run', headers: {}, cookies: { ticketops_op: { value: token }, ticketops_s_op: { value: forged } } } });
assert.equal(r.statusCode, 401);
console.log('viewer gate: session ends five minutes before its token; forged expiry refused: passed');
