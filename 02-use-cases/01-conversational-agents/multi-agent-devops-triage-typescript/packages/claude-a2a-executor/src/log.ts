/**
 * Lightweight delegation-trail logging.
 *
 * One-line, greppable entries so a CloudWatch Logs Insights query across
 * the agents' log groups can reconstruct an invocation: who was called,
 * with what, what came back, what it cost.
 *
 * NOTE for production use: these lines include (truncated) prompt and
 * response content. Real deployments should redact or disable content
 * logging — set A2A_LOG_CONTENT=0 to log metadata only.
 */

const LOG_CONTENT = process.env.A2A_LOG_CONTENT !== '0';
const SNIPPET_LENGTH = 200;

export function snippet(text: string): string {
  if (!LOG_CONTENT) return '(content logging disabled)';
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > SNIPPET_LENGTH ? `${flat.slice(0, SNIPPET_LENGTH)}…` : flat;
}

/** `[a2a] <component> <event> <key=value …>` — stable, grep-friendly. */
export function logEvent(
  component: string,
  event: string,
  fields: Record<string, string | number | undefined>,
): void {
  const parts = Object.entries(fields)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`);
  console.log(`[a2a] ${component} ${event} ${parts.join(' ')}`);
}
