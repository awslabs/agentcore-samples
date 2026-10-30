import { useState, useRef, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useChat, type ChatMessage } from '../hooks/useChat';
import { useSessions } from '../hooks/useSessions';
import Sidebar from '../components/Sidebar';
import Markdown from '../components/Markdown';
import './ChatPage.css';

const CAPABILITIES = [
  'File and process insurance claims',
  'Investigate coverage and fraud indicators',
  'Make adjudication decisions (approve / deny / escalate)',
  'Learn from past claims via episodic memory',
];

const SAMPLE_PROMPTS = [
  'I was rear-ended at a stoplight yesterday. My bumper is damaged. Policy AU-2024-1001.',
  'My basement flooded from a burst pipe. Policy HO-2024-1001. Happened about a week ago.',
  'Someone broke into my garage and stole my tools and electronics. Policy HO-2024-1087.',
  'A big storm blew shingles off my roof and broke a window. Policy HO-2024-1042.',
];

export default function ChatPage() {
  const { session, logout } = useAuth();
  const [input, setInput] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const {
    sessions, currentSession, listSessions,
    createSession, updateTitle, deleteSession,
    getMessages, switchSession, clearCurrent,
  } = useSessions();

  const { messages, isLoading, sendMessage, loadMessages, clearMessages } = useChat();

  // Load sessions on mount
  useEffect(() => { listSessions(); }, [listSessions]);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // --- Session actions ---

  const handleNewConversation = useCallback(() => {
    clearCurrent();
    clearMessages();
  }, [clearCurrent, clearMessages]);

  const handleSelectSession = useCallback(async (sessionId: string) => {
    const s = switchSession(sessionId);
    if (!s) return;
    setLoadingHistory(true);
    clearMessages();
    try {
      const msgs = await getMessages(sessionId);
      if (msgs.length > 0) loadMessages(msgs);
    } catch (e) {
      console.error('Failed to load messages:', e);
    } finally {
      setLoadingHistory(false);
    }
  }, [switchSession, getMessages, loadMessages, clearMessages]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    await deleteSession(sessionId);
    if (currentSession?.session_id === sessionId) clearMessages();
  }, [deleteSession, currentSession, clearMessages]);

  // --- Send ---

  const handleSend = useCallback(async (text?: string) => {
    const prompt = (text ?? input).trim();
    if (!prompt || isLoading) return;
    if (!text) setInput('');

    const actorId = session?.user.actor_id ?? '';

    // Lazy session creation on first message
    let sid = currentSession?.session_id;
    if (!sid) {
      const title = prompt.length <= 50 ? prompt : prompt.slice(0, 50).trim() + '…';
      const created = await createSession(title);
      sid = created.session_id;
    } else if (messages.length === 0) {
      // First message in existing session with default title — auto-title it
      const title = prompt.length <= 50 ? prompt : prompt.slice(0, 50).trim() + '…';
      updateTitle(sid, title).catch(() => {});
    }

    sendMessage(prompt, actorId, sid);
  }, [input, isLoading, session, currentSession, messages.length, createSession, updateTitle, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!session) return null;

  const hasMessages = messages.length > 0;

  return (
    <div className="chat-page">
      {/* Sidebar */}
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSession?.session_id ?? null}
        onNewConversation={handleNewConversation}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        collapsed={sidebarCollapsed}
      />

      {/* Main area */}
      <div className="chat-main-area">
        {/* Top bar */}
        <header className="chat-topbar">
          <div className="chat-topbar-left">
            <button
              className="chat-topbar-toggle"
              onClick={() => setSidebarCollapsed(c => !c)}
              title="Toggle sidebar"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="3" y1="6" x2="21" y2="6" />
                <line x1="3" y1="12" x2="21" y2="12" />
                <line x1="3" y1="18" x2="21" y2="18" />
              </svg>
            </button>
            <span className="chat-topbar-icon chat-agent-mark"></span>
            <span className="chat-topbar-title">Claims Agent</span>
          </div>
          <div className="chat-topbar-right">
            <div className="chat-topbar-user">
              <span className="chat-topbar-avatar">
                {session.user.username.charAt(0).toUpperCase()}
              </span>
              <span className="chat-topbar-username">{session.user.username}</span>
            </div>
            <button className="chat-topbar-logout" onClick={logout}>Logout</button>
          </div>
        </header>

        {/* Content */}
        <main className="chat-main">
          {loadingHistory ? (
            <div className="chat-loading-history">Loading conversation…</div>
          ) : !hasMessages ? (
            <div className="chat-welcome">
              <h2 className="chat-welcome-title">Welcome to Claims Agent 👋</h2>
              <p className="chat-welcome-subtitle">
                I'm your AI claims processing assistant. I can help you with:
              </p>
              <ul className="chat-welcome-list">
                {CAPABILITIES.map(cap => (
                  <li key={cap}>
                    <span className="chat-welcome-check">✓</span>
                    {cap}
                  </li>
                ))}
              </ul>
              <div className="chat-prompts">
                <p className="chat-prompts-label">Try one of these:</p>
                <div className="chat-prompts-grid">
                  {SAMPLE_PROMPTS.map(prompt => (
                    <button
                      key={prompt}
                      className="chat-prompt-chip"
                      onClick={() => handleSend(prompt)}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="chat-messages">
              {messages.map(msg => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {isLoading && messages[messages.length - 1]?.content === '' && (
                <div className="chat-thinking">
                  <span className="chat-thinking-dot" />
                  <span className="chat-thinking-dot" />
                  <span className="chat-thinking-dot" />
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}

          {/* Input */}
          <div className="chat-input-bar">
            <input
              className="chat-input"
              type="text"
              placeholder="Describe your claim…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isLoading}
            />
            <button
              className="chat-send-btn"
              aria-label="Send"
              onClick={() => handleSend()}
              disabled={isLoading || !input.trim()}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const isError = message.role === 'error';
  const isAdjuster = message.role === 'adjuster';

  if (isAdjuster) {
    return (
      <div className="chat-bubble-row chat-bubble-adjuster">
        <div className="chat-bubble-avatar-agent"><span className="chat-agent-mark"></span></div>
        <div className="chat-bubble chat-bubble-decision">
          <div className="chat-bubble-decision-label">Adjuster Decision</div>
          <div className="chat-bubble-content">{message.content || '…'}</div>
          <div className="chat-bubble-time">
            {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`chat-bubble-row ${isUser ? 'chat-bubble-user' : 'chat-bubble-assistant'}`}>
      {!isUser && <div className="chat-bubble-avatar-agent"><span className="chat-agent-mark"></span></div>}
      <div className={`chat-bubble ${isError ? 'chat-bubble-error' : ''}`}>
        <div className="chat-bubble-content">
          {isUser || isError ? (
            message.content || '…'
          ) : message.content ? (
            <Markdown content={message.content} />
          ) : (
            '…'
          )}
        </div>
        <div className="chat-bubble-time">
          {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
      </div>
      {isUser && (
        <div className="chat-bubble-avatar-user">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 12c2.7 0 5-2.3 5-5s-2.3-5-5-5-5 2.3-5 5 2.3 5 5 5zm0 2c-3.3 0-10 1.7-10 5v2h20v-2c0-3.3-6.7-5-10-5z"/>
          </svg>
        </div>
      )}
    </div>
  );
}
