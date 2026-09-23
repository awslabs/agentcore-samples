import React, { useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Table,
  Badge,
  Link,
  Modal
} from '@cloudscape-design/components';
import CodeEditor from './CodeEditor';
import CodeDisplay from './CodeDisplay';
import ImageDisplay from './ImageDisplay';

const SessionHistory = ({ sessionId, history, onRefresh, onExecuteCode }) => {
  const [selectedItem, setSelectedItem] = React.useState(null);
  const [showCodeModal, setShowCodeModal] = React.useState(false);
  const [copySuccess, setCopySuccess] = React.useState(false);
  const [isRefreshing, setIsRefreshing] = React.useState(false);

  const handleCopyCode = async (code) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Failed to copy code: ', err);
    }
  };

  const handleRefresh = async () => {
    if (onRefresh && !isRefreshing) {
      setIsRefreshing(true);
      try {
        await onRefresh();
      } finally {
        setIsRefreshing(false);
      }
    }
  };

  // Only load history once when component mounts if no history exists
  React.useEffect(() => {
    if (sessionId && !history && onRefresh) {
      handleRefresh();
    }
  }, [sessionId]); // Remove onRefresh from dependencies to prevent infinite calls

  const formatTimestamp = (timestamp) => {
    return new Date(timestamp * 1000).toLocaleString();
  };

  const getTypeLabel = (type) => {
    switch (type) {
      case 'generation':
        return <Badge color="blue">Code Generated</Badge>;
      case 'file_upload':
        return <Badge color="green">File Uploaded</Badge>;
      default:
        return <Badge>{type}</Badge>;
    }
  };

  const conversationColumns = [
    {
      id: 'timestamp',
      header: 'Time',
      cell: item => formatTimestamp(item.timestamp),
      sortingField: 'timestamp',
      width: 150
    },
    {
      id: 'type',
      header: 'Type',
      cell: item => getTypeLabel(item.type),
      width: 120
    },
    {
      id: 'description',
      header: 'Description',
      cell: item => {
        if (item.type === 'generation') {
          return item.prompt;
        } else if (item.type === 'file_upload') {
          return `Uploaded: ${item.filename}`;
        }
        return 'N/A';
      }
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: item => (
        <SpaceBetween direction="horizontal" size="xs">
          {item.code && (
            <>
              <Link
                onFollow={() => {
                  setSelectedItem(item);
                  setShowCodeModal(true);
                }}
              >
                View Code
              </Link>
              <Button
                size="small"
                onClick={() => onExecuteCode(item.code)}
              >
                Execute
              </Button>
            </>
          )}
          {item.content && (
            <Link
              onFollow={() => {
                setSelectedItem(item);
                setShowCodeModal(true);
              }}
            >
              View File
            </Link>
          )}
        </SpaceBetween>
      ),
      width: 150
    }
  ];

  const executionColumns = [
    {
      id: 'timestamp',
      header: 'Executed At',
      cell: item => formatTimestamp(item.timestamp),
      sortingField: 'timestamp',
      width: 150
    },
    {
      id: 'prompt',
      header: 'User Prompt',
      cell: item => {
        // Try to get the original prompt from the code or use a fallback
        if (item.prompt) {
          return (
            <Box fontSize="body-s">
              {item.prompt.length > 60 ? `${item.prompt.substring(0, 60)}...` : item.prompt}
            </Box>
          );
        } else if (item.code) {
          // Fallback: show code preview if no prompt available
          return (
            <Box fontSize="body-s" fontFamily="monospace" color="text-body-secondary">
              {item.code.substring(0, 50)}...
            </Box>
          );
        }
        return <Box fontSize="body-s" color="text-body-secondary">Direct execution</Box>;
      }
    },
    {
      id: 'duration',
      header: 'Duration',
      cell: item => {
        if (item.execution_duration) {
          const duration = parseFloat(item.execution_duration);
          if (duration < 1) {
            return <Box fontSize="body-s">{(duration * 1000).toFixed(0)}ms</Box>;
          } else {
            return <Box fontSize="body-s">{duration.toFixed(1)}s</Box>;
          }
        } else if (item.start_time && item.end_time) {
          // Calculate duration from timestamps if available
          const duration = item.end_time - item.start_time;
          if (duration < 1) {
            return <Box fontSize="body-s">{(duration * 1000).toFixed(0)}ms</Box>;
          } else {
            return <Box fontSize="body-s">{duration.toFixed(1)}s</Box>;
          }
        }
        return <Box fontSize="body-s" color="text-body-secondary">-</Box>;
      },
      width: 80
    },
    {
      id: 'result',
      header: 'Result Status',
      cell: item => {
        const isError = item.result && item.result.toLowerCase().includes('error');
        return (
          <Badge color={isError ? "red" : "green"}>
            {isError ? "Error" : "Success"}
          </Badge>
        );
      },
      width: 100
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: item => (
        <SpaceBetween direction="horizontal" size="xs">
          <Link
            onFollow={() => {
              setSelectedItem(item);
              setShowCodeModal(true);
            }}
          >
            View Details
          </Link>
          <Button
            size="small"
            onClick={() => onExecuteCode(item.code)}
          >
            Re-execute
          </Button>
        </SpaceBetween>
      ),
      width: 150
    }
  ];

  if (!history) {
    return (
      <Container header={<Header variant="h2">Session History</Header>}>
        <Box textAlign="center" color="text-body-secondary">
          Loading session history...
        </Box>
      </Container>
    );
  }

  return (
    <Container 
      header={
        <Header 
          variant="h2"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button 
                onClick={handleRefresh}
                loading={isRefreshing}
                iconName="refresh"
              >
                Refresh History
              </Button>
            </SpaceBetween>
          }
        >
          Session History
        </Header>
      }
    >
      <SpaceBetween direction="vertical" size="l">
        <Container header={<Header variant="h3">Execution History</Header>}>
          {history.execution_results && history.execution_results.length > 0 ? (
            <Table
              columnDefinitions={executionColumns}
              items={history.execution_results.sort((a, b) => b.timestamp - a.timestamp)}
              sortingDisabled={false}
              defaultSortingColumn={{ sortingField: 'timestamp' }}
              defaultSortingIsDescending={true}
              empty={
                <Box textAlign="center" color="text-body-secondary">
                  No execution history
                </Box>
              }
            />
          ) : (
            <Box textAlign="center" color="text-body-secondary">
              No code has been executed yet
            </Box>
          )}
        </Container>

        <Container header={<Header variant="h3">Conversation History</Header>}>
          {history.conversation_history && history.conversation_history.length > 0 ? (
            <Table
              columnDefinitions={conversationColumns}
              items={history.conversation_history}
              sortingDisabled={false}
              empty={
                <Box textAlign="center" color="text-body-secondary">
                  No conversation history
                </Box>
              }
            />
          ) : (
            <Box textAlign="center" color="text-body-secondary">
              No conversation history yet
            </Box>
          )}
        </Container>
      </SpaceBetween>

      <Modal
        visible={showCodeModal}
        onDismiss={() => setShowCodeModal(false)}
        header={selectedItem?.type === 'file_upload' ? `File: ${selectedItem.filename}` : "Code Details"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowCodeModal(false)}>
                Close
              </Button>
              {selectedItem?.code && (
                <Button 
                  variant="primary" 
                  onClick={() => {
                    onExecuteCode(selectedItem.code);
                    setShowCodeModal(false);
                  }}
                >
                  Execute
                </Button>
              )}
            </SpaceBetween>
          </Box>
        }
        size="large"
      >
        {selectedItem && (
          <SpaceBetween direction="vertical" size="m">
            {selectedItem.prompt && (
              <Box>
                <Box variant="awsui-key-label">Original Prompt</Box>
                <Box>{selectedItem.prompt}</Box>
              </Box>
            )}
            
            {(selectedItem.code || selectedItem.content) && (
              <Box>
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Box variant="awsui-key-label">
                    {selectedItem.type === 'file_upload' ? 'File Content' : 'Generated Code'}
                  </Box>
                  <Button
                    size="small"
                    iconName={copySuccess ? "check" : "copy"}
                    onClick={() => handleCopyCode(selectedItem.code || selectedItem.content)}
                  >
                    {copySuccess ? 'Copied!' : 'Copy Code'}
                  </Button>
                </SpaceBetween>
                <CodeEditor
                  value={selectedItem.code || selectedItem.content}
                  readOnly={true}
                  height="300px"
                />
              </Box>
            )}
            
            {selectedItem.result && (
              <Box>
                <Box variant="awsui-key-label">Execution Result</Box>
                <CodeDisplay content={selectedItem.result} />
                
                {/* Render charts if available */}
                {selectedItem.images && selectedItem.images.length > 0 && (
                  <Box marginTop="m">
                    <Box variant="awsui-key-label">Generated Charts</Box>
                    <ImageDisplay images={selectedItem.images} />
                  </Box>
                )}
              </Box>
            )}
          </SpaceBetween>
        )}
      </Modal>
    </Container>
  );
};

export default SessionHistory;
