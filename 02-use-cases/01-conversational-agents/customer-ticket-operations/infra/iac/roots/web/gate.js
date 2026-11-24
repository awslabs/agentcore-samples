// CloudFront Function (cloudfront-js-2.0), viewer request, every path except /auth/* and /signed-out.
//
// The session check runs inside CloudFront, not Lambda, so page loads do not consume Lambda
// concurrency (a fresh account has 10). Sign-in (Lambda@Edge, /auth/*) issues two HttpOnly cookies per
// realm: the Cognito access token, and a session cookie "<realm>.<exp>.<hmac>" whose HMAC-SHA256
// covers the realm, the expiry and a hash of that token, keyed by a secret in this distribution's
// KeyValueStore. Here we check the HMAC and the expiry; the API verifies the token's RS256 signature
// again before doing anything. No valid session means a redirect to sign-in (pages) or 401 (API).
import cf from 'cloudfront';
import crypto from 'crypto';

const kvs = cf.kvs();

// AgentCore Runtime and Gateway refuse a token in its last minute ("Ineffectual token, will expire
// within the next minute"; the Gateway answers 403 insufficient_scope). A session therefore ends
// here five minutes before its token does: the caller signs in again and gets a fresh token, so a
// run or an approval never starts with one the services are about to refuse.
const RENEW_BEFORE = 300; // seconds

function same(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function valid(realm, session, token) {
  if (!session || !token) return false;
  const parts = session.split('.');
  if (parts.length !== 3 || parts[0] !== realm) return false;
  const exp = parseInt(parts[1], 10);
  if (!(exp - RENEW_BEFORE > Math.floor(Date.now() / 1000))) return false;
  let key;
  try {
    key = await kvs.get('session_key');
  } catch (e) {
    return false; // fail closed
  }
  const th = crypto.createHash('sha256').update(token).digest('hex');
  const want = crypto.createHmac('sha256', key).update(realm + '.' + parts[1] + '.' + th).digest('hex');
  return same(want, parts[2]);
}

async function handler(event) {
  const req = event.request;
  const uri = req.uri;
  delete req.headers['x-ticketops-access-token']; // never trust a client-supplied value
  const realm = uri.startsWith('/portal') || uri.startsWith('/api/portal') ? 'portal' : 'op';
  const c = req.cookies || {};
  const token = c['ticketops_' + realm] ? c['ticketops_' + realm].value : '';
  const session = c['ticketops_s_' + realm] ? c['ticketops_s_' + realm].value : '';

  if (!(await valid(realm, session, token))) {
    if (uri.startsWith('/api')) {
      return {
        statusCode: 401,
        statusDescription: 'Unauthorized',
        headers: { 'content-type': { value: 'application/json' }, 'cache-control': { value: 'no-store' } },
        body: { encoding: 'text', data: '{"error":"session expired: reload the page"}' },
      };
    }
    const back = realm === 'portal' ? '/portal/' : uri;
    return {
      statusCode: 302,
      statusDescription: 'Found',
      headers: {
        location: { value: '/auth/start?r=' + realm + '&b=' + encodeURIComponent(back) },
        'cache-control': { value: 'no-store' },
      },
    };
  }

  if (uri.startsWith('/api')) req.headers['x-ticketops-access-token'] = { value: token };
  else if (uri === '/') req.uri = '/index.html';
  else if (uri === '/portal' || uri === '/portal/') req.uri = '/portal/index.html';
  return req;
}
