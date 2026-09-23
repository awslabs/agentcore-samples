import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor for logging
api.interceptors.request.use(
  (config) => {
    console.log(`Making ${config.method.toUpperCase()} request to ${config.url}`);
    return config;
  },
  (error) => {
    console.error('Request error:', error);
    return Promise.reject(error);
  }
);

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => {
    return response.data;
  },
  (error) => {
    console.error('API Error:', error);
    
    if (error.response) {
      // Server responded with error status
      const message = error.response.data?.detail || error.response.data?.message || 'Server error';
      throw new Error(message);
    } else if (error.request) {
      // Request was made but no response received
      throw new Error('No response from server. Please check if the backend is running.');
    } else {
      // Something else happened
      throw new Error(error.message || 'An unexpected error occurred');
    }
  }
);

export const generateCode = async (prompt, sessionId = null, actorId = null) => {
  try {
    const response = await api.post('/api/generate-code', {
      prompt,
      session_id: sessionId,
      actor_id: actorId
    });
    return response;
  } catch (error) {
    console.error('Generate code error:', error);
    throw error;
  }
};

export const executeCode = async (code, sessionId = null, interactive = false, inputs = null, actorId = null) => {
  try {
    const response = await api.post('/api/execute-code', {
      code,
      session_id: sessionId,
      actor_id: actorId,
      interactive,
      inputs
    });
    return response;
  } catch (error) {
    console.error('Execute code error:', error);
    throw error;
  }
};

export const analyzeCode = async (code, sessionId = null) => {
  try {
    const response = await api.post('/api/analyze-code', {
      code,
      session_id: sessionId
    });
    return response;
  } catch (error) {
    console.error('Analyze code error:', error);
    throw error;
  }
};

export const uploadCsvFile = async (filename, content, sessionId = null) => {
  try {
    const response = await api.post('/api/upload-csv', {
      filename,
      content,
      session_id: sessionId
    });
    return response;
  } catch (error) {
    console.error('CSV upload error:', error);
    throw error;
  }
};

export const uploadFile = async (filename, content, sessionId = null) => {
  try {
    const response = await api.post('/api/upload-file', {
      filename,
      content,
      session_id: sessionId
    });
    return response;
  } catch (error) {
    console.error('Upload file error:', error);
    throw error;
  }
};

export const getSessionHistory = async (sessionId) => {
  try {
    const response = await api.get(`/api/session/${sessionId}/history`);
    return response;
  } catch (error) {
    console.error('Get session history error:', error);
    throw error;
  }
};

export const getActorSessions = async (actorId) => {
  try {
    const response = await api.get(`/api/memory/sessions/${actorId}`);
    return response;
  } catch (error) {
    console.error('Get actor sessions error:', error);
    throw error;
  }
};

export const getMemorySessionHistory = async (sessionId, actorId) => {
  try {
    const response = await api.get(`/api/memory/history`, {
      params: { session_id: sessionId, actor_id: actorId }
    });
    return response;
  } catch (error) {
    console.error('Get memory session history error:', error);
    throw error;
  }
};

export const clearSession = async (sessionId) => {
  try {
    const response = await api.delete(`/api/session/${sessionId}`);
    return response;
  } catch (error) {
    console.error('Clear session error:', error);
    throw error;
  }
};

export const getAgentsStatus = async () => {
  try {
    const response = await api.get('/api/agents/status');
    return response;
  } catch (error) {
    console.error('Get agents status error:', error);
    throw error;
  }
};

export const healthCheck = async () => {
  try {
    const response = await api.get('/health');
    return response;
  } catch (error) {
    console.error('Health check error:', error);
    throw error;
  }
};

export const getGuardrailsStatus = async () => {
  try {
    const response = await api.get('/api/guardrails/status');
    return response;
  } catch (error) {
    console.error('Get guardrails status error:', error);
    throw error;
  }
};

export const getSessionsStatus = async () => {
  try {
    const response = await api.get('/api/sessions/status');
    return response;
  } catch (error) {
    console.error('Get sessions status error:', error);
    throw error;
  }
};

export const deleteMemorySession = async (actorId, sessionId) => {
  try {
    const response = await api.delete(`/api/memory/sessions/${actorId}/${sessionId}`);
    return response;
  } catch (error) {
    console.error('Delete memory session error:', error);
    throw error;
  }
};

// WebSocket connection for real-time communication
export class WebSocketService {
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.ws = null;
    this.listeners = {};
  }

  connect() {
    const wsUrl = `ws://localhost:8000/ws/${this.sessionId}`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.emit('connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.emit('message', data);
        
        if (data.type) {
          this.emit(data.type, data);
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };

    this.ws.onclose = () => {
      console.log('WebSocket disconnected');
      this.emit('disconnected');
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      this.emit('error', error);
    };
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  send(message) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.error('WebSocket is not connected');
    }
  }

  on(event, callback) {
    if (!this.listeners[event]) {
      this.listeners[event] = [];
    }
    this.listeners[event].push(callback);
  }

  off(event, callback) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
    }
  }

  emit(event, data) {
    if (this.listeners[event]) {
      this.listeners[event].forEach(callback => callback(data));
    }
  }

  executeCode(code) {
    this.send({
      type: 'execute_code',
      code: code
    });
  }
}

export default api;
