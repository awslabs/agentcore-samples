import { useState } from 'react';
import { ChevronDown, ChevronRight, Wrench, Loader2, CheckCircle, XCircle } from 'lucide-react';
import { cn } from '../utils';
import type { ToolUseBlock } from '../types';

interface ToolUseBlockProps {
  toolBlock: ToolUseBlock;
}

export function ToolUseBlockComponent({ toolBlock }: ToolUseBlockProps) {
  const [showInput, setShowInput] = useState(false);
  // Expand results by default to show agentic workflow
  const [showResult, setShowResult] = useState(false);

  const statusIcon = {
    loading: <Loader2 className="w-4 h-4 animate-spin text-blue-400" />,
    success: <CheckCircle className="w-4 h-4 text-green-400" />,
    error: <XCircle className="w-4 h-4 text-red-400" />,
  }[toolBlock.status || 'success'];

  // Format the result - try to parse JSON from the string
  const formatResult = (result: string) => {
    if (!result) return '';

    // Try to extract JSON from the result string
    try {
      // Look for JSON object in the string
      const jsonMatch = result.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const jsonObj = JSON.parse(jsonMatch[0]);

        // If it has a body field with stringified content, parse that too
        if (jsonObj.body && typeof jsonObj.body === 'string') {
          try {
            // The body might be a JSON string, try to parse and format it
            const bodyObj = JSON.parse(jsonObj.body);
            return JSON.stringify(bodyObj, null, 2);
          } catch {
            // If body is not JSON, just return it with newlines converted
            return jsonObj.body.replace(/\\n/g, '\n');
          }
        }

        return JSON.stringify(jsonObj, null, 2);
      }
    } catch {
      // If JSON parsing fails, just format the string nicely
    }

    // Fallback: convert \n to actual newlines and return as-is
    return result.replace(/\\n/g, '\n');
  };

  const formattedResult = toolBlock.result ? formatResult(toolBlock.result) : '';

  return (
    <div className="my-3 rounded-lg border border-[#3a3f4b] bg-[#1a1d24] overflow-hidden">
      {/* Tool Header */}
      <div className="px-3 py-2 bg-[#23272f] border-b border-[#3a3f4b] flex items-center gap-2">
        <Wrench className="w-4 h-4 text-purple-400" />
        <span className="text-sm font-medium text-gray-200">{toolBlock.name}</span>
        {statusIcon}
        <span className="text-xs text-gray-500 ml-auto">ID: {toolBlock.toolUseId.slice(0, 8)}...</span>
      </div>

      {/* Tool Input Section */}
      <div className="border-b border-[#3a3f4b]">
        <button
          onClick={() => setShowInput(!showInput)}
          className="w-full px-3 py-2 flex items-center gap-2 hover:bg-[#23272f] transition-colors text-left"
        >
          {showInput ? (
            <ChevronDown className="w-4 h-4 text-gray-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-gray-400" />
          )}
          <span className="text-xs font-medium text-gray-400">Input</span>
        </button>
        {showInput && (
          <div className="px-3 pb-3">
            <pre className="text-xs text-gray-300 bg-[#0d0f14] p-2 rounded overflow-x-auto">
              {JSON.stringify(toolBlock.input, null, 2)}
            </pre>
          </div>
        )}
      </div>

      {/* Tool Result Section */}
      {toolBlock.result && (
        <div>
          <button
            onClick={() => setShowResult(!showResult)}
            className="w-full px-3 py-2 flex items-center gap-2 hover:bg-[#23272f] transition-colors text-left"
          >
            {showResult ? (
              <ChevronDown className="w-4 h-4 text-gray-400" />
            ) : (
              <ChevronRight className="w-4 h-4 text-gray-400" />
            )}
            <span className="text-xs font-medium text-gray-400">Result</span>
          </button>
          {showResult && (
            <div className="px-3 pb-3">
              <pre className="text-xs text-gray-300 bg-[#0d0f14] p-2 rounded overflow-x-auto whitespace-pre-wrap">
                {formattedResult}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
