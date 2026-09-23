# ESLint Error Fixes

## Common ESLint Errors and Solutions

### 1. `'ComponentName' is not defined` (react/jsx-no-undef)

**Error Example:**
```
ERROR
[eslint] 
src/components/ExecutionResults.js
  Line 67:20:  'Badge' is not defined  react/jsx-no-undef
```

**Solution:**
Add the missing component to the import statement:

```javascript
// Before (missing Badge)
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button
} from '@cloudscape-design/components';

// After (Badge added)
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Badge
} from '@cloudscape-design/components';
```

### 2. Unused Variables Warning

**Error Example:**
```
WARNING
[eslint] 
src/components/CodeDisplay.js
  Line 2:35:  'SpaceBetween' is defined but never used  no-unused-vars
```

**Solution:**
Remove unused imports:

```javascript
// Before (SpaceBetween unused)
import { Box, Button, SpaceBetween } from '@cloudscape-design/components';

// After (SpaceBetween removed)
import { Box, Button } from '@cloudscape-design/components';
```

### 3. Console Statements Warning

**Error Example:**
```
WARNING
[eslint] 
src/App.js
  Line 45:7:  Unexpected console statement  no-console
```

**Solution:**
Either remove console statements for production or use eslint-disable:

```javascript
// Option 1: Remove console statements
// console.log('Debug info'); // Remove this

// Option 2: Disable eslint for specific line
// eslint-disable-next-line no-console
console.log('Debug info');

// Option 3: Use proper logging in production
if (process.env.NODE_ENV === 'development') {
  console.log('Debug info');
}
```

## Common Cloudscape Components

Here are the most commonly used Cloudscape components that need to be imported:

```javascript
import {
  // Layout
  AppLayout,
  ContentLayout,
  Container,
  Header,
  Box,
  SpaceBetween,
  ColumnLayout,
  
  // Form Controls
  Button,
  Input,
  Textarea,
  FormField,
  FileUpload,
  Select,
  
  // Feedback
  Alert,
  Spinner,
  StatusIndicator,
  Badge,
  
  // Navigation
  Tabs,
  Modal,
  Link,
  
  // Data Display
  Table,
  CodeEditor
} from '@cloudscape-design/components';
```

## Quick Fix Commands

### Run ESLint Check
```bash
cd frontend
npm run lint
```

### Auto-fix ESLint Issues
```bash
cd frontend
npx eslint src/ --fix
```

### Check Specific File
```bash
cd frontend
npx eslint src/components/ExecutionResults.js
```

## Prevention Tips

1. **Use IDE Extensions:** Install ESLint extension for your IDE
2. **Pre-commit Hooks:** Set up ESLint to run before commits
3. **Regular Checks:** Run `npm run lint` regularly during development
4. **Import Organization:** Keep imports organized and remove unused ones

## Testing Frontend

Run the frontend test script to check for common issues:

```bash
node test_frontend.js
```

This will check for:
- Missing files
- Missing dependencies
- Import issues
- Deprecated component usage
