import { memo, useState } from 'react'
import { Bot, User, ChevronDown, ChevronRight, Info } from 'lucide-react'
import { cn, makeUrlsClickable, formatElapsedTime } from '../utils'
import type { Message } from '../types'
import { ToolUseBlockComponent } from './ToolUseBlock'

interface ChatMessageProps {
  message: Message
  isStreaming?: boolean
}

export const ChatMessage = memo(function ChatMessage({
  message,
  isStreaming = false,
}: ChatMessageProps) {
  const isUser = message.role === 'user'
  const contentWithLinks = makeUrlsClickable(message.content)
  const [showMetadata, setShowMetadata] = useState(false)

  // Debug logging for assistant messages
  if (!isUser && message.contentBlocks) {
    console.log('[ChatMessage] Rendering assistant message with', message.contentBlocks.length, 'content blocks')
    const toolBlocks = message.contentBlocks.filter(b => b.type === 'tool')
    if (toolBlocks.length > 0) {
      console.log('[ChatMessage] 🔧 Rendering', toolBlocks.length, 'tool blocks:',
        toolBlocks.map(b => b.type === 'tool' ? b.toolBlock.name : ''))
    }
  }

  return (
    <div
      className={cn(
        "flex w-full animate-fade-in-up",
        isUser ? "justify-end" : "justify-start"
      )}
    >
      <div
        className={cn(
          "flex gap-3 max-w-[85%] rounded-2xl px-4 py-3 shadow-sm",
          isUser
            ? "bg-[#23272f] text-gray-200 border border-[#3a3f4b]"
            : cn(
                "bg-[#0b2545] text-gray-200 border",
                isStreaming
                  ? "border-[#4fc3f7] shadow-[0_0_10px_rgba(79,195,247,0.3)] animate-pulse-border"
                  : "border-[#298dff]"
              )
        )}
      >
        <div className="flex-shrink-0 mt-1">
          {isUser ? (
            <User className="w-5 h-5 text-gray-400" />
          ) : (
            <Bot className="w-5 h-5 text-blue-400" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          {/* Always render contentBlocks if available (for ordered display) */}
          {!isUser && message.contentBlocks && message.contentBlocks.length > 0 && (
            <div>
              {message.contentBlocks.map((block, index) => {
                if (block.type === 'text') {
                  const textWithLinks = makeUrlsClickable(block.content);
                  const isLastBlock = index === message.contentBlocks!.length - 1;
                  return (
                    <div
                      key={`text-${index}`}
                      className={cn(
                        "whitespace-pre-wrap break-words text-sm leading-relaxed",
                        index > 0 && "mt-3"
                      )}
                    >
                      <span dangerouslySetInnerHTML={{ __html: textWithLinks }} />
                      {isStreaming && isLastBlock && (
                        <span className="inline-block ml-1 text-[#4fc3f7] animate-cursor-blink">▋</span>
                      )}
                    </div>
                  );
                } else if (block.type === 'tool') {
                  return (
                    <div key={`tool-${block.toolBlock.toolUseId}`} className="mt-3">
                      <ToolUseBlockComponent toolBlock={block.toolBlock} />
                    </div>
                  );
                }
                return null;
              })}
            </div>
          )}

          {/* Fallback for user messages or old format */}
          {(isUser || (!message.contentBlocks || message.contentBlocks.length === 0)) && (
            <>
              <div
                className={cn(
                  "whitespace-pre-wrap break-words text-sm leading-relaxed",
                  isStreaming && "relative"
                )}
                dangerouslySetInnerHTML={{ __html: contentWithLinks }}
              />

              {isStreaming && (
                <span className="inline-block ml-1 text-[#4fc3f7] animate-cursor-blink">▋</span>
              )}
            </>
          )}

          {/* Metadata section */}
          {!isUser && message.metadata && !isStreaming && (
            <div className="mt-3">
              <button
                onClick={() => setShowMetadata(!showMetadata)}
                className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-400 transition-colors"
              >
                {showMetadata ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                <Info className="w-3 h-3" />
                <span>Metadata</span>
              </button>

              {showMetadata && (
                <div className="mt-2 p-3 bg-[#1a1d24] rounded-lg border border-[#3a3f4b] text-xs">
                  {message.metadata.usage && (
                    <div className="mb-2">
                      <div className="font-medium text-gray-400 mb-1">Token Usage</div>
                      <div className="text-gray-500 space-y-1">
                        <div>Input: {message.metadata.usage.inputTokens}</div>
                        <div>Output: {message.metadata.usage.outputTokens}</div>
                        <div>Total: {message.metadata.usage.totalTokens}</div>
                      </div>
                    </div>
                  )}

                  {message.metadata.metrics && (
                    <div className="mb-2">
                      <div className="font-medium text-gray-400 mb-1">Performance</div>
                      <div className="text-gray-500">
                        {message.metadata.metrics.totalLatencyMs !== undefined && (
                          <div>Total Latency: {(message.metadata.metrics.totalLatencyMs / 1000).toFixed(2)}s</div>
                        )}
                      </div>
                    </div>
                  )}

                  {message.metadata.toolMetrics && Object.keys(message.metadata.toolMetrics).length > 0 && (
                    <div className="mb-2">
                      <div className="font-medium text-gray-400 mb-1">Tool Metrics</div>
                      {Object.entries(message.metadata.toolMetrics).map(([toolName, metrics]) => (
                        <div key={toolName} className="text-gray-500 mb-1">
                          <div className="font-medium">{toolName}</div>
                          <div className="ml-2 space-y-0.5">
                            <div>Invocations: {metrics.invocations}</div>
                            <div>Avg Duration: {metrics.average_duration_seconds.toFixed(3)}s</div>
                            <div>Total Duration: {metrics.total_duration_seconds.toFixed(3)}s</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {message.metadata.cycleDurations && message.metadata.cycleDurations.length > 0 && (
                    <div>
                      <div className="font-medium text-gray-400 mb-1">Event Loop Cycles</div>
                      <div className="text-gray-500">
                        Cycles: {message.metadata.cycleDurations.length}
                        <div className="ml-2">
                          {message.metadata.cycleDurations.map((duration, idx) => (
                            <div key={idx}>Cycle {idx + 1}: {duration.toFixed(3)}s</div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}

                  {message.metadata.stopReason && (
                    <div className="mt-2 pt-2 border-t border-[#3a3f4b]">
                      <div className="font-medium text-gray-400">Stop Reason</div>
                      <div className="text-gray-500">{message.metadata.stopReason}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Elapsed time for simple responses */}
          {!isUser && message.elapsed !== undefined && !isStreaming && !message.metadata && (
            <div className="mt-2 text-xs text-gray-500">
              ⏱️ Response time: {formatElapsedTime(message.elapsed)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
})
