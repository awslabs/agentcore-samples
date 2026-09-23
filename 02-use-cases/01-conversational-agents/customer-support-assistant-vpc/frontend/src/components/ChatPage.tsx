import { Sidebar } from './Sidebar'
import { ChatContainer } from './ChatContainer'

interface ChatPageProps {
  signOut: () => void
  user: any
}

export function ChatPage({ signOut, user }: ChatPageProps) {
  return (
    <div className="h-screen bg-[#181c24] flex flex-col">
      {/* Header - Fixed at top */}
      <header className="flex-shrink-0 bg-[#1a1e27] border-b border-gray-700 px-6 py-4">
        <h1 className="text-3xl font-bold text-gray-200 text-center">
          Customer Support Assistant
        </h1>
        <div className="h-px bg-gradient-to-r from-[#298dff] via-[#298dff] to-transparent mt-3 mx-auto max-w-2xl" />
      </header>

      {/* Main Content - Takes remaining height */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        <Sidebar signOut={signOut} />
        <ChatContainer user={user} />
      </div>
    </div>
  )
}
