// The CloudFront stand-in forwards what the real distributions forward. Ports 9440-9442.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createDistribution, installLambdaGlobals } from '../cloudfront-sim.mjs';

const events = [];
const buffered = async (event) => {
  events.push(event);
  if (event.rawPath === '/binary') return { statusCode: 200, headers: { 'Content-Type': 'image/png' }, body: Buffer.from([1, 2, 3]).toString('base64'), isBase64Encoded: true };
  return { statusCode: 200, headers: { 'content-type': 'text/plain', 'x-frame-options': 'DENY' }, body: 'hello' };
};
installLambdaGlobals();
const streamed = globalThis.awslambda.streamifyResponse(async (event, responseStream) => {
  const s = globalThis.awslambda.HttpResponseStream.from(responseStream, { statusCode: 201, headers: { 'content-type': 'text/plain' } });
  s.write('str');
  s.end('eamed');
});

let wb; let wv; let st;
before(async () => {
  wb = createDistribution({ site: 'workbench', handler: buffered, log: () => {} });
  wv = createDistribution({ site: 'webview', handler: buffered, log: () => {} });
  st = createDistribution({ site: 'workbench', handler: streamed, log: () => {} });
  await Promise.all([[wb, 9440], [wv, 9441], [st, 9442]].map(([s, p]) => new Promise((r) => s.listen(p, '127.0.0.1', r))));
});
after(() => Promise.all([wb, wv, st].map((s) => new Promise((r) => s.close(r)))));

test('static behavior: no cookies, no query, normalized Accept-Encoding, origin custom header wins', async () => {
  await fetch('http://127.0.0.1:9440/stable-abc/static/out/x.js?tkn=1', { headers: { cookie: 'a=b', 'accept-encoding': 'gzip, deflate, br, zstd', 'x-devbox-site': 'webview', referer: 'x' } });
  const e = events.at(-1);
  assert.equal(e.rawPath, '/stable-abc/static/out/x.js');
  assert.equal(e.rawQueryString, '');
  assert.equal(e.cookies, undefined);
  assert.equal(e.headers['accept-encoding'], 'br,gzip');
  assert.equal(e.headers['x-devbox-site'], 'workbench', 'the viewer cannot pick the site');
  assert.equal(e.headers.referer, undefined);
  assert.equal(e.version, '2.0');
  assert.equal(e.requestContext.http.method, 'GET');
});

test('default behavior: all viewer headers except Host, cookies and the query string', async () => {
  await fetch('http://127.0.0.1:9441/callback?x=1', { headers: { cookie: 'a=b; c=d', 'x-custom': 'v' } });
  const e = events.at(-1);
  assert.equal(e.rawQueryString, 'x=1');
  assert.deepEqual(e.cookies, ['a=b', 'c=d']);
  assert.equal(e.headers['x-custom'], 'v');
  assert.equal(e.headers['x-devbox-site'], 'webview');
  assert.equal(e.headers.host, 'webview.local.cloudfront.net');
});

test('security headers on the workbench only, without overriding the origin (except nosniff)', async () => {
  const w = await fetch('http://127.0.0.1:9440/');
  assert.equal(w.headers.get('x-frame-options'), 'DENY');
  assert.equal(w.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(w.headers.get('referrer-policy'), 'strict-origin-when-cross-origin');
  const v = await fetch('http://127.0.0.1:9441/');
  assert.equal(v.headers.get('referrer-policy'), null);
});

test('base64 bodies, HEAD, methods and a streaming handler', async () => {
  assert.deepEqual([...new Uint8Array(await (await fetch('http://127.0.0.1:9440/binary')).arrayBuffer())], [1, 2, 3]);
  const head = await fetch('http://127.0.0.1:9440/', { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal((await head.text()).length, 0);
  assert.equal((await fetch('http://127.0.0.1:9440/', { method: 'POST', body: 'x' })).status, 403);
  const s = await fetch('http://127.0.0.1:9442/');
  assert.equal(s.status, 201);
  assert.equal(await s.text(), 'streamed');
});
