import { useState, useEffect, useRef } from "react";
import {
  invokeAgent,
  invokeAgentStream,
  generateSessionId,
  setSessionWarmupPromise,
  clearSessionState,
} from "../utils/agent";
import type { AppConfig, Message } from "../types";

interface ChatProps {
  config: AppConfig;
  accessToken: string | null;
  onLogout: () => void;
}

type Mode = "normal" | "optimized";

export default function Chat({ config, accessToken, onLogout }: ChatProps) {
  const [mode, setMode] = useState<Mode>("normal");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState(generateSessionId());
  const [isWarmedUp, setIsWarmedUp] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [latencyStats, setLatencyStats] = useState<{
    normal: number[];
    optimized: number[];
  }>({
    normal: [],
    optimized: [],
  });
  const [ttftStats, setTtftStats] = useState<{
    normal: number[];
    optimized: number[];
  }>({
    normal: [],
    optimized: [],
  });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Focus input when sending completes
  useEffect(() => {
    if (!isSending) {
      inputRef.current?.focus();
    }
  }, [isSending]);

  // Warmup on session creation in optimized mode
  useEffect(() => {
    if (mode === "optimized" && !isWarmedUp && config.agentArn) {
      const warmupPromise = warmupAgent();
      setSessionWarmupPromise(sessionId, warmupPromise);
    }
  }, [mode, sessionId]);

  const warmupAgent = async () => {
    if (!config.agentArn) return;

    try {
      await invokeAgent(
        config.agentArn,
        config.region,
        sessionId,
        { ping: "now" },
        accessToken,
      );
      setIsWarmedUp(true);
    } catch (error) {
      console.error("Warmup failed:", error);
    }
  };

  const handleNewSession = () => {
    clearSessionState(sessionId);
    setMessages([]);
    setSessionId(generateSessionId());
    setIsWarmedUp(false);
  };

  const handleSend = async () => {
    if (!input.trim() || !config.agentArn || isSending) return;

    const userMessage: Message = {
      id: `msg-${Date.now()}`,
      role: "user",
      content: input,
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsSending(true);

    // Create placeholder for streaming response
    const assistantMessageId = `msg-${Date.now()}-assistant`;
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };

    setMessages((prev) => [...prev, assistantMessage]);

    let streamedContent = "";

    const result = await invokeAgentStream(
      config.agentArn,
      config.region,
      sessionId,
      { prompt: input },
      accessToken,
      (chunk) => {
        streamedContent += chunk;
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: streamedContent }
              : msg,
          ),
        );
      },
    );

    setIsSending(false);

    // Update with final latency and ttft
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === assistantMessageId
          ? {
              ...msg,
              content: result.success
                ? streamedContent || "No response"
                : `Error: ${result.error}`,
              latency: result.latency,
              ttft: result.ttft,
            }
          : msg,
      ),
    );

    // Track latency and TTFT
    setLatencyStats((prev) => ({
      ...prev,
      [mode]: [...prev[mode], result.latency],
    }));
    setTtftStats((prev) => ({
      ...prev,
      [mode]: [...prev[mode], result.ttft],
    }));
  };

  const handleModeChange = (newMode: Mode) => {
    setMode(newMode);
    handleNewSession();
  };

  const getFirstTtft = (ttfts: number[]) => {
    return ttfts.length > 0 ? Math.round(ttfts[0]) : 0;
  };

  const getLastTtft = (ttfts: number[]) => {
    return ttfts.length > 0 ? Math.round(ttfts[ttfts.length - 1]) : 0;
  };

  const avgLatency = (latencies: number[]) => {
    if (latencies.length === 0) return 0;
    return Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="bg-white rounded-lg shadow-lg overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-r from-primary-teal to-primary-teal/90 p-6 text-white">
          <div className="flex justify-between items-start">
            <div>
              <h1 className="text-3xl font-bold mb-2">AgentCore Warmup Demo</h1>
              <p className="text-primary-yellow/90">
                Compare normal vs optimized agent invocation latency
              </p>
            </div>
            <button
              onClick={onLogout}
              className="px-4 py-2 bg-primary-teal/80 hover:bg-primary-teal/70 rounded-lg text-sm font-medium transition-colors"
            >
              Sign Out
            </button>
          </div>
        </div>

        {/* Mode Selector */}
        <div className="border-b bg-gray-50 p-4">
          <div className="flex items-center gap-4">
            <span className="font-semibold text-gray-700">Mode:</span>
            <button
              onClick={() => handleModeChange("normal")}
              className={`px-6 py-2 rounded-lg font-medium transition-all ${
                mode === "normal"
                  ? "bg-primary-coral text-white shadow-md"
                  : "bg-white text-gray-700 border border-gray-300 hover:bg-gray-50"
              }`}
            >
              Normal
            </button>
            <button
              onClick={() => handleModeChange("optimized")}
              className={`px-6 py-2 rounded-lg font-medium transition-all ${
                mode === "optimized"
                  ? "bg-primary-orange text-white shadow-md"
                  : "bg-white text-gray-700 border border-gray-300 hover:bg-gray-50"
              }`}
            >
              Optimized (Pre-warmed)
            </button>
            {mode === "optimized" && (
              <span
                className={`ml-2 px-3 py-1 rounded-full text-sm font-medium ${
                  isWarmedUp
                    ? "bg-green-100 text-green-800"
                    : "bg-yellow-100 text-yellow-800"
                }`}
              >
                {isWarmedUp ? "✓ Warmed Up" : "⏳ Warming..."}
              </span>
            )}
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-4 p-4 bg-gray-50 border-b">
          <div className="bg-white p-4 rounded-lg border">
            <div className="text-sm text-gray-600 mb-1">
              Normal Mode Avg Latency
            </div>
            <div className="text-2xl font-bold text-primary-coral">
              {avgLatency(latencyStats.normal)}ms
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {latencyStats.normal.length} requests
            </div>
            <div className="mt-3 pt-3 border-t border-gray-200">
              <div className="flex justify-between text-xs text-gray-600 mb-1">
                <span>First TTFT:</span>
                <span className="font-semibold">
                  {getFirstTtft(ttftStats.normal)}ms
                </span>
              </div>
              <div className="flex justify-between text-xs text-gray-600">
                <span>Last TTFT:</span>
                <span className="font-semibold">
                  {getLastTtft(ttftStats.normal)}ms
                </span>
              </div>
            </div>
          </div>
          <div className="bg-white p-4 rounded-lg border">
            <div className="text-sm text-gray-600 mb-1">
              Optimized Mode Avg Latency
            </div>
            <div className="text-2xl font-bold text-primary-orange">
              {avgLatency(latencyStats.optimized)}ms
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {latencyStats.optimized.length} requests
            </div>
            <div className="mt-3 pt-3 border-t border-gray-200">
              <div className="flex justify-between text-xs text-gray-600 mb-1">
                <span>First TTFT:</span>
                <span className="font-semibold">
                  {getFirstTtft(ttftStats.optimized)}ms
                </span>
              </div>
              <div className="flex justify-between text-xs text-gray-600">
                <span>Last TTFT:</span>
                <span className="font-semibold">
                  {getLastTtft(ttftStats.optimized)}ms
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Messages */}
        <div className="h-96 overflow-y-auto p-6 bg-gray-50">
          {messages.length === 0 ? (
            <div className="text-center text-gray-500 mt-20">
              <p className="text-lg mb-2">No messages yet</p>
              <p className="text-sm">
                {mode === "normal"
                  ? "Start chatting to see normal invocation latency"
                  : "Agent is pre-warmed! Start chatting to see reduced latency"}
              </p>
            </div>
          ) : (
            messages.map((message) => (
              <div
                key={message.id}
                className={`mb-4 flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-xl rounded-lg px-4 py-3 ${
                    message.role === "user"
                      ? "bg-primary-teal text-white"
                      : "bg-white border border-gray-200 text-gray-800"
                  }`}
                >
                  <div className="whitespace-pre-wrap">{message.content}</div>
                  {message.latency && (
                    <div
                      className={`text-xs mt-2 ${
                        message.role === "user"
                          ? "text-primary-yellow/90"
                          : "text-gray-500"
                      }`}
                    >
                      TTFT: {Math.round(message.ttft || 0)}ms | Total:{" "}
                      {Math.round(message.latency)}ms
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t bg-white p-4">
          <div className="flex gap-3">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSend()}
              placeholder="Type your message..."
              disabled={isSending}
              className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-teal disabled:bg-gray-100"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || isSending}
              className="px-6 py-3 bg-primary-teal text-white rounded-lg font-medium hover:bg-primary-teal/90 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
            >
              {isSending ? "Sending..." : "Send"}
            </button>
            <button
              onClick={handleNewSession}
              className="px-6 py-3 bg-gray-600 text-white rounded-lg font-medium hover:bg-gray-700 transition-colors"
            >
              New Session
            </button>
          </div>
        </div>
      </div>

      {/* Info Panel */}
      <div className="mt-6 bg-primary-yellow/10 border border-primary-yellow/30 rounded-lg p-6">
        <h3 className="font-semibold text-primary-teal mb-3">How it works:</h3>
        <ul className="space-y-2 text-sm text-gray-700">
          <li className="flex items-start">
            <span className="mr-2">•</span>
            <span>
              <strong>Normal Mode:</strong> Agent is invoked only when you send
              a message, experiencing cold start latency
            </span>
          </li>
          <li className="flex items-start">
            <span className="mr-2">•</span>
            <span>
              <strong>Optimized Mode:</strong> Agent is pre-warmed with a ping
              request when the session starts, reducing subsequent invocation
              latency
            </span>
          </li>
          <li className="flex items-start">
            <span className="mr-2">•</span>
            <span>
              Compare the average latencies to see the performance improvement
              from pre-emptive warmup
            </span>
          </li>
        </ul>
      </div>
    </div>
  );
}
