export interface CognitoConfig {
  poolId: string;
  clientId: string;
  discoveryUrl: string;
  domainPrefix: string;
  redirectUri: string;
}

export interface AppConfig {
  agentId: string | null;
  agentArn: string | null;
  region: string;
  cognito: CognitoConfig;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  latency?: number;
  ttft?: number;
}

export interface InvokeResult {
  data?: any;
  error?: string;
  latency: number;
  success: boolean;
}
