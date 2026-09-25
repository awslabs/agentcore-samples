import { useState, useCallback } from 'react';
import type { AdminMemoryResponse, AdminSession } from '../types';
import { useAuth } from '../context/AuthContext';

// Admin API backend (API Gateway + Lambda).
// Falls back to Flask for local dev if VITE_ADMIN_API_URL is not set.
const API_BASE = import.meta.env.VITE_ADMIN_API_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8080';

export function useAdminMemory() {
  const { session } = useAuth();
  const [data, setData] = useState<AdminMemoryResponse | null>(null);
  const [sessions, setSessions] = useState<AdminSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const authHeader = useCallback(
    () => ({ Authorization: `Bearer ${session?.idToken ?? ''}` }),
    [session?.idToken],
  );

  const loadSessions = useCallback(
    async (actorId: string): Promise<AdminSession[]> => {
      try {
        const res = await fetch(`${API_BASE}/admin/sessions?actorId=${encodeURIComponent(actorId)}`, {
          headers: authHeader(),
        });
        if (!res.ok) throw new Error(`Failed to load sessions (${res.status})`);
        const json = await res.json();
        const list: AdminSession[] = json.sessions ?? [];
        setSessions(list);
        return list;
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load sessions');
        setSessions([]);
        return [];
      }
    },
    [authHeader],
  );

  const load = useCallback(
    async (actorId: string, sessionId: string) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (actorId) params.set('actorId', actorId);
        if (sessionId) params.set('sessionId', sessionId);
        const res = await fetch(`${API_BASE}/admin/memory?${params.toString()}`, {
          headers: authHeader(),
        });
        if (!res.ok) throw new Error(`Failed to load memory (${res.status})`);
        const json: AdminMemoryResponse = await res.json();
        setData(json);
        return json;
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load memory');
        return null;
      } finally {
        setLoading(false);
      }
    },
    [authHeader],
  );

  const [searchResults, setSearchResults] = useState<Array<{ recordId: string; score: number | null; text: string; parsed: Record<string, unknown> | null; namespaces: string[] | null }>>([]);
  const [searching, setSearching] = useState(false);

  const semanticSearch = useCallback(
    async (query: string, grounding: string = '', topK: number = 5) => {
      setSearching(true);
      setSearchResults([]);
      try {
        const params = new URLSearchParams({ query, topK: String(topK) });
        if (grounding && grounding !== 'all') params.set('grounding', grounding);
        const res = await fetch(`${API_BASE}/admin/memory/search?${params.toString()}`, {
          headers: authHeader(),
        });
        if (!res.ok) throw new Error(`Search failed (${res.status})`);
        const json = await res.json();
        setSearchResults(json.results ?? []);
        return json.results ?? [];
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Search failed');
        return [];
      } finally {
        setSearching(false);
      }
    },
    [authHeader],
  );

  const clearSearch = useCallback(() => setSearchResults([]), []);

  return { data, sessions, loading, error, load, loadSessions, searchResults, searching, semanticSearch, clearSearch };
}
