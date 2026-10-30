/* Shared types for the insurance claims demo frontend */

export interface DemoUser {
  username: string;
  actor_id: string;
}

export interface AuthSession {
  user: DemoUser;
  accessToken?: string;
  idToken?: string;
  groups?: string[];
}

export interface Session {
  user_id: string;
  session_id: string;
  actor_id: string;
  user_email: string;
  session_title: string;
  created_at: string;
  updated_at: string;
}

/* ----- HITL review tasks (adjuster console) ----- */

export interface PriorClaim {
  claim_id: string;
  date: string;
  type: string;
  amount: string;
  outcome: string;
  description: string;
  policy: string;
}

export interface ReviewTask {
  task_id: string;
  session_id: string;
  actor_id: string;
  policyholder_name?: string;
  decision_mode?: string;
  user_id: string | null;
  status: 'OPEN' | 'RESOLVED';
  created_at: string;
  claim: {
    policy_number?: string;
    policy_type?: string;
    incident_type?: string;
    incident_date?: string;
    filing_date?: string;
    claimed_amount?: string;
    description?: string;
  };
  signals: {
    policy?: {
      found?: boolean;
      status?: string;
      type?: string;
      deductible?: string;
      coverage_limit?: string;
      exclusions?: string[];
    };
    coverage?: { determination?: string; matched_term?: string; message?: string };
    fraud?: {
      risk_level?: string;
      risk_score?: number;
      delay_days?: number;
      prior_count?: number;
      flags?: string[];
    };
    claims_history?: { prior_count?: number; claims?: PriorClaim[] };
    precedent_patterns?: { query?: string; filter?: string; count?: number; patterns?: Record<string, unknown>[] };
    policyholder_episodes?: { actor_id?: string; query?: string; count?: number; episodes?: Record<string, unknown>[] };
    adjudication?: { decision?: string; amount?: number | null; internal_reasoning?: string; cited_patterns?: string[] };
  };
  transcript_ref?: { memory_id?: string; actor_id?: string; session_id?: string };
  resolution: { decision: string; adjuster_id: string; notes: string; resolved_at: string } | null;
}

/* ----- Admin memory inspector ----- */

export interface MemoryEvent {
  eventId: string;
  timestamp: string;
  created_at: string | null;
  branch: unknown;
  kind: 'message' | 'tool_use' | 'tool_result' | 'state' | 'other';
  role: string | null;
  tool: string | null;
  text: string;
  metadata: Record<string, unknown>;
}

export interface MemoryRecord {
  recordId: string;
  createdAt: string;
  namespaces: string[] | null;
  strategyId: string | null;
  text: string;
  parsed: Record<string, unknown> | null;
  metadata: Record<string, { stringValue?: string; numberValue?: number; dateTimeValue?: string }> | null;
}

export interface SubtoolEvent {
  eventId: string;
  timestamp: string;
  tool: string;
  query: string;
  filter: string;
  result_count: number;
  results: string[];
}

export interface AdminMemoryResponse {
  memory_id: string;
  decision_mode: string;
  events: MemoryEvent[];
  subtools: SubtoolEvent[];
  episodes: MemoryRecord[];
  reflections: MemoryRecord[];
  counts: { events: number; subtools: number; episodes: number; reflections: number };
}

export interface AdminSession {
  session_id: string;
  title: string;
  created_at: string;
  actor_id: string;
}
