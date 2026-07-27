import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import type { RequestHeaders, ServerCallContextBuilder } from '@a2a-js/sdk/server';
import { ServerCallContext, STATE_HEADERS_KEY } from '@a2a-js/sdk/server';

/**
 * Bedrock context propagation for the A2A protocol path — the TypeScript
 * analog of the Python SDK's `BedrockCallContextBuilder` (runtime/a2a.py).
 *
 * AgentCore Runtime injects per-request headers (session id, request id,
 * workload access token, OAuth callback URL) into every proxied A2A call.
 * `BedrockAgentCoreApp` extracts these on the HTTP protocol path and hands
 * them to the handler as a typed context; this module does the same for
 * A2A servers, exposing them via AsyncLocalStorage so executors and any
 * tool handlers they invoke can call `getBedrockContext()` — mirroring the
 * `getContext()` API of `bedrock-agentcore/runtime`.
 */

/** AgentCore Runtime request context, extracted from the injected headers. */
export interface BedrockA2AContext {
  /** From `x-amzn-bedrock-agentcore-runtime-session-id`, if present. */
  sessionId?: string;
  /** From `x-amzn-bedrock-agentcore-runtime-request-id`; generated when absent. */
  requestId: string;
  /**
   * AgentCore Identity workload access token (`WorkloadAccessToken` header).
   * Required for `withApiKey` / OAuth egress via `bedrock-agentcore/identity`.
   */
  workloadAccessToken?: string;
  /** From `OAuth2CallbackUrl`, for 3-legged OAuth flows. */
  oauth2CallbackUrl?: string;
  /**
   * Forwardable caller headers: `Authorization` plus
   * `x-amzn-bedrock-agentcore-runtime-custom-*` — the only headers
   * AgentCore's proxy lets a caller pass through.
   */
  headers: Record<string, string>;
}

const SESSION_HEADER = 'x-amzn-bedrock-agentcore-runtime-session-id';
const REQUEST_ID_HEADER = 'x-amzn-bedrock-agentcore-runtime-request-id';
const WORKLOAD_TOKEN_HEADER = 'workloadaccesstoken';
const OAUTH2_CALLBACK_HEADER = 'oauth2callbackurl';
const CUSTOM_HEADER_PREFIX = 'x-amzn-bedrock-agentcore-runtime-custom-';

/**
 * Headers that must not be forwarded to agent code — the AgentCore runtime
 * header allowlist (docs: runtime-header-allowlist.html), same list the
 * Python SDK ships in runtime/models.py. Grouped as in the docs.
 */
const RESTRICTED_HEADERS: ReadonlySet<string> = new Set(
  [
    // Authentication & Authorization
    'Proxy-Authorization',
    'WWW-Authenticate',
    // Content Negotiation
    'Accept',
    'Accept-Charset',
    'Accept-Encoding',
    'Accept-Language',
    'Content-Type',
    'Content-Length',
    'Content-Encoding',
    'Content-Language',
    'Content-Location',
    'Content-Range',
    // Caching
    'Cache-Control',
    'ETag',
    'Expires',
    'If-Match',
    'If-Modified-Since',
    'If-None-Match',
    'If-Range',
    'If-Unmodified-Since',
    'Last-Modified',
    'Pragma',
    'Vary',
    // Connection Management
    'Connection',
    'Keep-Alive',
    'Proxy-Connection',
    'Upgrade',
    // Request Context
    'Host',
    'User-Agent',
    'Referer',
    'From',
    // Range / Transfer
    'Range',
    'Accept-Ranges',
    'Transfer-Encoding',
    'TE',
    'Trailer',
    // Server Information
    'Server',
    'Date',
    'Location',
    'Retry-After',
    // Cookies
    'Set-Cookie',
    'Cookie',
    // Security
    'Content-Security-Policy',
    'Content-Security-Policy-Report-Only',
    'Strict-Transport-Security',
    'X-Content-Type-Options',
    'X-Frame-Options',
    'X-XSS-Protection',
    'Referrer-Policy',
    'Permissions-Policy',
    'Cross-Origin-Embedder-Policy',
    'Cross-Origin-Opener-Policy',
    'Cross-Origin-Resource-Policy',
    // CORS
    'Access-Control-Allow-Origin',
    'Access-Control-Allow-Methods',
    'Access-Control-Allow-Headers',
    'Access-Control-Allow-Credentials',
    'Access-Control-Expose-Headers',
    'Access-Control-Max-Age',
    'Access-Control-Request-Method',
    'Access-Control-Request-Headers',
    'Origin',
    // Client Hints
    'Accept-CH',
    'Accept-CH-Lifetime',
    'DPR',
    'Width',
    'Viewport-Width',
    'Downlink',
    'ECT',
    'RTT',
    'Save-Data',
    // Experimental / Proposed
    'Clear-Site-Data',
    'Feature-Policy',
    'Expect-CT',
    'Public-Key-Pins',
    'Public-Key-Pins-Report-Only',
    // Proxy
    'Via',
    'Forwarded',
    'X-Forwarded-For',
    'X-Forwarded-Host',
    'X-Forwarded-Proto',
    'X-Real-IP',
    'X-Requested-With',
    'X-CSRF-Token',
    // IP Spoofing / URL Manipulation
    'True-Client-IP',
    'X-Client-IP',
    'X-Cluster-Client-IP',
    'X-Originating-IP',
    'X-Source-IP',
    'X-Original-URL',
    'X-Original-Host',
    'X-Rewrite-URL',
    // CDN / Proxy
    'CF-Ray',
    'CF-Connecting-IP',
    'X-Amz-Cf-Id',
    'X-Cache',
    'X-Served-By',
    // HTTP/2 Pseudo Headers
    ':method',
    ':path',
    ':scheme',
    ':authority',
    ':status',
    // Server Push
    'Link',
    // WebSocket
    'Sec-WebSocket-Key',
    'Sec-WebSocket-Accept',
    'Sec-WebSocket-Version',
    'Sec-WebSocket-Protocol',
    'Sec-WebSocket-Extensions',
  ].map((header) => header.toLowerCase()),
);

/**
 * Whether a header may be forwarded to agent code — the port of the Python
 * SDK's `is_forwardable_header` (runtime/models.py). Rules from the
 * AgentCore runtime header-allowlist docs:
 *  - not in the restricted set,
 *  - not `x-amz-*` (reserved for SigV4 signing),
 *  - not `x-amzn-*` unless it carries the runtime-custom prefix.
 */
export function isForwardableHeader(headerName: string): boolean {
  const lower = headerName.toLowerCase();
  if (RESTRICTED_HEADERS.has(lower)) return false;
  if (lower.startsWith('x-amz-')) return false;
  if (lower.startsWith('x-amzn-') && !lower.startsWith(CUSTOM_HEADER_PREFIX)) return false;
  return true;
}

const contextStorage = new AsyncLocalStorage<BedrockA2AContext>();

function headerValue(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value.join(', ');
  return value;
}

/**
 * Builds a {@link BedrockA2AContext} from raw request headers. Forwarding
 * follows the AgentCore runtime header allowlist via
 * {@link isForwardableHeader}, which notably lets `traceparent`/`baggage`
 * through for trace propagation. The context-bearing headers themselves
 * (session id, request id, token, callback URL) land in typed fields, not
 * in `headers`.
 */
export function extractBedrockContext(headers: RequestHeaders): BedrockA2AContext {
  const forwardable: Record<string, string> = {};
  for (const [key, raw] of Object.entries(headers)) {
    const value = headerValue(raw);
    if (value === undefined) continue;
    const lower = key.toLowerCase();
    if (lower === 'authorization' || isForwardableHeader(lower)) {
      forwardable[key] = value;
    }
  }

  return {
    sessionId: headerValue(headers[SESSION_HEADER]),
    requestId: headerValue(headers[REQUEST_ID_HEADER]) ?? randomUUID(),
    workloadAccessToken: headerValue(headers[WORKLOAD_TOKEN_HEADER]),
    oauth2CallbackUrl: headerValue(headers[OAUTH2_CALLBACK_HEADER]),
    headers: forwardable,
  };
}

/**
 * Runs `fn` with `context` as the ambient Bedrock context, retrievable via
 * {@link getBedrockContext} anywhere in the async call chain — executor,
 * Claude Agent SDK tool handlers, identity calls.
 */
export function runWithBedrockContext<T>(context: BedrockA2AContext, fn: () => T): T {
  return contextStorage.run(context, fn);
}

/**
 * Returns the current request's Bedrock context, or undefined outside a
 * request scope (e.g. during server startup).
 */
export function getBedrockContext(): BedrockA2AContext | undefined {
  return contextStorage.getStore();
}

/**
 * A `ServerCallContextBuilder` for `@a2a-js/sdk` handlers that mirrors the
 * Python SDK's `BedrockCallContextBuilder`: the extracted Bedrock fields
 * land in `ServerCallContext.state`, so executors can also read them from
 * `requestContext.context.state` without relying on AsyncLocalStorage.
 *
 * Reuses the ambient context established by `serveA2A`'s middleware when
 * present, keeping generated request ids consistent across both surfaces.
 */
export const bedrockCallContextBuilder: ServerCallContextBuilder = (options) => {
  const bedrock = getBedrockContext() ?? extractBedrockContext(options.headers);

  const state = new Map<string, unknown>();
  state.set(STATE_HEADERS_KEY, options.headers);
  state.set('requestId', bedrock.requestId);
  if (bedrock.sessionId !== undefined) state.set('sessionId', bedrock.sessionId);
  if (bedrock.workloadAccessToken !== undefined) {
    state.set('workloadAccessToken', bedrock.workloadAccessToken);
  }
  if (bedrock.oauth2CallbackUrl !== undefined) {
    state.set('oauth2CallbackUrl', bedrock.oauth2CallbackUrl);
  }

  return new ServerCallContext({
    requestedExtensions: options.extensions,
    user: options.user,
    tenant: options.tenant,
    requestedVersion: options.requestedVersion,
    state,
  });
};
