import {
  ClientFactory,
  ClientFactoryOptions,
  DefaultAgentCardResolver,
  JsonRpcTransportFactory,
} from '@a2a-js/sdk/client';
import { createSigV4Fetch } from '@sample/aws-sigv4-fetch';

/**
 * A2A transport selection for the lead agent.
 *
 * Local mode (worker URL is plain http://host:port): plain fetch, plus the
 * runtime session header so the delegation trail correlates locally too.
 *
 * Deployed mode (worker URL is an AgentCore Runtime invocation URL,
 * https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<escaped-arn>/invocations/):
 * the same A2A JSON-RPC payloads go through the InvokeAgentRuntime HTTP
 * endpoint — AgentCore proxies them to the worker container unmodified.
 * Requests must be SigV4-signed (service: bedrock-agentcore) and carry the
 * runtime session header, so we hand the a2a-js client a signing fetch.
 */

// Runtime URL construction comes from the SDK (buildRuntimeUrl in
// bedrock-agentcore/runtime/a2a) — main.ts imports it from there.

function isAgentCoreRuntimeUrl(url: string): boolean {
  return /^https:\/\/bedrock-agentcore(?:-[a-z0-9]+)?\.[a-z0-9-]+\.amazonaws\.com\//.test(url);
}

/**
 * Builds a ClientFactory for the given worker base URL, scoped to one
 * AgentCore runtime session.
 *
 * `sessionId` is the session the lead itself was invoked with, not a fresh
 * one: forwarding it means a single id spans client → lead → both workers, so
 * one CloudWatch Logs Insights query reconstructs the whole invocation. It
 * also keeps unrelated conversations in separate worker sessions, which
 * matters because AgentCore uses the session for routing affinity and the
 * workers key task state by it.
 *
 * The header is set on plain-HTTP workers too (local and compose mode), where
 * it costs nothing and keeps the delegation trail correlated in all three run
 * modes. For AgentCore Runtime URLs the request is additionally SigV4-signed —
 * agent-card fetches included, since the runtime's card endpoint requires it.
 */
export function createA2AClientFactory(
  baseUrl: string,
  region: string,
  sessionId: string,
): ClientFactory {
  const baseFetch: typeof fetch = isAgentCoreRuntimeUrl(baseUrl)
    ? createSigV4Fetch({ service: 'bedrock-agentcore', region })
    : (input, init) => fetch(input, init);

  const fetchWithSession: typeof fetch = (input, init) => {
    const request = new Request(input, init);
    const headers = new Headers(request.headers);
    headers.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sessionId);
    return baseFetch(new Request(request, { headers }));
  };

  return new ClientFactory(
    ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
      // Both the JSON-RPC transport and the card resolver must use the
      // signing fetch — the runtime's card endpoint requires SigV4 too.
      transports: [new JsonRpcTransportFactory({ fetchImpl: fetchWithSession })],
      cardResolver: new DefaultAgentCardResolver({
        fetchImpl: fetchWithSession,
        legacyCompat: { enabled: true },
      }),
    }),
  );
}
