import { BedrockAgentCoreApp } from 'bedrock-agentcore/runtime';
import { createSdkMcpServer, query, tool } from '@anthropic-ai/claude-agent-sdk';
import { runtimeUrlFromArn } from '@sample/claude-a2a-executor';
import { z } from 'zod';

import { notifyIncidentChannel } from './notify.js';
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
  if (arn) return runtimeUrlFromArn(arn, REGION);
  return process.env[urlVar] ?? fallback;
}

const logAnalyst = new WorkerClient(
  workerUrl('LOG_ANALYST_RUNTIME_ARN', 'LOG_ANALYST_URL', 'http://localhost:9001'),
  REGION,
);
const runbook = new WorkerClient(
  workerUrl('RUNBOOK_RUNTIME_ARN', 'RUNBOOK_URL', 'http://localhost:9002'),
  REGION,
);

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

// Delegation tools run in-process via the SDK's MCP transport — the A2A
// calls happen inside the tool handlers, so worker output never needs
// envelope handling anywhere.
const delegationServer = createSdkMcpServer({
  name: 'workers',
  version: '1.0.0',
  tools: [
    tool(
      'delegate_to_log_analyst',
      'Delegate log/metric analysis to the log-analyst worker agent. Include the raw log lines and metrics in the request.',
      { request: z.string().describe('The analysis request, including all relevant log/metric data') },
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

async function triage(prompt: string): Promise<string> {
  const session = query({
    prompt,
    options: {
      systemPrompt: SYSTEM_PROMPT,
      env: { ...process.env, CLAUDE_CODE_USE_BEDROCK: '1' },
      model: process.env.ANTHROPIC_MODEL,
      tools: [],
      mcpServers: { workers: delegationServer },
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
      context.log.info({ prompt: request.prompt }, 'triage request received');
      const answer = await triage(request.prompt);
      // Pain #7: outbound credential via AgentCore Identity (withApiKey),
      // not hand-rolled env plumbing. Best-effort; skipped in local mode.
      const notified = await notifyIncidentChannel(answer, context.workloadAccessToken);
      return { answer, notified };
    },
  },
});

app.run({ port: Number(process.env.PORT ?? 8080) });
