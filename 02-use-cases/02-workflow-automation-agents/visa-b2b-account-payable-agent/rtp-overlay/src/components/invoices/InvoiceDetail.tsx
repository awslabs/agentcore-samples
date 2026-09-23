import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Container,
  Box,
  Typography,
  Breadcrumbs,
  Link,
  Alert,
  CircularProgress,
  Tabs,
  Tab,
  Button,
  Snackbar,
} from '@mui/material';
import {
  Home as HomeIcon,
  ArrowBack as ArrowBackIcon,
} from '@mui/icons-material';
import { useInvoice } from '../../contexts/InvoiceContext';
import { invoiceService } from '../../services/invoice.service';
import { InvoiceHeader } from './InvoiceHeader';
import { ExtractedDataSection } from './ExtractedDataSection';
import { MatchedPOCard } from './MatchedPOCard';
import { GoodsReceiptCard } from './GoodsReceiptCard';
import { PaymentStatusTimeline } from './PaymentStatusTimeline';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

const TabPanel: React.FC<TabPanelProps> = ({ children, value, index }) => {
  return (
    <div role="tabpanel" hidden={value !== index}>
      {value === index && <Box sx={{ py: 3 }}>{children}</Box>}
    </div>
  );
};

export const InvoiceDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { fetchInvoiceById } = useInvoice();

  const [invoice, setInvoice] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tabValue, setTabValue] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const loadInvoice = async () => {
      if (!id) return;

      try {
        setLoading(true);
        setError(null);
        const data = await fetchInvoiceById(id);
        setInvoice(data);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to load invoice';
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    loadInvoice();
  }, [id, fetchInvoiceById]);

  const handleDownloadPDF = async () => {
    if (!invoice) return;

    try {
      setDownloading(true);
      const response = await invoiceService.downloadInvoice(invoice.id);
      
      // Open the signed URL in a new tab
      window.open(response.url, '_blank');
      setSuccessMessage('Invoice PDF download started');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to download invoice';
      setErrorMessage(message);
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadXML = async () => {
    if (!invoice || !invoice.iso20022FileKey) return;

    try {
      setDownloading(true);
      const url = await invoiceService.getISO20022FileUrl(invoice.id);
      
      // Open the signed URL in a new tab
      window.open(url, '_blank');
      setSuccessMessage('ISO 20022 XML download started');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to download XML file';
      setErrorMessage(message);
    } finally {
      setDownloading(false);
    }
  };

  const handleLinkPO = () => {
    // TODO: Implement Link PO dialog
    setErrorMessage('Link PO functionality coming soon');
  };

  const handleUpdateStatus = () => {
    // TODO: Implement Update Status dialog
    setErrorMessage('Update Status functionality coming soon');
  };

  const handleBack = () => {
    navigate('/invoices');
  };

  if (loading) {
    return (
      <Container maxWidth="xl" sx={{ py: 4 }}>
        <Box display="flex" justifyContent="center" alignItems="center" minHeight="400px">
          <CircularProgress />
        </Box>
      </Container>
    );
  }

  if (error || !invoice) {
    return (
      <Container maxWidth="xl" sx={{ py: 4 }}>
        <Alert severity="error" sx={{ mb: 3 }}>
          {error || 'Invoice not found'}
        </Alert>
        <Button startIcon={<ArrowBackIcon />} onClick={handleBack}>
          Back to Invoices
        </Button>
      </Container>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      {/* Breadcrumbs */}
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link
          href="/"
          underline="hover"
          color="inherit"
          sx={{ display: 'flex', alignItems: 'center' }}
        >
          <HomeIcon sx={{ mr: 0.5 }} fontSize="small" />
          Home
        </Link>
        <Link href="/invoices" underline="hover" color="inherit">
          Invoices
        </Link>
        <Typography color="text.primary">{invoice.invoiceNumber}</Typography>
      </Breadcrumbs>

      {/* Back Button */}
      <Button
        startIcon={<ArrowBackIcon />}
        onClick={handleBack}
        sx={{ mb: 2 }}
      >
        Back to Invoices
      </Button>

      {/* Invoice Header */}
      <InvoiceHeader
        invoice={invoice}
        onDownloadPDF={handleDownloadPDF}
        onDownloadXML={handleDownloadXML}
        onLinkPO={handleLinkPO}
        onUpdateStatus={handleUpdateStatus}
        downloading={downloading}
      />

      {/* Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 3 }}>
        <Tabs value={tabValue} onChange={(_, newValue) => setTabValue(newValue)}>
          <Tab label="Overview" />
          <Tab label="Matching" />
          <Tab label="Timeline" />
        </Tabs>
      </Box>

      {/* Tab Panels */}
      <TabPanel value={tabValue} index={0}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <ExtractedDataSection
            data={invoice.extractedData}
            currency={invoice.currency}
          />
        </Box>
      </TabPanel>

      <TabPanel value={tabValue} index={1}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <MatchedPOCard invoice={invoice} onLinkPO={handleLinkPO} />
          <GoodsReceiptCard invoice={invoice} />
        </Box>
      </TabPanel>

      <TabPanel value={tabValue} index={2}>
        <PaymentStatusTimeline
          currentStatus={invoice.paymentStatus}
          createdAt={invoice.createdAt}
          updatedAt={invoice.updatedAt}
        />
      </TabPanel>

      {/* Success Snackbar */}
      <Snackbar
        open={!!successMessage}
        autoHideDuration={6000}
        onClose={() => setSuccessMessage(null)}
        message={successMessage}
      />

      {/* Error Snackbar */}
      <Snackbar
        open={!!errorMessage}
        autoHideDuration={6000}
        onClose={() => setErrorMessage(null)}
      >
        <Alert severity="error" onClose={() => setErrorMessage(null)}>
          {errorMessage}
        </Alert>
      </Snackbar>
    </Container>
  );
};
