import type { Tool, ToolConfiguration } from "@aws-sdk/client-bedrock-runtime";

import type { McpTool } from "../mcp/types.js";

// Bedrock types the tool inputSchema `json` field as its internal DocumentType,
// which is not exported. An MCP JSON-Schema object is a valid document; narrow
// via this alias at the single boundary.
type ToolInputSchema = NonNullable<
  NonNullable<Tool["toolSpec"]>["inputSchema"]
>;

/**
 * Map MCP tools to a Bedrock Converse toolConfig. The gateway tool names
 * (e.g. "banking-tools___get_account_balance") pass through unchanged: the model
 * emits them and MCP expects them.
 */
export function mcpToolsToConverseToolConfig(tools: McpTool[]): ToolConfiguration {
  return {
    tools: tools.map(
      (t): Tool => ({
        toolSpec: {
          name: t.name,
          description: t.description || t.name,
          inputSchema: { json: t.inputSchema } as ToolInputSchema,
        },
      }),
    ),
  };
}
