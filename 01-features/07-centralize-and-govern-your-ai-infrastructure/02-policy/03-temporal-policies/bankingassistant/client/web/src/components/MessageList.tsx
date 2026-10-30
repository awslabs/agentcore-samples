import { useEffect, useRef } from "react";

import type { ChatMessage } from "../types";
import { MessageBubble } from "./MessageBubble";

export function MessageList({
  messages,
  streaming = false,
}: {
  messages: ChatMessage[];
  streaming?: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const lastIdx = messages.length - 1;
  return (
    <div className="message-list">
      {messages.length === 0 && (
        <div className="muted">No messages yet. Try "Check the balance of ACC-1001".</div>
      )}
      {messages.map((m, i) => (
        <MessageBubble
          key={i}
          message={m}
          streaming={streaming && i === lastIdx && m.role === "assistant"}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}
