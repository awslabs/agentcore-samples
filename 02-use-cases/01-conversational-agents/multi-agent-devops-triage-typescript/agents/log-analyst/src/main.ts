import {
  buildAgentCard,
  ClaudeAgentExecutor,
  serveA2A,
} from '@sample/claude-a2a-executor';

/**
 * Log analyst worker — an A2A server on the AgentCore Runtime A2A protocol
 * path (port 9000). It receives log/metric snippets inside the A2A message
 * and reasons over them; it needs no tools of its own.
 */

const SYSTEM_PROMPT = `You are a log analysis specialist for DevOps incident triage.
You receive log excerpts, metrics, and deployment timelines inside the user message.
Analyze only the provided data — do not invent log lines or metrics.
Identify anomalies, error patterns, and correlations with deploy events.
Reply with a concise findings summary: suspected component, evidence (quote the
relevant log lines), and confidence level. Do not use any tools; reason over the
provided text directly.`;

const PORT = Number(process.env.PORT ?? 9000);

const executor = new ClaudeAgentExecutor({
  systemPrompt: SYSTEM_PROMPT,
  queryOptions: {
    // Model access goes through Amazon Bedrock — never the Anthropic API.
    // CLAUDE_CODE_USE_BEDROCK / ANTHROPIC_MODEL / AWS_REGION come from the
    // container environment; spread process.env so the subprocess keeps
    // AWS credentials and PATH.
    env: { ...process.env, CLAUDE_CODE_USE_BEDROCK: '1' },
    model: process.env.ANTHROPIC_MODEL,
    // Analysis is text-in/text-out: no tools, no filesystem access needed.
    tools: [],
    maxTurns: 4,
    // Keep the container hermetic: don't read ~/.claude settings.
    settingSources: [],
  },
});

await serveA2A({
  agentCard: buildAgentCard({
    name: 'log-analyst',
    description:
      'Analyzes log excerpts and metrics to identify anomalies and correlate them with deployments.',
    skills: [
      {
        id: 'analyze-logs',
        name: 'Analyze logs',
        description:
          'Reasons over provided log/metric snippets to find error patterns and likely causes.',
        tags: ['logs', 'incident-triage'],
        examples: ['Analyze these API gateway logs for the latency spike after the 14:00 deploy.'],
      },
    ],
    // Local A2A clients route via the URL in the card, so it must carry the
    // actual listen port. When deployed, AGENTCORE_RUNTIME_URL wins.
    port: PORT,
  }),
  executor,
  port: PORT,
});
