import React, { useState } from 'react';
import {
  Modal,
  Box,
  SpaceBetween,
  Button,
  FileUpload,
  Alert,
  Header,
  Container,
  TextContent
} from '@cloudscape-design/components';

const CsvUploadModal = ({ visible, onDismiss, onUpload, loading = false }) => {
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [error, setError] = useState(null);

  const handleFileChange = ({ detail }) => {
    setSelectedFiles(detail.value);
    setError(null);
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError('Please select a CSV file to upload.');
      return;
    }

    const file = selectedFiles[0];
    
    // Validate file type
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Please select a CSV file (.csv extension required).');
      return;
    }

    try {
      // Read file content
      const reader = new FileReader();
      reader.onload = async (e) => {
        try {
          await onUpload({
            name: file.name,
            content: e.target.result
          });
          setSelectedFiles([]);
          setError(null);
        } catch (err) {
          setError(`Upload failed: ${err.message}`);
        }
      };
      reader.readAsText(file);
    } catch (err) {
      setError(`Failed to read file: ${err.message}`);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Upload CSV File"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={onDismiss} disabled={loading}>
              Cancel
            </Button>
            <Button 
              variant="primary" 
              onClick={handleUpload}
              loading={loading}
              disabled={selectedFiles.length === 0}
            >
              Upload
            </Button>
          </SpaceBetween>
        </Box>
      }
      size="medium"
    >
      <SpaceBetween size="m">
        <Container>
          <SpaceBetween size="s">
            <Header variant="h3">Select CSV File</Header>
            <TextContent>
              <p>Upload a CSV file to use in your code generation. The file will be available for analysis and processing in the generated Python code.</p>
            </TextContent>
          </SpaceBetween>
        </Container>

        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        <FileUpload
          onChange={handleFileChange}
          value={selectedFiles}
          i18nStrings={{
            uploadButtonText: e => e ? "Choose files" : "Choose file",
            dropzoneText: e => e ? "Drop files to upload" : "Drop file to upload",
            removeFileAriaLabel: e => `Remove file ${e + 1}`,
            limitShowFewer: "Show fewer files",
            limitShowMore: "Show more files",
            errorIconAriaLabel: "Error"
          }}
          showFileLastModified
          showFileSize
          showFileThumbnail
          tokenLimit={3}
          accept=".csv"
          constraintText="Only CSV files are allowed. Maximum file size: 10MB."
        />
      </SpaceBetween>
    </Modal>
  );
};

export default CsvUploadModal;
