import { useState } from "react";

export function Composer({
  sending,
  onSend,
}: {
  sending: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t || sending) return;
    onSend(t);
    setText("");
  };

  return (
    <div className="composer">
      <textarea
        value={text}
        placeholder="Message the banking assistant…"
        rows={2}
        disabled={sending}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button onClick={submit} disabled={sending || !text.trim()}>
        {sending ? "…" : "Send"}
      </button>
    </div>
  );
}
