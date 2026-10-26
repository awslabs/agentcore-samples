import type { ProtocolVersion, SessionSummary } from "../types";
import { NewSessionButton } from "./NewSessionButton";

export function SessionSidebar({
  sessions,
  activeId,
  onSelect,
  onCreate,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: (protocol: ProtocolVersion, policySessionId?: string) => void;
}) {
  return (
    <aside className="sidebar">
      <h2>Sessions</h2>
      <NewSessionButton onCreate={onCreate} />
      <ul className="session-list">
        {sessions.map((s) => (
          <li
            key={s.id}
            className={s.id === activeId ? "active" : ""}
            onClick={() => onSelect(s.id)}
          >
            <span className="session-label">{s.label}</span>
            <span className={`badge badge-${s.protocol}`}>{s.protocol}</span>
          </li>
        ))}
        {sessions.length === 0 && <li className="muted">No sessions yet</li>}
      </ul>
    </aside>
  );
}
