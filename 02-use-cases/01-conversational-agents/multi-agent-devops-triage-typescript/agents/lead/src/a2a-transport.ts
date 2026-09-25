import {
  ClientFactory,
  ClientFactoryOptions,
  DefaultAgentCardResolver,
  JsonRpcTransportFactory,
} from '@a2a-js/sdk/client';
import { createSigV4Fetch } from '@sample/aws-sigv4-fetch';

function isAgentCoreRuntimeUrl(url: string): boolean {
  return /^https:\/\/bedrock-agentcore(?:-[a-z0-9]+)?\.[a-z0-9-]+\.amazonaws\.com\//.test(url);
}

/**
 * Builds a ClientFactory for a worker, scoped to one AgentCore session.
 *
 * `sessionId` is the session the lead itself was invoked with, not a fresh one:
 * forwarding it makes a single id span client → lead → workers, and keeps
 * unrelated conversations in separate worker sessions — AgentCore uses the
 * session for routing affinity and the workers key task state by it. It is sent
 * to plain-HTTP workers too, so the trail correlates in local and compose mode.
 *
 * Deployed, the worker URL is an InvokeAgentRuntime endpoint that proxies the
 * A2A payloads unmodified, and every request needs SigV4 — agent-card fetches
 * included, since the runtime's card endpoint requires it too.
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
