import { useState } from 'react';
import { AlertCircle, CheckCircle, Loader, Layers, User, MessageCircle, X } from 'lucide-react';

const LongTermMemoryForm = ({ onMemoryFetch, memoryConfig, availableNamespaces }) => {
  const [formData, setFormData] = useState({
    namespace: '',
    max_results: 20
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  
  // Modal state for collecting missing values
  const [showModal, setShowModal] = useState(false);
  const [modalData, setModalData] = useState({
    originalNamespace: '',
    missingValues: {},
    resolvedNamespace: ''
  });

  // Helper function to detect placeholders in namespace
  const detectPlaceholders = (namespace) => {
    const placeholderPattern = /\{(\w+)\}/g;
    const placeholders = [];
    let match;
    
    while ((match = placeholderPattern.exec(namespace)) !== null) {
      placeholders.push(match[1]);
    }
    
    return placeholders;
  };

  // Helper function to resolve namespace with available values
  const resolveNamespace = (namespace, values = {}) => {
    let resolved = namespace;
    
    // Use provided values or fall back to memoryConfig
    const allValues = {
      actorId: values.actorId || memoryConfig.actor_id,
      sessionId: values.sessionId || memoryConfig.session_id,
      ...values
    };
    
    // Replace all placeholders
    Object.entries(allValues).forEach(([key, value]) => {
      if (value && value.trim()) {
        resolved = resolved.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
      }
    });
    
    return resolved;
  };

  // Helper function to get missing values
  const getMissingValues = (namespace) => {
    const placeholders = detectPlaceholders(namespace);
    const missing = {};
    
    placeholders.forEach(placeholder => {
      const configKey = placeholder === 'actorId' ? 'actor_id' : 
                       placeholder === 'sessionId' ? 'session_id' : placeholder;
      
      if (!memoryConfig[configKey] || !memoryConfig[configKey].trim()) {
        missing[placeholder] = '';
      }
    });
    
    return missing;
  };

  const handleNamespaceSelection = (originalNamespace) => {
    const missingValues = getMissingValues(originalNamespace);
    
    if (Object.keys(missingValues).length > 0) {
      // Show modal to collect missing values
      setModalData({
        originalNamespace,
        missingValues,
        resolvedNamespace: resolveNamespace(originalNamespace)
      });
      setShowModal(true);
    } else {
      // No missing values, proceed directly
      const resolvedNamespace = resolveNamespace(originalNamespace);
      setFormData(prev => ({ ...prev, namespace: resolvedNamespace }));
      handleAutoFetch({ ...formData, namespace: resolvedNamespace });
    }
  };

  const handleInputChange = (field, value) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
    setError('');
    setSuccess('');
    
    // Auto-fetch when namespace is selected (for manual input)
    if (field === 'namespace' && value.trim()) {
      const updatedFormData = { ...formData, [field]: value };
      handleAutoFetch(updatedFormData);
    }
  };

  const handleModalValueChange = (key, value) => {
    setModalData(prev => ({
      ...prev,
      missingValues: {
        ...prev.missingValues,
        [key]: value
      }
    }));
  };

  const handleModalSubmit = () => {
    const resolvedNamespace = resolveNamespace(modalData.originalNamespace, modalData.missingValues);
    setFormData(prev => ({ ...prev, namespace: resolvedNamespace }));
    setShowModal(false);
    handleAutoFetch({ ...formData, namespace: resolvedNamespace });
  };

  const handleModalCancel = () => {
    setShowModal(false);
    setModalData({
      originalNamespace: '',
      missingValues: {},
      resolvedNamespace: ''
    });
  };

  const handleAutoFetch = async (currentFormData) => {
    if (!currentFormData.namespace.trim()) return;
    
    setLoading(true);
    setError('');
    setSuccess('');

    const requestPayload = {
      ...currentFormData,
      memory_id: memoryConfig.memory_id,
      content_type: 'all',
      sort_by: 'timestamp',
      sort_order: 'desc'
    };

    console.log('🚀 Auto-fetching long-term memory:', requestPayload);

    try {
      const response = await fetch('http://localhost:8000/api/agentcore/getLongTermMemory', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestPayload)
      });

      console.log('📡 Response status:', response.status);

      const textResponse = await response.text();
      let data;
      
      try {
        data = JSON.parse(textResponse);
      } catch (parseError) {
        console.error('Non-JSON response from backend:', textResponse);
        throw new Error(`Backend returned non-JSON response (status ${response.status}). Check backend logs.`);
      }

      if (!response.ok) {
        console.error('❌ Error response:', data);
        const errorMessage = data.detail || `Request failed with status ${response.status}`;
        throw new Error(errorMessage);
      }
      console.log('✅ Response data:', data);
      
      if (data.memories && data.memories.length > 0) {
        setSuccess(`Found ${data.memories.length} long-term memory entries!`);
        onMemoryFetch(data.memories);
      } else {
        setSuccess('Query completed successfully.');
        onMemoryFetch([]); // Pass empty array to show empty state in main area
      }

    } catch (err) {
      console.error('❌ Long-term memory fetch error:', err);
      
      // Parse specific error messages from backend
      let errorMessage = 'Failed to fetch long-term memory';
      
      if (err.response?.status === 404) {
        errorMessage = err.response.data?.detail || 'Memory ID or namespace not found. Please verify they exist and you have access permissions.';
      } else if (err.response?.status === 403) {
        errorMessage = err.response.data?.detail || 'Access denied. Please check your AWS credentials and permissions.';
      } else if (err.response?.data?.detail) {
        errorMessage = err.response.data.detail;
      } else if (err.message) {
        errorMessage = err.message;
      }
      
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!formData.namespace.trim()) {
      setError('Namespace is required.');
      return;
    }
    
    setLoading(true);
    setError('');
    setSuccess('');

    const requestPayload = {
      ...formData,
      memory_id: memoryConfig.memory_id,
      content_type: 'all',
      sort_by: 'timestamp',
      sort_order: 'desc'
    };

    console.log('🚀 Sending long-term memory request:', requestPayload);

    try {
      const response = await fetch('http://localhost:8000/api/agentcore/getLongTermMemory', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestPayload)
      });

      console.log('📡 Response status:', response.status);

      const textResponse = await response.text();
      let data;
      
      try {
        data = JSON.parse(textResponse);
      } catch (parseError) {
        console.error('Non-JSON response from backend:', textResponse);
        throw new Error(`Backend returned non-JSON response (status ${response.status}). Check backend logs.`);
      }

      if (!response.ok) {
        console.error('❌ Error response:', data);
        const errorMessage = data.detail || `Request failed with status ${response.status}`;
        throw new Error(errorMessage);
      }
      console.log('✅ Response data:', data);
      
      if (data.memories && data.memories.length > 0) {
        setSuccess(`Found ${data.memories.length} long-term memory entries!`);
        onMemoryFetch(data.memories);
      } else {
        setSuccess('Query completed successfully.');
        onMemoryFetch([]); // Pass empty array to show empty state in main area
      }

    } catch (err) {
      console.error('❌ Long-term memory submit error:', err);
      
      // Parse specific error messages from backend
      let errorMessage = 'Failed to fetch long-term memory';
      
      if (err.response?.status === 404) {
        errorMessage = err.response.data?.detail || 'Memory ID or namespace not found. Please verify they exist and you have access permissions.';
      } else if (err.response?.status === 403) {
        errorMessage = err.response.data?.detail || 'Access denied. Please check your AWS credentials and permissions.';
      } else if (err.response?.data?.detail) {
        errorMessage = err.response.data.detail;
      } else if (err.message) {
        errorMessage = err.message;
      }
      
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  console.log('🔍 LongTermMemoryForm render:', { 
    availableNamespaces, 
    availableNamespacesLength: availableNamespaces.length,
    memoryConfig 
  });

  return (
    <div className="long-term-memory-form">


      <div className="memory-form">
        <div className="form-grid">
          <div className="form-group">
            <label htmlFor="namespace">
              <Layers size={16} />
              Namespace (Required)
            </label>
            {availableNamespaces.length > 0 ? (
              <div className="namespace-selector">
                {availableNamespaces.map((ns, index) => {
                  // Check if this namespace has missing values
                  const missingValues = getMissingValues(ns.namespace);
                  const hasMissingValues = Object.keys(missingValues).length > 0;
                  
                  // For display, show resolved namespace only if no values are missing
                  const displayNamespace = hasMissingValues ? ns.namespace : resolveNamespace(ns.namespace);
                  
                  const isSelected = formData.namespace === displayNamespace || 
                                   (!hasMissingValues && formData.namespace === resolveNamespace(ns.namespace));
                  
                  return (
                    <div 
                      key={index}
                      className={`namespace-option ${isSelected ? 'selected' : ''}`}
                      onClick={() => handleNamespaceSelection(ns.namespace)}
                    >
                      <div className="namespace-type">
                        <span className={`type-badge ${ns.type.toLowerCase().replace(/[^a-z0-9]/g, '-')}`}>
                          {(() => {
                            // Standard AgentCore strategy types
                            const standardTypes = {
                              'SEMANTIC': 'Facts',
                              'USER_PREFERENCE': 'Preferences',
                              'SUMMARIZATION': 'Summaries'
                            };
                            
                            // If it's a standard type, use the friendly name
                            if (standardTypes[ns.type]) {
                              return standardTypes[ns.type];
                            }
                            
                            // For custom types, format them nicely
                            return ns.type
                              .split('_')
                              .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
                              .join(' ');
                          })()}
                        </span>
                      </div>
                      <div className="namespace-path">
                        {displayNamespace.split('/').slice(0, -1).join('/') || displayNamespace}
                        {hasMissingValues && (
                          <span className="missing-values-indicator"> (requires values)</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <input
                id="namespace"
                type="text"
                value={formData.namespace}
                onChange={(e) => handleInputChange('namespace', e.target.value)}
                placeholder="e.g., your-namespace/facts, company/user/preferences"
                className="form-input"
                required
              />
            )}
            <div className="form-help">
              {availableNamespaces.length > 0 
                ? `Select from ${availableNamespaces.length} available namespaces discovered from your memory strategies`
                : 'Specify the exact namespace to query (e.g., your-namespace/facts, company/user/preferences)'
              }
            </div>
          </div>



          {loading && (
            <div className="loading-indicator">
              <Loader size={16} className="spinning" />
              <span>Loading memory data...</span>
            </div>
          )}
        </div>

        {/* Status Messages */}
        {error && (
          <div className="status-message error">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div className="status-message success">
            <CheckCircle size={16} />
            <span>{success}</span>
          </div>
        )}
      </div>

      {/* Modal for collecting missing values */}
      {showModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3>Complete Namespace Configuration</h3>
              <button className="modal-close" onClick={handleModalCancel}>
                <X size={20} />
              </button>
            </div>
            
            <div className="modal-body">
              <p>This namespace requires additional values:</p>
              <div className="namespace-preview">
                <strong>Namespace:</strong> {modalData.originalNamespace}
              </div>
              
              <div className="missing-values-form">
                {Object.entries(modalData.missingValues).map(([key, value]) => (
                  <div key={key} className="form-group">
                    <label htmlFor={`modal-${key}`}>
                      {key === 'actorId' ? <User size={16} /> : <MessageCircle size={16} />}
                      {key === 'actorId' ? 'Actor ID' : 
                       key === 'sessionId' ? 'Session ID' : key}
                    </label>
                    <input
                      id={`modal-${key}`}
                      type="text"
                      value={value}
                      onChange={(e) => handleModalValueChange(key, e.target.value)}
                      placeholder={key === 'actorId' ? 'e.g., DEFAULT, user123' : 
                                  key === 'sessionId' ? 'e.g., session-abc123' : `Enter ${key}`}
                      className="form-input"
                    />
                  </div>
                ))}
              </div>
              
              <div className="resolved-preview">
                <strong>Resolved namespace:</strong>
                <code>{resolveNamespace(modalData.originalNamespace, modalData.missingValues)}</code>
              </div>
            </div>
            
            <div className="modal-footer">
              <button className="modal-btn cancel" onClick={handleModalCancel}>
                Cancel
              </button>
              <button 
                className="modal-btn submit" 
                onClick={handleModalSubmit}
                disabled={Object.values(modalData.missingValues).some(v => !v.trim())}
              >
                Use Namespace
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default LongTermMemoryForm;