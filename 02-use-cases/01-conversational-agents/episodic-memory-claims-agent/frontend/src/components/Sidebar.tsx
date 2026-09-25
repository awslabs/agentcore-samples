import type { Session } from '../types';
import './Sidebar.css';

interface SidebarProps {
  sessions: Session[];
  currentSessionId: string | null;
  onNewConversation: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  collapsed: boolean;
}

export default function Sidebar({
  sessions,
  currentSessionId,
  onNewConversation,
  onSelectSession,
  onDeleteSession,
  collapsed,
}: SidebarProps) {
  return (
    <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
      <div className="sidebar-header">
        <span className="sidebar-heading">Conversations</span>
        <button
          className="sidebar-new-btn"
          onClick={onNewConversation}
          title="New conversation"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="8" y1="3" x2="8" y2="13" />
            <line x1="3" y1="8" x2="13" y2="8" />
          </svg>
        </button>
      </div>

      <div className="sidebar-list">
        {sessions.length === 0 ? (
          <div className="sidebar-empty">No conversations yet</div>
        ) : (
          sessions.map(s => (
            <SessionItem
              key={s.session_id}
              session={s}
              active={s.session_id === currentSessionId}
              onSelect={() => onSelectSession(s.session_id)}
              onDelete={() => onDeleteSession(s.session_id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}

function SessionItem({
  session,
  active,
  onSelect,
  onDelete,
}: {
  session: Session;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`sidebar-item ${active ? 'sidebar-item--active' : ''}`}
      onClick={onSelect}
    >
      <div className="sidebar-item-title" title={session.session_title}>
        {session.session_title}
      </div>
      <div className="sidebar-item-date">{formatDate(session.updated_at)}</div>
      <button
        className="sidebar-item-delete"
        title="Delete"
        onClick={e => { e.stopPropagation(); onDelete(); }}
      >
        ×
      </button>
    </div>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString();
}
