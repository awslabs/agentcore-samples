import axios from "axios";
import type { InvokeResult } from "../types";

function buildAgentCoreUrl(agentArn: string, region: string): string {
  // Encode the ARN for use in URL
  const encodedArn = encodeURIComponent(agentArn);

  // Build the AgentCore invocation URL
  // Format: https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded-arn}/invocations?qualifier=DEFAULT
  return `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encodedArn}/invocations?qualifier=DEFAULT`;
}

// Track warmup state per session
const sessionWarmupState = new Map<
  string,
  { isWarmedUp: boolean; warmupPromise: Promise<void> | null }
>();

function getSessionState(sessionId: string) {
  if (!sessionWarmupState.has(sessionId)) {
    sessionWarmupState.set(sessionId, {
      isWarmedUp: false,
      warmupPromise: null,
    });
  }
  return sessionWarmupState.get(sessionId)!;
}

export function setSessionWarmupPromise(
  sessionId: string,
  promise: Promise<void>,
) {
  const state = getSessionState(sessionId);
  state.warmupPromise = promise;
  promise.then(() => {
    state.isWarmedUp = true;
  });
}

export function clearSessionState(sessionId: string) {
  sessionWarmupState.delete(sessionId);
}

async function waitForWarmup(sessionId: string): Promise<void> {
  const state = getSessionState(sessionId);
  if (state.isWarmedUp) return;
  if (state.warmupPromise) {
    await state.warmupPromise;
  }
}

export async function invokeAgent(
  agentArn: string,
  region: string,
  sessionId: string,
  payload: { prompt?: string; ping?: string },
  accessToken: string | null,
): Promise<InvokeResult> {
  const startTime = performance.now();

  try {
    const url = buildAgentCoreUrl(agentArn, region);
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    };
    if (accessToken) {
      headers["Authorization"] = `Bearer ${accessToken}`;
    }
    const response = await axios.post(url, payload, {
      headers,
    });

    const endTime = performance.now();
    const latency = endTime - startTime;

    return {
      data: response.data,
      latency,
      success: true,
    };
  } catch (error) {
    const endTime = performance.now();
    const latency = endTime - startTime;

    return {
      error: error instanceof Error ? error.message : "Unknown error",
      latency,
      success: false,
    };
  }
}

export async function invokeAgentStream(
  agentArn: string,
  region: string,
  sessionId: string,
  payload: { prompt?: string; ping?: string },
  accessToken: string | null,
  onChunk: (chunk: string) => void,
): Promise<{
  latency: number;
  ttft: number;
  success: boolean;
  error?: string;
}> {
  // If this is a prompt request (not a ping), wait for warmup to complete first
  if (payload.prompt && !payload.ping) {
    await waitForWarmup(sessionId);
  }

  const startTime = performance.now();
  let firstTokenTime: number | null = null;

  try {
    const url = buildAgentCoreUrl(agentArn, region);
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
    };
    if (accessToken) {
      headers["Authorization"] = `Bearer ${accessToken}`;
    }
    const response = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    if (!reader) {
      throw new Error("No response body");
    }

    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      firstTokenTime ??= performance.now();

      buffer += decoder.decode(value, { stream: true });

      // Process complete lines from buffer
      const lines = buffer.split("\n");
      buffer = lines.pop() || ""; // Keep incomplete line in buffer

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        // Handle SSE format: "data: <json>"
        if (trimmed.startsWith("data:")) {
          const jsonStr = trimmed.slice(5).trim();
          try {
            const parsed = JSON.parse(jsonStr);
            if (typeof parsed === "string") {
              onChunk(parsed);
            } else if (parsed.data) {
              onChunk(parsed.data);
            }
          } catch {
            // If JSON parse fails, use the raw string after "data:"
            onChunk(jsonStr);
          }
        } else {
          // Try parsing as plain JSON
          try {
            const parsed = JSON.parse(trimmed);
            if (typeof parsed === "string") {
              onChunk(parsed);
            } else if (parsed.data) {
              onChunk(parsed.data);
            }
          } catch {
            // Not JSON, use as-is
            onChunk(trimmed);
          }
        }
      }
    }

    const endTime = performance.now();
    const latency = endTime - startTime;
    const ttft = firstTokenTime ? firstTokenTime - startTime : latency;

    return {
      latency,
      ttft,
      success: true,
    };
  } catch (error) {
    const endTime = performance.now();
    const latency = endTime - startTime;

    return {
      error: error instanceof Error ? error.message : "Unknown error",
      latency,
      ttft: latency,
      success: false,
    };
  }
}

export function generateSessionId(): string {
  // Generate a session ID that is at least 33 characters long
  const timestamp = Date.now();

  // Use Web Crypto API for cryptographically secure random values
  const array1 = new Uint32Array(2);
  const array2 = new Uint32Array(2);
  crypto.getRandomValues(array1);
  crypto.getRandomValues(array2);

  const random1 = Array.from(array1, (num) => num.toString(36)).join("");
  const random2 = Array.from(array2, (num) => num.toString(36)).join("");

  return `session-${timestamp}-${random1}-${random2}`;
}
