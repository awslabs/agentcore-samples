import { useState, useEffect, useCallback, createContext, useContext, ReactNode } from 'react';
import type { ChatState, Message, ToolUseBlock, MessageMetadata, StreamingEvent, ContentBlock } from '../types';
import { invokeAgentStream } from '../services/chatService';
import { generateUUID } from '../utils';

interface ChatContextType extends ChatState {
  sendMessage: (message: string, bearerToken: string, actorId: string) => Promise<void>;
  initializeConversation: (email: string, bearerToken: string, actorId: string) => Promise<void>;
  clearMessages: () => void;
  isInitialized: boolean;
  initializationError: string | null;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

interface ChatProviderProps {
  children: ReactNode;
}

export function ChatProvider({ children }: ChatProviderProps) {
  const [chatState, setChatState] = useState<ChatState>({
    messages: [],
    isStreaming: false,
    sessionId: generateUUID(),
    agentArn: import.meta.env.VITE_AGENT_ARN || '',
    region: import.meta.env.VITE_AWS_REGION || 'us-west-2',
  });

  const [isInitialized, setIsInitialized] = useState(false);
  const [initializationError, setInitializationError] = useState<string | null>(null);

  // Initialize agent from environment variables
  useEffect(() => {
    const agentArn = import.meta.env.VITE_AGENT_ARN;
    const region = import.meta.env.VITE_AWS_REGION;

    if (!agentArn) {
      setInitializationError('Agent ARN not configured. Please set VITE_AGENT_ARN in .env file');
      return;
    }

    setIsInitialized(true);
  }, []);

  const sendMessage = useCallback(
    async (message: string, bearerToken: string, actorId: string) => {
      if (!chatState.agentArn || !chatState.region) {
        throw new Error('Agent not initialized');
      }

      // Add user message
      const userMessage: Message = {
        role: 'user',
        content: message,
        timestamp: Date.now(),
      };

      setChatState((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
        isStreaming: true,
      }));

      const startTime = Date.now();
      let accumulatedResponse = '';
      const toolBlocks: Map<string, ToolUseBlock> = new Map();
      // Changed: Sequential list of all content (text and tools) in order received
      const orderedContent: Array<{ type: 'text' | 'tool'; content?: string; toolBlock?: ToolUseBlock; toolUseId?: string }> = [];
      let currentTextBlock: { type: 'text'; content: string } | null = null;
      let metadata: MessageMetadata = {};

      try {
        for await (const event of invokeAgentStream(
          chatState.agentArn,
          chatState.region,
          chatState.sessionId,
          bearerToken,
          message,
          actorId
        )) {
          // Type guard: ensure event is an object
          if (typeof event !== 'object' || event === null) {
            continue
          }

          // Handle nested event structure: {event: {contentBlockDelta: {delta: {text: "..."}}}}
          if ('event' in event && event.event && typeof event.event === 'object') {
            const innerEvent = event.event as any;

            // Handle contentBlockStart events (tool invocation start)
            if ('contentBlockStart' in innerEvent) {
              const start = innerEvent.contentBlockStart?.start;
              const contentBlockIndex = innerEvent.contentBlockStart?.contentBlockIndex ?? 0;

              if (start?.toolUse) {
                const { toolUseId, name } = start.toolUse;

                // Check if we already have this tool (avoid duplicates)
                if (!toolBlocks.has(toolUseId)) {
                  // Finalize current text block if exists
                  if (currentTextBlock && currentTextBlock.content) {
                    orderedContent.push({ ...currentTextBlock });
                    currentTextBlock = null;
                  }

                  const newToolBlock: ToolUseBlock = {
                    toolUseId,
                    name,
                    input: {},
                    status: 'loading',
                  };
                  toolBlocks.set(toolUseId, newToolBlock);

                  // Add tool to sequential list
                  orderedContent.push({
                    type: 'tool',
                    toolBlock: newToolBlock,
                    toolUseId: toolUseId, // Store ID for later updates
                  });
                }
              }
            }

            // Handle contentBlockDelta events
            if ('contentBlockDelta' in innerEvent) {
              const delta = innerEvent.contentBlockDelta?.delta;
              const contentBlockIndex = innerEvent.contentBlockDelta?.contentBlockIndex ?? 0;

              // Handle text delta
              if (delta?.text) {
                accumulatedResponse += delta.text;

                // Accumulate text in current text block
                if (!currentTextBlock) {
                  currentTextBlock = { type: 'text', content: '' };
                }
                currentTextBlock.content += delta.text;
              }

              // Handle tool input delta (accumulate tool input)
              if (delta?.toolUse?.input) {
                // Tool inputs are accumulated in the delta - we'll get the final input later
              }
            }

            // Handle contentBlockStop events (tool execution complete)
            if ('contentBlockStop' in innerEvent) {
              // Mark all loading tools as success when content block stops
              toolBlocks.forEach((tool, id) => {
                if (tool.status === 'loading') {
                  toolBlocks.set(id, { ...tool, status: 'success' });
                }
              });
            }

            // Handle messageStop events
            if ('messageStop' in innerEvent) {
              metadata.stopReason = innerEvent.messageStop?.stopReason;
            }

            // Handle metadata events
            if ('metadata' in innerEvent) {
              const meta = innerEvent.metadata;
              if (meta?.usage) {
                metadata.usage = meta.usage;
              }
              if (meta?.metrics) {
                metadata.metrics = meta.metrics;
              }
            }
          }

          // Handle direct text delta events (fallback format: {data: "...", delta: {text: "..."}})
          else if ('delta' in event && event.delta && typeof event.delta === 'object' && 'text' in event.delta) {
            const text = (event.delta as any).text;
            if (text) {
              accumulatedResponse += text;

              // Accumulate in current text block
              if (!currentTextBlock) {
                currentTextBlock = { type: 'text', content: '' };
              }
              currentTextBlock.content += text;
            }
          }
          // Handle simple data events
          else if ('data' in event && typeof event.data === 'string') {
            accumulatedResponse += event.data;

            // Accumulate in current text block
            if (!currentTextBlock) {
              currentTextBlock = { type: 'text', content: '' };
            }
            currentTextBlock.content += event.data;
          }

          // Handle message event (contains complete tool information)
          if ('message' in event) {
            const message = event.message as any;
            if (message?.content && Array.isArray(message.content)) {
              message.content.forEach((item: any, idx: number) => {
                // Handle tool use in message content
                if (item.toolUse) {
                  const { toolUseId, name, input } = item.toolUse;

                  const existing = toolBlocks.get(toolUseId);
                  if (existing) {
                    // Update with complete input
                    toolBlocks.set(toolUseId, {
                      ...existing,
                      input: input || {},
                      status: 'success',
                    });
                  } else {
                    // Tool wasn't captured during streaming, add it now

                    // Finalize current text block if exists
                    if (currentTextBlock && currentTextBlock.content) {
                      orderedContent.push({ ...currentTextBlock });
                      currentTextBlock = null;
                    }

                    const newTool: ToolUseBlock = {
                      toolUseId,
                      name,
                      input: input || {},
                      status: 'success',
                    };
                    toolBlocks.set(toolUseId, newTool);

                    // Add to sequential list
                    orderedContent.push({
                      type: 'tool',
                      toolBlock: newTool,
                      toolUseId: toolUseId,
                    });
                  }
                }
                // Handle tool result in message content
                if (item.toolResult) {
                  const { toolUseId, content } = item.toolResult;
                  const existing = toolBlocks.get(toolUseId);
                  if (existing) {
                    // Add result to tool block
                    const resultText = Array.isArray(content)
                      ? content.map(c => c.text || c.json || JSON.stringify(c)).join('\n')
                      : typeof content === 'string' ? content : JSON.stringify(content);
                    toolBlocks.set(toolUseId, {
                      ...existing,
                      result: resultText,
                      status: 'success',
                    });
                  }
                }
              });
            }
          }

          // Handle current_tool_use events (if backend sends properly formatted JSON)
          if ('current_tool_use' in event) {
            const toolUse = event.current_tool_use as any;
            const { toolUseId, name, input } = toolUse;
            const existing = toolBlocks.get(toolUseId);
            if (existing) {
              // Update existing tool with input
              toolBlocks.set(toolUseId, {
                ...existing,
                input: input || {},
              });
            } else {
              // Create new tool block
              toolBlocks.set(toolUseId, {
                toolUseId,
                name,
                input: input || {},
                status: 'loading',
              });
            }
          }

          // Handle tool stream events (results)
          if ('tool_stream_event' in event) {
            const toolStreamEvent = event.tool_stream_event as any;
            const { tool_use, data } = toolStreamEvent;
            const existing = toolBlocks.get(tool_use.toolUseId);
            if (existing) {
              toolBlocks.set(tool_use.toolUseId, {
                ...existing,
                result: (existing.result || '') + data,
                status: 'success',
              });
            }
          }

          // Handle final result event
          if ('result' in event) {
            const result = event.result as any;
            if (result.tool_metrics) {
              metadata.toolMetrics = result.tool_metrics;
            }
            if (result.cycle_durations) {
              metadata.cycleDurations = result.cycle_durations;
            }
            if (result.accumulated_usage) {
              metadata.usage = {
                inputTokens: result.accumulated_usage.input_tokens,
                outputTokens: result.accumulated_usage.output_tokens,
                totalTokens: result.accumulated_usage.input_tokens + result.accumulated_usage.output_tokens,
              };
            }
          }

          // Handle metadata in event
          if ('metadata' in event) {
            const meta = event.metadata as any;
            if (meta?.usage) {
              metadata.usage = meta.usage;
            }
            if (meta?.metrics) {
              metadata.metrics = meta.metrics;
            }
          }

          // Handle stop_reason
          if ('stop_reason' in event) {
            metadata.stopReason = event.stop_reason as string;
          }

          // Build streaming content blocks array with updated tool data
          const orderedContentBlocks: ContentBlock[] = [];

          // Add all finalized content from orderedContent
          for (const item of orderedContent) {
            if (item.type === 'text' && item.content) {
              orderedContentBlocks.push({
                type: 'text',
                content: item.content,
              });
            } else if (item.type === 'tool' && item.toolUseId) {
              // Get latest tool state from toolBlocks map
              const latestToolBlock = toolBlocks.get(item.toolUseId);
              if (latestToolBlock) {
                orderedContentBlocks.push({
                  type: 'tool',
                  toolBlock: latestToolBlock,
                });
              }
            }
          }

          // Add current in-progress text block (for live streaming updates)
          if (currentTextBlock && currentTextBlock.content) {
            orderedContentBlocks.push({
              type: 'text',
              content: currentTextBlock.content,
            });
          }

          // Update streaming message
          setChatState((prev) => {
            const messages = [...prev.messages];
            const lastMessage = messages[messages.length - 1];

            if (lastMessage && lastMessage.role === 'assistant') {
              // Update existing assistant message
              messages[messages.length - 1] = {
                ...lastMessage,
                content: accumulatedResponse,
                toolBlocks: Array.from(toolBlocks.values()),
                contentBlocks: orderedContentBlocks,
                metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
              };
            } else {
              // Add new assistant message
              messages.push({
                role: 'assistant',
                content: accumulatedResponse,
                timestamp: Date.now(),
                toolBlocks: Array.from(toolBlocks.values()),
                contentBlocks: orderedContentBlocks,
                metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
              });
            }

            return {
              ...prev,
              messages,
            };
          });
        }

        // Finalize any pending text block AFTER stream completes
        if (currentTextBlock && currentTextBlock.content) {
          orderedContent.push({ ...currentTextBlock });
          currentTextBlock = null;
        }

        // Build FINAL content blocks with all finalized content
        const finalContentBlocks: ContentBlock[] = [];
        for (const item of orderedContent) {
          if (item.type === 'text' && item.content) {
            finalContentBlocks.push({
              type: 'text',
              content: item.content,
            });
          } else if (item.type === 'tool' && item.toolUseId) {
            const latestToolBlock = toolBlocks.get(item.toolUseId);
            if (latestToolBlock) {
              finalContentBlocks.push({
                type: 'tool',
                toolBlock: latestToolBlock,
              });
            }
          }
        }

        const elapsed = (Date.now() - startTime) / 1000;
        const totalLatencyMs = Date.now() - startTime;

        // Finalize message with elapsed time and total latency
        setChatState((prev) => {
          const messages = [...prev.messages];
          const lastMessage = messages[messages.length - 1];

          if (lastMessage && lastMessage.role === 'assistant') {
            // Add total latency to metadata
            const finalMetadata = {
              ...lastMessage.metadata,
              metrics: {
                ...lastMessage.metadata?.metrics,
                latencyMs: lastMessage.metadata?.metrics?.latencyMs || 0,
                totalLatencyMs,
              },
            };

            messages[messages.length - 1] = {
              ...lastMessage,
              elapsed,
              contentBlocks: finalContentBlocks,
              toolBlocks: Array.from(toolBlocks.values()),
              metadata: Object.keys(finalMetadata).length > 0 ? finalMetadata : undefined,
            };
          }

          return {
            ...prev,
            messages,
            isStreaming: false,
          };
        });
      } catch (error) {
        console.error('Error sending message:', error);
        setChatState((prev) => ({
          ...prev,
          isStreaming: false,
        }));

        // Add error message
        setChatState((prev) => ({
          ...prev,
          messages: [
            ...prev.messages,
            {
              role: 'assistant',
              content: `Error: Failed to get response from assistant. ${error instanceof Error ? error.message : 'Unknown error'}`,
              timestamp: Date.now(),
            },
          ],
        }));
      }
    },
    [chatState.agentArn, chatState.region, chatState.sessionId]
  );

  const initializeConversation = useCallback(
    async (email: string, bearerToken: string, actorId: string) => {
      const defaultPrompt = `Hi, how are you doing?`;
      await sendMessage(defaultPrompt, bearerToken, actorId);
    },
    [sendMessage]
  );

  const clearMessages = useCallback(() => {
    setChatState((prev) => ({
      ...prev,
      messages: [],
      sessionId: generateUUID(),
    }));
  }, []);

  return (
    <ChatContext.Provider
      value={{
        ...chatState,
        sendMessage,
        initializeConversation,
        clearMessages,
        isInitialized,
        initializationError,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
}
