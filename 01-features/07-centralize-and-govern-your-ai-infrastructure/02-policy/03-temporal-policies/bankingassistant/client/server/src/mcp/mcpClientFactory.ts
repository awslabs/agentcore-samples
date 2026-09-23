import { createMockMcpClient } from "./mockMcpClient.js";
import { createSdkMcpClient } from "./sdkMcpClient.js";
import { createStatelessMcpClient } from "./statelessMcpClient.js";
import { McpClient, McpClientDeps, ProtocolVersion } from "./types.js";

export function createMcpClient(
  protocol: ProtocolVersion,
  deps: McpClientDeps,
  opts: { mock: boolean },
): McpClient {
  if (opts.mock) return createMockMcpClient(deps, protocol);
  return protocol === "2026-07-28"
    ? createStatelessMcpClient(deps)
    : createSdkMcpClient(deps);
}
