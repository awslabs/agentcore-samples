import { useState, useCallback, useRef } from 'react';
import type { Session } from '../types';
import { useAuth } from '../context/AuthContext';

const SESSION_API = import.meta.env.VITE_SESSION_API_URL ?? '';

export function useSessions() {
  const { session: auth } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const sessionsRef = useRef<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(false);

  const headers = useCallback(() => ({
    Authorization: `Bearer ${auth?.idToken ?? ''}`,
    'Content-Type': 'application/json',
  }), [auth?.idToken]);

  const setSessionsBoth = useCallback((updater: Session[] | ((prev: Session[]) => Session[])) => {
    setSessions(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      sessionsRef.current = next;
      return next;
    });
  }, []);

  const listSessions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${SESSION_API}/sessions`, { headers: headers() });
      if (!res.ok) throw new Error('Failed to list sessions');
      const data = await res.json();
      const list: Session[] = data.sessions ?? [];
      setSessionsBoth(list);
      return list;
    } catch (e) {
      console.error('listSessions:', e);
      return [];
    } finally {
      setLoading(false);
    }
  }, [headers, setSessionsBoth]);

  const createSession = useCallback(async (title = 'New conversation') => {
    const res = await fetch(`${SESSION_API}/sessions`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ session_title: title }),
    });
    if (!res.ok) throw new Error('Failed to create session');
    const data = await res.json();
    const created: Session = data.session;
    setSessionsBoth(prev => [created, ...prev]);
    setCurrentSession(created);
    return created;
  }, [headers, setSessionsBoth]);

  const updateTitle = useCallback(async (sessionId: string, title: string) => {
    const res = await fetch(`${SESSION_API}/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify({ session_title: title }),
    });
    if (!res.ok) throw new Error('Failed to update session');
    const data = await res.json();
    const updated: Session = data.session;
    setSessionsBoth(prev => prev.map(s => s.session_id === sessionId ? updated : s));
    if (currentSession?.session_id === sessionId) setCurrentSession(updated);
    return updated;
  }, [headers, currentSession, setSessionsBoth]);

  const deleteSession = useCallback(async (sessionId: string) => {
    const res = await fetch(`${SESSION_API}/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: headers(),
    });
    if (!res.ok && res.status !== 404) throw new Error('Failed to delete session');
    setSessionsBoth(prev => prev.filter(s => s.session_id !== sessionId));
    if (currentSession?.session_id === sessionId) setCurrentSession(null);
  }, [headers, currentSession, setSessionsBoth]);

  const getMessages = useCallback(async (sessionId: string) => {
    const res = await fetch(`${SESSION_API}/sessions/${sessionId}/messages`, {
      headers: headers(),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.messages ?? [];
  }, [headers]);

  const switchSession = useCallback((sessionId: string) => {
    const s = sessionsRef.current.find(s => s.session_id === sessionId) ?? null;
    setCurrentSession(s);
    return s;
  }, []);

  const clearCurrent = useCallback(() => setCurrentSession(null), []);

  return {
    sessions,
    currentSession,
    loading,
    listSessions,
    createSession,
    updateTitle,
    deleteSession,
    getMessages,
    switchSession,
    clearCurrent,
  };
}
