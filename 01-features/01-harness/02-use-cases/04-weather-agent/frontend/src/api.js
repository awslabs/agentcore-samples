const BASE = '';

export async function getStatus() {
  const res = await fetch(`${BASE}/api/status`);
  return res.json();
}

export async function streamChat(message, sessionId, onEvent) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6));
          onEvent(event);
        } catch (e) {
          // skip malformed
        }
      }
    }
  }
}

export async function getTraces(minutes = 10) {
  const res = await fetch(`${BASE}/api/traces?minutes=${minutes}`);
  return res.json();
}

export async function runEvaluation(sessionId) {
  const res = await fetch(`${BASE}/api/evaluate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  });
  return res.json();
}

export async function generateReport(sessionId, city) {
  const res = await fetch(`${BASE}/api/generate-report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, city }),
  });
  return res.json();
}

export async function runOptimization() {
  const res = await fetch(`${BASE}/api/optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return res.json();
}
