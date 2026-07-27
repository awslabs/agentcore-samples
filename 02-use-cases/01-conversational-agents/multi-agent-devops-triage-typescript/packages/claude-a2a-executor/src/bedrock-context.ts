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

const contextStorage = new AsyncLocalStorage<BedrockA2AContext>();

function headerValue(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value.join(', ');
  return value;
}

/**
 * Builds a {@link BedrockA2AContext} from raw request headers, applying the
 * same extraction and forwarding rules as `BedrockAgentCoreApp` on the HTTP
 * protocol path (bedrock-agentcore runtime/app.ts).
 */
export function extractBedrockContext(headers: RequestHeaders): BedrockA2AContext {
  const forwardable: Record<string, string> = {};
  for (const [key, raw] of Object.entries(headers)) {
    const value = headerValue(raw);
    if (value === undefined) continue;
    const lower = key.toLowerCase();
    if (lower === 'authorization' || lower.startsWith(CUSTOM_HEADER_PREFIX)) {
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
