import { randomUUID } from 'node:crypto';

import { BedrockAgentCoreApp } from 'bedrock-agentcore/runtime';
import { createSdkMcpServer, query, tool } from '@anthropic-ai/claude-agent-sdk';
import { buildRuntimeUrl } from 'bedrock-agentcore/runtime/a2a';
import { z } from 'zod';

import { WorkerClient } from './worker-client.js';

/**
 * Lead triage agent.
 *
 * Inbound edge: HTTP protocol (`BedrockAgentCoreApp`, port 8080) — its
 * caller is an application, not another agent, so A2A JSON-RPC framing
 * would add friction for no benefit.
 *
 * Outbound edge: A2A JSON-RPC to the two workers. Delegation is exposed to
 * Claude as two in-process SDK tools (`delegate_to_log_analyst`,
 * `delegate_to_runbook`) that wrap the A2A client.
 */

// Worker addressing, in precedence order:
//  - *_RUNTIME_ARN (deployed): A2A goes through the SigV4-signed
//    InvokeAgentRuntime endpoint derived from the ARN
//  - *_URL (local / compose): direct A2A to the worker container
const REGION = process.env.AWS_REGION ?? 'us-east-1';

function workerUrl(arnVar: string, urlVar: string, fallback: string): string {
  const arn = process.env[arnVar];
  if (arn) return buildRuntimeUrl(arn, REGION);
  return process.env[urlVar] ?? fallback;
}

const LOG_ANALYST_URL = workerUrl(
  'LOG_ANALYST_RUNTIME_ARN',
  'LOG_ANALYST_URL',
  'http://localhost:9001',
);
const RUNBOOK_URL = workerUrl('RUNBOOK_RUNTIME_ARN', 'RUNBOOK_URL', 'http://localhost:9002');

const SYSTEM_PROMPT = `You are the lead agent of a DevOps incident triage copilot.
You have two specialist workers available as tools:
- delegate_to_log_analyst: analyzes log/metric excerpts. Pass it ALL log and
  metric data from the user's report, verbatim, plus the question.
- delegate_to_runbook: looks up service ownership, escalation contacts, and
  runbook steps from the service catalog. Tell it the service name and symptom.
For an incident report, consult BOTH workers, then compose a triage summary:
1. Suspected cause (from log analysis)
2. Owning team and escalation contact (from the runbook worker)
3. Recommended next steps (from the runbook worker, tailored to the findings)
Keep the final answer concise and actionable.`;

/**
 * Builds the delegation tools for one triage request.
 *
 * Per-request rather than once at startup because the worker clients carry the
 * caller's `sessionId`; sharing them would funnel unrelated conversations
 * through a single worker session. Costs one agent-card fetch per worker per
 * request — the in-process MCP server itself does no I/O.
 */
function buildDelegationServer(sessionId: string): ReturnType<typeof createSdkMcpServer> {
  const logAnalyst = new WorkerClient(LOG_ANALYST_URL, sessionId, REGION);
  const runbook = new WorkerClient(RUNBOOK_URL, sessionId, REGION);

  return createSdkMcpServer({
    name: 'workers',
    version: '1.0.0',
    tools: [
      tool(
        'delegate_to_log_analyst',
        'Delegate log/metric analysis to the log-analyst worker agent. Include the raw log lines and metrics in the request.',
        {
          request: z
            .string()
            .describe('The analysis request, including all relevant log/metric data'),
        },
        async ({ request }) => {
          // Streaming A2A call (message/stream) — exercises the second
          // interaction pattern required by the sample. WorkerClient logs the
          // delegate.stream/response trail; this callback surfaces the
          // worker's intermediate output as it streams in.
          const answer = await logAnalyst.stream(request, (update) =>
            console.log(`[lead] log-analyst update: ${update.slice(0, 120)}`),
          );
          return { content: [{ type: 'text', text: answer }] };
        },
      ),
      tool(
        'delegate_to_runbook',
        'Delegate a service-catalog/runbook lookup to the runbook worker agent. Name the service and the observed symptom.',
        { request: z.string().describe('The lookup request, naming the service and symptom') },
        async ({ request }) => {
          // Blocking A2A call (message/send). WorkerClient logs the
          // delegate.send/response/failed trail.
          const answer = await runbook.send(request);
          return { content: [{ type: 'text', text: answer }] };
        },
      ),
    ],
  });
}

async function triage(prompt: string, sessionId: string): Promise<string> {
  const session = query({
    prompt,
    options: {
      systemPrompt: SYSTEM_PROMPT,
      env: { ...process.env, CLAUDE_CODE_USE_BEDROCK: '1' },
      model: process.env.ANTHROPIC_MODEL,
      tools: [],
      mcpServers: { workers: buildDelegationServer(sessionId) },
      allowedTools: [
        'mcp__workers__delegate_to_log_analyst',
        'mcp__workers__delegate_to_runbook',
      ],
      maxTurns: 12,
      settingSources: [],
    },
  });

  for await (const message of session) {
    if (message.type === 'result') {
      if (message.subtype === 'success') return message.result;
      throw new Error(`Lead agent query failed: ${message.subtype}`);
    }
  }
  throw new Error('Lead agent query ended without a result');
}

const app = new BedrockAgentCoreApp({
  invocationHandler: {
    requestSchema: z.object({ prompt: z.string() }),
    process: async (request, context) => {
      // Forward the caller's session to the workers so one id ties the
      // whole delegation chain together in the logs. A direct curl without the
      // session header (local dev) gets a generated one.
      const sessionId = context.sessionId || randomUUID();
      context.log.info({ prompt: request.prompt, sessionId }, 'triage request received');
      const answer = await triage(request.prompt, sessionId);
      return { answer };
    },
  },
});

app.run({ port: Number(process.env.PORT ?? 8080) });
