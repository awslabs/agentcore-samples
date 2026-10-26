/**
 * Tiny structured logger. Redacts anything that looks like a bearer token or a
 * client secret so tokens never land in stdout.
 */

const SECRET_PATTERNS: RegExp[] = [
  /(Bearer\s+)[A-Za-z0-9._-]+/gi,
  /(access_token"\s*:\s*")[^"]+/gi,
  /(client_secret=)[^&\s]+/gi,
];

function redact(value: unknown): unknown {
  if (typeof value !== "string") return value;
  let out = value;
  for (const pat of SECRET_PATTERNS) {
    out = out.replace(pat, "$1[redacted]");
  }
  return out;
}

function emit(level: string, msg: string, extra?: Record<string, unknown>): void {
  const parts: unknown[] = [`[${level}]`, redact(msg)];
  if (extra) {
    const safe: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(extra)) safe[k] = redact(v);
    parts.push(safe);
  }
  // eslint-disable-next-line no-console
  console.log(...parts);
}

export const log = {
  info: (msg: string, extra?: Record<string, unknown>) => emit("info", msg, extra),
  warn: (msg: string, extra?: Record<string, unknown>) => emit("warn", msg, extra),
  error: (msg: string, extra?: Record<string, unknown>) => emit("error", msg, extra),
  debug: (msg: string, extra?: Record<string, unknown>) => {
    if (process.env.DEBUG_MCP) emit("debug", msg, extra);
  },
};
