import { useChat } from '../hooks/useChat'
import strandsIcon from '../icons/strands.png'
import openaiSdkIcon from '../icons/openaisdk.png'

interface SidebarProps {
  signOut: () => void
}

export function Sidebar({ signOut }: SidebarProps) {
  const { sessionId, agentCards } = useChat()

  // Map agent names to their icons
  const agentIcons: Record<string, string> = {
    'monitor_agent': strandsIcon,
    'websearch_agent': openaiSdkIcon,
  };

  return (
    <aside className="w-full h-full bg-[#1a1e27] flex flex-col flex-shrink-0">
      <div className="p-4 border-b border-gray-700 flex-shrink-0">
        <h2 className="text-lg font-semibold text-gray-200 mb-2">Session Info</h2>
      </div>

      <div className="flex-1 p-4 space-y-4 overflow-y-auto min-h-0">
        {/* Session ID */}
        <div>
          <h3 className="text-sm font-medium text-gray-400 mb-2">Session ID</h3>
          <div className="bg-[#23272f] border border-gray-600 rounded-lg p-3 text-xs text-gray-300 font-mono break-all">
            {sessionId}
          </div>
        </div>

        {/* Agent Cards */}
        {agentCards && Object.keys(agentCards).length > 0 && (
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-2">Sub-Agents</h3>
            <div className="space-y-3">
              {Object.entries(agentCards).map(([agentName, agentInfo]) => (
                <div key={agentName} className="bg-[#23272f] border border-green-500 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    {agentIcons[agentName] && (
                      <img src={agentIcons[agentName]} alt={agentName} className="w-6 h-6 object-contain" />
                    )}
                    <div className="text-sm font-semibold text-gray-200">{agentName}</div>
                  </div>
                  <div className="text-xs text-gray-400 mb-2">{agentInfo.agent_card.description || 'No description'}</div>
                  <div className="space-y-1">
                    <div className="text-xs">
                      <span className="text-gray-500">Name:</span>
                      <span className="text-gray-300 ml-1">{agentInfo.agent_card.name}</span>
                    </div>
                    <div className="text-xs">
                      <span className="text-gray-500">Protocol:</span>
                      <span className="text-gray-300 ml-1">A2A</span>
                    </div>
                    {agentInfo.agent_card.version && (
                      <div className="text-xs">
                        <span className="text-gray-500">Version:</span>
                        <span className="text-gray-300 ml-1">{agentInfo.agent_card.version}</span>
                      </div>
                    )}
                    <details className="text-xs">
                      <summary className="text-gray-500 cursor-pointer hover:text-gray-400">URL</summary>
                      <div className="text-gray-300 font-mono break-all mt-1 ml-2">{agentInfo.agent_card.url}</div>
                    </details>
                    <details className="text-xs mt-2">
                      <summary className="text-gray-500 cursor-pointer hover:text-gray-400">Full Agent Card</summary>
                      <pre className="text-gray-300 font-mono text-[10px] mt-1 ml-2 overflow-x-auto bg-[#1a1e27] p-2 rounded">
                        {JSON.stringify(agentInfo.agent_card, null, 2)}
                      </pre>
                    </details>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Action Buttons */}
      <div className="flex-shrink-0 p-6 border-t border-gray-700">
        <div className="flex gap-2">
          <button
            onClick={() => window.location.reload()}
            className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors text-sm font-medium"
          >
            New Session
          </button>
          <button
            onClick={signOut}
            className="flex-1 px-3 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors text-sm font-medium"
          >
            Sign Out
          </button>
        </div>
      </div>
    </aside>
  )
}
