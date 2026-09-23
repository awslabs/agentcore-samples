/**
 * Main App Component with Routing
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { MockAuthProvider } from './contexts/MockAuthContext';
import MainLayout from './components/layout/MainLayout';
import MockLogin from './pages/Login/MockLogin';
import Dashboard from './pages/Dashboard/Dashboard';
import PurchaseOrderList from './pages/PurchaseOrders/PurchaseOrderList';
import { CreatePurchaseOrder } from './pages/PurchaseOrders/CreatePurchaseOrder';
import { PurchaseOrderDetail } from './pages/PurchaseOrders/PurchaseOrderDetail';
import GoodsReceiptList from './pages/GoodsReceipts/GoodsReceiptList';
import { UploadGoodsReceipt } from './pages/GoodsReceipts/UploadGoodsReceipt';
import { GoodsReceiptDetail } from './pages/GoodsReceipts/GoodsReceiptDetail';
import QCRecordList from './pages/QualityControl/QCRecordList';
import { CreateQCRecord } from './pages/QualityControl/CreateQCRecord';
import { QCRecordDetail } from './pages/QualityControl/QCRecordDetail';
import InvoiceList from './pages/Invoices/InvoiceList';
import { UploadInvoice } from './pages/Invoices/UploadInvoice';
import { InvoiceMatchDetail } from './pages/Invoices/InvoiceMatchDetail';
import SupplierLogin from './pages/Supplier/SupplierLogin';
import SupplierPaymentView from './pages/Supplier/SupplierPaymentView';

function App() {
  return (
    <MockAuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<MockLogin />} />
          <Route
            path="/dashboard"
            element={
              <MainLayout>
                <Dashboard />
              </MainLayout>
            }
          />
          <Route
            path="/purchase-orders"
            element={
              <MainLayout>
                <PurchaseOrderList />
              </MainLayout>
            }
          />
          <Route
            path="/purchase-orders/create"
            element={
              <MainLayout>
                <CreatePurchaseOrder />
              </MainLayout>
            }
          />
          <Route
            path="/purchase-orders/:id"
            element={
              <MainLayout>
                <PurchaseOrderDetail />
              </MainLayout>
            }
          />
          <Route
            path="/goods-receipts"
            element={
              <MainLayout>
                <GoodsReceiptList />
              </MainLayout>
            }
          />
          <Route
            path="/goods-receipts/upload"
            element={
              <MainLayout>
                <UploadGoodsReceipt />
              </MainLayout>
            }
          />
          <Route
            path="/goods-receipts/:id"
            element={
              <MainLayout>
                <GoodsReceiptDetail />
              </MainLayout>
            }
          />
          <Route
            path="/quality-control"
            element={
              <MainLayout>
                <QCRecordList />
              </MainLayout>
            }
          />
          <Route
            path="/quality-control/create"
            element={
              <MainLayout>
                <CreateQCRecord />
              </MainLayout>
            }
          />
          <Route
            path="/quality-control/:id"
            element={
              <MainLayout>
                <QCRecordDetail />
              </MainLayout>
            }
          />
          <Route
            path="/invoices"
            element={
              <MainLayout>
                <InvoiceList />
              </MainLayout>
            }
          />
          <Route
            path="/invoices/upload"
            element={
              <MainLayout>
                <UploadInvoice />
              </MainLayout>
            }
          />
          <Route
            path="/invoices/:id"
            element={
              <MainLayout>
                <InvoiceMatchDetail />
              </MainLayout>
            }
          />
          {/* Supplier Portal Routes (No MainLayout - external access) */}
          <Route path="/supplier/login" element={<SupplierLogin />} />
          <Route path="/supplier/payment/:id" element={<SupplierPaymentView />} />
          
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </MockAuthProvider>
  );
}

export default App;
