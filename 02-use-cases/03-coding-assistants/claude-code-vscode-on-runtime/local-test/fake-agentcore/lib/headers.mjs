// requestHeaderConfiguration.requestHeaderAllowlist rules (runtime-header-allowlist) and the Custom-* prefix.
export const CUSTOM_PREFIX = 'x-amzn-bedrock-agentcore-runtime-custom-';
export const SESSION_HEADER = 'x-amzn-bedrock-agentcore-runtime-session-id';
export const REQUEST_ID_HEADER = 'x-amzn-bedrock-agentcore-runtime-request-id';
export const MAX_HEADER_VALUE_BYTES = 4096;
const MAX_ALLOWLIST = 20;

const RESTRICTED = new Set([
  'proxy-authorization', 'www-authenticate',
  'accept', 'accept-charset', 'accept-encoding', 'accept-language', 'content-type', 'content-length', 'content-encoding',
  'content-language', 'content-location', 'content-range',
  'cache-control', 'etag', 'expires', 'if-match', 'if-modified-since', 'if-none-match', 'if-range', 'if-unmodified-since',
  'last-modified', 'pragma', 'vary',
  'connection', 'keep-alive', 'proxy-connection', 'upgrade',
  'host', 'user-agent', 'referer', 'from',
  'range', 'accept-ranges', 'transfer-encoding', 'te', 'trailer',
  'server', 'date', 'location', 'retry-after',
  'set-cookie', 'cookie',
  'content-security-policy', 'content-security-policy-report-only', 'strict-transport-security', 'x-content-type-options',
  'x-frame-options', 'x-xss-protection', 'referrer-policy', 'permissions-policy', 'cross-origin-embedder-policy',
  'cross-origin-opener-policy', 'cross-origin-resource-policy',
  'access-control-allow-origin', 'access-control-allow-methods', 'access-control-allow-headers', 'access-control-allow-credentials',
  'access-control-expose-headers', 'access-control-max-age', 'access-control-request-method', 'access-control-request-headers', 'origin',
  'accept-ch', 'accept-ch-lifetime', 'dpr', 'width', 'viewport-width', 'downlink', 'ect', 'rtt', 'save-data',
  'clear-site-data', 'feature-policy', 'expect-ct', 'public-key-pins', 'public-key-pins-report-only',
  'via', 'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip', 'x-requested-with', 'x-csrf-token',
  'true-client-ip', 'x-client-ip', 'x-cluster-client-ip', 'x-originating-ip', 'x-source-ip', 'x-original-url', 'x-original-host', 'x-rewrite-url',
  'cf-ray', 'cf-connecting-ip', 'x-amz-cf-id', 'x-cache', 'x-served-by',
  'link',
  'sec-websocket-key', 'sec-websocket-accept', 'sec-websocket-version', 'sec-websocket-protocol', 'sec-websocket-extensions',
]);

// Returns the allowlist as a Set of lower-case names; throws on a configuration the control plane would reject.
export function validateAllowlist(list = [], { hasJwtAuthorizer }) {
  if (list.length > MAX_ALLOWLIST) throw new Error(`requestHeaderAllowlist allows at most ${MAX_ALLOWLIST} headers`);
  const out = new Set();
  for (const raw of list) {
    const name = String(raw).toLowerCase();
    if (!/^[a-z0-9_-]+$/.test(name)) throw new Error(`invalid header name ${raw}`);
    if (out.has(name)) throw new Error(`duplicate header ${raw}`);
    if (RESTRICTED.has(name) || name.startsWith(':')) throw new Error(`header ${raw} is restricted`);
    if (name.startsWith('x-amz-')) throw new Error(`header ${raw} is reserved (x-amz-)`);
    if (name.startsWith('x-amzn-') && !name.startsWith(CUSTOM_PREFIX)) throw new Error(`header ${raw} is reserved (x-amzn-)`);
    if (name === 'authorization' && !hasJwtAuthorizer) {
      throw new Error('Authorization header can be specified in requestHeaderAllowlist only when runtime is set up with customJWTAuthorizer');
    }
    out.add(name);
  }
  return out;
}

// A value that looks like a JWT: used to flag tokens that leak into URLs.
export function looksLikeJwt(value) {
  return /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/.test(String(value));
}
