import React, { useState, useEffect, useRef } from 'react';
import { getStatus, streamChat, getTraces, runEvaluation, generateReport, runOptimization } from './api';

function App() {
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [activeTab, setActiveTab] = useState('weather');
  const [traces, setTraces] = useState([]);
  const [evalResults, setEvalResults] = useState([]);
  const [evalLoading, setEvalLoading] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportResult, setReportResult] = useState(null);
  const [optimizeLoading, setOptimizeLoading] = useState(false);
  const [optimizeResult, setOptimizeResult] = useState(null);
  const [evalBatchId, setEvalBatchId] = useState(null);
  const [weatherData, setWeatherData] = useState([]);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    const poll = setInterval(async () => {
      try {
        const s = await getStatus();
        setStatus(s);
        if (s.ready) {
          setReady(true);
          clearInterval(poll);
        }
      } catch (e) { /* backend not up yet */ }
    }, 2000);
    return () => clearInterval(poll);
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const parseWeatherData = (text) => {
    const cards = [];

    // Extract city name from the text
    const cityMatch = text.match(/(?:weather|temperature|wind|conditions|data|information|forecast)\s+(?:in|for|at|of)\s+([A-Z][a-zA-Z\s\-]+?)(?:[,.:;\n]|\s+(?:is|are|shows|right|today|this|currently|here|based|for\s+you))/i)
      || text.match(/(?:in|for)\s+([A-Z][a-zA-Z\s\-]+?)(?:[,.:;\n]|\s+(?:is|are|right now|today|this|currently|here's|based|for\s+you))/);
    let city = cityMatch ? cityMatch[1].trim() : '';
    city = city.replace(/\s+for$/i, '');

    const tempMatch = text.match(/(-?\d+\.?\d*)\s*°?\s*[CF]|temperature[:\s]+(-?\d+\.?\d*)/i);
    if (tempMatch) cards.push({ icon: '🌡️', label: 'Temperature', value: tempMatch[0], detail: city || 'Current' });

    const windMatch = text.match(/(\d+\.?\d*)\s*(km\/h|mph|m\/s|kph|knots)/i);
    if (windMatch) cards.push({ icon: '💨', label: 'Wind', value: windMatch[0], detail: city || 'Speed' });

    const uvMatch = text.match(/UV\s*(?:index)?[:\s]*(\d+\.?\d*)/i);
    if (uvMatch) cards.push({ icon: '☀️', label: 'UV Index', value: uvMatch[1], detail: city || (uvMatch[1] > 6 ? 'High — use sunscreen' : 'Moderate') });

    const sunriseMatch = text.match(/sunrise[:\s|]*(\d{1,2}:\d{2}\s*(?:AM|PM)?)/i);
    if (sunriseMatch) cards.push({ icon: '🌅', label: 'Sunrise', value: sunriseMatch[1], detail: city });

    const sunsetMatch = text.match(/sunset[:\s|]*(\d{1,2}:\d{2}\s*(?:AM|PM)?)/i)
      || text.match(/(\d{1,2}:\d{2}\s*PM)\s*(?:\||\n|$)/i);
    if (sunsetMatch) cards.push({ icon: '🌇', label: 'Sunset', value: sunsetMatch[1], detail: city });

    const humidityMatch = text.match(/humidity[:\s]*(\d+\.?\d*)\s*%?/i);
    if (humidityMatch) cards.push({ icon: '💧', label: 'Humidity', value: `${humidityMatch[1]}%`, detail: city });

    const moonMatch = text.match(/(waxing|waning|full|new|crescent|gibbous|quarter)\s*(moon|gibbous|crescent|quarter)?/i);
    if (moonMatch) cards.push({ icon: '🌙', label: 'Moon', value: moonMatch[0], detail: city });

    return cards;
  };

  const handleSend = async () => {
    if (!input.trim() || streaming) return;

    const userMsg = input.trim();
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setStreaming(true);

    let allText = '';
    let currentChunk = '';
    let currentSessionId = sessionId;

    await streamChat(userMsg, sessionId, (event) => {
      if (event.type === 'session_id') {
        currentSessionId = event.session_id;
        setSessionId(event.session_id);
      } else if (event.type === 'text') {
        allText += event.content;
        currentChunk += event.content;
        setMessages(prev => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last && last.role === 'assistant') {
            msgs[msgs.length - 1] = { ...last, content: last.content + event.content };
          } else {
            msgs.push({ role: 'assistant', content: event.content });
          }
          return msgs;
        });
      } else if (event.type === 'tool') {
        currentChunk = '';
        setMessages(prev => [...prev, { role: 'tool', content: `Using tool: ${event.name}` }]);
      } else if (event.type === 'error') {
        setMessages(prev => [...prev, { role: 'error', content: event.content }]);
      }
    });

    // Parse weather data from the full response text
    if (allText) {
      const parsed = parseWeatherData(allText);
      if (parsed.length > 0) {
        setWeatherData(prev => [...prev, ...parsed]);
      }
    }

    setStreaming(false);

    // Fetch traces after a delay
    setTimeout(async () => {
      const t = await getTraces(5);
      setTraces(t.traces || []);
    }, 3000);
  };


  const handleEval = async () => {
    if (!sessionId) return;
    setEvalLoading(true);
    setEvalResults([]);

    try {
      const result = await runEvaluation(sessionId);
      setEvalResults(result.scores || []);
      setEvalBatchId(result.batch_name || result.batch_id || null);
      if (result.error) {
        setEvalResults([{ evaluator: 'Error', score: 0, label: result.error }]);
      }
    } catch (e) {
      setEvalResults([{ evaluator: 'Error', score: 0, label: e.message }]);
    }
    setEvalLoading(false);
  };

  const handleGenerateReport = async () => {
    if (!sessionId) return;
    setReportLoading(true);
    setReportResult(null);
    try {
      const result = await generateReport(sessionId);
      setReportResult(result);
      if (result.success && result.file_data) {
        const blob = new Blob(
          [Uint8Array.from(atob(result.file_data), c => c.charCodeAt(0))],
          { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }
        );
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = result.filename || 'weather_forecast.xlsx';
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      setReportResult({ success: false, error: e.message });
    }
    setReportLoading(false);
  };

  const handleOptimize = async () => {
    setOptimizeLoading(true);
    setOptimizeResult(null);
    try {
      const result = await runOptimization();
      setOptimizeResult(result);
    } catch (e) {
      setOptimizeResult({ status: 'FAILED', error: e.message });
    }
    setOptimizeLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const renderMarkdown = (text) => {
    const lines = text.split('\n');
    return lines.map((line, lineIdx) => {
      let content = line;
      let isHeading = false;
      let headingLevel = 0;

      if (line.startsWith('### ')) {
        content = line.slice(4);
        isHeading = true;
        headingLevel = 3;
      } else if (line.startsWith('## ')) {
        content = line.slice(3);
        isHeading = true;
        headingLevel = 2;
      } else if (line.startsWith('# ')) {
        content = line.slice(2);
        isHeading = true;
        headingLevel = 1;
      }

      const inlineParts = content.split(/(\*\*.*?\*\*)/g).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        return part;
      });

      if (isHeading) {
        const style = { fontWeight: 600, fontSize: headingLevel === 1 ? '1.2em' : headingLevel === 2 ? '1.1em' : '1em', marginTop: '8px' };
        return <div key={lineIdx} style={style}>{inlineParts}</div>;
      }

      return <span key={lineIdx}>{inlineParts}{lineIdx < lines.length - 1 ? '\n' : ''}</span>;
    });
  };

  if (!ready) {
    return (
      <div className="provisioning">
        <div className="spinner" style={{ width: 32, height: 32 }} />
        <h2>Provisioning AWS Resources</h2>
        <p>Setting up Gateway, Harness, and Guardrail...</p>
      </div>
    );
  }

  return (
    <div className="app">
      <div className="header">
        <h1>Weather Agent</h1>
        <div className="status">
          <span className="status-dot" />
          <span>Harness: {status?.harness_name || status?.harness_id}</span>
          <span>|</span>
          <span>Gateway: {status?.gateway_name || status?.gateway_id}</span>
          {status?.guardrail_id && <><span>|</span><span>Guardrail: {status?.guardrail_name || status?.guardrail_id}</span></>}
        </div>
      </div>

      <div className="main">
        {/* Left: Chat */}
        <div className="chat-panel">
          <div className="messages">
            {messages.length === 0 && (
              <div className="empty-state">
                <div className="empty-icon">⛅</div>
                <p>Ask about the weather anywhere in the world. Try: "What's the weather in Tokyo?"</p>
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`message ${msg.role}`}>
                {msg.role === 'assistant' ? renderMarkdown(msg.content) : msg.content}
              </div>
            ))}
            {streaming && messages[messages.length - 1]?.role === 'user' && (
              <div className="message assistant typing">
                <span className="typing-dots">
                  <span /><span /><span />
                </span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <div className="chat-input">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about weather, wind, UV, sunrise..."
              disabled={streaming}
            />
            <button onClick={handleSend} disabled={streaming || !input.trim()}>
              {streaming ? <span className="spinner" /> : 'Send'}
            </button>
          </div>
        </div>

        {/* Right: Weather / Traces / Evals */}
        <div className="right-panel">
          <div className="panel-tabs">
            <button className={activeTab === 'weather' ? 'active' : ''} onClick={() => setActiveTab('weather')}>
              Weather
            </button>
            <button className={activeTab === 'traces' ? 'active' : ''} onClick={() => setActiveTab('traces')}>
              Traces ({traces.length})
            </button>
            <button className={activeTab === 'skills' ? 'active' : ''} onClick={() => setActiveTab('skills')}>
              Skills
            </button>
            <button className={activeTab === 'optimize' ? 'active' : ''} onClick={() => setActiveTab('optimize')}>
              Optimization
            </button>
            <button className={activeTab === 'evals' ? 'active' : ''} onClick={() => setActiveTab('evals')}>
              Evaluations
            </button>
          </div>

          <div className="panel-content">
            {activeTab === 'weather' && (
              weatherData.length > 0 ? (
                <div className="weather-cards">
                  {weatherData.map((card, i) => (
                    <div key={i} className="weather-card">
                      <div className="icon">{card.icon}</div>
                      <div className="label">{card.label}</div>
                      <div className="value">{card.value}</div>
                      {card.detail && <div className="detail">{card.detail}</div>}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-state">
                  <div className="empty-icon">📊</div>
                  <p>Weather data will appear here as the agent responds with specific metrics.</p>
                </div>
              )
            )}

            {activeTab === 'traces' && (
              <div>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Traces (last 5 min)</span>
                  <button className="btn-secondary" onClick={async () => { const t = await getTraces(5); setTraces(t.traces || []); }}>
                    Refresh
                  </button>
                </div>
                {traces.length > 0 && traces.some(t => t.trace_id && !t.error) ? (
                  <div>
                    <div className="traces-list">
                      {traces.filter(t => t.trace_id).map((t, i) => (
                        <div key={i} className="trace-item">
                          <span className={`trace-dot ${t.has_error || t.has_fault ? 'error' : ''}`} />
                          <span className="trace-id">{t.trace_id}</span>
                          <span className="trace-duration">{t.spans ? `${t.spans} spans` : `${t.duration}s`}</span>
                        </div>
                      ))}
                    </div>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12 }}>
                      Search these trace IDs in CloudWatch &gt; GenAI Observability &gt; Bedrock AgentCore &gt; Traces (may take a few minutes to appear)
                    </p>
                  </div>
                ) : traces.length > 0 && (traces[0]?.error || traces.some(t => !t.trace_id)) ? (
                  <div className="empty-state">
                    <div className="empty-icon">🔍</div>
                    <p>Traces could not be loaded. Ensure CloudWatch Transaction Search is enabled in your account and region.</p>
                  </div>
                ) : (
                  <div className="empty-state">
                    <div className="empty-icon">🔍</div>
                    <p>Traces appear after you send messages. They may take a few seconds to index.</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'skills' && (
              <div>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>XLSX Report Generation</span>
                  <button className="btn-secondary" onClick={handleGenerateReport} disabled={!sessionId || reportLoading}>
                    {reportLoading ? 'Generating...' : 'Generate Report'}
                  </button>
                </div>
                {reportLoading && (
                  <div className="empty-state">
                    <div className="spinner" style={{ width: 24, height: 24 }} />
                    <p style={{ marginTop: 12, fontSize: '0.85rem', lineHeight: '1.6' }}>
                      Generating weather forecast spreadsheet using the xlsx skill... This typically takes 1-2 minutes.
                    </p>
                  </div>
                )}
                {!reportLoading && reportResult && (
                  <div className="eval-item" style={{ textAlign: 'center', padding: 20 }}>
                    {reportResult.success ? (
                      <>
                        <p style={{ color: 'var(--accent-green)', fontWeight: 500, marginBottom: 8 }}>Report generated and downloaded</p>
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{reportResult.filename}</p>
                      </>
                    ) : (
                      <>
                        <p style={{ color: 'var(--accent-red)', fontWeight: 500, marginBottom: 8 }}>Report generation failed</p>
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{reportResult.error}</p>
                      </>
                    )}
                  </div>
                )}
                {!reportLoading && !reportResult && (
                  <div className="empty-state">
                    <div className="empty-icon">📊</div>
                    <p style={{ fontSize: '0.85rem', lineHeight: '1.6' }}>
                      Generate a 7-day weather forecast as an Excel spreadsheet using the AgentCore xlsx skill. The report will use the last city you asked about.
                    </p>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12 }}>
                      The skill is fetched from Git at invocation time — no container setup or pre-installation required.
                    </p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'evals' && (
              <div>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Batch Evaluation</span>
                  <button className="btn-secondary" onClick={handleEval} disabled={!sessionId || evalLoading || optimizeLoading}>
                    {evalLoading ? 'Running...' : 'Run Eval'}
                  </button>
                </div>
                {evalLoading && (
                  <div className="empty-state">
                    <div className="spinner" style={{ width: 24, height: 24 }} />
                    <p style={{ marginTop: 12, fontSize: '0.85rem', lineHeight: '1.6' }}>Running batch evaluation... This typically takes 2-5 minutes.</p>
                    <p style={{ marginTop: 12, fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: '1.6' }}>Optimization is disabled while evaluation is running. You can track progress in Bedrock AgentCore &gt; Evaluations &gt; Batch evaluation.</p>
                  </div>
                )}
                {!evalLoading && evalResults.length > 0 && (
                  <div>
                    <div className="eval-results">
                      {evalResults.map((r, i) => {
                        const score = r.score != null ? r.score : 0;
                        const hasError = r.evaluator === 'Error';
                        const color = hasError ? 'var(--text-secondary)' : score >= 0.8 ? 'var(--accent-green)' : score >= 0.5 ? 'var(--accent-orange)' : 'var(--accent-red)';
                        return (
                          <div key={i} className="eval-item">
                            <div className="eval-header">
                              <span className="eval-name">{r.evaluator}</span>
                              <span className="eval-score" style={{ color }}>{hasError ? '—' : score.toFixed(2)}</span>
                            </div>
                            {!hasError && (
                              <div className="eval-bar">
                                <div className="eval-bar-fill" style={{ width: `${score * 100}%`, background: color }} />
                              </div>
                            )}
                            <div className="eval-label">{hasError ? r.label : ''}</div>
                          </div>
                        );
                      })}
                    </div>
                    {evalResults.some(r => r.evaluator === 'Error') && (
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12, textAlign: 'center' }}>
                        This may happen if Transaction Search was recently enabled and traces haven't fully indexed yet. Try again in a few minutes.
                      </p>
                    )}
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12, textAlign: 'center' }}>
                      View full details in Bedrock AgentCore &gt; Evaluations &gt; Batch evaluation{evalBatchId && <> — <strong>{evalBatchId}</strong></>}
                    </p>
                  </div>
                )}
                {!evalLoading && evalResults.length === 0 && (
                  <div className="empty-state">
                    <div className="empty-icon">📋</div>
                    <p style={{ fontSize: '0.85rem', lineHeight: '1.6' }}>
                      Send some weather questions first, then click "Run Eval" to score the session.
                    </p>
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 12, lineHeight: '1.6' }}>
                      Scores your conversation using built-in evaluators: Helpfulness, Correctness, Coherence, Faithfulness, and more.
                    </p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'optimize' && (
              <div>
                <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>System Prompt Recommendation</span>
                  <button className="btn-secondary" onClick={handleOptimize} disabled={optimizeLoading || evalLoading}>
                    {optimizeLoading ? 'Running...' : 'Optimize'}
                  </button>
                </div>
                {optimizeLoading && (
                  <div className="empty-state">
                    <div className="spinner" style={{ width: 24, height: 24 }} />
                    <p style={{ marginTop: 12, fontSize: '0.85rem', lineHeight: '1.6' }}>
                      Analyzing traces and generating an optimized system prompt... This typically takes 1-3 minutes.
                    </p>
                    <p style={{ marginTop: 12, fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
                      Evaluations is disabled while optimization is running. You can track progress in Bedrock AgentCore &gt; Optimizations &gt; Recommendations.
                    </p>
                  </div>
                )}
                {!optimizeLoading && optimizeResult && optimizeResult.status === 'COMPLETED' && (
                  <div>
                    <div className="eval-item" style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 6 }}>RECOMMENDED SYSTEM PROMPT</div>
                      <p style={{ fontSize: '0.8rem', lineHeight: '1.6', whiteSpace: 'pre-wrap' }}>
                        {optimizeResult.recommended_prompt}
                      </p>
                    </div>
                    {optimizeResult.explanation && (
                      <div className="eval-item" style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: 6 }}>EXPLANATION</div>
                        <p style={{ fontSize: '0.8rem', lineHeight: '1.6' }}>
                          {optimizeResult.explanation}
                        </p>
                      </div>
                    )}
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12, textAlign: 'center' }}>
                      View full details in Bedrock AgentCore &gt; Optimizations &gt; Recommendations — <strong>{optimizeResult.recommendation_name}</strong>
                    </p>
                  </div>
                )}
                {!optimizeLoading && optimizeResult && optimizeResult.status !== 'COMPLETED' && (
                  <div className="eval-item" style={{ textAlign: 'center', padding: 20 }}>
                    <p style={{ color: 'var(--accent-red)', fontWeight: 500, marginBottom: 8 }}>Optimization failed</p>
                    <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{optimizeResult.error}</p>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12 }}>Traces may need more time to index. Try again after a few minutes, or send more weather questions first.</p>
                  </div>
                )}
                {!optimizeLoading && !optimizeResult && (
                  <div className="empty-state">
                    <div className="empty-icon">🚀</div>
                    <p style={{ fontSize: '0.85rem', lineHeight: '1.6' }}>
                      Analyze your agent's traces and generate an AI-improved system prompt optimized for goal success.
                    </p>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 12, lineHeight: '1.6' }}>
                      Send some weather questions first, then click "Optimize" to generate a recommendation. View full details in Bedrock AgentCore &gt; Optimizations &gt; Recommendations.
                    </p>
                  </div>
                )}
              </div>
            )}

          </div>
        </div>
      </div>

      <div className="footer">
        <span>AgentCore Harness Demo — Gateway + Guardrails + Skills + Observability + Evaluations + Optimization</span>
        <span><span className="status-dot" style={{ display: 'inline-block', marginRight: 6 }} />{status?.region}</span>
      </div>
    </div>
  );
}

export default App;
