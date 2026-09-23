import React, { memo } from 'react';
import Editor from '@monaco-editor/react';
import { Box } from '@cloudscape-design/components';

const CodeEditor = memo(({ 
  value, 
  onChange, 
  readOnly = false, 
  height = '300px',
  language = 'python'
}) => {
  
  const handleEditorChange = (newValue) => {
    if (onChange && !readOnly) {
      onChange(newValue);
    }
  };

  const editorOptions = {
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    fontSize: 14,
    lineNumbers: 'on',
    roundedSelection: false,
    readOnly: readOnly,
    cursorStyle: 'line',
    automaticLayout: true,
    theme: 'vs-dark',
    wordWrap: 'on',
    folding: true,
    lineDecorationsWidth: 10,
    lineNumbersMinChars: 3,
    renderLineHighlight: 'all',
    selectOnLineNumbers: true,
    tabSize: 4,
    insertSpaces: true
  };

  return (
    <Box>
      <div style={{ 
        border: '1px solid #d5dbdb', 
        borderRadius: '4px',
        overflow: 'hidden'
      }}>
        <Editor
          height={height}
          language={language}
          value={value || ''}
          onChange={handleEditorChange}
          options={editorOptions}
          theme="vs-dark"
        />
      </div>
    </Box>
  );
});

CodeEditor.displayName = 'CodeEditor';

export default CodeEditor;
