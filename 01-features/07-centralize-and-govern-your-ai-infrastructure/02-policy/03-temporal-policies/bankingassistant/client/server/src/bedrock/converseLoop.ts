import {
  BedrockRuntimeClient,
  ContentBlock,
  ConverseStreamCommand,
  Message,
} from "@aws-sdk/client-bedrock-runtime";

import { log } from "../logger.js";
import type { McpClient, McpTool } from "../mcp/types.js";
import type { ChatMessage, ToolEvent } from "../sessions/types.js";
import { mcpToolsToConverseToolConfig } from "./toolConfig.js";

/** Events emitted during a streaming turn, consumed by the route and forwarded to the UI. */
export type ConverseEvent =
  | { type: "text"; delta: string }
  | { type: "tool_start"; name: string; args: unknown }
  | { type: "tool"; event: ToolEvent }
  | { type: "ids"; mcpSessionId: string | null; policySessionId: string | null };

export interface ConverseResult {
  assistantText: string;
  toolEvents: ToolEvent[];
}

const MAX_TOOL_ROUNDS = 10;

/** Extract plain text from an MCP tool result's content blocks. */
function mcpResultToText(content: unknown): string {
  if (Array.isArray(content)) {
    const parts = content
      .map((b) =>
        b && typeof b === "object" && "text" in b
          ? String((b as { text: unknown }).text)
          : JSON.stringify(b),
      )
      .filter(Boolean);
    if (parts.length) return parts.join("\n");
  }
  return typeof content === "string" ? content : JSON.stringify(content);
}

function priorTurnsToMessages(history: ChatMessage[]): Message[] {
  return history.map((m) => ({
    role: m.role,
    content: [{ text: m.content || "(no text)" }],
  }));
}

/** Accumulator for one streamed content block (text or a tool-use call). */
interface StreamBlock {
  text?: string;
  toolUse?: { toolUseId: string; name: string; inputJson: string };
}

// Bedrock types a tool-use `input` as its internal recursive DocumentType,
// which is not exported. A parsed JSON value is valid at runtime; alias to the
// field's declared type so we narrow at one boundary.
type ToolUseInput = NonNullable<
  NonNullable<ContentBlock.ToolUseMember["toolUse"]>["input"]
>;

/** Parse a streamed tool-input JSON string into an object (empty on failure). */
function parseToolInput(json: string): Record<string, unknown> {
  try {
    return json ? (JSON.parse(json) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/**
 * Run one user turn through the Bedrock ConverseStream tool-use loop. Text
 * deltas and tool events are pushed to `emit` as they arrive; tool calls are
 * dispatched through the session's MCP client and fed back until the model
 * finishes. Returns the assembled assistant text + tool events for the transcript.
 */
export async function runConverseStream(
  args: {
    client: BedrockRuntimeClient;
    modelId: string;
    system: string;
    history: ChatMessage[];
    userText: string;
    tools: McpTool[];
    mcp: McpClient;
    mockLlm: boolean;
  },
  emit: (ev: ConverseEvent) => void,
): Promise<ConverseResult> {
  const { client, modelId, system, history, userText, tools, mcp, mockLlm } = args;

  if (mockLlm) return mockConverseStream(userText, tools, mcp, emit);

  const toolConfig = mcpToolsToConverseToolConfig(tools);
  const messages: Message[] = [
    ...priorTurnsToMessages(history),
    { role: "user", content: [{ text: userText }] },
  ];
  const toolEvents: ToolEvent[] = [];
  let assistantText = "";

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const resp = await client.send(
      new ConverseStreamCommand({
        modelId,
        system: [{ text: system }],
        messages,
        toolConfig,
      }),
    );

    const blocks = new Map<number, StreamBlock>();
    let stopReason: string | undefined;

    for await (const ev of resp.stream ?? []) {
      const startTool = ev.contentBlockStart?.start?.toolUse;
      if (startTool) {
        const i = ev.contentBlockStart!.contentBlockIndex ?? 0;
        blocks.set(i, {
          toolUse: {
            toolUseId: startTool.toolUseId!,
            name: startTool.name!,
            inputJson: "",
          },
        });
      }

      const delta = ev.contentBlockDelta?.delta;
      if (delta) {
        const i = ev.contentBlockDelta!.contentBlockIndex ?? 0;
        const block = blocks.get(i) ?? {};
        if (delta.text) {
          block.text = (block.text ?? "") + delta.text;
          assistantText += delta.text;
          emit({ type: "text", delta: delta.text });
        }
        if (delta.toolUse?.input && block.toolUse) {
          block.toolUse.inputJson += delta.toolUse.input;
        }
        blocks.set(i, block);
      }

      if (ev.messageStop) stopReason = ev.messageStop.stopReason;
    }

    // Rebuild the assistant message (text + toolUse blocks) for the next turn.
    const ordered = [...blocks.entries()].sort((a, b) => a[0] - b[0]);
    const assistantContent: ContentBlock[] = ordered.map(([, b]): ContentBlock => {
      if (b.toolUse) {
        return {
          toolUse: {
            toolUseId: b.toolUse.toolUseId,
            name: b.toolUse.name,
            // Bedrock types this as its recursive DocumentType; a parsed JSON
            // object is valid at runtime, so narrow at this single boundary.
            input: parseToolInput(b.toolUse.inputJson) as ToolUseInput,
          },
        };
      }
      return { text: b.text ?? "" };
    });
    if (assistantContent.length) {
      messages.push({ role: "assistant", content: assistantContent });
    }

    if (stopReason !== "tool_use") break;

    // Dispatch each tool_use block; collect toolResults for the next turn.
    const toolResults: ContentBlock[] = [];
    for (const [, b] of ordered) {
      if (!b.toolUse) continue;
      const toolInput = parseToolInput(b.toolUse.inputJson);
      emit({ type: "tool_start", name: b.toolUse.name, args: toolInput });

      try {
        const res = await Promise.race([
          mcp.callTool(b.toolUse.name, toolInput),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error("Tool call timed out after 30s")), 30_000),
          ),
        ]);
        const text = mcpResultToText(res.content);
        const event: ToolEvent = {
          name: b.toolUse.name,
          args: toolInput,
          result: res.content,
          isError: res.isError,
        };
        toolEvents.push(event);
        emit({ type: "tool", event });
        toolResults.push({
          toolResult: {
            toolUseId: b.toolUse.toolUseId,
            content: [{ text }],
            status: res.isError ? "error" : "success",
          },
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const event: ToolEvent = {
          name: b.toolUse.name,
          args: toolInput,
          result: msg,
          isError: true,
        };
        toolEvents.push(event);
        emit({ type: "tool", event });
        toolResults.push({
          toolResult: {
            toolUseId: b.toolUse.toolUseId,
            content: [{ text: msg }],
            status: "error",
          },
        });
      }
    }
    // A tool call may have just captured the policy session id — surface it live.
    emit({
      type: "ids",
      mcpSessionId: mcp.mcpSessionId,
      policySessionId: mcp.policySessionId,
    });
    messages.push({ role: "user", content: toolResults });
  }

  log.info("Converse turn complete", { toolCalls: toolEvents.length });
  return { assistantText: assistantText.trim(), toolEvents };
}

/** MOCK_LLM=1: skip Bedrock, call the first tool heuristically, stream a canned reply. */
async function mockConverseStream(
  userText: string,
  tools: McpTool[],
  mcp: McpClient,
  emit: (ev: ConverseEvent) => void,
): Promise<ConverseResult> {
  const tool = tools[0];
  if (!tool) {
    const text = `(mock) ${userText}`;
    for (const w of text.split(" ")) emit({ type: "text", delta: w + " " });
    return { assistantText: text, toolEvents: [] };
  }
  const res = await mcp.callTool(tool.name, {
    account_id: "ACC-1001",
    client_id: "CLIENT-001",
  });
  const event: ToolEvent = {
    name: tool.name,
    args: {},
    result: res.content,
    isError: res.isError,
  };
  emit({ type: "tool", event });
  emit({
    type: "ids",
    mcpSessionId: mcp.mcpSessionId,
    policySessionId: mcp.policySessionId,
  });
  const text = `(mock LLM) You said: "${userText}". I called ${tool.name}.`;
  for (const w of text.split(" ")) emit({ type: "text", delta: w + " " });
  return { assistantText: text, toolEvents: [event] };
}
