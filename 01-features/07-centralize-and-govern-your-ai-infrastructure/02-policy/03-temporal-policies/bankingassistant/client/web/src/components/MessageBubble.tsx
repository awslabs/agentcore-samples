import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ChatMessage } from "../types";

function fmt(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function MessageBubble({
  message,
  streaming = false,
}: {
  message: ChatMessage;
  streaming?: boolean;
}) {
  const noContentYet = !message.content && (message.toolEvents?.length ?? 0) === 0;
  return (
    <div className={`bubble ${message.role}`}>
      <div className="role">{message.role}</div>
      {message.content &&
        (message.role === "assistant" ? (
          <div className="text markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="text">{message.content}</div>
        ))}
      {streaming && noContentYet && <div className="thinking">…</div>}
      {message.toolEvents?.map((ev, i) => {
        const pending = ev.result === null;
        return (
        <details key={i} open={pending} className={`tool-event ${ev.isError ? "err" : ""} ${pending ? "pending" : ""}`}>
          <summary>
            {pending ? "⏳ " : ev.isError ? "⚠ " : "🔧 "}
            {ev.name}
            {pending && <span className="tool-waiting"> calling…</span>}
          </summary>
          <div className="tool-body">
            <div className="tool-label">args</div>
            <pre>{fmt(ev.args)}</pre>
            {!pending && (
              <>
                <div className="tool-label">{ev.isError ? "error" : "result"}</div>
                <pre>{fmt(ev.result)}</pre>
              </>
            )}
          </div>
        </details>
        );
      })}
    </div>
  );
}
