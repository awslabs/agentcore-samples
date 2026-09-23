import React from 'react';
import {
  Paper,
  Typography,
  Stepper,
  Step,
  StepLabel,
  StepContent,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  RadioButtonUnchecked as RadioButtonUncheckedIcon,
  Error as ErrorIcon,
} from '@mui/icons-material';
import type { PaymentStatus } from '../../types/invoice.types';
import { formatDateTime } from '../../utils/formatters';

interface PaymentStatusTimelineProps {
  currentStatus: PaymentStatus;
  createdAt: string;
  updatedAt: string;
}

const statusSteps: PaymentStatus[] = [
  'pending',
  'processing',
  'generated',
  'sent',
  'paid',
];

const statusLabels: Record<PaymentStatus, string> = {
  pending: 'Invoice Created',
  processing: 'Processing',
  generated: 'ISO 20022 Generated',
  sent: 'Payment Sent',
  paid: 'Payment Completed',
  failed: 'Payment Failed',
};

const statusDescriptions: Record<PaymentStatus, string> = {
  pending: 'Invoice uploaded and awaiting processing',
  processing: 'Extracting data and validating invoice',
  generated: 'ISO 20022 payment file generated',
  sent: 'Payment instruction sent to bank',
  paid: 'Payment confirmed and completed',
  failed: 'Payment processing failed',
};

export const PaymentStatusTimeline: React.FC<PaymentStatusTimelineProps> = ({
  currentStatus,
  createdAt,
  updatedAt,
}) => {
  const activeStep = statusSteps.indexOf(currentStatus);
  const isFailed = currentStatus === 'failed';

  const getStepIcon = (index: number) => {
    if (isFailed && index === activeStep) {
      return <ErrorIcon color="error" />;
    }
    if (index < activeStep || (index === activeStep && !isFailed)) {
      return <CheckCircleIcon color="success" />;
    }
    return <RadioButtonUncheckedIcon color="disabled" />;
  };

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h6" gutterBottom fontWeight="bold">
        Payment Status Timeline
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Track the progress of this invoice through the payment process
      </Typography>

      <Stepper activeStep={activeStep} orientation="vertical">
        {statusSteps.map((status, index) => (
          <Step key={status} completed={index < activeStep}>
            <StepLabel
              StepIconComponent={() => getStepIcon(index)}
              error={isFailed && index === activeStep}
            >
              <Typography variant="subtitle2" fontWeight="medium">
                {statusLabels[status]}
              </Typography>
              {index === activeStep && (
                <Typography variant="caption" color="text.secondary">
                  {formatDateTime(updatedAt)}
                </Typography>
              )}
              {index === 0 && (
                <Typography variant="caption" color="text.secondary">
                  {formatDateTime(createdAt)}
                </Typography>
              )}
            </StepLabel>
            <StepContent>
              <Typography variant="body2" color="text.secondary">
                {statusDescriptions[status]}
              </Typography>
            </StepContent>
          </Step>
        ))}

        {isFailed && (
          <Step active>
            <StepLabel StepIconComponent={() => <ErrorIcon color="error" />} error>
              <Typography variant="subtitle2" fontWeight="medium" color="error">
                {statusLabels.failed}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {formatDateTime(updatedAt)}
              </Typography>
            </StepLabel>
            <StepContent>
              <Typography variant="body2" color="error">
                {statusDescriptions.failed}
              </Typography>
            </StepContent>
          </Step>
        )}
      </Stepper>
    </Paper>
  );
};
