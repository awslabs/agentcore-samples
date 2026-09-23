import React, { useState, useEffect, useMemo } from 'react';
import {
  AppLayout,
  ContentLayout,
  Header,
  SpaceBetween,
  Container,
  Button,
  Textarea,
  Alert,
  Tabs,
  Box,
  Spinner,
  Modal,
  FormField,
  FileUpload,
  Badge
} from '@cloudscape-design/components';
import CodeEditor from './components/CodeEditor.jsx';
import ExecutionResults from './components/ExecutionResults.jsx';
import SessionHistory from './components/SessionHistory.jsx';
import InteractiveExecutionModal from './components/InteractiveExecutionModal.jsx';
// CSV upload disabled
// import CsvUploadModal from './components/CsvUploadModal.jsx';
import ExecutionTimer from './components/ExecutionTimer.jsx';
import { generateCode, executeCode, uploadFile, getSessionHistory, analyzeCode, getActorSessions, getMemorySessionHistory, deleteMemorySession } from './services/api';
import { v4 as uuidv4 } from 'uuid';

function App() {
  const [sessionId, setSessionId] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [generatedCode, setGeneratedCode] = useState('');
  const [editedCode, setEditedCode] = useState('');
  const [executionResult, setExecutionResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('generate');
  const [showEditModal, setShowEditModal] = useState(false);
  const [sessionHistory, setSessionHistory] = useState(null);
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [successMessage, setSuccessMessage] = useState(null);
  const [showInteractiveModal, setShowInteractiveModal] = useState(false);
  const [codeAnalysis, setCodeAnalysis] = useState(null);
  const [pendingExecutionCode, setPendingExecutionCode] = useState(null);
  // CSV upload disabled — focused on Python code generation only
  // const [showCsvUploadModal, setShowCsvUploadModal] = useState(false);
  // const [uploadedCsv, setUploadedCsv] = useState(null);
  // const [csvUploadLoading, setCsvUploadLoading] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  // Stable actor ID persisted in localStorage — survives browser refresh and new sessions
  const [actorId] = useState(() => {
    const stored = localStorage.getItem('agentcore_actor_id');
    if (stored) return stored;
    const newId = `user-${uuidv4()}`;
    localStorage.setItem('agentcore_actor_id', newId);
    return newId;
  });

  const [actorSessions, setActorSessions] = useState([]);
  const [selectedPastSession, setSelectedPastSession] = useState(null);
  const [pastSessionTurns, setPastSessionTurns] = useState([]);
  const [loadingPastSession, setLoadingPastSession] = useState(false);

  // Memoized session ID initialization
  const initialSessionId = useMemo(() => uuidv4(), []);

  useEffect(() => {
    // Initialize session
    setSessionId(initialSessionId);
  }, [initialSessionId]);

  useEffect(() => {
    // Initialize WebSocket connection when sessionId is available
    if (sessionId) {
      const ws = new WebSocket(`ws://localhost:8000/ws/${sessionId}`);
      
      ws.onopen = () => {
        // WebSocket connection established
      };
      
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.type === 'code_generated' && data.success) {
          const code = typeof data.code === 'string' ? data.code : '';
          setGeneratedCode(code);
          setEditedCode(code);
          setActiveTab('editor');
          setSuccessMessage('Code generated successfully via WebSocket!');
          setTimeout(() => setSuccessMessage(null), 5000);
        } else if (data.type === 'execution_result' && data.success) {
          setExecutionResult({
            code: editedCode,
            result: data.result,
            success: data.success,
            images: data.images || [],
            timestamp: new Date().toISOString()
          });
          setActiveTab('results');
        }
      };
      
      ws.onclose = () => {
        // WebSocket connection closed
      };
      
      ws.onerror = (error) => {
      };
      
      // Cleanup on unmount
      return () => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.close();
        }
      };
    }
  }, [sessionId]);

  const handleGenerateCode = async () => {
    if (!prompt.trim()) {
      setError('Please enter a prompt to generate code');
      return;
    }

    setLoading(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const response = await generateCode(prompt, sessionId, actorId);
      
      // CSV upload disabled
      // if (!response.success && response.requires_file) {
      //   setLoading(false);
      //   setShowCsvUploadModal(true);
      //   return;
      // }
      
      const code = typeof response.code === 'string' ? response.code : '';
      
      setGeneratedCode(code);
      setEditedCode(code);
      
      let successMsg = 'Code generated successfully! The code is now available in the Code Editor tab.';
      setSuccessMessage(successMsg);
      
      // Automatically switch to Code Editor tab and make code available
      setActiveTab('editor');
      
      // Clear any previous execution results when new code is generated
      setExecutionResult(null);
      
      // Auto-dismiss success message after 5 seconds
      setTimeout(() => setSuccessMessage(null), 5000);
    } catch (err) {
      setError(`Code generation failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteCode = async (codeToExecute = null, interactive = false, inputs = null) => {
    const code = codeToExecute || editedCode || generatedCode;
    
    if (!code.trim()) {
      setError('No code to execute');
      return;
    }

    // If not explicitly interactive, check if code needs interactive execution
    if (!interactive && !inputs) {
      try {
        const analysis = await analyzeCode(code, sessionId);
        if (analysis.interactive) {
          // Show interactive modal
          setCodeAnalysis(analysis.analysis);
          setPendingExecutionCode(code);
          setShowInteractiveModal(true);
          return;
        }
      } catch (err) {
        console.warn('Code analysis failed, proceeding with execution:', err.message);
      }
    }

    setLoading(true);
    setIsExecuting(true);
    setError(null);

    try {
      const response = await executeCode(code, sessionId, interactive, inputs, actorId);
      setExecutionResult({
        code: code,
        result: response.result,
        success: response.success,
        interactive: response.interactive,
        inputs_used: response.inputs_used,
        images: response.images || [],
        timestamp: new Date().toISOString()
      });
      setActiveTab('results');
      
      // Clear session history to force refresh when user visits history tab
      if (sessionHistory) {
        setSessionHistory(null);
      }
    } catch (err) {
      setError(`Code execution failed: ${err.message}`);
    } finally {
      setLoading(false);
      setIsExecuting(false);
    }
  };

  const handleFileUpload = async (files) => {
    if (files.length === 0) return;

    const file = files[0];
    const reader = new FileReader();
    
    reader.onload = async (e) => {
      try {
        setLoading(true);
        const content = e.target.result;
        const codeContent = typeof content === 'string' ? content : '';
        
        await uploadFile(file.name, codeContent, sessionId);
        setUploadedFiles([...uploadedFiles, { name: file.name, content: codeContent }]);
        setEditedCode(codeContent);
        setActiveTab('editor');
        
        setError(null);
      } catch (err) {
        setError(`File upload failed: ${err.message}`);
      } finally {
        setLoading(false);
      }
    };
    
    reader.readAsText(file);
  };

  const handleSaveCode = () => {
    if (!editedCode || typeof editedCode !== 'string' || !editedCode.trim()) {
      setError('No code to save');
      return;
    }
    
    try {
      const blob = new Blob([editedCode], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'code.py';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      
      setSuccessMessage('Code saved successfully!');
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setError('Failed to save code');
    }
  };

  const handleCopyCode = async () => {
    if (!editedCode || typeof editedCode !== 'string' || !editedCode.trim()) {
      setError('No code to copy');
      return;
    }
    
    try {
      await navigator.clipboard.writeText(editedCode);
      setSuccessMessage('Code copied to clipboard!');
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setError('Failed to copy code');
    }
  };

  // CSV upload disabled — focused on Python code generation only
  /*
  const handleCsvRemoval = async () => {
    try {
      setUploadedCsv(null);
      await fetch(`/api/sessions/${sessionId}/clear-csv`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      setSuccessMessage('CSV file removed successfully');
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      console.error('Failed to remove CSV file:', err);
      setUploadedCsv(null);
    }
  };

  const handleCsvUpload = async (file) => {
    setCsvUploadLoading(true);
    setError(null);
    try {
      const response = await uploadCsvFile(file.name, file.content, sessionId);
      setUploadedCsv({ filename: response.filename, preview: response.preview });
      setShowCsvUploadModal(false);
      setSuccessMessage(`CSV file "${response.filename}" uploaded successfully!`);
      setTimeout(() => setSuccessMessage(null), 3000);
      if (activeTab === 'generate') {
        setTimeout(() => { handleGenerateCode(); }, 500);
      }
    } catch (err) {
      setError(`CSV upload failed: ${err.message}`);
    } finally {
      setCsvUploadLoading(false);
    }
  };
  */

  const loadSessionHistory = async () => {
    if (!sessionId) return;
    try {
      const history = await getSessionHistory(sessionId);
      setSessionHistory(history);
    } catch (err) {}
    // Also load past sessions from AgentCore Memory
    try {
      const resp = await getActorSessions(actorId);
      if (resp.enabled) setActorSessions(resp.sessions || []);
    } catch (err) {}
  };

  const loadPastSession = async (pastSessionId) => {
    setSelectedPastSession(pastSessionId);
    setPastSessionTurns([]);
    setLoadingPastSession(true);
    try {
      const resp = await getMemorySessionHistory(pastSessionId, actorId);
      console.log('Memory history response:', resp);
      const turns = resp.turns || [];
      setPastSessionTurns(turns);
      // Automatically resume the session after loading turns
      if (turns.length > 0) {
        handleResumeSession(pastSessionId, turns);
      }
    } catch (err) {
      console.error('Failed to load past session:', err);
      setError(`Failed to resume session: ${err.message}`);
    } finally {
      setLoadingPastSession(false);
    }
  };

  const handleResumeSession = (pastSessionId, turns) => {
    console.log('Resume session:', pastSessionId, 'turns:', turns.length);

    let lastCode = '';
    let lastPrompt = '';

    // Extract Python code from content (may be wrapped in markdown)
    const extractCode = (content) => {
      if (!content) return '';
      // Match ```python ... ``` blocks
      const pyMatch = content.match(/```python\s*\n([\s\S]*?)```/);
      if (pyMatch) return pyMatch[1].trim();
      // Match generic ``` ... ``` blocks
      const genericMatch = content.match(/```\s*\n([\s\S]*?)```/);
      if (genericMatch) return genericMatch[1].trim();
      // Return the raw content (strip leading/trailing whitespace)
      return content.trim();
    };

    // Walk turns from newest to oldest
    for (let i = turns.length - 1; i >= 0; i--) {
      const turn = turns[i];
      const role = (turn.role || '').toLowerCase();
      console.log(`  Turn ${i}: role=${role}, content length=${(turn.content||'').length}`);
      if (role === 'assistant' && !lastCode) {
        const content = turn.content || '';
        // Skip error messages
        if (content.startsWith('Error:') || content.includes('ModuleNotFoundError')) continue;
        const code = extractCode(content);
        console.log(`  -> extracted code length: ${code.length}`);
        if (code && code.length > 10) lastCode = code;
      }
      if (role === 'user' && !lastPrompt) {
        const content = turn.content || '';
        if (!content.startsWith('Execute:')) {
          lastPrompt = content;
        }
      }
      if (lastCode && lastPrompt) break;
    }

    console.log('Resume result:', { lastCode: lastCode.substring(0, 100), lastPrompt });

    // Restore state
    setError(null);
    setExecutionResult(null);
    setSessionId(pastSessionId);

    if (lastCode) {
      setGeneratedCode(lastCode);
      setEditedCode(lastCode);
      setPrompt(lastPrompt || '');
      setSuccessMessage(`Session resumed! Code loaded (${lastCode.split('\n').length} lines). Switch to Code Editor tab.`);
      // Use setTimeout to ensure state is committed before tab switch
      setTimeout(() => setActiveTab('editor'), 50);
    } else if (lastPrompt) {
      setPrompt(lastPrompt);
      setSuccessMessage('Session resumed! Your last prompt has been restored. Switch to Generate Code tab.');
      setTimeout(() => setActiveTab('generate'), 50);
    } else {
      setSuccessMessage('Session resumed, but no code was found to restore.');
    }

    setTimeout(() => setSuccessMessage(null), 8000);
  };

  const handleDeleteSession = async (pastSessionId) => {
    try {
      await deleteMemorySession(actorId, pastSessionId);
      setActorSessions(prev => prev.filter(s => s.session_id !== pastSessionId));
      if (selectedPastSession === pastSessionId) {
        setSelectedPastSession(null);
        setPastSessionTurns([]);
      }
      setSuccessMessage('Session deleted successfully.');
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setError(`Failed to delete session: ${err.message}`);
    }
  };

  const handleSaveEdit = () => {
    setShowEditModal(false);
  };

  const handleInteractiveExecution = async (code, interactive, inputs) => {
    await handleExecuteCode(code, interactive, inputs);
  };

  const clearSession = () => {
    setPrompt('');
    setGeneratedCode('');
    setEditedCode('');
    setExecutionResult(null);
    setError(null);
    setSuccessMessage(null);
    setSessionHistory(null);
    setUploadedFiles([]);
    setShowInteractiveModal(false);
    setCodeAnalysis(null);
    setPendingExecutionCode(null);
    const newSessionId = uuidv4();
    setSessionId(newSessionId);
    setActiveTab('generate');
  };

  const tabs = [
    {
      id: 'generate',
      label: 'Generate Code',
      content: (
        <Container header={<Header variant="h2">Generate Python Code</Header>}>
          <SpaceBetween direction="vertical" size="l">
            {/* CSV upload section disabled — focused on Python code generation only */}

            <FormField
              label="Describe what you want the Python code to do"
              description="Enter a detailed description of the functionality you need"
            >
              <Textarea
                value={prompt}
                onChange={({ detail }) => setPrompt(detail.value)}
                placeholder="e.g., Create a function that calculates the Fibonacci sequence"
                rows={4}
              />
            </FormField>
            
            <Box textAlign="center">
              <Button
                variant="primary"
                onClick={handleGenerateCode}
                loading={loading}
                disabled={!prompt.trim()}
              >
                Generate Code
              </Button>
            </Box>

            {generatedCode && (
              <Container header={
                <Header 
                  variant="h3"
                  actions={
                    <SpaceBetween direction="horizontal" size="s">
                      <Badge color="blue">Generated by Strands Agent</Badge>
                      <Button
                        size="small"
                        onClick={() => setActiveTab('editor')}
                      >
                        Go to Editor
                      </Button>
                    </SpaceBetween>
                  }
                >
                  Generated Code Preview
                </Header>
              }>
                <SpaceBetween direction="vertical" size="m">
                  <Alert type="success" header="Code Generated Successfully">
                    Your code has been generated and is now available in the Code Editor tab. 
                    You can review it below, then switch to the editor to modify or execute it.
                  </Alert>
                  
                  <CodeEditor
                    value={generatedCode}
                    readOnly={true}
                    height="300px"
                  />
                  
                  <SpaceBetween direction="horizontal" size="s">
                    <Button 
                      variant="primary"
                      onClick={() => setActiveTab('editor')}
                    >
                      Edit in Code Editor
                    </Button>
                    <Button onClick={() => handleExecuteCode(generatedCode)}>
                      Execute As Is
                    </Button>
                  </SpaceBetween>
                </SpaceBetween>
              </Container>
            )}
          </SpaceBetween>
        </Container>
      )
    },
    {
      id: 'editor',
      label: 'Code Editor',
      content: (
        <Container header={
          <Header 
            variant="h2"
            description={generatedCode ? "Edit your generated code or upload a new file" : "Upload a Python file or paste your code"}
          >
            Python Code Editor
          </Header>
        }>
          <SpaceBetween direction="vertical" size="l">
            <SpaceBetween direction="horizontal" size="s">
              <FormField label="Upload Python File">
                <FileUpload
                  onChange={({ detail }) => handleFileUpload(detail.value)}
                  value={[]}
                  accept=".py,.txt"
                  showFileLastModified
                  showFileSize
                  constraintText="Supported formats: .py, .txt"
                />
              </FormField>

              <FormField label="Session">
                <Button onClick={clearSession}>
                  New Session
                </Button>
              </FormField>
            </SpaceBetween>

            {generatedCode && editedCode === generatedCode && (
              <Alert 
                type="info" 
                header="Generated Code Loaded"
                dismissible
                onDismiss={() => setGeneratedCode('')}
              >
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Box>Code generated from your prompt is now loaded in the editor.</Box>
                  <Badge color="blue">Strands Agent</Badge>
                </SpaceBetween>
              </Alert>
            )}

            <CodeEditor
              value={editedCode}
              onChange={setEditedCode}
              height="400px"
            />

            <Box textAlign="center">
              <SpaceBetween direction="vertical" size="s">
                {isExecuting && (
                  <ExecutionTimer 
                    isRunning={isExecuting}
                    onReset={() => setIsExecuting(false)}
                  />
                )}
                
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Button
                    variant="primary"
                    onClick={() => handleExecuteCode()}
                    loading={loading}
                    disabled={!editedCode || typeof editedCode !== 'string' || !editedCode.trim()}
                  >
                    Execute Code
                  </Button>
                <Button
                  onClick={handleSaveCode}
                  disabled={!editedCode || typeof editedCode !== 'string' || !editedCode.trim()}
                  iconName="download"
                >
                  Save Code
                </Button>
                <Button
                  onClick={handleCopyCode}
                  disabled={!editedCode || typeof editedCode !== 'string' || !editedCode.trim()}
                  iconName="copy"
                >
                  Copy Code
                </Button>
                {editedCode && typeof editedCode === 'string' && (
                  <Box fontSize="body-s" color="text-body-secondary">
                    {editedCode.split('\n').length} lines of code ready to execute
                  </Box>
                )}
                </SpaceBetween>
              </SpaceBetween>
            </Box>
          </SpaceBetween>
        </Container>
      )
    },
    {
      id: 'results',
      label: 'Execution Results',
      content: (
        <ExecutionResults
          result={executionResult}
          onExecuteAgain={() => handleExecuteCode()}
        />
      )
    },
    {
      id: 'history',
      label: 'Session History',
      content: (
        <SessionHistory
          sessionId={sessionId}
          actorId={actorId}
          history={sessionHistory}
          actorSessions={actorSessions}
          selectedPastSession={selectedPastSession}
          pastSessionTurns={pastSessionTurns}
          loadingPastSession={loadingPastSession}
          onRefresh={loadSessionHistory}
          onLoadPastSession={loadPastSession}
          onExecuteCode={handleExecuteCode}
          onResumeSession={handleResumeSession}
          onDeleteSession={handleDeleteSession}
        />
      )
    }
  ];

  return (
    <AppLayout
      navigationHide={true}
      content={
        <ContentLayout
          header={
            <Header
              variant="h1"
              description="Generate, edit, and execute Python code using Amazon Bedrock AgentCore"
            >
              Text to Python IDE
            </Header>
          }
        >
          <SpaceBetween direction="vertical" size="l">
            {error && (
              <Alert
                type="error"
                dismissible
                onDismiss={() => setError(null)}
              >
                {error}
              </Alert>
            )}

            {successMessage && (
              <Alert
                type="success"
                dismissible
                onDismiss={() => setSuccessMessage(null)}
              >
                {successMessage}
              </Alert>
            )}

            {loading && (
              <Box textAlign="center">
                <Spinner size="large" />
              </Box>
            )}

            <Tabs
              tabs={tabs}
              activeTabId={activeTab}
              onChange={({ detail }) => {
                const newTabId = detail.activeTabId;
                setActiveTab(newTabId);
                
                // Load session history only when Session History tab is clicked
                if (newTabId === 'history' && sessionId && !sessionHistory) {
                  loadSessionHistory();
                }
              }}
            />
          </SpaceBetween>

          <InteractiveExecutionModal
            visible={showInteractiveModal}
            onDismiss={() => setShowInteractiveModal(false)}
            code={pendingExecutionCode}
            analysis={codeAnalysis}
            onExecute={handleInteractiveExecution}
          />

          {/* CSV upload modal disabled */}

          <Modal
            visible={showEditModal}
            onDismiss={() => setShowEditModal(false)}
            header="Edit Generated Code"
            footer={
              <Box float="right">
                <SpaceBetween direction="horizontal" size="xs">
                  <Button onClick={() => setShowEditModal(false)}>
                    Cancel
                  </Button>
                  <Button variant="primary" onClick={handleSaveEdit}>
                    Save & Execute
                  </Button>
                </SpaceBetween>
              </Box>
            }
            size="large"
          >
            <CodeEditor
              value={editedCode}
              onChange={setEditedCode}
              height="400px"
            />
          </Modal>
          
          {/* Footer text */}
          <Box textAlign="center" margin={{ top: "xl" }} fontSize="body-s" color="text-body-secondary">
            Developed using Amazon Q Developer CLI
          </Box>
        </ContentLayout>
      }
    />
  );
}

export default App;
