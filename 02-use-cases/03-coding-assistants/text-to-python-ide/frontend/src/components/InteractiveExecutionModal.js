import React, { useState, useEffect } from 'react';
import {
  Modal,
  Box,
  Button,
  SpaceBetween,
  FormField,
  Input,
  Alert,
  Container,
  Header,
  Spinner,
  Badge,
  Textarea
} from '@cloudscape-design/components';
import CodeDisplay from './CodeDisplay';

const InteractiveExecutionModal = ({ 
  visible, 
  onDismiss, 
  code, 
  onExecute,
  analysis 
}) => {
  const [inputs, setInputs] = useState(['']);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (visible) {
      // Reset inputs when modal opens
      setInputs(['']);
      setError(null);
    }
  }, [visible]);

  const addInput = () => {
    setInputs([...inputs, '']);
  };

  const removeInput = (index) => {
    if (inputs.length > 1) {
      const newInputs = inputs.filter((_, i) => i !== index);
      setInputs(newInputs);
    }
  };

  const updateInput = (index, value) => {
    const newInputs = [...inputs];
    newInputs[index] = value;
    setInputs(newInputs);
  };

  const handleExecute = async () => {
    setLoading(true);
    setError(null);
    
    try {
      // Filter out empty inputs
      const validInputs = inputs.filter(input => input.trim() !== '');
      await onExecute(code, true, validInputs);
      onDismiss();
    } catch (err) {
      setError(`Execution failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Interactive Code Execution"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss} disabled={loading}>
              Cancel
            </Button>
            <Button 
              variant="primary" 
              onClick={handleExecute}
              loading={loading}
            >
              Execute with Inputs
            </Button>
          </SpaceBetween>
        </Box>
      }
      size="large"
    >
      <SpaceBetween direction="vertical" size="l">
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Alert type="info" header="Interactive Code Detected">
          <SpaceBetween direction="horizontal" size="s" alignItems="center">
            <Box>This code requires user input during execution.</Box>
            <Badge color="blue">Interactive</Badge>
          </SpaceBetween>
        </Alert>

        {analysis && (
          <Container header={<Header variant="h3">Code Analysis</Header>}>
            <CodeDisplay content={analysis} maxHeight="200px" showCopyButton={false} />
          </Container>
        )}

        <Container header={<Header variant="h3">Provide Input Values</Header>}>
          <SpaceBetween direction="vertical" size="m">
            <Box color="text-body-secondary">
              Enter the values that should be provided when the code asks for input. 
              The inputs will be used in the order you specify them.
            </Box>
            
            {inputs.map((input, index) => (
              <FormField
                key={index}
                label={`Input ${index + 1}`}
                description={`Value to provide for the ${index === 0 ? 'first' : index === 1 ? 'second' : index === 2 ? 'third' : `${index + 1}th`} input() call`}
              >
                <SpaceBetween direction="horizontal" size="s">
                  <Input
                    value={input}
                    onChange={({ detail }) => updateInput(index, detail.value)}
                    placeholder="Enter input value..."
                  />
                  <Button
                    onClick={() => removeInput(index)}
                    disabled={inputs.length === 1}
                  >
                    Remove
                  </Button>
                </SpaceBetween>
              </FormField>
            ))}
            
            <Box textAlign="center">
              <Button onClick={addInput}>
                Add Another Input
              </Button>
            </Box>
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h3">Code to Execute</Header>}>
          <CodeDisplay content={code} maxHeight="200px" showCopyButton={false} />
        </Container>
      </SpaceBetween>
    </Modal>
  );
};

export default InteractiveExecutionModal;
