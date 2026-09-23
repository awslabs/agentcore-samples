import React, { memo, useMemo } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Alert,
  ColumnLayout,
  StatusIndicator,
  Badge
} from '@cloudscape-design/components';
import CodeEditor from './CodeEditor.jsx';
import CodeDisplay from './CodeDisplay.jsx';
import ImageDisplay from './ImageDisplay.jsx';

const ExecutionResults = memo(({ result, onExecuteAgain }) => {
  const isError = useMemo(() => {
    // Check if there's an explicit success field (false means error)
    if (result?.success !== undefined) {
      return !result.success;
    }
    
    // Fallback: check if result contains actual error indicators
    // Only consider it an error if it contains error keywords at the beginning
    // or has typical Python error patterns
    if (result?.result && typeof result.result === 'string') {
      const resultText = result.result.toLowerCase();
      return (
        resultText.startsWith('error:') ||
        resultText.startsWith('traceback') ||
        resultText.includes('exception:') ||
        resultText.includes('error occurred') ||
        resultText.includes('failed to execute')
      );
    }
    
    return false;
  }, [result?.result, result?.success]);

  const formatTimestamp = useMemo(() => {
    if (!result?.timestamp) return '';
    return new Date(result.timestamp).toLocaleString();
  }, [result?.timestamp]);

  if (!result) {
    return (
      <Container header={<Header variant="h2">Execution Results</Header>}>
        <Box textAlign="center" color="text-body-secondary">
          No execution results yet. Execute some code to see results here.
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
            <Button onClick={onExecuteAgain}>
              Execute Again
            </Button>
          }
        >
          Execution Results
        </Header>
      }
    >
      <SpaceBetween direction="vertical" size="l">
        <ColumnLayout columns={2}>
          <Box>
            <Box variant="awsui-key-label">Status</Box>
            <StatusIndicator type={isError ? "error" : "success"}>
              {isError ? "Execution Failed" : "Execution Successful"}
            </StatusIndicator>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Executed At</Box>
            {formatTimestamp}
          </Box>
        </ColumnLayout>

        {result.interactive && (
          <Container header={<Header variant="h3">Interactive Execution Details</Header>}>
            <SpaceBetween direction="vertical" size="s">
              <Box>
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Badge color="blue">Interactive Code</Badge>
                  <Box fontSize="body-s">This code required user input during execution</Box>
                </SpaceBetween>
              </Box>
              {result.inputs_used && result.inputs_used.length > 0 && (
                <Box>
                  <Box variant="awsui-key-label">Inputs Provided:</Box>
                  <Box fontSize="body-s" fontFamily="monospace">
                    {result.inputs_used.map((input, index) => (
                      <div key={index}>Input {index + 1}: "{input}"</div>
                    ))}
                  </Box>
                </Box>
              )}
            </SpaceBetween>
          </Container>
        )}

        <Container header={<Header variant="h3">Executed Code</Header>}>
          <CodeEditor
            value={result.code}
            readOnly={true}
            height="200px"
          />
        </Container>

        <Container header={<Header variant="h3">Output</Header>}>
          {isError ? (
            <Alert type="error" header="Execution Error">
              <CodeDisplay content={result.result} />
            </Alert>
          ) : (
            <CodeDisplay content={result.result || 'No output generated'} />
          )}
        </Container>

        {result.images && result.images.length > 0 && (
          <ImageDisplay images={result.images} />
        )}

        <Box textAlign="center">
          <Button onClick={onExecuteAgain}>
            Execute Again
          </Button>
        </Box>
      </SpaceBetween>
    </Container>
  );
});

ExecutionResults.displayName = 'ExecutionResults';

export default ExecutionResults;
