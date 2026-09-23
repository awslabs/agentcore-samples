import React, { useState, useEffect, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';

interface Message {
  id: string;
  type: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  timestamp: Date;
  isStreaming?: boolean;
}

interface Source {
  type: 'database' | 'web';
  name?: string;
  title?: string;
  url?: string;
  query?: string;
}

interface StreamChunk {
  type: 'status' | 'database_results' | 'web_results' | 'complete' | 'error';
  message?: string;
  data?: any;
  response?: {
    answer: string;
    sources: Source[];
    reasoning: string[];
    citations: any[];
  };
  error?: string;
}

const SAMPLE_QUERIES = [
  "What were our Classic Cars sales in Q1 2025 compare to industry trends in the United States?",
  "What are our top sales across all transport categories?",
  "Compare our Q4 2024 performance across all transport product lines"
];

function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => uuidv4());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Removed authentication

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSampleQuery = (query: string) => {
    setInputValue(query);
    inputRef.current?.focus();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;

    const userMessage: Message = {
      id: uuidv4(),
      type: 'user',
      content: inputValue.trim(),
      timestamp: new Date()
    };

    const assistantMessage: Message = {
      id: uuidv4(),
      type: 'assistant',
      content: '',
      sources: [],
      timestamp: new Date(),
      isStreaming: true
    };

    setMessages(prev => [...prev, userMessage, assistantMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      
      // No authentication required

      const apiUrl = process.env.REACT_APP_API_URL || '';
      // amazonq-ignore-next-line
      const response = await fetch(`${apiUrl}/api/chat/message`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: userMessage.content,
          sessionId
        })
      });

      if (!response.ok) {
        // Handle error without authentication
        throw new Error('Failed to send message');
      }

      // Handle streaming response from Strands runtime
      const responseText = await response.text();
      console.log('[Frontend Debug] Response:', responseText);
      
      try {
        const data = JSON.parse(responseText);
        console.log('[Frontend Debug] Stream data:', data);
        
        let accumulatedContent = '';
        let sources: Source[] = [];
        
        if (data.type === 'complete') {
          accumulatedContent = data.response?.answer || '';
          if (data.response?.sources) {
            sources = data.response.sources;
            console.log('[Frontend Debug] Received sources:', sources);
          }
        } else if (data.type === 'error') {
          accumulatedContent = `Error: ${data.error}`;
        }
        
        setMessages(prev => prev.map(msg => 
          msg.id === assistantMessage.id 
            ? { ...msg, content: accumulatedContent, sources, isStreaming: false }
            : msg
        ));
        
      } catch (parseError) {
        console.error('Failed to parse JSON response:', parseError);
        // If not JSON, treat as plain text
        setMessages(prev => prev.map(msg => 
          msg.id === assistantMessage.id 
            ? { ...msg, content: responseText, isStreaming: false }
            : msg
        ));
      }

    } catch (error) {
      console.error('Chat error:', error);
      setMessages(prev => prev.map(msg => 
        msg.id === assistantMessage.id 
          ? { ...msg, content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}`, isStreaming: false }
          : msg
      ));
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as any);
    }
  };

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <div className="header-brand">
            <div className="aws-logo">
              <div className="logo-icon">Your Co.</div>
              <span className="brand-name">Biz Ltd</span>
            </div>
            <h1>Agentic Sales Analyst</h1>
            <p>Agentic sales analyst combining database insights and internal knowledge with market intelligence</p>
          </div>
        </div>
      </header>

      <div className="chat-container">
        <div className="sample-queries">
          <h3>Try these sample queries:</h3>
          <div className="sample-query-list">
            {SAMPLE_QUERIES.map((query, index) => (
              <button
                key={index}
                className="sample-query"
                onClick={() => handleSampleQuery(query)}
              >
                {query}
              </button>
            ))}
          </div>
        </div>

        <div className="messages-container">
          {messages.map((message) => (
            <div key={message.id} className={`message ${message.type}`}>
              <div className="message-avatar">
                {message.type === 'user' ? 'U' : 'AI'}
              </div>
              <div className="message-content">
                <div className="message-text">
                  {message.content}
                  {message.isStreaming && (
                    <div className="typing-indicator">
                      <div className="typing-dots">
                        <div className="typing-dot"></div>
                        <div className="typing-dot"></div>
                        <div className="typing-dot"></div>
                      </div>
                    </div>
                  )}
                </div>
                
                {message.sources && message.sources.length > 0 && (
                  <div className="message-sources">
                    <h4>Sources:</h4>
                    {message.sources.map((source, index) => (
                      <div key={index} className="source-item">
                        <span className={`source-badge ${source.type || 'unknown'}`}>
                          {(source.type || 'unknown').toUpperCase()}
                        </span>
                        <span>
                          {source.type === 'database' 
                            ? (source.name || 'Database Query')
                            : (source.title || source.url || 'Web Source')
                          }
                        </span>
                        {source.url && (
                          <a 
                            href={source.url} 
                            target="_blank" 
                            rel="noopener noreferrer"
                            style={{ color: '#667eea', textDecoration: 'none' }}
                          >
                            ↗
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-container">
          <form onSubmit={handleSubmit} className="input-form">
            <div className="input-wrapper">
              <textarea
                ref={inputRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Ask about sales data, customer insights, or industry trends..."
                className="message-input"
                disabled={isLoading}
                rows={2}
              />
            </div>
            <button
              type="submit"
              disabled={!inputValue.trim() || isLoading}
              className="send-button"
            >
              {isLoading ? '⟳' : '→'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

function App() {
  return <ChatInterface />;
}

export default App;