import { useState, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8080';
const AGENTCORE_URL = import.meta.env.VITE_AGENTCORE_URL ?? '';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'adjuster' | 'error';
  content: string;
  timestamp: Date;
}

export function useChat() {
  const { session } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(async (
    prompt: string,
    actorId: string,
    sessionId: string,
  ) => {
    if (!prompt.trim() || isLoading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: prompt.trim(),
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setIsLoading(true);

    const assistantId = `assistant-${Date.now()}`;
    setMessages(prev => [...prev, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    }]);

    try {
      abortRef.current = new AbortController();

      const useAgentCore = !!AGENTCORE_URL;
      const url = useAgentCore ? AGENTCORE_URL : `${API_BASE}/invoke`;
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (useAgentCore && session?.accessToken) {
        headers['Authorization'] = `Bearer ${session.accessToken}`;
      }

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          prompt: prompt.trim(),
          actorId,
          sessionId,
          memoryMode: 'reflections',
        }),
        signal: abortRef.current.signal,
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error ?? `Server error (${res.status})`);
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      if (!reader) throw new Error('No response body');

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;
          try {
            const parsed = JSON.parse(jsonStr);
            // AgentCore format: nested JSON in contentBlockDelta
            const delta = parsed?.event?.contentBlockDelta?.delta?.text;
            if (delta) {
              const event = JSON.parse(delta);
              if (event.event === 'message') {
                setMessages(prev =>
                  prev.map(m => m.id === assistantId ? { ...m, content: event.data } : m)
                );
              } else if (event.event === 'error') {
                setMessages(prev =>
                  prev.map(m => m.id === assistantId ? { ...m, role: 'error', content: event.data } : m)
                );
              }
              continue;
            }
            // Flask SSE format: direct event object
            if (parsed.event === 'message') {
              setMessages(prev =>
                prev.map(m => m.id === assistantId ? { ...m, content: parsed.data } : m)
              );
            } else if (parsed.event === 'error') {
              setMessages(prev =>
                prev.map(m => m.id === assistantId ? { ...m, role: 'error', content: parsed.data } : m)
              );
            }
          } catch { /* skip malformed */ }
        }
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      const message = err instanceof Error ? err.message : 'Request failed';
      setMessages(prev =>
        prev.map(m => m.id === assistantId ? { ...m, role: 'error', content: message } : m)
      );
    } finally {
      setIsLoading(false);
      abortRef.current = null;
    }
  }, [isLoading]);

  /** Load messages from memory API response into state */
  const loadMessages = useCallback((
    memoryMessages: Array<{ role: string; content: string; timestamp?: string }>
  ) => {
    const mapped: ChatMessage[] = memoryMessages
      .filter((m) => m.role !== 'adjuster')
      .map((m, i) => ({
        id: `history-${i}-${Date.now()}`,
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content,
        timestamp: m.timestamp ? new Date(m.timestamp) : new Date(),
      }));
    setMessages(mapped);
  }, []);

  const clearMessages = useCallback(() => setMessages([]), []);

  return { messages, isLoading, sendMessage, loadMessages, clearMessages };
}
