import { describe, expect, it, vi } from 'vitest';
import { Role, TaskState } from '@a2a-js/sdk';
import type { Message, SendMessageRequest } from '@a2a-js/sdk';
import type { AgentExecutionEvent } from '@a2a-js/sdk/server';
import {
  DefaultExecutionEventBus,
  RequestContext,
  ServerCallContext,
} from '@a2a-js/sdk/server';
import type { SDKMessage } from '@anthropic-ai/claude-agent-sdk';

import {
  ClaudeAgentExecutor,
  buildAgentCard,
  extractBedrockContext,
  extractText,
  runWithBedrockContext,
  textPart,
} from '../src/index.js';
import type { QueryFn } from '../src/index.js';

function userMessage(text: string, taskId: string, contextId: string): Message {
  return {
    messageId: 'msg-1',
    contextId,
    taskId,
    role: Role.ROLE_USER,
    parts: [textPart(text)],
    metadata: undefined,
    extensions: [],
    referenceTaskIds: [],
  };
}

function makeRequestContext(text: string, taskId = 'task-1', contextId = 'ctx-1'): RequestContext {
  const request: SendMessageRequest = {
    tenant: '',
    message: userMessage(text, taskId, contextId),
    configuration: undefined,
    metadata: undefined,
  };
  return new RequestContext(request, taskId, contextId, new ServerCallContext());
}

/** Collects every event the executor publishes until it returns. */
async function runExecutor(
  executor: ClaudeAgentExecutor,
  requestContext: RequestContext,
): Promise<AgentExecutionEvent[]> {
  const events: AgentExecutionEvent[] = [];
  const bus = new DefaultExecutionEventBus();
  bus.on('event', (event) => events.push(event));
  await executor.execute(requestContext, bus);
  return events;
}

function statusStates(events: AgentExecutionEvent[]): TaskState[] {
  return events
    .filter((e) => e.kind === 'statusUpdate')
    .map((e) => (e.kind === 'statusUpdate' ? e.data.status!.state : TaskState.UNRECOGNIZED));
}

/** Fake Claude Agent SDK session: yields scripted messages. */
function mockQuery(messages: SDKMessage[]): QueryFn {
  return () =>
    (async function* () {
      for (const message of messages) {
        yield message;
      }
    })();
}

function assistantMessage(text: string): SDKMessage {
  return {
    type: 'assistant',
    message: { content: [{ type: 'text', text }] },
    parent_tool_use_id: null,
    uuid: '00000000-0000-0000-0000-000000000001',
    session_id: 'session-1',
  } as unknown as SDKMessage;
}

function resultMessage(result: string): SDKMessage {
  return {
    type: 'result',
    subtype: 'success',
    result,
    is_error: false,
  } as unknown as SDKMessage;
}

describe('ClaudeAgentExecutor task lifecycle', () => {
  it('publishes task → working → completed for a successful query', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([assistantMessage('thinking...'), resultMessage('the answer')]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));

    expect(events[0]?.kind).toBe('task');
    if (events[0]?.kind === 'task') {
      expect(events[0].data.id).toBe('task-1');
      expect(events[0].data.status?.state).toBe(TaskState.TASK_STATE_SUBMITTED);
    }

    const states = statusStates(events);
    expect(states[0]).toBe(TaskState.TASK_STATE_WORKING);
    expect(states.at(-1)).toBe(TaskState.TASK_STATE_COMPLETED);
  });

  it('carries the result text on the completed status and as an artifact', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([resultMessage('final result text')]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));

    const completed = events.find(
      (e) => e.kind === 'statusUpdate' && e.data.status?.state === TaskState.TASK_STATE_COMPLETED,
    );
    expect(completed).toBeDefined();
    if (completed?.kind === 'statusUpdate') {
      expect(extractText(completed.data.status?.message)).toBe('final result text');
    }

    const artifact = events.find((e) => e.kind === 'artifactUpdate');
    expect(artifact).toBeDefined();
    if (artifact?.kind === 'artifactUpdate') {
      expect(artifact.data.artifact?.name).toBe('agent_response');
      expect(extractText({ parts: artifact.data.artifact!.parts } as Message)).toBe(
        'final result text',
      );
    }
  });

  it('streams intermediate assistant output as working status updates', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([
        assistantMessage('step one'),
        assistantMessage('step two'),
        resultMessage('done'),
      ]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));

    const workingTexts = events
      .filter(
        (e) => e.kind === 'statusUpdate' && e.data.status?.state === TaskState.TASK_STATE_WORKING,
      )
      .map((e) => (e.kind === 'statusUpdate' ? extractText(e.data.status?.message) : ''));

    expect(workingTexts).toContain('step one');
    expect(workingTexts).toContain('step two');
  });

  it('suppresses intermediate updates when streamIntermediateUpdates is false', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      streamIntermediateUpdates: false,
      queryFn: mockQuery([assistantMessage('internal reasoning'), resultMessage('done')]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));

    const workingWithText = events.filter(
      (e) =>
        e.kind === 'statusUpdate' &&
        e.data.status?.state === TaskState.TASK_STATE_WORKING &&
        e.data.status.message !== undefined,
    );
    expect(workingWithText).toHaveLength(0);
  });

  it('publishes failed when the SDK reports an error result', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([
        {
          type: 'result',
          subtype: 'error_max_turns',
          is_error: true,
        } as unknown as SDKMessage,
      ]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));
    expect(statusStates(events).at(-1)).toBe(TaskState.TASK_STATE_FAILED);
  });

  it('publishes failed when the query throws', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: () =>
        (async function* (): AsyncGenerator<SDKMessage> {
          throw new Error('subprocess exploded');
          yield undefined as never;
        })(),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));

    const failed = events.find(
      (e) => e.kind === 'statusUpdate' && e.data.status?.state === TaskState.TASK_STATE_FAILED,
    );
    expect(failed).toBeDefined();
    if (failed?.kind === 'statusUpdate') {
      expect(extractText(failed.data.status?.message)).toContain('subprocess exploded');
    }
  });

  it('publishes failed when the stream ends without a result message', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([assistantMessage('partial')]),
    });

    const events = await runExecutor(executor, makeRequestContext('question'));
    expect(statusStates(events).at(-1)).toBe(TaskState.TASK_STATE_FAILED);
  });

  it('re-publishes the existing task on follow-up turns instead of a new submitted task', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([resultMessage('follow-up answer')]),
    });

    const existingTask = {
      id: 'task-1',
      contextId: 'ctx-1',
      status: {
        state: TaskState.TASK_STATE_INPUT_REQUIRED,
        message: undefined,
        timestamp: new Date().toISOString(),
      },
      artifacts: [],
      history: [],
      metadata: undefined,
    };
    const request: SendMessageRequest = {
      tenant: '',
      message: userMessage('more', 'task-1', 'ctx-1'),
      configuration: undefined,
      metadata: undefined,
    };
    const requestContext = new RequestContext(
      request,
      'task-1',
      'ctx-1',
      new ServerCallContext(),
      existingTask,
    );

    const events = await runExecutor(executor, requestContext);

    expect(events[0]?.kind).toBe('task');
    if (events[0]?.kind === 'task') {
      expect(events[0].data.status?.state).toBe(TaskState.TASK_STATE_INPUT_REQUIRED);
    }
  });
});

describe('ClaudeAgentExecutor delegation-trail logging', () => {
  it('includes the Bedrock session and request ids when a context is active', async () => {
    const lines: string[] = [];
    const spy = vi.spyOn(console, 'log').mockImplementation((line: string) => {
      lines.push(String(line));
    });
    try {
      const executor = new ClaudeAgentExecutor({
        systemPrompt: 'test',
        queryFn: mockQuery([resultMessage('done')]),
      });

      const context = extractBedrockContext({
        'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-log',
        'x-amzn-bedrock-agentcore-runtime-request-id': 'req-log',
      });
      await runWithBedrockContext(context, () =>
        runExecutor(executor, makeRequestContext('question')),
      );

      const received = lines.find((l) => l.includes('executor task.received'));
      expect(received).toContain('sess-log');
      expect(received).toContain('req-log');
    } finally {
      spy.mockRestore();
    }
  });

  it('logs task lifecycle and tool calls in greppable form', async () => {
    const lines: string[] = [];
    const spy = vi.spyOn(console, 'log').mockImplementation((line: string) => {
      lines.push(String(line));
    });
    try {
      const toolUseMessage = {
        type: 'assistant',
        message: {
          content: [
            { type: 'tool_use', id: 'toolu_1', name: 'lookup_service', input: { service: 'orders-api' } },
          ],
        },
        parent_tool_use_id: null,
        uuid: '00000000-0000-0000-0000-000000000002',
        session_id: 'session-1',
      } as unknown as SDKMessage;

      const executor = new ClaudeAgentExecutor({
        systemPrompt: 'test',
        queryFn: mockQuery([toolUseMessage, resultMessage('done')]),
      });
      await runExecutor(executor, makeRequestContext('inspect the logs'));

      expect(lines.some((l) => l.includes('executor task.received') && l.includes('task-1'))).toBe(true);
      expect(lines.some((l) => l.includes('executor tool.call') && l.includes('lookup_service'))).toBe(true);
      expect(lines.some((l) => l.includes('executor task.result') && l.includes('done'))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });
});

describe('ClaudeAgentExecutor cancellation', () => {
  it('aborts the in-flight query and publishes canceled', async () => {
    let aborted = false;

    const queryFn: QueryFn = ({ options }) => {
      const signal = options?.abortController?.signal;
      return (async function* (): AsyncGenerator<SDKMessage> {
        yield assistantMessage('working on it');
        // Simulate a long-running query that reacts to abort.
        await new Promise<void>((resolve) => {
          if (signal?.aborted) return resolve();
          signal?.addEventListener('abort', () => {
            aborted = true;
            resolve();
          });
        });
      })();
    };

    const executor = new ClaudeAgentExecutor({ systemPrompt: 'test', queryFn });

    const events: AgentExecutionEvent[] = [];
    const bus = new DefaultExecutionEventBus();
    bus.on('event', (event) => events.push(event));

    const running = executor.execute(makeRequestContext('question'), bus);
    // Give the executor a tick to start consuming the mock stream.
    await new Promise((resolve) => setTimeout(resolve, 10));
    await executor.cancelTask('task-1', bus);
    await running;

    expect(aborted).toBe(true);
    const states = statusStates(events);
    expect(states).toContain(TaskState.TASK_STATE_CANCELED);
    expect(states.at(-1)).not.toBe(TaskState.TASK_STATE_COMPLETED);
  });

  it('publishes canceled even when no query is in flight', async () => {
    const executor = new ClaudeAgentExecutor({
      systemPrompt: 'test',
      queryFn: mockQuery([]),
    });

    const events: AgentExecutionEvent[] = [];
    const bus = new DefaultExecutionEventBus();
    bus.on('event', (event) => events.push(event));

    await executor.cancelTask('unknown-task', bus);
    expect(statusStates(events)).toContain(TaskState.TASK_STATE_CANCELED);
  });
});

describe('buildAgentCard', () => {
  it('populates the card from name/description with sensible defaults', () => {
    const card = buildAgentCard({
      name: 'Test Agent',
      description: 'An agent for testing',
      skills: [{ id: 'skill-1', name: 'Testing', description: 'Runs tests' }],
    });

    expect(card.name).toBe('Test Agent');
    expect(card.description).toBe('An agent for testing');
    expect(card.capabilities?.streaming).toBe(true);
    expect(card.skills).toHaveLength(1);
    expect(card.supportedInterfaces.length).toBeGreaterThanOrEqual(1);
    expect(card.supportedInterfaces[0]?.url).toBe('http://localhost:9000/');
    expect(card.supportedInterfaces[0]?.protocolBinding).toBe('JSONRPC');
  });

  it('respects AGENTCORE_RUNTIME_URL when set (deployed mode)', () => {
    const runtimeUrl = 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn/invocations/';
    process.env.AGENTCORE_RUNTIME_URL = runtimeUrl;
    try {
      const card = buildAgentCard({ name: 'Deployed', description: 'x' });
      expect(card.supportedInterfaces[0]?.url).toBe(runtimeUrl);
    } finally {
      delete process.env.AGENTCORE_RUNTIME_URL;
    }
  });

  it('declares a v0.3 mirror interface for legacy clients', () => {
    const card = buildAgentCard({ name: 'Compat', description: 'x' });
    const versions = card.supportedInterfaces.map((i) => i.protocolVersion);
    expect(versions).toContain('1.0');
    expect(versions).toContain('0.3');
  });
});
