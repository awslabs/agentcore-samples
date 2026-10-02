import { useEffect, useState, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { useReviews, type Decision } from '../hooks/useReviews';
import type { ReviewTask } from '../types';
import './AdjusterConsole.css';

const DECISIONS: Decision[] = ['APPROVE', 'DENY'];

// Claim amounts arrive inconsistently from intake — bare numbers ("7200") or
// formatted strings ("$8,880"). Strip non-numeric chars before parsing so we
// never render "$NaN"; return a fallback when there's no usable number.
function formatAmount(value: unknown, fallback = ''): string {
  if (value == null || value === '') return fallback;
  const n = Number(String(value).replace(/[^0-9.-]/g, ''));
  return Number.isFinite(n) ? `$${n.toLocaleString()}` : fallback;
}

export default function AdjusterConsole() {
  const { session, logout } = useAuth();
  const { tasks, loading, error, listReviews, getReview, resolveReview } = useReviews();
  const [statusFilter, setStatusFilter] = useState<'OPEN' | 'RESOLVED'>('OPEN');
  const [selected, setSelected] = useState<ReviewTask | null>(null);
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    listReviews(statusFilter);
  }, [listReviews, statusFilter]);

  const openTask = useCallback(
    async (taskId: string) => {
      const t = await getReview(taskId);
      setSelected(t);
      setNotes('');
    },
    [getReview],
  );

  const handleResolve = useCallback(
    async (decision: Decision) => {
      if (!selected) return;
      setSubmitting(true);
      try {
        await resolveReview(selected.task_id, decision, notes);
        setToast(`Claim ${decision.toLowerCase()}d`);
        setSelected(null);
        await listReviews(statusFilter);
      } catch (e) {
        setToast(e instanceof Error ? e.message : 'Resolve failed');
      } finally {
        setSubmitting(false);
        setTimeout(() => setToast(null), 3000);
      }
    },
    [selected, notes, resolveReview, listReviews, statusFilter],
  );

  return (
    <div className="adj-page">
      <header className="adj-topbar">
        <div className="adj-topbar-left">
          <span className="adj-icon">🗂️</span>
          <span className="adj-title">Adjuster Console</span>
        </div>
        <div className="adj-topbar-right">
          <span className="adj-user">{session?.user.username}</span>
          <button className="adj-logout" onClick={logout}>Logout</button>
        </div>
      </header>

      <div className="adj-body">
        {/* Queue */}
        <aside className="adj-queue">
          <div className="adj-queue-head">
            <div className="adj-tabs">
              {(['OPEN', 'RESOLVED'] as const).map((s) => (
                <button
                  key={s}
                  className={`adj-tab ${statusFilter === s ? 'active' : ''}`}
                  onClick={() => { setStatusFilter(s); setSelected(null); }}
                >
                  {s}
                </button>
              ))}
            </div>
            <button className="adj-refresh" onClick={() => listReviews(statusFilter)} title="Refresh">↻</button>
          </div>

          {loading && <div className="adj-empty">Loading…</div>}
          {error && <div className="adj-error">{error}</div>}
          {!loading && !error && tasks.length === 0 && (
            <div className="adj-empty">No {statusFilter.toLowerCase()} tasks</div>
          )}

          <ul className="adj-list">
            {tasks.map((t) => (
              <li
                key={t.task_id}
                className={`adj-list-item ${selected?.task_id === t.task_id ? 'selected' : ''}`}
                onClick={() => openTask(t.task_id)}
              >
                <div className="adj-list-row">
                  <span className="adj-list-incident">{t.claim?.incident_type ?? 'Claim'}</span>
                  <span className="adj-list-amount">{formatAmount(t.claim?.claimed_amount)}</span>
                </div>
                <div className="adj-list-row adj-list-sub">
                  <span>{t.policyholder_name || t.actor_id}</span>
                  <FraudBadge level={t.signals?.fraud?.risk_level} />
                </div>
              </li>
            ))}
          </ul>
        </aside>

        {/* Detail */}
        <main className="adj-detail">
          {!selected ? (
            <div className="adj-detail-empty">Select a claim from the queue to review.</div>
          ) : (
            <TaskDetail
              task={selected}
              notes={notes}
              setNotes={setNotes}
              submitting={submitting}
              onResolve={handleResolve}
            />
          )}
        </main>
      </div>

      {toast && <div className="adj-toast">{toast}</div>}
    </div>
  );
}

function FraudBadge({ level }: { level?: string }) {
  if (!level) return null;
  return <span className={`adj-badge fraud-${level.toLowerCase()}`}>{level}</span>;
}

function CoverageBadge({ determination }: { determination?: string }) {
  if (!determination) return null;
  const cls = determination === 'COVERED' ? 'cov-covered' : determination === 'EXCLUDED' ? 'cov-excluded' : 'cov-uncertain';
  return <span className={`adj-badge ${cls}`}>{determination}</span>;
}

function TaskDetail({
  task, notes, setNotes, submitting, onResolve,
}: {
  task: ReviewTask;
  notes: string;
  setNotes: (s: string) => void;
  submitting: boolean;
  onResolve: (d: Decision) => void;
}) {
  const { claim, signals, resolution } = task;
  const resolved = task.status === 'RESOLVED';

  return (
    <div className="adj-detail-inner">
      <div className="adj-detail-head">
        <div>
          <h2>{claim?.incident_type ?? 'Claim'}</h2>
          <div className="adj-detail-meta">
            {task.policyholder_name || task.actor_id} · {claim?.policy_type} policy {claim?.policy_number} · Filed {claim?.filing_date}
            {task.decision_mode === 'auto' && <span className="adj-mode-tag">Agent Escalated</span>}
            {task.decision_mode === 'human' && <span className="adj-mode-tag human">Human Review</span>}
          </div>
        </div>
        <div className="adj-detail-badges">
          <CoverageBadge determination={signals?.coverage?.determination} />
          <FraudBadge level={signals?.fraud?.risk_level} />
        </div>
      </div>

      {/* Claim summary */}
      <section className="adj-card">
        <h3>Claim</h3>
        <div className="adj-kv"><span>Incident date</span><span>{claim?.incident_date ?? '—'}</span></div>
        <div className="adj-kv"><span>Amount</span><span>{formatAmount(claim?.claimed_amount, '—')}</span></div>
        <p className="adj-desc">{claim?.description}</p>
      </section>

      {/* Factual signals */}
      <div className="adj-signals">
        {signals?.adjudication && (
          <section className="adj-card adj-reasoning-card">
            <h3>Agent Analysis <span className={`adj-badge fraud-${(signals.adjudication.decision || '').toLowerCase()}`}>{signals.adjudication.decision}</span></h3>
            <pre className="adj-rubric">{signals.adjudication.internal_reasoning}</pre>
            {(signals.adjudication.cited_patterns?.length ?? 0) > 0 && (
              <div className="adj-cited">
                <strong>Cited patterns:</strong>
                <ul>{signals.adjudication.cited_patterns!.map((p, i) => <li key={i}>{p}</li>)}</ul>
              </div>
            )}
          </section>
        )}

        <section className="adj-card">
          <h3>Coverage</h3>
          <div className="adj-kv"><span>Determination</span><CoverageBadge determination={signals?.coverage?.determination} /></div>
          <div className="adj-kv"><span>Matched</span><span>{signals?.coverage?.matched_term ?? '—'}</span></div>
          <p className="adj-note">{signals?.coverage?.message}</p>
        </section>

        <section className="adj-card">
          <h3>Fraud signals</h3>
          <div className="adj-kv"><span>Risk</span><FraudBadge level={signals?.fraud?.risk_level} /></div>
          <div className="adj-kv"><span>Score</span><span>{signals?.fraud?.risk_score ?? 0}/100</span></div>
          <div className="adj-kv"><span>Reporting delay</span><span>{signals?.fraud?.delay_days ?? '—'} day(s)</span></div>
          {(signals?.fraud?.flags?.length ?? 0) > 0 ? (
            <ul className="adj-flags">
              {signals!.fraud!.flags!.map((f, i) => <li key={i}>⚠ {f}</li>)}
            </ul>
          ) : (
            <p className="adj-note">No fraud indicators.</p>
          )}
        </section>

        <section className="adj-card">
          <h3>Policy</h3>
          <div className="adj-kv"><span>Status</span><span>{signals?.policy?.status ?? '—'}</span></div>
          <div className="adj-kv"><span>Deductible</span><span>{signals?.policy?.deductible ?? '—'}</span></div>
          <div className="adj-kv"><span>Limit</span><span>{signals?.policy?.coverage_limit ?? '—'}</span></div>
          <div className="adj-kv"><span>Exclusions</span><span>{signals?.policy?.exclusions?.join(', ') ?? '—'}</span></div>
        </section>

        <section className="adj-card">
          <h3>Claims history ({signals?.claims_history?.prior_count ?? 0})</h3>
          {(signals?.claims_history?.claims?.length ?? 0) > 0 ? (
            <ul className="adj-history">
              {signals!.claims_history!.claims!.map((c) => (
                <li key={c.claim_id}>
                  <strong>{c.type}</strong> · {c.amount} · {c.outcome} <span className="adj-muted">({c.date})</span>
                  <div className="adj-muted">{c.description}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="adj-note">No prior claims.</p>
          )}
        </section>

        {signals?.precedent_patterns && (
          <section className="adj-card">
            <h3>Precedent patterns ({signals.precedent_patterns.count ?? 0})</h3>
            {signals.precedent_patterns.filter && (
              <div className="adj-kv"><span>Filter</span><span>{signals.precedent_patterns.filter}</span></div>
            )}
            {(signals.precedent_patterns.patterns ?? []).length > 0 ? (
              <ul className="adj-patterns">
                {(signals.precedent_patterns.patterns ?? []).map((p, i) => {
                  const title = String(p.title || 'Untitled');
                  const conf = p.confidence ? String(p.confidence) : '';
                  const uses = p.use_cases ? String(p.use_cases).slice(0, 150) : '';
                  return (
                    <li key={i}>
                      <strong>{title}</strong>
                      {conf && <span className="adj-confidence">{conf}</span>}
                      {uses && <div className="adj-muted">{uses}</div>}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="adj-note">No relevant patterns found.</p>
            )}
          </section>
        )}

        {signals?.policyholder_episodes && (
          <section className="adj-card">
            <h3>Policyholder episodes ({signals.policyholder_episodes.count ?? 0})</h3>
            {(signals.policyholder_episodes.episodes ?? []).length > 0 ? (
              <ul className="adj-history">
                {(signals.policyholder_episodes.episodes ?? []).map((ep, i) => {
                  const sit = String(ep.situation || '').slice(0, 80);
                  const just = ep.justification ? String(ep.justification).slice(0, 150) : '';
                  return (
                    <li key={i}>
                      <strong>{sit}</strong>
                      {just && <div className="adj-muted">{just}</div>}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="adj-note">No prior episodes for this policyholder.</p>
            )}
          </section>
        )}
      </div>

      {/* Resolution / actions */}
      {resolved ? (
        <section className="adj-card adj-resolved">
          <h3>Resolved</h3>
          <div className="adj-kv"><span>Decision</span><span className="adj-decision">{resolution?.decision}</span></div>
          <div className="adj-kv"><span>Adjuster</span><span>{resolution?.adjuster_id}</span></div>
          <div className="adj-kv"><span>When</span><span>{resolution?.resolved_at}</span></div>
          {resolution?.notes && <p className="adj-desc">{resolution.notes}</p>}
        </section>
      ) : (
        <section className="adj-card adj-actions">
          <h3>Decision</h3>
          <textarea
            className="adj-notes"
            placeholder="Adjuster notes (optional)…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={submitting}
          />
          <div className="adj-buttons">
            {DECISIONS.map((d) => (
              <button
                key={d}
                className={`adj-action adj-action-${d.toLowerCase()}`}
                onClick={() => onResolve(d)}
                disabled={submitting}
              >
                {d}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
