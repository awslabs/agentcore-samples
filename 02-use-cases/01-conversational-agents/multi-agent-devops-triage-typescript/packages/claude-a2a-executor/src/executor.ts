import type {
  AgentExecutor,
  ExecutionEventBus,
  RequestContext,
} from '@a2a-js/sdk/server';
import { AgentEvent } from '@a2a-js/sdk/server';
import type { Task, TaskStatus } from '@a2a-js/sdk';
import { TaskState } from '@a2a-js/sdk';
import type { Options, Query, SDKMessage } from '@anthropic-ai/claude-agent-sdk';
import { query as sdkQuery } from '@anthropic-ai/claude-agent-sdk';

import { getBedrockContext } from './bedrock-context.js';
import { logEvent, snippet } from './log.js';
import { agentMessage, extractText, textPart } from './messages.js';

/**
 * A function with the same shape as the Claude Agent SDK's `query()`.
 * Injectable so unit tests can substitute a mock session without spawning
 * the Claude Code subprocess.
 */
export type QueryFn = (params: {
  prompt: string;
  options?: Options;
}) => AsyncIterable<SDKMessage> & Partial<Pick<Query, 'interrupt' | 'close'>>;

export interface ClaudeAgentExecutorConfig {
  /**
   * System prompt for the agent. Scenario-specific behavior belongs here,
   * in the caller — never inside this package.
   */
  systemPrompt: string;
  /**
   * Claude Agent SDK options merged into every query. Use this to set
   * `model`, `env`, `mcpServers`, `allowedTools`, etc.
   */
  queryOptions?: Options;
  /**
   * Emit intermediate assistant text as A2A `working` status updates while
   * the query runs. Defaults to true.
   */
  streamIntermediateUpdates?: boolean;
  /** Injectable query function; defaults to the real Claude Agent SDK. */
  queryFn?: QueryFn;
}

/**
 * Bridges a Claude Agent SDK session to the `@a2a-js/sdk` `AgentExecutor`
 * interface — the TypeScript analog of the Python SDK's `StrandsA2AExecutor`.
 *
 * Task lifecycle mapping:
 *  - A2A `submitted`  → published immediately for new tasks
 *  - A2A `working`    → published when the SDK query starts; intermediate
 *                       assistant messages stream as further `working` updates
 *  - A2A `completed`  → published with the SDK result text when the query
 *                       finishes successfully
 *  - A2A `failed`     → published when the query throws or returns an error
 *  - A2A `canceled`   → published when `cancelTask` interrupts the query
 */
export class ClaudeAgentExecutor implements AgentExecutor {
  private readonly config: ClaudeAgentExecutorConfig;
  private readonly queryFn: QueryFn;
  /** AbortControllers of in-flight queries, keyed by A2A task id. */
  private readonly activeQueries = new Map<string, AbortController>();

  constructor(config: ClaudeAgentExecutorConfig) {
    this.config = config;
    this.queryFn = config.queryFn ?? (sdkQuery as unknown as QueryFn);
  }

  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const { taskId, contextId } = requestContext;
    const prompt = extractText(requestContext.userMessage);

    // Correlation with the AgentCore runtime's own request log: sessionId
    // and requestId come from the injected headers (see bedrock-context.ts)
    // and are undefined when running outside a serveA2A request scope.
    const bedrock = getBedrockContext();
    logEvent('executor', 'task.received', {
      taskId,
      contextId,
      sessionId: bedrock?.sessionId,
      requestId: bedrock?.requestId,
      prompt: snippet(prompt),
    });

    // The server requires the first published event to be a `task` (or
    // bare `message`). Publish `submitted` only for brand-new tasks;
    // follow-up turns re-publish the existing task object as-is.
    if (!requestContext.task) {
      eventBus.publish(AgentEvent.task(this.newTask(taskId, contextId)));
    } else {
      eventBus.publish(AgentEvent.task(requestContext.task));
    }

    this.publishStatus(eventBus, taskId, contextId, TaskState.TASK_STATE_WORKING);

    const abortController = new AbortController();
    this.activeQueries.set(taskId, abortController);

    try {
      const session = this.queryFn({
        prompt,
        options: {
          ...this.config.queryOptions,
          systemPrompt: this.config.systemPrompt,
          abortController,
        },
      });

      let resultText: string | undefined;
      let errorText: string | undefined;

      for await (const message of session) {
        if (abortController.signal.aborted) break;

        if (message.type === 'assistant') {
          // Tool calls run inside the SDK subprocess, so they never appear
          // as outbound HTTP from this process — the message stream is the
          // one place to log the worker's "outgoing requests".
          logToolUses(taskId, message);
          const text = assistantText(message);
          if (text && this.config.streamIntermediateUpdates !== false) {
            this.publishStatus(eventBus, taskId, contextId, TaskState.TASK_STATE_WORKING, text);
          }
        } else if (message.type === 'result') {
          if (message.subtype === 'success') {
            resultText = message.result;
            logEvent('executor', 'task.result', {
              taskId,
              turns: message.num_turns,
              costUsd: message.total_cost_usd,
              durationMs: message.duration_ms,
              result: snippet(message.result),
            });
          } else {
            errorText = `Claude Agent SDK query failed: ${message.subtype}`;
          }
        }
      }

      if (abortController.signal.aborted) {
        // cancelTask already published the `canceled` status.
        return;
      }

      if (errorText !== undefined || resultText === undefined) {
        const failure = errorText ?? 'Claude Agent SDK query produced no result message';
        logEvent('executor', 'task.failed', { taskId, error: failure });
        this.publishStatus(eventBus, taskId, contextId, TaskState.TASK_STATE_FAILED, failure);
        return;
      }

      this.publishStatus(eventBus, taskId, contextId, TaskState.TASK_STATE_COMPLETED, resultText);
    } catch (error) {
      if (!abortController.signal.aborted) {
        const failure = error instanceof Error ? error.message : String(error);
        logEvent('executor', 'task.failed', { taskId, error: failure });
        this.publishStatus(eventBus, taskId, contextId, TaskState.TASK_STATE_FAILED, failure);
      }
    } finally {
      this.activeQueries.delete(taskId);
    }
  }

  async cancelTask(taskId: string, eventBus: ExecutionEventBus): Promise<void> {
    logEvent('executor', 'task.canceled', { taskId });
    const abortController = this.activeQueries.get(taskId);
    if (abortController) {
      abortController.abort();
      this.activeQueries.delete(taskId);
    }
    // contextId is unknown at cancellation time on this code path; the
    // ResultManager reconciles the update against the stored task by taskId.
    eventBus.publish(
      AgentEvent.statusUpdate({
        taskId,
        contextId: '',
        status: {
          state: TaskState.TASK_STATE_CANCELED,
          message: undefined,
          timestamp: new Date().toISOString(),
        },
        metadata: undefined,
      }),
    );
  }

  private newTask(taskId: string, contextId: string): Task {
    return {
      id: taskId,
      contextId,
      status: this.status(TaskState.TASK_STATE_SUBMITTED),
      artifacts: [],
      history: [],
      metadata: undefined,
    };
  }

  private status(state: TaskState, taskId?: string, contextId?: string, text?: string): TaskStatus {
    return {
      state,
      message:
        text !== undefined && taskId !== undefined && contextId !== undefined
          ? agentMessage({ text, taskId, contextId })
          : undefined,
      timestamp: new Date().toISOString(),
    };
  }

  private publishStatus(
    eventBus: ExecutionEventBus,
    taskId: string,
    contextId: string,
    state: TaskState,
    text?: string,
  ): void {
    // On completion, attach the answer as a task artifact so blocking
    // (message/send) callers find it on the returned Task object. This must
    // be published BEFORE the terminal status update: the server's event
    // queue stops consuming once it sees a terminal state.
    if (state === TaskState.TASK_STATE_COMPLETED && text !== undefined) {
      eventBus.publish(
        AgentEvent.artifactUpdate({
          taskId,
          contextId,
          artifact: {
            artifactId: `${taskId}-result`,
            name: 'agent_response',
            description: '',
            parts: [textPart(text)],
            metadata: undefined,
            extensions: [],
          },
          append: false,
          lastChunk: true,
          metadata: undefined,
        }),
      );
    }
    eventBus.publish(
      AgentEvent.statusUpdate({
        taskId,
        contextId,
        status: this.status(state, taskId, contextId, text),
        metadata: undefined,
      }),
    );
  }
}

/** Concatenates the text blocks of an SDK assistant message. */
function assistantText(message: Extract<SDKMessage, { type: 'assistant' }>): string {
  const content = message.message.content;
  if (typeof content === 'string') return content;
  return content
    .map((block) => ('text' in block && typeof block.text === 'string' ? block.text : ''))
    .filter((text) => text.length > 0)
    .join('\n');
}

/** Logs every tool_use block in an assistant message (the worker's "outgoing requests"). */
function logToolUses(taskId: string, message: Extract<SDKMessage, { type: 'assistant' }>): void {
  const content = message.message.content;
  if (typeof content === 'string') return;
  for (const block of content) {
    if (block.type === 'tool_use') {
      logEvent('executor', 'tool.call', {
        taskId,
        tool: block.name,
        input: snippet(JSON.stringify(block.input)),
      });
    }
  }
}
