// Server component — reads env vars and passes to client component
import { AssistantClient } from './AssistantClient';
import type { AssistantConfig } from '../../components/assistant';

export default function AssistantPage() {
  const assistantConfig: AssistantConfig = {
    agentRuntimeArn: process.env.AGENT_RUNTIME_ARN || '',
    agentEndpointName: process.env.AGENT_ENDPOINT_NAME || 'DEFAULT',
    welcomeMessage: process.env.WELCOME_MESSAGE || "I'm your AI Data Analyst, crunching data for insights.",
    appName: process.env.APP_NAME || 'Data Analyst Assistant',
    modelIdForChart: process.env.MODEL_ID_FOR_CHART || 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    questionAnswersTableName: process.env.QUESTION_ANSWERS_TABLE_NAME || '',
    memoryId: process.env.MEMORY_ID || '',
    maxLengthInputSearch: parseInt(process.env.MAX_LENGTH_INPUT_SEARCH || '500', 10),
  };

  return <AssistantClient assistantConfig={assistantConfig} />;
}
