// ─── Assistant Component Types ───────────────────────────────────────────────

export interface ToolItem {
  type: 'tool';
  toolUseId: string;
  name: string;
  inputs: Record<string, unknown> | string;
}

export interface TextItem {
  type: 'text';
  content: string;
}

export type MessageItem = ToolItem | TextItem;

export interface QueryResult {
  query: string;
  query_results: Record<string, unknown>[];
  query_description: string;
}

export interface ChartConfig {
  chart_type: string;
  chart_configuration: {
    options: ApexCharts.ApexOptions;
    series: ApexCharts.ApexOptions['series'];
  };
  caption: string;
}

export interface ChartRationale {
  rationale: string;
}

export type ChartData = ChartConfig | ChartRationale | 'loading';

export interface Answer {
  query?: string;
  text?: MessageItem[];
  queryResults?: QueryResult[];
  chart?: ChartData;
  queryUuid?: string;
  currentToolName?: string;
  error?: boolean;
}

export interface ControlAnswer {
  current_tab_view?: 'answer' | 'records' | 'chart';
}

export interface AssistantConfig {
  agentRuntimeArn: string;
  agentEndpointName: string;
  welcomeMessage: string;
  appName: string;
  modelIdForChart: string;
  questionAnswersTableName: string;
  memoryId: string;
  maxLengthInputSearch?: number;
}
