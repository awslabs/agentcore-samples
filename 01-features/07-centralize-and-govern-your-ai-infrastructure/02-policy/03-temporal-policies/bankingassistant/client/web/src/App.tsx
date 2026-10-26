import { SessionSidebar } from "./components/SessionSidebar";
import { SessionHeader } from "./components/SessionHeader";
import { ToolList } from "./components/ToolList";
import { ChatWindow } from "./components/ChatWindow";
import { useSessions } from "./state/useSessions";

export function App() {
  const s = useSessions();

  return (
    <div className="app">
      <SessionSidebar
        sessions={s.sessions}
        activeId={s.activeId}
        onSelect={s.selectSession}
        onCreate={s.createSession}
      />
      <main className="main">
        <div className="topbar">
          <h1>Banking Assistant</h1>
          {s.config && (
            <span className="gateway">
              {s.config.mock ? "MOCK MODE · " : ""}
              {s.config.region} · {s.config.gatewayUrl}
            </span>
          )}
        </div>

        {s.error && (
          <div className="error" role="alert" onClick={s.clearError}>
            {s.error} <span className="dismiss">(dismiss)</span>
          </div>
        )}

        {s.notice && (
          <div className="notice" role="status" onClick={s.clearNotice}>
            {s.notice} <span className="dismiss">(dismiss)</span>
          </div>
        )}

        {s.activeSession ? (
          <>
            <SessionHeader session={s.activeSession} />
            <ToolList tools={s.activeSession.tools} onRefresh={s.refreshTools} />
            <ChatWindow
              session={s.activeSession}
              sending={s.sending}
              onSend={s.sendMessage}
            />
          </>
        ) : (
          <div className="empty">
            Create a session to start. Pick a protocol on the left.
          </div>
        )}
      </main>
    </div>
  );
}
