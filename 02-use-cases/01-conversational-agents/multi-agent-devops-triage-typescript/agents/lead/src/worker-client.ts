import type { Message, SendMessageRequest, Task } from '@a2a-js/sdk';
import { Role, TaskState } from '@a2a-js/sdk';
import type { Client, ClientFactory } from '@a2a-js/sdk/client';
import { extractText, logEvent, snippet, textPart } from '@sample/claude-a2a-executor';

import { createA2AClientFactory } from './a2a-transport.js';

/**
 * Thin A2A client wrapper for calling worker agents.
 *
 * Local mode: base URLs are plain `http://localhost:9000` and requests use
 * plain fetch. Deployed mode: base URLs are AgentCore Runtime invocation
 * URLs and the factory signs every request with SigV4 (see a2a-transport).
 * The A2A payloads are identical in both modes — the runtime is a
 * transparent JSON-RPC proxy.
 */
export class WorkerClient {
  private client?: Client;
  private agentName = 'unknown';
  private readonly factory: ClientFactory;

  constructor(
    private readonly baseUrl: string,
    region: string = process.env.AWS_REGION ?? 'us-east-1',
  ) {
    this.factory = createA2AClientFactory(baseUrl, region);
  }

  /** Discovers the worker via its agent card and prepares the transport. */
  private async connect(): Promise<Client> {
    if (!this.client) {
      // createFromUrl fetches /.well-known/agent-card.json and picks the
      // preferred interface from the card.
      this.client = await this.factory.createFromUrl(this.baseUrl);
      const card = await this.client.getAgentCard();
      this.agentName = card.name;
    }
    return this.client;
  }

  async describe(): Promise<{ name: string; description: string }> {
    const client = await this.connect();
    const card = await client.getAgentCard();
    return { name: card.name, description: card.description };
  }

  /**
   * Sends a message with `message/send` (blocking) and returns the final
   * text: the completed task's last artifact or status message.
   */
  async send(text: string): Promise<string> {
    const client = await this.connect();
    const startedAt = Date.now();
    logEvent('lead', 'delegate.send', { worker: this.agentName, request: snippet(text) });
    try {
      const result = await client.sendMessage(this.request(text));
      const answer = this.finalText(result);
      logEvent('lead', 'delegate.response', {
        worker: this.agentName,
        durationMs: Date.now() - startedAt,
        response: snippet(answer),
      });
      return answer;
    } catch (error) {
      logEvent('lead', 'delegate.failed', {
        worker: this.agentName,
        durationMs: Date.now() - startedAt,
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
  }

  /**
   * Sends a message with `message/stream` and consumes task status updates
   * until a terminal state. `onUpdate` receives intermediate working text
   * (useful for surfacing worker progress to the caller).
   */
  async stream(text: string, onUpdate?: (update: string) => void): Promise<string> {
    const client = await this.connect();
    const startedAt = Date.now();
    logEvent('lead', 'delegate.stream', { worker: this.agentName, request: snippet(text) });
    let lastWorkingText = '';
    let finalText = '';

    for await (const event of client.sendMessageStream(this.request(text))) {
      const payload = event.payload;
      if (!payload) continue;
      switch (payload.$case) {
        case 'statusUpdate': {
          const status = payload.value.status;
          const statusText = extractText(status?.message);
          if (status?.state === TaskState.TASK_STATE_WORKING && statusText) {
            lastWorkingText = statusText;
            onUpdate?.(statusText);
          } else if (status?.state === TaskState.TASK_STATE_COMPLETED && statusText) {
            finalText = statusText;
          } else if (status?.state === TaskState.TASK_STATE_FAILED) {
            throw new Error(
              `${this.agentName} task failed: ${statusText || 'no error details'}`,
            );
          }
          break;
        }
        case 'artifactUpdate': {
          const artifact = payload.value.artifact;
          if (artifact) {
            finalText = extractText({ parts: artifact.parts } as Message);
          }
          break;
        }
        case 'task':
        case 'message':
          break;
      }
    }

    const answer = finalText || lastWorkingText;
    logEvent('lead', 'delegate.response', {
      worker: this.agentName,
      durationMs: Date.now() - startedAt,
      response: snippet(answer),
    });
    return answer;
  }

  private request(text: string): SendMessageRequest {
    return {
      tenant: '',
      message: {
        messageId: crypto.randomUUID(),
        contextId: '',
        taskId: '',
        role: Role.ROLE_USER,
        parts: [textPart(text)],
        metadata: undefined,
        extensions: [],
        referenceTaskIds: [],
      },
      configuration: undefined,
      metadata: undefined,
    };
  }

  private finalText(result: Message | Task): string {
    // Bare message response
    if ('parts' in result) {
      return extractText(result);
    }
    // Task response: prefer the artifact, fall back to the status message.
    const task = result;
    if (task.status?.state === TaskState.TASK_STATE_FAILED) {
      throw new Error(
        `${this.agentName} task failed: ${extractText(task.status.message) || 'no error details'}`,
      );
    }
    const artifact = task.artifacts.at(-1);
    if (artifact) {
      const text = extractText({ parts: artifact.parts } as Message);
      if (text) return text;
    }
    return extractText(task.status?.message);
  }
}
