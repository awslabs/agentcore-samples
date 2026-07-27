import type { AgentCard, AgentSkill } from '@a2a-js/sdk';
import { duplicateInterfacesForLegacy } from '@a2a-js/sdk/compat/v0_3';

export interface AgentCardParams {
  name: string;
  description: string;
  version?: string;
  skills?: Array<Pick<AgentSkill, 'id' | 'name' | 'description'> & Partial<AgentSkill>>;
  /**
   * Base URL where this agent is reachable. Defaults to the
   * `AGENTCORE_RUNTIME_URL` env var (set when deployed on AgentCore
   * Runtime), falling back to localhost — mirroring the Python SDK's
   * `serve_a2a` behavior.
   */
  url?: string;
  port?: number;
}

/**
 * Returns a copy of `card` with every JSONRPC interface pointing at `url`
 * (appending one if the card has none) — the analog of the Python SDK's
 * `_set_jsonrpc_url`. Used by `serveA2A` so a caller-provided card never
 * advertises a stale URL when `AGENTCORE_RUNTIME_URL` is set. The runtime
 * invocation URL serves both protocol versions, so all JSONRPC interfaces
 * (v1.0 and the v0.3 legacy mirror) get the same value.
 */
export function withJsonRpcUrl(card: AgentCard, url: string): AgentCard {
  const hasJsonRpc = card.supportedInterfaces.some((i) => i.protocolBinding === 'JSONRPC');
  const supportedInterfaces = hasJsonRpc
    ? card.supportedInterfaces.map((i) => (i.protocolBinding === 'JSONRPC' ? { ...i, url } : i))
    : [
        ...card.supportedInterfaces,
        { url, protocolBinding: 'JSONRPC', tenant: '', protocolVersion: '1.0' },
      ];
  return { ...card, supportedInterfaces };
}

/**
 * Builds a v1.0 AgentCard from a name/description plus optional skills.
 * The service URL resolution order is: explicit `url` param →
 * `AGENTCORE_RUNTIME_URL` env var (deployed on AgentCore) → local default.
 */
export function buildAgentCard(params: AgentCardParams): AgentCard {
  const url =
    params.url ??
    process.env.AGENTCORE_RUNTIME_URL ??
    `http://localhost:${params.port ?? 9000}/`;

  return {
    name: params.name,
    description: params.description,
    version: params.version ?? '1.0.0',
    // Declare both v1.0 and a v0.3 mirror: AgentCore's documented A2A shape
    // and the Python A2A ecosystem still speak v0.3 (`message/send`), while
    // @a2a-js/sdk v1.0 clients use the v1.0 methods (`SendMessage`). The
    // server's legacyCompat layer routes both.
    supportedInterfaces: duplicateInterfacesForLegacy(
      [{ url, protocolBinding: 'JSONRPC', tenant: '', protocolVersion: '1.0' }],
      ['JSONRPC'],
    ),
    provider: undefined,
    capabilities: {
      streaming: true,
      pushNotifications: false,
      extensions: [],
      extendedAgentCard: false,
    },
    securitySchemes: {},
    securityRequirements: [],
    defaultInputModes: ['text/plain'],
    defaultOutputModes: ['text/plain'],
    skills: (params.skills ?? []).map((skill) => ({
      id: skill.id,
      name: skill.name,
      description: skill.description,
      tags: skill.tags ?? [],
      examples: skill.examples ?? [],
      inputModes: skill.inputModes ?? [],
      outputModes: skill.outputModes ?? [],
      securityRequirements: skill.securityRequirements ?? [],
    })),
    signatures: [],
    iconUrl: undefined,
  };
}
