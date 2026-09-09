import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { useAdminMemory } from '../hooks/useAdminMemory';
import Markdown from '../components/Markdown';
import type { MemoryEvent, MemoryRecord, SubtoolEvent } from '../types';
import './AdminMemory.css';

const ACTORS = [
  { id: 'PH-1001', name: 'Bob Thompson' },
  { id: 'PH-1042', name: 'Alice Martinez' },
  { id: 'PH-1087', name: 'Charlie Davis' },
  { id: 'PH-2001', name: 'David Park' },
  { id: 'PH-2050', name: 'Sarah Chen' },
  { id: 'PH-3001', name: 'Marcus Rivera' },
  { id: 'PH-3050', name: 'Lisa Nguyen' },
];
type Tab = 'conversation' | 'graph-trace' | 'episodes';

// Event filter categories + default visibility.
const EVENT_TYPES: [string, string][] = [
  ['user', 'User'],
  ['assistant', 'Assistant'],
  ['adjuster', 'Adjuster'],
  ['trace', 'Trace'],
];
const DEFAULT_EVENT_FILTERS: Record<string, boolean> = {
  user: true, assistant: true, adjuster: true, trace: true,
};

// Graph node groupings for the pipeline tab
const INVESTIGATION_TOOLS = ['lookup_policy', 'check_claims_history', 'check_fraud_indicators', 'validate_coverage'];
const PRECEDENT_TOOLS = ['search_claim_patterns', 'lookup_policyholder_history'];
const DECISION_TOOL = 'adjudication_decision';

function eventCategory(ev: MemoryEvent): string {
  if (ev.kind === 'message') {
    const r = (ev.role || 'other').toLowerCase();
    if (r === 'user' || r === 'assistant' || r === 'adjuster') return r;
    return 'other';
  }
  return 'other';
}

const ADMIN_API_URL = import.meta.env.VITE_ADMIN_API_URL ?? import.meta.env.VITE_API_URL ?? 'http://localhost:8080';

export default function AdminMemory() {
  const { session, logout } = useAuth();
  const { data, sessions, loading, error, load, loadSessions, searchResults, searching, semanticSearch, clearSearch } = useAdminMemory();
  const [actor, setActor] = useState('PH-1001');
  const [sessionId, setSessionId] = useState('');
  const [tab, setTab] = useState<Tab>('episodes');
  const [search, setSearch] = useState('');
  const [reflSearch, setReflSearch] = useState('');
  const [searchMode, setSearchMode] = useState<'keyword' | 'semantic'>('keyword');
  const [eventFilters, setEventFilters] = useState<Record<string, boolean>>(DEFAULT_EVENT_FILTERS);
  const [mode, setMode] = useState<string>(data?.decision_mode ?? '');
  const [modeLoading, setModeLoading] = useState(false);
  const [reflSort, setReflSort] = useState<'newest' | 'oldest'>('newest');
  const [groundingFilter, setGroundingFilter] = useState<'all' | 'human_adjuster' | 'agent_only'>('all');

  // Load sessions + memory whenever the actor changes (and on mount).
  useEffect(() => {
    setSessionId('');
    loadSessions(actor);
    load(actor, '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actor]);

  // Sync mode from server data
  useEffect(() => {
    if (data?.decision_mode) setMode(data.decision_mode);
  }, [data?.decision_mode]);

  const toggleMode = useCallback(async () => {
    const next = mode === 'human' ? 'auto' : 'human';
    setModeLoading(true);
    try {
      const resp = await fetch(`${ADMIN_API_URL}/admin/mode`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${session?.idToken ?? ''}`,
        },
        body: JSON.stringify({ mode: next }),
      });
      if (resp.ok) {
        const d = await resp.json();
        setMode(d.mode);
      }
    } finally {
      setModeLoading(false);
    }
  }, [mode, session?.idToken]);

  const onPickSession = (sid: string) => {
    setSessionId(sid);
    load(actor, sid);
    if (sid) setTab('conversation');
  };

  const q = search.trim().toLowerCase();
  const match = (obj: unknown) => !q || JSON.stringify(obj).toLowerCase().includes(q);

  const matchGrounding = (r: MemoryRecord) =>
    groundingFilter === 'all' || (r.metadata?.['grounding_source'] as unknown as string) === groundingFilter;

  const events = useMemo(
    () => (data?.events ?? []).filter((e) => match(e) && eventFilters[eventCategory(e)]),
    [data, q, eventFilters],
  );
  const episodes = useMemo(
    () => (data?.episodes ?? []).filter((r) => match(r) && matchGrounding(r)),
    [data, q, groundingFilter],
  );
  const rq = reflSearch.trim().toLowerCase();
  const reflections = useMemo(() => {
    const filtered = (data?.reflections ?? []).filter(
      (r) => (!rq || JSON.stringify(r).toLowerCase().includes(rq)) && matchGrounding(r),
    );
    return filtered.sort((a, b) => {
      const ta = a.createdAt || '';
      const tb = b.createdAt || '';
      return reflSort === 'newest' ? tb.localeCompare(ta) : ta.localeCompare(tb);
    });
  }, [data, rq, reflSort, groundingFilter]);

  const actorName = ACTORS.find((a) => a.id === actor)?.name ?? '';

  // Resizable + collapsible panes
  const [reflWidth, setReflWidth] = useState(420);
  const [reflCollapsed, setReflCollapsed] = useState(false);
  const [mainCollapsed, setMainCollapsed] = useState(false);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(420);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startW.current = reflWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = startX.current - ev.clientX;
      const maxW = window.innerWidth - 300;
      setReflWidth(Math.max(200, Math.min(maxW, startW.current + delta)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [reflWidth]);

  return (
    <div className="adm-page">
      <header className="adm-topbar">
        <div className="adm-left">
          <span className="adm-icon">🧠</span>
          <span className="adm-title">Memory Inspector</span>
          {data && (
            <span className="adm-meta">{data.memory_id}</span>
          )}
        </div>
        <div className="adm-right">
          <div className="adm-mode-switch">
            <span className={`adm-mode-opt ${mode === 'auto' ? 'active' : ''}`}>auto</span>
            <button
              className={`adm-mode-track ${mode}`}
              onClick={toggleMode}
              disabled={modeLoading}
              title={`Switch to ${mode === 'human' ? 'auto' : 'human'} mode`}
            >
              <span className="adm-mode-thumb" />
            </button>
            <span className={`adm-mode-opt ${mode === 'human' ? 'active' : ''}`}>human</span>
          </div>
          <span className="adm-user">{session?.user.username}</span>
          <button className="adm-logout" onClick={logout}>Logout</button>
        </div>
      </header>

      {error && <div className="adm-error">{error}</div>}

      <div className="adm-layout">
        {mainCollapsed ? null : (
          <div className="adm-main">
            <div className="adm-main-header">
              <div className="adm-controls">
                <label>Actor
                  <select value={actor} onChange={(e) => setActor(e.target.value)}>
                    {ACTORS.map((a) => (
                      <option key={a.id} value={a.id}>{a.id} — {a.name}</option>
                    ))}
                  </select>
                </label>
                <label>Session ({actorName})
                  <select value={sessionId} onChange={(e) => onPickSession(e.target.value)}>
                    <option value="">— select a claim (for raw events) —</option>
                    {sessions.map((s) => (
                      <option key={s.session_id} value={s.session_id}>
                        {(s.title || s.session_id).slice(0, 60)}
                        {s.created_at ? ` · ${s.created_at.slice(0, 16).replace('T', ' ')}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="adm-load" onClick={() => load(actor, sessionId)} disabled={loading}>
                  {loading ? 'Loading…' : 'Reload'}
                </button>
                <input
                  className="adm-search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search text / metadata…"
                />
                <label className="adm-grounding-label">Grounding
                  <select
                    className="adm-grounding-filter"
                    value={groundingFilter}
                    onChange={(e) => setGroundingFilter(e.target.value as 'all' | 'human_adjuster' | 'agent_only')}
                  >
                    <option value="all">all sources</option>
                    <option value="human_adjuster">human adjuster</option>
                    <option value="agent_only">agent only</option>
                  </select>
                </label>
              </div>
            </div>
            <div className="adm-main-pane-header">
              <span className="adm-main-pane-title">Claim Details</span>
              <span className="adm-main-pane-sub">per-actor · per-session</span>
            </div>
            <div className="adm-tabs">
              {([['episodes', 'Episodes'], ['graph-trace', 'Trace'], ['conversation', 'Conversation']] as [Tab, string][]).map(([t, label]) => (
                <button
                  key={t}
                  className={`adm-tab ${tab === t ? 'active' : ''}`}
                  onClick={() => setTab(t)}
                >
                  {label} ({t === 'graph-trace' ? data?.counts?.subtools ?? 0 : t === 'conversation' ? (data?.events ?? []).filter((e) => e.kind === 'message').length : data?.counts?.episodes ?? 0})
                </button>
              ))}
            </div>

            <div className="adm-body">
              {tab === 'conversation' && (
                <>
                  <div className="adm-filters">
                    {EVENT_TYPES.map(([key, label]) => (
                      <label key={key} className="adm-check">
                        <input
                          type="checkbox"
                          checked={!!eventFilters[key]}
                          onChange={(e) =>
                            setEventFilters((f) => ({ ...f, [key]: e.target.checked }))
                          }
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                  {events.length
                    ? <ConversationTimeline events={events} allEvents={data?.events ?? []} subtools={data?.subtools ?? []} showTrace={!!eventFilters['trace']} />
                    : <Empty msg={sessionId ? 'No events match the current filters.' : 'Pick a claim above to view raw events.'} />}
                </>
              )}
              {tab === 'graph-trace' && (
                <PipelineView subtools={data?.subtools ?? []} sessionId={sessionId} />
              )}
              {tab === 'episodes' && (

                episodes.length
                  ? episodes.map((r) => <RecordCard key={r.recordId} rec={r} kind="episode" />)
                  : <Empty msg={sessionId
                      ? 'No episode for this claim yet (may still be extracting — ~10–15 min).'
                      : "No episodes for this actor yet."} />
              )}
            </div>
          </div>
        )}

        {/* Panel divider with collapse toggles */}
        <div className="adm-refl-divider">
          {!mainCollapsed && !reflCollapsed && (
            <div className="adm-refl-drag" onMouseDown={onDragStart} />
          )}
          <button
            className="adm-panel-toggle"
            onClick={() => setMainCollapsed((c) => !c)}
            title={mainCollapsed ? 'Show episodes/events' : 'Hide episodes/events'}
          >
            {mainCollapsed ? '›' : '‹'}
          </button>
          <button
            className="adm-panel-toggle"
            onClick={() => setReflCollapsed((c) => !c)}
            title={reflCollapsed ? 'Show reflections' : 'Hide reflections'}
          >
            {reflCollapsed ? '‹' : '›'}
          </button>
        </div>

        {reflCollapsed ? null : (
          <aside className={`adm-refl-pane ${mainCollapsed ? 'adm-refl-expanded' : ''}`} style={mainCollapsed ? undefined : { width: reflWidth }}>
            <div className="adm-refl-head">
              <div className="adm-refl-head-row">
                <span className="adm-refl-title">Reflections ({data?.counts?.reflections ?? 0})</span>
                <span className="adm-refl-sub">cross-claim · global</span>
              </div>
              <div className="adm-refl-toolbar">
                <div className="adm-search-mode-toggle">
                  <button
                    className={`adm-mode-btn ${searchMode === 'keyword' ? 'active' : ''}`}
                    onClick={() => { setSearchMode('keyword'); clearSearch(); }}
                  >keyword</button>
                  <button
                    className={`adm-mode-btn ${searchMode === 'semantic' ? 'active' : ''}`}
                    onClick={() => setSearchMode('semantic')}
                  >semantic</button>
                </div>
                <input
                  className="adm-refl-search"
                  value={reflSearch}
                  onChange={(e) => { setReflSearch(e.target.value); if (searchMode === 'keyword') clearSearch(); }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && searchMode === 'semantic' && reflSearch.trim()) {
                      semanticSearch(reflSearch.trim(), groundingFilter, 5);
                    }
                  }}
                  placeholder={searchMode === 'semantic' ? 'Semantic query (Enter to search)…' : 'Filter reflections…'}
                />
                {searchMode === 'keyword' && (
                  <button
                    className="adm-sort-btn"
                    onClick={() => setReflSort((s) => s === 'newest' ? 'oldest' : 'newest')}
                    title={`Sort by ${reflSort === 'newest' ? 'oldest' : 'newest'} first`}
                  >
                    {reflSort === 'newest' ? '↓ newest' : '↑ oldest'}
                  </button>
                )}
                {searchMode === 'semantic' && (
                  <button
                    className="adm-sort-btn"
                    onClick={() => { if (reflSearch.trim()) semanticSearch(reflSearch.trim(), groundingFilter, 5); }}
                    disabled={searching}
                  >
                    {searching ? '…' : '🔍'}
                  </button>
                )}
              </div>
            </div>
            <div className="adm-refl-list">
              {searchMode === 'semantic' && searchResults.length > 0 ? (
                searchResults.map((r, i) => (
                  <div key={r.recordId || `sr-${i}`} className="adm-search-result">
                    {r.score != null && <span className="adm-score-badge">{(r.score as number).toFixed(2)}</span>}
                    <RecordCard rec={{ ...r, recordId: r.recordId || '', createdAt: '', strategyId: null, metadata: (r as Record<string, unknown>).metadata as MemoryRecord['metadata'] ?? null }} kind="reflection" />
                  </div>
                ))
              ) : searchMode === 'semantic' && searching ? (
                <Empty msg="Searching…" />
              ) : searchMode === 'semantic' && reflSearch.trim() && !searching && searchResults.length === 0 ? (
                <Empty msg="No semantic matches. Try a different query." />
              ) : reflections.length ? (
                reflections.map((r) => <RecordCard key={r.recordId} rec={r} kind="reflection" />)
              ) : (
                <Empty msg="No reflections yet." />
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

function ConversationTimeline({ events, allEvents, subtools, showTrace }: { events: MemoryEvent[]; allEvents: MemoryEvent[]; subtools: SubtoolEvent[]; showTrace: boolean }) {
  const toMs = (t: string) => {
    if (!t) return 0;
    const n = Number(t);
    if (!isNaN(n) && n > 1e12) return n;
    return new Date(t).getTime() || 0;
  };

  // Find the process_claim_tool call and result from ALL events (unfiltered)
  const toolCallEvent = allEvents.find((e) => e.kind === 'tool_use' && e.tool === 'process_claim_tool');
  const toolResultEvent = allEvents.find((e) => e.kind === 'tool_result');
  let toolCallTime = toolCallEvent ? toMs(toolCallEvent.created_at || toolCallEvent.timestamp || '') : 0;
  const toolResultTime = toolResultEvent ? toMs(toolResultEvent.created_at || toolResultEvent.timestamp || '') : 0;

  // Fallback: if no explicit tool_use event, use the earliest subtool timestamp
  if (!toolCallTime && subtools.length > 0) {
    const subtoolTimes = subtools.map((s) => toMs(s.timestamp || '')).filter((t) => t > 0);
    if (subtoolTimes.length > 0) toolCallTime = Math.min(...subtoolTimes);
  }

  // Group subtools by agent
  const investigationTools = subtools.filter((s) => INVESTIGATION_TOOLS.includes(s.tool));
  const precedentTools = subtools.filter((s) => PRECEDENT_TOOLS.includes(s.tool));
  const adjudicationTool = subtools.find((s) => s.tool === DECISION_TOOL);

  // Build timeline from message events only
  const conversationEvents = events.filter((e) => e.kind === 'message');

  // Determine where to insert the trace: find the gap where the graph ran
  // (the user message right before a >10s gap to the next assistant message)
  let traceInsertIdx = -1;
  if (showTrace && subtools.length > 0) {
    for (let i = 0; i < conversationEvents.length - 1; i++) {
      const curr = conversationEvents[i];
      const next = conversationEvents[i + 1];
      const currTime = toMs(curr.created_at || curr.timestamp || '');
      const nextTime = toMs(next.created_at || next.timestamp || '');
      const gap = nextTime - currTime;
      // The graph takes 10-60s; find the largest gap in the conversation
      if (gap > 10000 && (traceInsertIdx === -1 || gap > toMs(conversationEvents[traceInsertIdx + 1]?.created_at || conversationEvents[traceInsertIdx + 1]?.timestamp || '') - toMs(conversationEvents[traceInsertIdx]?.created_at || conversationEvents[traceInsertIdx]?.timestamp || ''))) {
        traceInsertIdx = i;
      }
    }
  }

  return (
    <div className="adm-conversation-timeline">
      {conversationEvents.map((ev, idx) => {
        const showTraceHere = idx === traceInsertIdx;

        return (
          <div key={ev.eventId || `ev-${idx}`}>
            <EventCard ev={ev} />
            {showTraceHere && (
              <div className="adm-trace-block">
                <div className="adm-trace-block-header">
                  <span className="adm-trace-block-label">process_claim_tool</span>
                  {toolCallTime > 0 && toolResultTime > 0 && (
                    <span className="adm-trace-block-duration">{((toolResultTime - toolCallTime) / 1000).toFixed(1)}s</span>
                  )}
                </div>
                <div className="adm-trace-block-body">
                  {/* Investigation Agent */}
                  {investigationTools.length > 0 && (
                    <TraceAgentGroup name="Investigation Agent" tools={investigationTools} />
                  )}
                  {/* Precedent Agent */}
                  {precedentTools.length > 0 && (
                    <TraceAgentGroup name="Precedent Agent" tools={precedentTools} />
                  )}
                  {/* Adjudication */}
                  {adjudicationTool && (
                    <TraceAgentGroup name="Adjudication Agent" tools={[adjudicationTool]} />
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TraceAgentGroup({ name, tools }: { name: string; tools: SubtoolEvent[] }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="adm-trace-agent">
      <button className="adm-trace-agent-header" onClick={() => setOpen((o) => !o)}>
        <span className="adm-trace-agent-caret">{open ? '▾' : '▸'}</span>
        <span className="adm-trace-agent-name">{name}</span>
        <span className="adm-trace-agent-count">{tools.length} call{tools.length !== 1 ? 's' : ''}</span>
      </button>
      {open && (
        <div className="adm-trace-agent-tools">
          {tools.map((st, i) => (
            <div key={st.eventId || `t-${i}`} className="adm-trace-tool-row">
              <span className="adm-trace-tool-name">{st.tool}</span>
              <span className="adm-trace-tool-query">{st.query}</span>
              <span className="adm-trace-tool-count">{st.result_count} result(s)</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ToolCallRow({ st }: { st: SubtoolEvent }) {
  const [open, setOpen] = useState(false);
  const hasResults = st.results.length > 0;
  const isRichResults = hasResults && typeof st.results[0] === 'object';
  const hasMultiple = st.results.length > 1;

  return (
    <div className="adm-pipeline-tool">
      <button className="adm-pipeline-tool-row" onClick={() => setOpen((o) => !o)}>
        <span className="adm-pipeline-tool-caret">{hasResults ? (open ? '▾' : '▸') : '·'}</span>
        <span className="adm-pipeline-tool-name">{st.tool}</span>
        <span className="adm-pipeline-tool-query">{st.query}</span>
        <span className="adm-pipeline-tool-count">{st.result_count} result(s){st.filter !== 'none' && st.filter !== 'n/a' ? ` [${st.filter}]` : ''}</span>
      </button>
      {!open && !hasMultiple && hasResults && !isRichResults && (
        <div className="adm-pipeline-tool-single">{typeof st.results[0] === 'string' ? st.results[0] : ''}</div>
      )}
      {open && hasResults && (
        <div className="adm-pipeline-tool-expanded">
          {st.results.map((r, j) => (
            typeof r === 'object' && r !== null
              ? <MemoryRecordCard key={j} record={r as Record<string, unknown>} />
              : <div key={j} className="adm-pipeline-tool-text-result">{String(r)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryRecordCard({ record }: { record: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const confidence = record.confidence as string | undefined;

  // Reflections use: title, use_cases, hints
  const reflTitle = record.title as string | undefined;
  const useCases = record.use_cases as string | undefined;
  const hints = record.hints as string | undefined;

  // Episodes use: situation, intent, justification, assessment
  const situation = record.situation as string | undefined;
  const intent = record.intent as string | undefined;
  const justification = record.justification as string | undefined;

  // Fallback
  const text = record.text as string | undefined;

  // Derive display title
  const title = reflTitle || (situation ? situation.slice(0, 80) + (situation.length > 80 ? '...' : '') : 'Untitled');

  const hasDetail = !!(useCases || hints || situation || justification || text);

  return (
    <div className="adm-memory-card">
      <button className="adm-memory-card-header" onClick={() => hasDetail && setOpen((o) => !o)}>
        {hasDetail && <span className="adm-memory-card-caret">{open ? '▾' : '▸'}</span>}
        <span className="adm-memory-card-title">{title}</span>
        {confidence && <span className="adm-memory-card-confidence">{confidence}</span>}
      </button>
      {open && (
        <div className="adm-memory-card-body">
          {useCases && (
            <div className="adm-memory-card-section">
              <label>When it applies</label>
              <p>{useCases}</p>
            </div>
          )}
          {hints && (
            <div className="adm-memory-card-section">
              <label>Guidance</label>
              <p>{hints}</p>
            </div>
          )}
          {situation && (
            <div className="adm-memory-card-section">
              <label>Situation</label>
              <p>{situation}</p>
            </div>
          )}
          {intent && (
            <div className="adm-memory-card-section">
              <label>Intent</label>
              <p>{intent}</p>
            </div>
          )}
          {justification && (
            <div className="adm-memory-card-section">
              <label>Justification</label>
              <p>{justification}</p>
            </div>
          )}
          {text && !useCases && !hints && !situation && (
            <div className="adm-memory-card-section">
              <p>{text}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PipelineView({ subtools, sessionId }: { subtools: SubtoolEvent[]; sessionId: string }) {
  if (!sessionId) return <Empty msg="Select a session to view pipeline trace." />;
  if (!subtools.length) return <Empty msg="No pipeline trace for this session (run in auto mode)." />;

  const decision = subtools.find((s) => s.tool === DECISION_TOOL);
  const investigation = subtools.filter((s) => INVESTIGATION_TOOLS.includes(s.tool));
  const precedent = subtools.filter((s) => PRECEDENT_TOOLS.includes(s.tool));

  // Parse decision details
  let decisionData: { decision?: string; amount?: number | null; internal_reasoning?: string; cited_patterns?: string[]; customer_reasoning?: string } | null = null;
  if (decision && decision.results.length > 0) {
    const raw = decision.results[0];
    try {
      decisionData = typeof raw === 'string' ? JSON.parse(raw) : raw;
    } catch { decisionData = null; }
  }

  return (
    <div className="adm-pipeline">
      {/* Graph Visualization */}
      <div className="adm-graph">
        <div className="adm-graph-parallel">
          <div className={`adm-graph-node ${investigation.length > 0 ? 'completed' : 'pending'}`}>
            <span className="adm-graph-node-icon">🔍</span>
            <span className="adm-graph-node-label">Investigation</span>
            {investigation.length > 0 && <span className="adm-graph-node-detail">{investigation.length} calls</span>}
          </div>
          <div className={`adm-graph-node ${precedent.length > 0 ? 'completed' : 'pending'}`}>
            <span className="adm-graph-node-icon">🧠</span>
            <span className="adm-graph-node-label">Precedent</span>
            {precedent.length > 0 && <span className="adm-graph-node-detail">{precedent.length} calls</span>}
          </div>
        </div>
        <div className="adm-graph-connector">
          <svg width="60" height="50" viewBox="0 0 60 50">
            <path d="M0 12 L30 25 L0 38" fill="none" stroke="#d1d5db" strokeWidth="2" />
            <path d="M30 25 L60 25" fill="none" stroke="#d1d5db" strokeWidth="2" />
            <polygon points="56,22 60,25 56,28" fill="#d1d5db" />
          </svg>
        </div>
        <div className={`adm-graph-node adjudication ${decisionData ? 'completed' : 'pending'}`}>
          <span className="adm-graph-node-icon">⚖️</span>
          <span className="adm-graph-node-label">Adjudication</span>
          {decisionData && (
            <span className={`adm-graph-node-decision ${(decisionData.decision || '').toLowerCase()}`}>
              {decisionData.decision}
            </span>
          )}
        </div>
      </div>

      {/* Decision Banner */}
      {decisionData && (
        <div className={`adm-decision-banner ${(decisionData.decision || '').toLowerCase()}`}>
          <div className="adm-decision-header">
            <span className={`adm-decision-badge ${(decisionData.decision || '').toLowerCase()}`}>
              {decisionData.decision}
            </span>
            {decisionData.amount != null && (
              <span className="adm-decision-amount">${decisionData.amount.toLocaleString()}</span>
            )}
          </div>

          <div className="adm-decision-body">
            <div className="adm-decision-section">
              <label>Internal Reasoning</label>
              <div className="adm-decision-reasoning">
                {(decisionData.internal_reasoning || '').split(/\n|(?<=\.)\s+(?=[A-Z0-9])/).filter(Boolean).map((chunk, i) => (
                  <p key={i} dangerouslySetInnerHTML={{ __html: chunk
                    .replace(/(HIGH|MEDIUM|LOW)/g, '<strong class="adm-hl-risk">$1</strong>')
                    .replace(/(DENY|APPROVE|DENIAL ISSUED|MANDATORY DENIAL)/g, '<strong class="adm-hl-decision">$1</strong>')
                    .replace(/(CLM-[\w-]+)/g, '<code class="adm-hl-id">$1</code>')
                    .replace(/('([^']{10,})')/g, '<em class="adm-hl-pattern">$1</em>')
                  }} />
                ))}
              </div>
            </div>

            {decisionData.cited_patterns && decisionData.cited_patterns.length > 0 && (
              <div className="adm-decision-section">
                <label>Cited Patterns</label>
                <div className="adm-decision-patterns">
                  {decisionData.cited_patterns.map((p, i) => (
                    <span key={i} className="adm-decision-pattern-tag">{p}</span>
                  ))}
                </div>
              </div>
            )}

            {decisionData.customer_reasoning && decisionData.customer_reasoning.trim() && (
              <div className="adm-decision-section adm-decision-customer">
                <label>Customer Message</label>
                <p>"{decisionData.customer_reasoning}"</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Investigation Node */}
      {investigation.length > 0 && (
        <div className="adm-pipeline-node">
          <div className="adm-pipeline-node-header">
            <span className="adm-pipeline-node-icon">🔍</span>
            <span className="adm-pipeline-node-title">Investigation Agent</span>
            <span className="adm-pipeline-node-badge">{investigation.length} tool call{investigation.length > 1 ? 's' : ''}</span>
          </div>
          <div className="adm-pipeline-node-body">
            {investigation.map((st, i) => (
              <ToolCallRow key={st.eventId || `inv-${i}`} st={st} />
            ))}
          </div>
        </div>
      )}

      {/* Precedent Node */}
      {precedent.length > 0 && (
        <div className="adm-pipeline-node">
          <div className="adm-pipeline-node-header">
            <span className="adm-pipeline-node-icon">🧠</span>
            <span className="adm-pipeline-node-title">Precedent Agent</span>
            <span className="adm-pipeline-node-badge">{precedent.length} tool call{precedent.length > 1 ? 's' : ''}</span>
          </div>
          <div className="adm-pipeline-node-body">
            {precedent.map((st, i) => (
              <ToolCallRow key={st.eventId || `mem-${i}`} st={st} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return <div className="adm-empty">{msg}</div>;
}

function Collapsible({ summary, badge, children }: {
  summary: React.ReactNode; badge?: React.ReactNode; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`adm-card ${open ? 'open' : ''}`}>
      <button className="adm-card-head" onClick={() => setOpen((o) => !o)}>
        <span className="adm-caret">{open ? '▾' : '▸'}</span>
        <span className="adm-summary">{summary}</span>
        {badge}
      </button>
      {open && <div className="adm-card-body">{children}</div>}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  message: '',
  tool_use: 'tool call',
  tool_result: 'tool result',
  state: 'state',
  other: '',
};

function EventCard({ ev }: { ev: MemoryEvent }) {
  const usage = (ev.metadata?.usage ?? null) as { totalTokens?: number } | null;
  const tag = ev.kind === 'tool_use' || ev.kind === 'tool_result'
    ? `${KIND_LABEL[ev.kind]}${ev.tool ? `: ${ev.tool}` : ''}`
    : ev.kind === 'state' ? 'state' : (ev.role || '');
  const cls = ev.kind === 'tool_use' || ev.kind === 'tool_result'
    ? 'role-tool'
    : ev.kind === 'state' ? 'role-state' : `role-${(ev.role || '').toLowerCase()}`;
  return (
    <Collapsible
      summary={<><b className={`adm-role ${cls}`}>{tag}</b> {(ev.text || '').slice(0, 90)}</>}
      badge={<span className="adm-time">{ev.created_at ?? ev.timestamp}</span>}
    >
      <div className="adm-event-body">
        <Markdown content={ev.text || '(no text)'} />
      </div>
      <div className="adm-kvs">
        <span>eventId: {ev.eventId}</span>
        <span>kind: {ev.kind}</span>
        {ev.tool && <span>tool: {ev.tool}</span>}
        {usage?.totalTokens != null && <span>tokens: {usage.totalTokens}</span>}
      </div>
    </Collapsible>
  );
}


function RecordCard({ rec, kind }: { rec: MemoryRecord; kind: 'episode' | 'reflection' }) {
  const p = rec.parsed as Record<string, unknown> | null;
  const turns = p && Array.isArray((p as { turns?: unknown }).turns)
    ? ((p as { turns?: unknown[] }).turns as Record<string, unknown>[])
    : null;
  const title =
    (p?.title as string) ||
    (p?.situation as string) ||
    rec.text.slice(0, 90) ||
    rec.recordId;
  const grounding = (rec.metadata?.['grounding_source'] as unknown as string) || null;
  return (
    <Collapsible
      summary={<>
        {grounding && <span className={`adm-grounding-tag ${grounding}`}>{grounding === 'human_adjuster' ? 'HUMAN' : 'AGENT'}</span>}
        {String(title).slice(0, 110)}
      </>}
      badge={<span className="adm-time">{rec.createdAt}</span>}
    >
      {p && kind === 'reflection' ? (
        <ReflectionBody p={p} />
      ) : p && kind === 'episode' ? (
        <EpisodeBody p={p} turns={turns} />
      ) : p ? (
        <div className="adm-fields">
          {Object.entries(p)
            .filter(([k]) => k !== 'turns')
            .map(([k, v]) => (
              <div className="adm-field" key={k}>
                <span className="adm-field-k">{k}</span>
                <span className="adm-field-v">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
              </div>
            ))}
        </div>
      ) : (
        <pre className="adm-pre">{rec.text}</pre>
      )}
      <div className="adm-kvs">
        <span>id: {rec.recordId}</span>
        <span>ns: {(rec.namespaces || []).join(', ')}</span>
        <span>{kind}</span>
      </div>
    </Collapsible>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="adm-section">
      <div className="adm-section-label">{label}</div>
      {children}
    </div>
  );
}

/** Render "(1)… (2)…" style hints as a numbered list; otherwise a paragraph. */
function Guidance({ text }: { text: string }) {
  const start = text.search(/\(1\)/);
  if (start >= 0 && /\(2\)/.test(text)) {
    const intro = text.slice(0, start).trim();
    const items = text.slice(start).split(/(?=\(\d+\)\s)/).map((s) => s.trim()).filter(Boolean);
    return (
      <>
        {intro && <p className="adm-para">{intro}</p>}
        <ol className="adm-bullets">
          {items.map((it, i) => <li key={i}>{it.replace(/^\(\d+\)\s*/, '')}</li>)}
        </ol>
      </>
    );
  }
  return <p className="adm-para">{text}</p>;
}

function ReflectionBody({ p }: { p: Record<string, unknown> }) {
  const conf = p.confidence as number | string | undefined;
  const useCases = p.use_cases as string | undefined;
  const hints = p.hints as string | undefined;
  const known = new Set(['title', 'confidence', 'use_cases', 'hints']);
  const extra = Object.entries(p).filter(([k]) => !known.has(k));
  return (
    <div className="adm-reflection">
      {conf != null && <span className="adm-conf">confidence {conf}</span>}
      {useCases && <Section label="When it applies"><p className="adm-para">{useCases}</p></Section>}
      {hints && <Section label="Guidance"><Guidance text={hints} /></Section>}
      {extra.map(([k, v]) => (
        <Section key={k} label={k}>
          <p className="adm-para">{typeof v === 'string' ? v : JSON.stringify(v)}</p>
        </Section>
      ))}
    </div>
  );
}

function TurnCard({ idx, turn }: { idx: number; turn: Record<string, unknown> }) {
  const summary = (turn.action as string) || (turn.situation as string) || `Turn ${idx + 1}`;
  const TURN_LABELS: Record<string, string> = {
    situation: 'Situation', intent: 'Intent', action: 'Action', thought: 'Thought',
    assessmentAssistant: 'Assistant assessment', assessmentUser: 'User assessment',
  };
  const order = ['situation', 'intent', 'action', 'thought', 'assessmentAssistant', 'assessmentUser'];
  const keys = [...order.filter((k) => k in turn), ...Object.keys(turn).filter((k) => !order.includes(k))];
  return (
    <Collapsible
      summary={<><b className="adm-turn-idx">{idx + 1}</b> {String(summary).slice(0, 100)}</>}
    >
      {keys.map((k) => (
        <Section key={k} label={TURN_LABELS[k] || k}>
          <p className="adm-para">{typeof turn[k] === 'string' ? (turn[k] as string) : JSON.stringify(turn[k])}</p>
        </Section>
      ))}
    </Collapsible>
  );
}

function EpisodeBody({ p, turns }: { p: Record<string, unknown>; turns: Record<string, unknown>[] | null }) {
  const assessment = p.assessment as string | undefined;
  const fields: [string, string][] = [
    ['situation', 'Situation'],
    ['intent', 'Intent'],
    ['justification', 'Justification'],
    ['reflection', 'Reflection'],
  ];
  const known = new Set(['situation', 'intent', 'assessment', 'justification', 'reflection', 'turns']);
  const extra = Object.entries(p).filter(([k]) => !known.has(k));
  const yes = String(assessment ?? '').toLowerCase() === 'yes';
  return (
    <div className="adm-reflection">
      {assessment != null && (
        <span className={`adm-conf ${yes ? 'conf-yes' : 'conf-no'}`}>goal achieved: {String(assessment)}</span>
      )}
      {fields.map(([k, label]) =>
        p[k] ? <Section key={k} label={label}><p className="adm-para">{String(p[k])}</p></Section> : null,
      )}
      {extra.map(([k, v]) => (
        <Section key={k} label={k}>
          <p className="adm-para">{typeof v === 'string' ? v : JSON.stringify(v)}</p>
        </Section>
      ))}
      {turns && (
        <Section label={`Turns (${turns.length})`}>
          <div className="adm-turns">
            {turns.map((t, i) => <TurnCard key={i} idx={i} turn={t} />)}
          </div>
        </Section>
      )}
    </div>
  );
}
