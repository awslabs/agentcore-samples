import type { SessionDTO } from "../types";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";

export function ChatWindow({
  session,
  sending,
  onSend,
}: {
  session: SessionDTO;
  sending: boolean;
  onSend: (text: string) => void;
}) {
  return (
    <div className="chat-window">
      <MessageList messages={session.messages} streaming={sending} />
      <Composer sending={sending} onSend={onSend} />
    </div>
  );
}
