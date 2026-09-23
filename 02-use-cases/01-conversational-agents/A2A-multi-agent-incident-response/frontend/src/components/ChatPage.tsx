import { useState, useRef, useEffect } from 'react';
import { Sidebar } from './Sidebar';
import { ChatContainer } from './ChatContainer';
import adkIcon from '../icons/adk.png';

interface ChatPageProps {
  signOut: () => void
  user: any
}

export function ChatPage({ signOut, user }: ChatPageProps) {
  const [sidebarWidth, setSidebarWidth] = useState(320); // Initial width in pixels (w-80 = 320px)
  const [isResizing, setIsResizing] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  const startResizing = () => {
    setIsResizing(true);
  };

  const stopResizing = () => {
    setIsResizing(false);
  };

  const resize = (e: MouseEvent) => {
    if (isResizing) {
      const newWidth = e.clientX;
      // Clamp width between 200px and 600px
      if (newWidth >= 200 && newWidth <= 600) {
        setSidebarWidth(newWidth);
      }
    }
  };

  useEffect(() => {
    const handleResize = (e: MouseEvent) => resize(e);
    const handleStopResize = () => stopResizing();

    if (isResizing) {
      window.addEventListener('mousemove', handleResize);
      window.addEventListener('mouseup', handleStopResize);
    }

    return () => {
      window.removeEventListener('mousemove', handleResize);
      window.removeEventListener('mouseup', handleStopResize);
    };
  }, [isResizing]);

  return (
    <div className="h-screen bg-[#181c24] flex flex-col" style={{ userSelect: isResizing ? 'none' : 'auto' }}>
      {/* Header - Fixed at top */}
      <header className="flex-shrink-0 bg-[#1a1e27] border-b border-gray-700 px-6 py-4">
        <div className="flex items-center justify-center gap-3">
          <img src={adkIcon} alt="ADK" className="h-10 object-contain" />
          <h1 className="text-3xl font-bold text-gray-200">
            Amazon Bedrock AgentCore Runtime
          </h1>
        </div>
        <div className="h-px bg-gradient-to-r from-[#298dff] via-[#298dff] to-transparent mt-3 mx-auto max-w-2xl" />
      </header>

      {/* Main Content - Takes remaining height */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        <div ref={sidebarRef} style={{ width: `${sidebarWidth}px` }}>
          <Sidebar signOut={signOut} />
        </div>

        {/* Resize Handle */}
        <div
          onMouseDown={startResizing}
          className="w-1 bg-gray-700 hover:bg-blue-500 cursor-col-resize transition-colors flex-shrink-0"
        />

        <div className="flex-1 min-w-0 flex flex-col h-full">
          <ChatContainer user={user} />
        </div>
      </div>
    </div>
  );
}
