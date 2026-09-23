import React, { useState } from 'react';
import { Box, Button } from '@cloudscape-design/components';

const CodeDisplay = ({ content, language = 'text', maxHeight = '400px', showCopyButton = true }) => {
  const [copySuccess, setCopySuccess] = useState(false);

  // Handle different content types
  const displayContent = React.useMemo(() => {
    if (!content) return 'No content to display';
    
    // If content is an object, stringify it
    if (typeof content === 'object') {
      try {
        return JSON.stringify(content, null, 2);
      } catch (e) {
        return String(content);
      }
    }
    
    return String(content);
  }, [content]);

  // Determine if content looks like an error
  const isError = React.useMemo(() => {
    const contentStr = displayContent.toLowerCase();
    return contentStr.includes('error') || 
           contentStr.includes('exception') || 
           contentStr.includes('traceback') ||
           contentStr.includes('failed');
  }, [displayContent]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(displayContent);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Failed to copy text: ', err);
    }
  };

  return (
    <Box>
      {showCopyButton && displayContent && displayContent !== 'No content to display' && (
        <Box float="right" margin={{ bottom: 'xs' }}>
          <Button
            size="small"
            onClick={handleCopy}
            iconName={copySuccess ? "check" : "copy"}
          >
            {copySuccess ? 'Copied!' : 'Copy'}
          </Button>
        </Box>
      )}
      <div style={{
        backgroundColor: '#f0f9f0', // Consistent light green background for all outputs
        border: `1px solid #d4edda`, // Light green border for all cases
        borderRadius: '4px',
        padding: '12px',
        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", Consolas, "Courier New", monospace',
        fontSize: '13px',
        lineHeight: '1.5',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        maxHeight: maxHeight,
        overflowY: 'auto',
        overflowX: 'auto',
        color: isError ? '#d73527' : '#155724', // Red for errors, dark green for success
        clear: 'both'
      }}>
        {displayContent}
      </div>
    </Box>
  );
};

export default CodeDisplay;
