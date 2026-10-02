// ─── Bedrock AgentCore Streaming Call ────────────────────────────────────────
// Direct client-side SDK call for streaming (same pattern as the original React app).
// Streaming responses need real-time React state updates, so they stay client-side.
// Non-streaming calls (query-results, generate-chart) use API routes.

import { v4 as uuidv4 } from 'uuid';
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from '@aws-sdk/client-bedrock-agentcore';
import { getAwsClient, type CognitoAuthParams } from '@/utils/aws-client';
import { getQueryResults } from './aws-calls';
import type { Answer, ControlAnswer, MessageItem } from '../types';

interface GetAnswerParams {
  query: string;
  sessionId: string;
  userId: string;
  userName: string;
  agentRuntimeArn: string;
  agentEndpointName: string;
  questionAnswersTableName: string;
  auth: CognitoAuthParams;
  setControlAnswers: React.Dispatch<React.SetStateAction<ControlAnswer[]>>;
  setAnswers: React.Dispatch<React.SetStateAction<Answer[]>>;
  setEnabled: React.Dispatch<React.SetStateAction<boolean>>;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setErrorMessage: React.Dispatch<React.SetStateAction<string>>;
  setQuery: React.Dispatch<React.SetStateAction<string>>;
  setCurrentWorkingToolId: React.Dispatch<React.SetStateAction<string | null>>;
}

export const getAnswer = async ({
  query: myQuery,
  sessionId,
  userId,
  userName,
  agentRuntimeArn,
  agentEndpointName,
  questionAnswersTableName,
  auth,
  setControlAnswers,
  setAnswers,
  setEnabled,
  setLoading,
  setErrorMessage,
  setQuery,
  setCurrentWorkingToolId,
}: GetAnswerParams) => {
  if (!myQuery) return;

  setControlAnswers((prev) => [...prev, {}]);
  setAnswers((prev) => [...prev, { query: myQuery }]);
  setEnabled(false);
  setLoading(true);
  setErrorMessage('');
  setQuery('');

  try {
    const queryUuid = uuidv4();
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

    const json: Answer = { text: [], queryUuid };

    setControlAnswers((prev) => [...prev, { current_tab_view: 'answer' }]);
    setAnswers((prev) => [...prev, json]);

    console.log('🆔 Query UUID:', queryUuid);

    const agentCore = getAwsClient(BedrockAgentCoreClient, auth);

    const payload = JSON.stringify({
      prompt: myQuery,
      session_id: sessionId,
      user_id: userId,
      user_name: userName,
      prompt_uuid: queryUuid,
      user_timezone: timezone,
    });

    const input = {
      agentRuntimeArn,
      qualifier: agentEndpointName,
      payload,
      runtimeSessionId: sessionId,
    };

    console.log('📤 Agent Core Input:', input);

    const command = new InvokeAgentRuntimeCommand(input);
    const response = await agentCore.send(command);

    let responseText = '';
    let currentTextItem = '';
    const textArray: MessageItem[] = [];

    console.log('🤖 Agent Response (Streaming):');

    try {
      if (response.response) {
        const stream = response.response.transformToWebStream();
        const reader = stream.getReader();
        const decoder = new TextDecoder();

        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            console.log('📦 Streaming Chunk:', chunk);

            if (
              chunk.includes('serviceUnavailableException') ||
              chunk.includes('Bedrock is unable to process your request')
            ) {
              throw new Error('Bedrock service is currently unavailable. Please try again in a few moments.');
            }

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const dataObjects: any[] = [];
            let currentToolName = '';

            chunk.split('\n').forEach((line) => {
              if (line.trim() && line.startsWith('data: ')) {
                const jsonString = line.replace(/^data: /, '{"data": ') + '}';
                try {
                  const obj = JSON.parse(jsonString);
                  if (obj.data && typeof obj.data === 'string') {
                    if (
                      obj.data.includes('serviceUnavailableException') ||
                      obj.data.includes('Bedrock is unable to process your request')
                    ) {
                      throw new Error('Bedrock service is currently unavailable.');
                    }
                  }
                  const dataObject = JSON.parse(obj.data);
                  dataObjects.push(dataObject);
                } catch (e) {
                  if ((e as Error).message?.includes('Bedrock service')) throw e;
                  // Expected: chunks can split JSON across boundaries — silently skip
                }
              }
            });

            for (const jsonData of dataObjects) {
              try {
                if (jsonData.event?.contentBlockStart?.start?.toolUse) {
                  if (currentTextItem.trim()) {
                    textArray.push({ type: 'text', content: currentTextItem });
                    currentTextItem = '';
                  }
                  const toolUse = jsonData.event.contentBlockStart.start.toolUse;
                  currentToolName = toolUse.name;
                  setCurrentWorkingToolId(toolUse.toolUseId);
                  console.log('🔧 Tool use:', toolUse.name, toolUse.toolUseId);
                  textArray.push({
                    type: 'tool',
                    toolUseId: toolUse.toolUseId,
                    name: toolUse.name,
                    inputs: '',
                  });
                } else if (jsonData.toolUseId && jsonData.name) {
                  const lastItem = textArray[textArray.length - 1];
                  if (lastItem?.type === 'tool' && lastItem.toolUseId === jsonData.toolUseId) {
                    lastItem.inputs = JSON.parse(jsonData.input);
                    setCurrentWorkingToolId(jsonData.toolUseId);
                    console.log('🔧 Tool inputs:', jsonData.name, lastItem.inputs);
                  }
                } else if (jsonData.event?.contentBlockStop) {
                  console.log('⏹️ Content block stopped');
                } else if (jsonData.start_event_loop) {
                  console.log('🔄 Start event loop');
                  currentToolName = '';
                } else if (jsonData.data) {
                  if (
                    typeof jsonData.data === 'string' &&
                    (jsonData.data.includes('serviceUnavailableException') ||
                      jsonData.data.includes('Bedrock is unable to process your request'))
                  ) {
                    throw new Error('Bedrock service is currently unavailable.');
                  }
                  currentToolName = '';
                  currentTextItem += jsonData.data;
                  responseText += jsonData.data;
                  setCurrentWorkingToolId(null);
                } else {
                  console.log('❓ Unknown event type:', jsonData);
                }
              } catch (e) {
                // Expected: partial data objects from chunked streaming
                if ((e as Error).message?.includes('Bedrock service')) {
                  console.error('❌ Bedrock service error:', e);
                }
              }
            }

            setAnswers((prev) => {
              const newAnswers = [...prev];
              const lastIndex = newAnswers.length - 1;
              const currentArray: MessageItem[] = [...textArray];
              if (currentTextItem.trim()) {
                currentArray.push({ type: 'text', content: currentTextItem });
              }
              newAnswers[lastIndex] = {
                ...newAnswers[lastIndex],
                text: currentArray,
                currentToolName,
              };
              return newAnswers;
            });
          }
        } finally {
          reader.releaseLock();
        }
      }
    } catch (streamError) {
      console.error('Error processing agent response stream:', streamError);
      throw streamError;
    }

    // Final update
    console.log('📝 Complete Agent Response:', responseText);

    setAnswers((prev) => {
      const newAnswers = [...prev];
      const lastIndex = newAnswers.length - 1;
      const finalArray: MessageItem[] = [...textArray];
      if (currentTextItem.trim()) {
        finalArray.push({ type: 'text', content: currentTextItem });
      }
      newAnswers[lastIndex] = { ...newAnswers[lastIndex], text: finalArray, queryUuid };
      return newAnswers;
    });

    // Fetch query results for charts/tables
    try {
      console.log('📊 Fetching query results for:', queryUuid);
      const queryResults = await getQueryResults(queryUuid, questionAnswersTableName, auth);
      console.log('📊 Query Results:', queryResults.length, 'result sets');

      if (queryResults.length > 0) {
        setAnswers((prev) => {
          const newAnswers = [...prev];
          const lastIndex = newAnswers.length - 1;
          newAnswers[lastIndex] = {
            ...newAnswers[lastIndex],
            queryResults,
            chart: 'loading',
          };
          return newAnswers;
        });
      }
    } catch (queryError) {
      console.error('Error fetching query results:', queryError);
    }

    setLoading(false);
    setEnabled(false);
    setCurrentWorkingToolId(null);
  } catch (error) {
    const err = error as Error;
    console.error('❌ Call failed:', err);

    if (err.message?.includes('Bedrock service is currently unavailable')) {
      setErrorMessage('Bedrock AI service is temporarily unavailable. Please try again in a few moments.');
    } else {
      setErrorMessage(err.toString());
    }
    setLoading(false);
    setEnabled(false);
    setCurrentWorkingToolId(null);

    setAnswers((prev) => {
      const newState = [...prev];
      for (let i = newState.length - 1; i >= 0; i--) {
        if (newState[i].text && Array.isArray(newState[i].text)) {
          newState[i] = {
            ...newState[i],
            text: [{ type: 'text', content: 'Error occurred while getting response' }],
            error: true,
          };
          break;
        }
      }
      return newState;
    });
  }
};
