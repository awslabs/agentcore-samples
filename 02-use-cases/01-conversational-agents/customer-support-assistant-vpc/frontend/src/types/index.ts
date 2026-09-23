// Tool-related types
export interface ToolUseBlock {
  toolUseId: string;
  name: string;
  input: Record<string, any>;
  result?: string;
  status?: 'loading' | 'success' | 'error';
}

// Metadata types
export interface Usage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

export interface Metrics {
  latencyMs: number; // Agent latency from backend
  totalLatencyMs?: number; // Total latency calculated on frontend
}

export interface ToolMetrics {
  [toolName: string]: {
    invocations: number;
    total_duration_seconds: number;
    average_duration_seconds: number;
  };
}

export interface MessageMetadata {
  usage?: Usage;
  metrics?: Metrics;
  stopReason?: string;
  toolMetrics?: ToolMetrics;
  cycleDurations?: number[];
}

// Streaming event types
export interface MessageStartEvent {
  event: {
    messageStart: {
      role: string;
    };
  };
}

export interface ContentBlockDeltaEvent {
  event: {
    contentBlockDelta: {
      delta: {
        text: string;
      };
      contentBlockIndex: number;
    };
  };
}

export interface ContentBlockStartEvent {
  event: {
    contentBlockStart: {
      start: {
        toolUse: {
          toolUseId: string;
          name: string;
        };
      };
      contentBlockIndex: number;
    };
  };
}

export interface ContentBlockStopEvent {
  event: {
    contentBlockStop: {
      contentBlockIndex: number;
    };
  };
}

export interface MessageStopEvent {
  event: {
    messageStop: {
      stopReason: string;
    };
  };
}

export interface MetadataEvent {
  event: {
    metadata: {
      usage: Usage;
      metrics: Metrics;
    };
  };
}

// Actual backend event format (flattened structure)
export interface DeltaTextEvent {
  data?: string;
  delta: {
    text: string;
  };
  agent?: any;
  event_loop_cycle_id?: string;
  request_state?: any;
  event_loop_cycle_trace?: any;
  event_loop_cycle_span?: any;
}

export interface CurrentToolUseEvent {
  current_tool_use: {
    toolUseId: string;
    name: string;
    input: Record<string, any>;
  };
}

export interface ToolStreamEvent {
  tool_stream_event: {
    tool_use: {
      toolUseId: string;
      name: string;
    };
    data: string;
  };
}

export interface DataEvent {
  data: string;
}

export interface AgentResult {
  metrics: {
    total_duration_seconds: number;
    total_event_loop_cycles: number;
    max_event_loop_cycles_reached: boolean;
  };
  tool_metrics: ToolMetrics;
  cycle_durations: number[];
  accumulated_usage: {
    input_tokens: number;
    output_tokens: number;
  };
}

export interface ResultEvent {
  result: AgentResult;
}

// Add message event type
export interface MessageEvent {
  message: {
    role: string;
    content: Array<{
      text?: string;
      toolUse?: {
        toolUseId: string;
        name: string;
        input: Record<string, any>;
      };
      toolResult?: {
        toolUseId: string;
        status?: string;
        content: Array<{ text?: string; json?: any }> | string;
      };
    }>;
  };
}

export type StreamingEvent =
  | MessageStartEvent
  | ContentBlockDeltaEvent
  | ContentBlockStartEvent
  | ContentBlockStopEvent
  | MessageStopEvent
  | MetadataEvent
  | MessageEvent
  | DeltaTextEvent
  | CurrentToolUseEvent
  | ToolStreamEvent
  | DataEvent
  | ResultEvent;

// Content block types for ordered display
export type ContentBlock =
  | { type: 'text'; content: string }
  | { type: 'tool'; toolBlock: ToolUseBlock };

// Message types
export interface Message {
  role: 'user' | 'assistant';
  content: string;
  elapsed?: number;
  timestamp?: number;
  toolBlocks?: ToolUseBlock[];
  contentBlocks?: ContentBlock[]; // Ordered sequence of text and tool blocks
  metadata?: MessageMetadata;
}

export interface TokenResponse {
  access_token: string;
  id_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

export interface UserClaims {
  sub: string;
  email: string;
  'cognito:username': string;
  email_verified: boolean;
  aud: string;
  token_use: string;
}

export interface AuthState {
  isAuthenticated: boolean;
  tokens: TokenResponse | null;
  userClaims: UserClaims | null;
  loading: boolean;
  error: string | null;
}

export interface ChatState {
  messages: Message[];
  isStreaming: boolean;
  sessionId: string;
  agentArn: string;
  region: string;
}

export interface AppConfig {
  stackName: string;
  cognitoDomain: string;
  clientId: string;
  redirectUri: string;
  scopes: string;
}
