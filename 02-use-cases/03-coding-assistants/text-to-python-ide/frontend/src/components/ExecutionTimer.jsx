import React, { useState, useEffect, memo } from 'react';
import {
  Box,
  Badge,
  StatusIndicator
} from '@cloudscape-design/components';

const ExecutionTimer = memo(({ 
  isRunning, 
  onReset, 
  warningThreshold = parseInt(import.meta.env.VITE_EXECUTION_TIMEOUT_WARNING || '300'),
  maxTime = parseInt(import.meta.env.VITE_MAX_EXECUTION_TIME || '600')
}) => {
  const [seconds, setSeconds] = useState(0);
  const [status, setStatus] = useState('info');

  useEffect(() => {
    let interval = null;
    
    if (isRunning) {
      interval = setInterval(() => {
        setSeconds(prevSeconds => {
          const newSeconds = prevSeconds + 1;
          
          // Update status based on time elapsed
          if (newSeconds >= maxTime) {
            setStatus('error');
          } else if (newSeconds >= warningThreshold) {
            setStatus('warning');
          } else {
            setStatus('info');
          }
          
          return newSeconds;
        });
      }, 1000);
    } else {
      clearInterval(interval);
    }

    return () => clearInterval(interval);
  }, [isRunning, warningThreshold, maxTime]);

  useEffect(() => {
    if (!isRunning && onReset) {
      // Reset timer when execution stops
      const timer = setTimeout(() => {
        setSeconds(0);
        setStatus('info');
      }, 2000); // Keep final time visible for 2 seconds
      
      return () => clearTimeout(timer);
    }
  }, [isRunning, onReset]);

  const formatTime = (totalSeconds) => {
    const minutes = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  };

  const getStatusText = () => {
    if (status === 'error') return 'Execution taking very long';
    if (status === 'warning') return 'Long execution';
    return 'Executing';
  };

  const getStatusType = () => {
    if (status === 'error') return 'error';
    if (status === 'warning') return 'warning';
    return 'in-progress';
  };

  if (!isRunning && seconds === 0) {
    return null;
  }

  return (
    <Box display="flex" alignItems="center">
      <StatusIndicator type={getStatusType()}>
        {getStatusText()}
      </StatusIndicator>
      <Box marginLeft="xs">
        <Badge 
          color={status === 'error' ? 'red' : status === 'warning' ? 'yellow' : 'blue'}
        >
          {formatTime(seconds)}
        </Badge>
      </Box>
      {status === 'warning' && (
        <Box marginLeft="s" fontSize="body-s" color="text-status-warning">
          Execution is taking longer than expected
        </Box>
      )}
      {status === 'error' && (
        <Box marginLeft="s" fontSize="body-s" color="text-status-error">
          Execution time exceeded {Math.floor(maxTime / 60)} minutes
        </Box>
      )}
    </Box>
  );
});

ExecutionTimer.displayName = 'ExecutionTimer';

export default ExecutionTimer;
