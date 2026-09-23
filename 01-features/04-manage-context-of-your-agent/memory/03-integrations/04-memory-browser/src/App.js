import React, { useState, useEffect } from 'react';
import {
  Clock,
  Database,
  RefreshCw,
  MessageSquare,
  Search,
  CheckCircle,
  Loader
} from 'lucide-react';
import ShortTermMemoryForm from './components/ShortTermMemoryForm';
import LongTermMemoryForm from './components/LongTermMemoryForm';

function App() {
  const [activeTab, setActiveTab] = useState('shortterm');
  const [lastUpdated, setLastUpdated] = useState(new Date());
  const [shortTermMemories, setShortTermMemories] = useState([]);
  const [longTermMemories, setLongTermMemories] = useState([]);
  // Global configuration for both memory types
  const [globalConfig, setGlobalConfig] = useState({ memory_id: '', actor_id: '' });
  const [availableNamespaces, setAvailableNamespaces] = useState([]);
  const [isConfigured, setIsConfigured] = useState(false);
  const [hasBeenConfigured, setHasBeenConfigured] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Short-term memory filter states
  const [eventTypeFilter, setEventTypeFilter] = useState('all');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortBy, setSortBy] = useState('timestamp');
  const [sortOrder, setSortOrder] = useState('desc');
  const [contentSearch, setContentSearch] = useState('');

  // Long-term memory filter states
  const [ltSearchQuery, setLtSearchQuery] = useState('');
  const [ltSortOrder, setLtSortOrder] = useState('desc');

  const refreshData = () => {
    setLoading(true);
    setLastUpdated(new Date());
    setTimeout(() => setLoading(false), 1000); // Simulate refresh
  };

  const handleShortTermMemoryFetch = (memories) => {
    setShortTermMemories(memories);
    console.log('Short-term memories fetched:', memories);
    console.log('Current filters:', { eventTypeFilter, roleFilter, contentSearch });

    // Debug: Show what types and roles are actually in the data
    const types = [...new Set(memories.map(m => m.type))];
    const roles = [...new Set(memories.map(m => m.role))];
    console.log('Available types in data:', types);
    console.log('Available roles in data:', roles);
  };

  const handleLongTermMemoryFetch = (memories) => {
    setLongTermMemories(memories);
    console.log('Long-term memories fetched:', memories);
  };

  const handleGlobalConfigUpdate = async (config) => {
    setLoading(true);
    setError('');
    console.log('Global memory configuration updated:', config);

    // Discover namespaces for long-term memory to validate the Memory ID
    try {
      const response = await fetch('http://localhost:8000/api/agentcore/listNamespaces', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          memory_id: config.memory_id,
          max_results: 100
        })
      });

      const textResponse = await response.text();
      let data;
      
      try {
        data = JSON.parse(textResponse);
      } catch (parseError) {
        console.error('Non-JSON response from backend:', textResponse);
        throw new Error(`Backend returned non-JSON response (status ${response.status}). Check backend logs.`);
      }

      if (!response.ok) {
        const errorMessage = data.detail || `Request failed with status ${response.status}`;
        throw new Error(errorMessage);
      }
      
      // Configuration is valid, update state
      setGlobalConfig(config);
      setIsConfigured(true);
      setHasBeenConfigured(true);
      
      if (data.namespaces && data.namespaces.length > 0) {
        setAvailableNamespaces(data.namespaces);
        console.log('Available namespaces discovered:', data.namespaces);
      }
      
    } catch (err) {
      console.error('❌ Memory configuration error:', err);
      
      // Parse specific error messages from backend
      let errorMessage = 'Failed to validate memory configuration';
      
      // Handle fetch API errors (not axios)
      if (err.message && err.message.includes('Failed to fetch')) {
        errorMessage = 'Unable to connect to backend server. Please ensure the backend is running.';
      } else if (err.message) {
        errorMessage = err.message;
      }
      
      setError(errorMessage);
      
      // Don't update configuration if validation failed
      setIsConfigured(false);
      setHasBeenConfigured(false);
    } finally {
      setLoading(false);
    }
  };

  const handleNamespacesFound = (namespaces) => {
    setAvailableNamespaces(namespaces);
    console.log('Available namespaces:', namespaces);
  };

  const tabs = [
    { id: 'shortterm', label: 'Short-Term Memory', icon: <MessageSquare size={16} /> },
    { id: 'longterm', label: 'Long-Term Memory', icon: <Database size={16} /> }
  ];

  const renderResultsContent = () => {
    switch (activeTab) {
      case 'shortterm':
        return shortTermMemories.length > 0 ? (
          <div className="results-section">
            <div className="results-header">
              <div className="results-title">
                <h2>Short-Term Memory Results</h2>
                <div className="results-count">
                  <span className="count-badge">
                    {shortTermMemories.filter(memory => {
                      if (contentSearch && (!memory.content || !memory.content.toLowerCase().includes(contentSearch.toLowerCase()))) {
                        return false;
                      }
                      if (eventTypeFilter !== 'all' && memory.type !== eventTypeFilter) {
                        return false;
                      }
                      if (roleFilter !== 'all' && memory.role !== roleFilter) {
                        return false;
                      }
                      return true;
                    }).length}
                  </span>
                  <span className="count-text">of {shortTermMemories.length} entries</span>
                </div>
              </div>
            </div>



            {/* Search and Filter Controls */}
            <div className="results-controls">
              <div className="search-control">
                <Search size={16} className="search-icon" />
                <input
                  type="text"
                  placeholder="Search in memory content..."
                  value={contentSearch}
                  onChange={(e) => setContentSearch(e.target.value)}
                  className="search-input"
                />
              </div>
              <select
                value={eventTypeFilter}
                onChange={(e) => setEventTypeFilter(e.target.value)}
                className="filter-select"
              >
                <option value="all">All Types</option>
                <option value="conversation">Conversations</option>
                <option value="event">Events</option>
              </select>
              <select
                value={roleFilter}
                onChange={(e) => setRoleFilter(e.target.value)}
                className="filter-select"
              >
                <option value="all">All Roles</option>
                <option value="user">User Only</option>
                <option value="assistant">Assistant Only</option>
              </select>
              <select
                value={sortOrder}
                onChange={(e) => setSortOrder(e.target.value)}
                className="sort-select"
              >
                <option value="desc">Newest First</option>
                <option value="asc">Oldest First</option>
              </select>
            </div>

            <div className="memory-list">
              {(() => {
                const filtered = shortTermMemories.filter(memory => {
                  if (contentSearch && (!memory.content || !memory.content.toLowerCase().includes(contentSearch.toLowerCase()))) {
                    return false;
                  }
                  if (eventTypeFilter !== 'all' && memory.type !== eventTypeFilter) {
                    return false;
                  }
                  if (roleFilter !== 'all') {
                    const memoryRole = (memory.role || '').toLowerCase();
                    if (roleFilter === 'user' && memoryRole !== 'user') {
                      return false;
                    }
                    if (roleFilter === 'assistant' && memoryRole !== 'assistant') {
                      return false;
                    }
                  }
                  return true;
                });

                console.log(`Filtering: ${shortTermMemories.length} → ${filtered.length} results`);
                console.log('Filter breakdown:', {
                  contentSearch: contentSearch || 'none',
                  eventTypeFilter,
                  roleFilter,
                  totalMemories: shortTermMemories.length,
                  filteredMemories: filtered.length
                });

                return filtered;
              })()
                .sort((a, b) => {
                  if (sortBy === 'timestamp') {
                    const aTime = new Date(a.timestamp || 0);
                    const bTime = new Date(b.timestamp || 0);
                    return sortOrder === 'desc' ? bTime - aTime : aTime - bTime;
                  } else if (sortBy === 'size') {
                    const aSize = a.size || 0;
                    const bSize = b.size || 0;
                    return sortOrder === 'desc' ? bSize - aSize : aSize - bSize;
                  }
                  return 0;
                })
                .map((memory, index) => (
                  <div key={memory.id || index} className="memory-item shortterm">
                    <div className="memory-item-header">
                      <div className="memory-badges">
                        <span className={`memory-type ${memory.type || 'event'}`}>
                          {memory.type || 'Event'}
                        </span>
                        {memory.event_type && (
                          <span className="event-type-badge">
                            {memory.event_type}
                          </span>
                        )}
                        {memory.role && (
                          <span className="role-badge">
                            {memory.role}
                          </span>
                        )}
                      </div>
                      <span className="memory-timestamp">
                        {new Date(memory.timestamp).toLocaleString()}
                      </span>
                    </div>

                    <div className="memory-content">
                      {memory.content}
                    </div>

                    <div className="memory-metadata">
                      {memory.actor_id && <span>Actor: {memory.actor_id}</span>}
                      {memory.session_id && <span>Session: {memory.session_id}</span>}
                      {memory.event_id && <span>Event ID: {memory.event_id}</span>}
                      <span>Size: {memory.size} chars</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <MessageSquare size={48} className="empty-icon" />
            <h3>No Records Found</h3>
            <p>No short-term memory data found for this session. Try different parameters.</p>
          </div>
        );

      case 'longterm':
        return longTermMemories.length > 0 ? (
          <div className="results-section">
            <div className="results-header">
              <div className="results-title">
                <h2>Long-Term Memory Results</h2>
                <div className="results-count">
                  <span className="count-badge">
                    {longTermMemories.filter(memory => {
                      if (ltSearchQuery && (!memory.content || !memory.content.toLowerCase().includes(ltSearchQuery.toLowerCase()))) {
                        return false;
                      }
                      return true;
                    }).length}
                  </span>
                  <span className="count-text">of {longTermMemories.length} entries</span>
                </div>
              </div>
            </div>

            {/* Search and Sort Controls */}
            <div className="results-controls">
              <div className="search-control">
                <Search size={16} className="search-icon" />
                <input
                  type="text"
                  placeholder="Search in memory content..."
                  value={ltSearchQuery}
                  onChange={(e) => setLtSearchQuery(e.target.value)}
                  className="search-input"
                />
              </div>
              <select
                value={ltSortOrder}
                onChange={(e) => setLtSortOrder(e.target.value)}
                className="sort-select"
              >
                <option value="desc">Newest First</option>
                <option value="asc">Oldest First</option>
              </select>
            </div>

            <div className="memory-list">
              {longTermMemories
                .filter(memory => {
                  if (ltSearchQuery && (!memory.content || !memory.content.toLowerCase().includes(ltSearchQuery.toLowerCase()))) {
                    return false;
                  }
                  return true;
                })
                .sort((a, b) => {
                  const aTime = new Date(a.timestamp || 0);
                  const bTime = new Date(b.timestamp || 0);
                  return ltSortOrder === 'desc' ? bTime - aTime : aTime - bTime;
                })
                .map((memory, index) => (
                  <div key={memory.id || index} className="memory-item longterm">
                    <div className="memory-item-header">
                      <div className="memory-badges">
                        <span className={`memory-type ${memory.type || 'record'}`}>
                          {memory.type || 'Record'}
                        </span>
                        {memory.namespace && (
                          <span className="namespace-badge">
                            {memory.namespace}
                          </span>
                        )}
                      </div>
                      <span className="memory-timestamp">
                        {new Date(memory.timestamp).toLocaleString()}
                      </span>
                    </div>

                    <div className="memory-content">
                      {memory.content}
                    </div>

                    <div className="memory-metadata">
                      {memory.namespace && <span>Namespace: {memory.namespace}</span>}
                      {memory.score && <span>Score: {memory.score}</span>}
                      <span>Size: {memory.size} chars</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <Database size={48} className="empty-icon" />
            <h3>No Data Found</h3>
            <p>No long-term memory records found for this namespace. Try different filters.</p>
          </div>
        );

      default:
        return (
          <div className="empty-state">
            <Database size={48} className="empty-icon" />
            <h3>Select Memory Type</h3>
            <p>Choose Short-Term or Long-Term memory from the sidebar to get started.</p>
          </div>
        );
    }
  };

  return (
    <div className="dashboard">
      <div className="container">
        <header className="modern-header">
          <div className="header-brand">
            <div className="brand-icon">
              <Database size={28} />
            </div>
            <div className="brand-text">
              <h1>AgentCore Memory</h1>
              <span className="brand-subtitle">Real-time monitoring dashboard</span>
            </div>
          </div>

          {/* Memory Configuration in Header */}
          <div className="header-config">
            <div className="config-field-inline">
              <label>Memory ID <span className="required-asterisk">*</span></label>
              <input
                type="text"
                value={globalConfig.memory_id}
                onChange={(e) => setGlobalConfig(prev => ({ ...prev, memory_id: e.target.value }))}
                placeholder="your-memory-id-here"
                className="header-input"
                required
              />
            </div>
            <div className="config-field-inline">
              <label>Actor ID <span className="required-asterisk">*</span></label>
              <input
                type="text"
                value={globalConfig.actor_id}
                onChange={(e) => setGlobalConfig(prev => ({ ...prev, actor_id: e.target.value }))}
                placeholder="DEFAULT"
                className="header-input"
                required
              />
            </div>

            <button
              onClick={async () => {
                if (hasBeenConfigured) {
                  // Reconfigure: Clear everything and reset
                  setGlobalConfig({ memory_id: '', actor_id: '' });
                  setHasBeenConfigured(false);
                  setIsConfigured(false);
                  setShortTermMemories([]);
                  setLongTermMemories([]);
                  setAvailableNamespaces([]);
                  setError('');
                } else {
                  // Configure: Validate and update
                  if (globalConfig.memory_id.trim() && globalConfig.actor_id.trim()) {
                    await handleGlobalConfigUpdate(globalConfig);
                  }
                }
              }}
              className="header-update-btn"
              disabled={loading || (!hasBeenConfigured && (!globalConfig.memory_id.trim() || !globalConfig.actor_id.trim()))}
            >
              {loading ? (
                <>
                  <Loader size={14} className="spinning" />
                  Validating...
                </>
              ) : (
                hasBeenConfigured ? 'Reconfigure' : 'Configure'
              )}
            </button>
          </div>

          <div className="header-actions">
            <div className="status-indicator">
              <div className="status-dot"></div>
              <span>Live</span>
            </div>

            <div className="last-updated-compact">
              <Clock size={14} />
              <span>{lastUpdated.toLocaleTimeString()}</span>
            </div>

            <button className="refresh-button-modern" onClick={refreshData} disabled={loading}>
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
              <span>{loading ? 'Refreshing...' : 'Refresh'}</span>
            </button>
          </div>

          {/* Error Display */}
          {error && (
            <div className="error-banner-modern">
              <div className="error-icon">⚠️</div>
              <span>{error}</span>
            </div>
          )}
        </header>

        <>
          {/* Sidebar Layout */}
          {(globalConfig.memory_id.trim() && globalConfig.actor_id.trim()) && (
            <div className="dashboard-layout">
              {/* Sidebar */}
              <div className="sidebar">
                {/* Memory Type Selection */}
                <div className="sidebar-section">
                  <div className="sidebar-section-header">
                    <MessageSquare size={16} />
                    <h3>Memory Type</h3>
                  </div>

                  <div className="memory-type-selector">
                    {tabs.map((tab) => (
                      <button
                        key={tab.id}
                        className={`memory-type-btn ${activeTab === tab.id ? 'active' : ''}`}
                        onClick={() => setActiveTab(tab.id)}
                      >
                        <div className="tab-icon">{tab.icon}</div>
                        <span className="tab-label">{tab.label}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Query Parameters */}
                <div className="sidebar-section">
                  <div className="sidebar-section-header">
                    <Search size={16} />
                    <h3>Query Parameters</h3>
                  </div>

                  {activeTab === 'shortterm' && (
                    <ShortTermMemoryForm
                      onMemoryFetch={handleShortTermMemoryFetch}
                      memoryConfig={globalConfig}
                    />
                  )}

                  {activeTab === 'longterm' && (
                    <LongTermMemoryForm
                      onMemoryFetch={handleLongTermMemoryFetch}
                      memoryConfig={globalConfig}
                      availableNamespaces={availableNamespaces}
                    />
                  )}
                </div>
              </div>

              {/* Main Content Area */}
              <div className="main-area">
                {(globalConfig.memory_id.trim() && globalConfig.actor_id.trim()) ? (
                  <div className="results-container">
                    {renderResultsContent()}
                  </div>
                ) : (
                  <div className="welcome-screen">
                    <Database size={48} className="welcome-icon" />
                    <h2>Welcome to AgentCore Memory Dashboard</h2>
                    <p>Configure your Memory ID and Actor ID in the sidebar to get started.</p>
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      </div>
    </div>
  );
}

export default App;