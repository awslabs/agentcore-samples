import { useState, useCallback } from 'react';
import type { ReviewTask } from '../types';
import { useAuth } from '../context/AuthContext';

const API_BASE = import.meta.env.VITE_REVIEWS_API_URL ?? 'http://localhost:8080';

export type Decision = 'APPROVE' | 'DENY';

export function useReviews() {
  const { session } = useAuth();
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const authHeaders = useCallback(
    () => ({
      Authorization: `Bearer ${session?.idToken ?? ''}`,
      'Content-Type': 'application/json',
    }),
    [session?.idToken],
  );

  const listReviews = useCallback(
    async (status: 'OPEN' | 'RESOLVED' = 'OPEN') => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/reviews?status=${status}`, {
          headers: authHeaders(),
        });
        if (!res.ok) throw new Error(`Failed to load reviews (${res.status})`);
        const data = await res.json();
        const list: ReviewTask[] = data.tasks ?? [];
        setTasks(list);
        return list;
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load reviews');
        return [];
      } finally {
        setLoading(false);
      }
    },
    [authHeaders],
  );

  const getReview = useCallback(
    async (taskId: string): Promise<ReviewTask | null> => {
      const res = await fetch(`${API_BASE}/reviews/${taskId}`, { headers: authHeaders() });
      if (!res.ok) return null;
      return res.json();
    },
    [authHeaders],
  );

  const resolveReview = useCallback(
    async (taskId: string, decision: Decision, notes: string): Promise<ReviewTask> => {
      const res = await fetch(`${API_BASE}/reviews/${taskId}/resolve`, {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ decision, notes }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Resolve failed (${res.status})`);
      }
      return res.json();
    },
    [authHeaders],
  );

  return { tasks, loading, error, listReviews, getReview, resolveReview };
}
