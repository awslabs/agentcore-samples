# Visa B2B Payment Integration - Testing Guide

## Overview

This guide provides step-by-step instructions for testing the Visa B2B Payment Integration after deployment. The system comes pre-populated with test data to help you quickly verify all functionality.

---

## Pre-Populated Test Data

After running the deployment and migration scripts, your database will contain:

### Test Vendors

| Vendor Name | Vendor Code | Contact Email | Phone |
|-------------|-------------|---------------|-------|
| Acme Corporation | ACME001 | contact@acme.com | +1-555-0100 |
| TechSupply Inc | TECH002 | sales@techsupply.com | +1-555-0200 |
| Global Parts Ltd | GLOBAL003 | info@globalparts.com | +1-555-0300 |

### Test Purchase Orders (Pre-Seeded)

10 pre-created purchase orders ready for testing:

- **PO-2025-00001** through **PO-2025-00010**
- Vendors: Acme Corporation, TechSupply Inc, Global Parts Ltd (rotating)
- Amounts: Range from ~$8,000 to ~$50,000
- Status: First 5 are "active", last 5 are "received" (with goods receipts)
- Items: Generic product items from each vendor

> **Note:** The first 5 POs (00001-00005) are active and ready for testing. The last 5 POs (00006-00010) already have goods receipts and can be used for invoice upload and payment testing.

---

## Document Creation Guidelines

### For Bill of Lading (BOL)

Each BOL should include:
- **BOL Number**: Unique identifier (e.g., BOL-2025-001)
- **PO Number**: Must match one of the POs above
- **Vendor Information**: Name, code, contact details
- **Ship Date**: Date goods shipped
- **Delivery Date**: Date goods received
- **Source Location**: From PO specification
- **Destination**: Warehouse from PO specification
- **Items**: Description, quantity (should match or be less than PO quantity)
- **Carrier Information**: Shipping company, tracking number
- **Signatures**: Shipper and receiver

### For Invoice

Each Invoice should include:
- **Invoice Number**: Unique identifier (e.g., INV-2025-001)
- **Invoice Date**: Date invoice issued
- **PO Number**: Must match the same PO as the BOL
- **Vendor Information**: Name, code, contact details, tax ID
- **Bill To**: Your company information
- **Line Items**: Description, quantity, unit price, total (should match PO)
- **Subtotal**: Sum of line items
- **Tax**: If applicable
- **Total Amount**: Should match PO total amount
- **Payment Terms**: Net 30, Net 60, etc.
- **Due Date**: Based on payment terms

### 3-Way Match Requirements

For successful 3-way matching:
1. **PO Number** must be identical across all three documents
2. **Vendor** must match
3. **Item descriptions** should be consistent
4. **Quantities**: BOL ≤ PO, Invoice ≤ GR (5% tolerance allowed)
5. **Amounts**: Invoice total should match PO total (10% variance tolerance)
   - **Partial invoices allowed**: Invoice amount can be less than PO amount
   - System will flag warnings if variance > 10% but will still allow payment
   - Useful for partial deliveries or progressive billing

---

## Prerequisites

Before testing, ensure:

1. ✅ All infrastructure deployed successfully
2. ✅ Database migrations completed (`./infrastructure/run-migrations.sh`)
3. ✅ Frontend deployed and accessible
4. ✅ Backend API URL configured in frontend
5. ✅ Visa B2B stub APIs deployed and accessible

---

## Testing Workflow

### Phase 1: Document Upload and 3-Way Matching

#### Step 1: Access Buyer Portal

1. Navigate to the frontend URL
2. Verify you can see the dashboard
3. Navigate to **Purchase Orders** section
4. Verify 5 pre-populated POs are visible

#### Step 2: Upload Bill of Lading (BOL)

> **Important**: BOL must be uploaded first to create Goods Receipt before invoice processing.

1. Navigate to **Goods Receipts** section
2. Click **Upload BOL** button
3. Upload a test BOL PDF that matches one of the pre-populated POs
4. Verify BOL is processed and Goods Receipt created:
   - PO number matched
   - Items and quantities extracted
   - Goods Receipt created with status
   - Linked to appropriate PO

**Expected Result:**
- BOL uploaded successfully
- Goods Receipt created
- Data extracted and matched to PO
- Status: `received` or `pending_qc`

#### Step 3: Upload Invoice

> **Note**: Invoice should reference the same PO as the BOL for 3-way matching.

1. Navigate to **Invoices** section
2. Click **Upload Invoice** button
3. Upload a test invoice PDF that matches the same PO
4. Verify invoice appears in the list with:
   - Invoice number extracted
   - Vendor matched to one of the test vendors
   - Amount extracted
   - PO number matched
   - Linked to Goods Receipt (3-way match)
   - Status: `pending` or `matched`

**Expected Result:**
- Invoice uploaded successfully
- Data extracted and displayed
- 3-way match completed (PO → GR → Invoice)
- Ready for payment processing

---

### Phase 2: Payment Processing (Visa B2B)

#### Step 3: Process Payment with Visa B2B

1. Click on an uploaded invoice to view details
2. Click **Process Payment** button
3. Wait for Payment Agent to make decision
4. Verify the following:

**Payment Agent Decision:**
- AI reasoning displayed (why Visa B2B was chosen)
- Payment method: `visa_b2b`
- Status: `card_issued`

**Virtual Card Display (Buyer View):**
- Card number displayed (masked: `**** 1234`)
- CVV masked (click to reveal)
- Expiration date shown
- Amount authorized matches invoice
- Payment ID displayed
- Tracking number displayed
- **Copy Card Details** button available

**Expected Result:**
- Payment processed successfully
- Virtual card generated
- Card details visible in buyer portal
- Payment status: `card_issued` → `completed` or `failed`

---

### Phase 3: Supplier Portal Testing

#### Step 4: Supplier Retrieves Card

1. Open a new browser window/incognito mode
2. Navigate to supplier portal: `<frontend-url>/supplier/payment`
3. Login with one of:
   - **Payment ID** (shown in buyer portal)
   - **Tracking Number** (shown in buyer portal)

**Supplier View Should Show:**
- Invoice information (number, amount, due date)
- Full virtual card details:
  - Complete card number (unmasked)
  - CVV (visible)
  - Expiration date
- **Copy Card Details** button
- Payment status
- Clean, simple interface

**Test Actions:**
1. Click **Copy Card Details** button
2. Verify card details copied to clipboard
3. Verify payment status displayed correctly

**Expected Result:**
- Supplier can login with Payment ID or Tracking Number
- Card details fully visible
- Copy functionality works
- Status matches buyer portal

---

### Phase 4: Payment Status Verification

#### Step 5: Verify Payment Status

1. Return to buyer portal
2. Refresh invoice details page
3. Verify payment status updated:
   - Initial: `card_issued`
   - After GetPaymentDetails check: `completed` or `failed`

**Check Both Portals:**
- **Buyer Portal**: Invoice screen shows payment status
- **Supplier Portal**: Payment status visible

**Expected Result:**
- Status consistent across both portals
- Status reflects actual payment state
- Timestamps recorded correctly

---

### Phase 5: ISO20022 Payment Testing (Alternative Flow)

#### Step 6: Process Payment with ISO20022

1. Upload a large invoice (≥ $5,000) to trigger ISO20022 selection
2. Click **Process Payment**
3. Verify Payment Agent chooses ISO20022 method

**Expected Result:**
- AI reasoning explains why ISO20022 chosen (large amount)
- ISO20022 XML file generated
- File stored in S3 bucket
- Payment status: `completed`
- No virtual card generated

---

## Test Scenarios

### Scenario 1: Small Invoice → Visa B2B

**Setup:**
- Use any PO with amount < $5,000 (e.g., PO-2025-00006, PO-2025-00007)
- Vendor: Check the PO details in the buyer portal
- Create matching BOL and Invoice documents

**Expected Flow:**
1. Upload BOL → Goods Receipt created (10 units)
2. Upload Invoice → 3-way match completed
3. Variance: -80% (partial invoice, warning shown)
4. Click "Process Payment"
5. Payment Agent selects Visa B2B (amount < $5,000)
6. Virtual card generated for $4,500
7. Card displayed in buyer portal
8. Supplier can retrieve card via supplier portal
9. Status: `card_issued` → `completed`

---

### Scenario 2: Large Invoice → ISO20022

**Setup:**
- Use any PO with amount ≥ $5,000 (e.g., PO-2025-00002, PO-2025-00005, PO-2025-00009)
- Vendor: Check the PO details in the buyer portal
- Create matching BOL and Invoice documents

**Expected Flow:**
1. Upload BOL → Goods Receipt created (1000 units)
2. Upload Invoice → 3-way match completed
3. Variance: 0% (exact match)
4. Click "Process Payment"
5. Payment Agent selects ISO20022 (amount ≥ $5,000)
6. XML file generated
7. File stored in S3
8. No virtual card generated
9. Status: `completed`

---

### Scenario 3: Partial Invoice (Less than PO Amount)

**Setup:**
- Use any active PO (e.g., PO-2025-00001 through 00005)
- Create a BOL for partial quantity (e.g., 50% of ordered items)
- Create an Invoice for partial amount (e.g., 50% of PO total)
- Vendor: Match the vendor from the selected PO

**Expected Flow:**
1. Upload BOL → Goods Receipt created (30 units)
2. Upload Invoice → 3-way match completed
3. System calculates variance: -94% (30 units out of 500)
4. Match status: `MISMATCHED` (variance > 10%)
5. Warning displayed: "Invoice amount variance exceeds tolerance: -94%"
6. Payment still allowed (3-way match exists)
7. Click "Process Payment"
8. Payment Agent selects Visa B2B (amount < $5,000)
9. Virtual card generated for $2,550
10. Payment processed successfully

**Note:** Partial invoices are supported for progressive billing or partial deliveries.

---

### Scenario 4: Multiple Partial Invoices for Same PO

**Setup:**
- Select any active PO (e.g., PO-2025-00001)
- Create first partial BOL and Invoice (e.g., 30% of total)
- Create second partial BOL and Invoice (e.g., another 30% of total)
- Vendor: Match the vendor from the selected PO

**Expected Flow:**
1. Upload first partial BOL + Invoice → First payment processed
2. Upload second partial BOL + Invoice → Second payment processed
3. Each invoice gets separate payment record
4. Each payment has unique Payment ID and Tracking Number
5. Payment method depends on invoice amounts (Visa B2B if < $5,000, ISO20022 if ≥ $5,000)
6. Supplier can retrieve each card independently (for Visa B2B payments)
7. PO tracks total billed vs remaining balance

**Note:** This tests progressive billing where multiple partial invoices are submitted for the same PO over time.

---

## Verification Checklist

### Buyer Portal

- [ ] Dashboard displays correctly
- [ ] Invoice upload works
- [ ] Invoice data extracted correctly
- [ ] Process Payment button visible
- [ ] Payment Agent decision displayed
- [ ] Virtual card details shown (masked)
- [ ] Click to reveal CVV works
- [ ] Copy Card Details button works
- [ ] Payment status updates correctly
- [ ] Payment history visible

### Supplier Portal

- [ ] Access with Payment ID works
- [ ] Access with Tracking Number works
- [ ] Invoice details displayed
- [ ] Virtual card details visible (unmasked)
- [ ] CVV visible
- [ ] Copy Card Details button works
- [ ] Payment status displayed
- [ ] Clean, simple interface

### Payment Agent

- [ ] AI decision logic works
- [ ] Reasoning stored and displayed
- [ ] Visa B2B selected for small amounts
- [ ] ISO20022 selected for large amounts
- [ ] ProcessPayments API called successfully
- [ ] GetSecurityCode API called successfully
- [ ] GetPaymentDetails API called successfully
- [ ] Status updated correctly

### Database

- [ ] Payments table populated
- [ ] Virtual card details encrypted
- [ ] Tracking numbers stored
- [ ] Agent reasoning stored
- [ ] Status transitions recorded
- [ ] Timestamps accurate

---

## Troubleshooting

### Issue: Payment Agent Not Responding

**Symptoms:**
- "Process Payment" button does nothing
- No payment record created

**Solutions:**
1. Check Payment Agent Lambda logs in CloudWatch
2. Verify AgentCore Gateway is deployed
3. Check Visa B2B stub APIs are accessible
4. Verify database connection

### Issue: Virtual Card Not Displayed

**Symptoms:**
- Payment processed but no card shown
- Card details missing

**Solutions:**
1. Check ProcessPayments API response in logs
2. Verify GetSecurityCode API called
3. Check database for encrypted card data
4. Verify decryption working correctly

### Issue: Supplier Portal Access Fails

**Symptoms:**
- Invalid Payment ID or Tracking Number error
- Cannot access card details

**Solutions:**
1. Verify Payment ID/Tracking Number copied correctly
2. Check payment exists in database
3. Verify supplier portal API endpoint configured
4. Check database query for payment lookup

### Issue: Status Not Updating

**Symptoms:**
- Status stuck at `card_issued`
- Status not changing to `completed`

**Solutions:**
1. Check GetPaymentDetails API response
2. Verify status code parsing logic
3. Check database update query
4. Verify frontend polling/refresh

---

## API Testing (Optional)

### Test Backend API Directly

**Get Invoices:**
```bash
curl -X GET <api-url>/api/invoices
```

**Process Payment:**
```bash
curl -X POST <api-url>/api/payments/process \
  -H "Content-Type: application/json" \
  -d '{"invoiceId":"<invoice-id>"}'
```

**Get Payment Details:**
```bash
curl -X GET <api-url>/api/payments/<payment-id>
```

**Supplier Access:**
```bash
curl -X POST <api-url>/api/supplier/payment \
  -H "Content-Type: application/json" \
  -d '{"paymentId":"<payment-id>"}'
```

---

## Success Criteria

Your testing is successful when:

✅ **Buyer Portal:**
- Can upload invoices
- Can process payments
- Can view virtual card details
- Can see payment status

✅ **Supplier Portal:**
- Can login with Payment ID or Tracking Number
- Can view complete card details
- Can copy card details
- Can see payment status

✅ **Payment Agent:**
- Makes intelligent payment decisions
- Generates virtual cards for small amounts
- Generates ISO20022 files for large amounts
- Stores reasoning and status

✅ **Integration:**
- Visa B2B stub APIs respond correctly
- AgentCore Gateway translates requests
- Database stores all payment data
- Status updates work correctly

---

## Next Steps

After successful testing:

1. **Production Deployment:**
   - Replace stub APIs with real Visa B2B endpoints
   - Configure production Visa credentials
   - Update AgentCore Gateway configuration
   - Enable production email delivery (replace supplier portal)

2. **Monitoring:**
   - Set up CloudWatch alarms for Lambda errors
   - Monitor payment success rates
   - Track Payment Agent decisions
   - Monitor API response times

3. **Enhancements:**
   - Implement periodic status tracking
   - Add supplier matching service
   - Enable Straight-Through Processing (STP)
   - Add payment notifications

---

## Creating Custom Purchase Orders

If you need to test additional scenarios beyond the pre-seeded POs, you can create custom purchase orders through the buyer portal.

### Step 1: Create Purchase Order via Frontend

1. Navigate to the buyer portal
2. Go to **Purchase Orders** section
3. Click **Create New PO** button (if available)
4. Fill in the PO details:
   - **PO Number**: e.g., PO-2025-00057
   - **Vendor**: Select from existing vendors (Acme Industrial, Global Equipment, or Premier Materials)
   - **Order Date**: Date PO was created
   - **Due Date**: Expected delivery date
   - **Total Amount**: Your custom amount (e.g., $3,000 for Visa B2B testing, $15,000 for ISO20022)
   - **Currency**: USD
   - **Warehouse**: Warehouse A, B, or C
   - **Source Location**: Supplier facility location
   - **Line Items**: Add product descriptions, quantities, unit prices
5. Click **Save** or **Create PO**

**Note:** If the frontend doesn't have a PO creation UI, you'll need to add POs through the database seeder or API. Contact your system administrator for assistance.

### Step 2: Create Matching BOL Document

**⚠️ IMPORTANT: You must create your own BOL PDF that matches the PO**

Your BOL must include:

1. **BOL Number**: Unique identifier (e.g., BOL-2025-007)
2. **PO Number**: Must exactly match your PO (e.g., PO-2025-00057)
3. **Vendor Information**: Name, code, contact details (must match PO vendor)
4. **Ship Date**: Date goods shipped
5. **Delivery Date**: Date goods received
6. **Source Location**: From your PO specification
7. **Destination**: Warehouse from your PO
8. **Items**: Description and quantity (must match or be ≤ PO quantity)
9. **Carrier Information**: Shipping company, tracking number
10. **Signatures**: Shipper and receiver sections

**Template Reference:**
Use the sample BOL template in the Appendix section of this guide as a reference.

### Step 3: Create Matching Invoice Document

**⚠️ IMPORTANT: You must create your own Invoice PDF that matches the PO and BOL**

Your Invoice must include:

1. **Invoice Number**: Unique identifier (e.g., INV-2025-007)
2. **Invoice Date**: Date invoice issued
3. **PO Number**: Must exactly match your PO (e.g., PO-2025-00057)
4. **Vendor Information**: Name, code, contact details, tax ID (must match PO vendor)
5. **Bill To**: Your company information
6. **Line Items**: Description, quantity, unit price, total (must match PO)
7. **Subtotal**: Sum of line items
8. **Tax**: If applicable
9. **Total Amount**: Should match PO total amount (or be proportional for partials)
10. **Payment Terms**: Net 30, Net 60, etc.
11. **Due Date**: Based on payment terms

**Template Reference:**
Use the sample Invoice template in the Appendix section of this guide as a reference.

### Step 4: Test Your Custom PO

1. Navigate to buyer portal
2. Verify your new PO appears in the Purchase Orders list
3. Upload your custom BOL → Goods Receipt created
4. Upload your custom Invoice → 3-way match completed
5. Process payment and verify correct payment method selected:
   - If amount < $5,000 → Visa B2B
   - If amount ≥ $5,000 → ISO20022

### Custom PO Testing Scenarios

**Scenario: Test Visa B2B with Custom Amount**
- Create PO with amount < $5,000 (e.g., $3,000)
- Create matching BOL and Invoice
- Verify Visa B2B payment method selected

**Scenario: Test ISO20022 with Custom Amount**
- Create PO with amount ≥ $5,000 (e.g., $50,000)
- Create matching BOL and Invoice
- Verify ISO20022 payment method selected

**Scenario: Test Partial Invoice with Custom PO**
- Create PO with amount $10,000
- Create BOL for partial quantity (e.g., 50% of items)
- Create Invoice for partial amount (e.g., $5,000)
- Verify variance warning displayed
- Verify payment still processes successfully

### Important Notes

- **PO Number Format**: Must match exactly across PO, BOL, and Invoice
- **Vendor Matching**: Vendor must be identical across all three documents
- **Quantity Validation**: Invoice quantity ≤ GR quantity ≤ PO quantity
- **Amount Tolerance**: System allows 10% variance, shows warning if exceeded
- **Document Format**: PDFs work best for upload and processing
- **Data Extraction**: System uses AI to extract data from documents, ensure text is clear and readable

---

## Support

For issues or questions:

1. Check CloudWatch logs for Lambda functions
2. Review API Gateway logs
3. Check database for payment records
4. Review Payment Agent reasoning in database
5. Consult VISA-B2B-PAYMENT-INTEGRATION.md for architecture details

---

## Appendix: Sample Document Templates

### Sample Bill of Lading (BOL)

```
BILL OF LADING

BOL Number: BOL-2025-001
Date: August 1, 2025

Shipper:
Acme Industrial Supplies
456 Supplier Ave
Vendor City, State 67890
Phone: +1-555-0100

Consignee:
Your Company Name
123 Main Street
City, State 12345

Purchase Order: PO-2025-00052
Source Location: Offshore-Platform-KG12
Destination: Warehouse A

Carrier: ABC Freight Lines
Tracking Number: TRK-ABC-12345

Items Shipped:
- Industrial Chemical Grade A
  Quantity: 1,000 units
  Weight: 10,000 lbs
  Packaging: 40 drums

Ship Date: August 1, 2025
Expected Delivery: August 5, 2025

Shipper Signature: ________________
Receiver Signature: ________________
```

### Sample Invoice

```
INVOICE

Invoice Number: INV-2025-001
Invoice Date: August 5, 2025

Vendor:
Acme Industrial Supplies
456 Supplier Ave
Vendor City, State 67890
Tax ID: 12-3456789
Phone: +1-555-0100
Email: orders@acme-industrial.com

Bill To:
Your Company Name
123 Main Street
City, State 12345

Purchase Order: PO-2025-00052
BOL Reference: BOL-2025-001

Line Items:
Item #  Description                      Qty      Unit Price    Total
1       Industrial Chemical Grade A      1,000    $25.50        $25,500.00

Subtotal:                                                       $25,500.00
Tax (0%):                                                       $0.00
Total:                                                          $25,500.00

Payment Terms: Net 30
Due Date: September 4, 2025

Please remit payment to:
Acme Industrial Supplies
Account: 1234567890
Routing: 987654321
```

### Creating Test Documents

**You need to create your own BOL and Invoice documents** that match the pre-seeded purchase orders.

**For Visa B2B Testing (Small Amounts < $5,000):**
- Select a PO with amount < $5,000 from the buyer portal
- Create matching BOL with partial or full quantities
- Create matching Invoice with amount < $5,000
- Upload BOL first, then Invoice
- Verify Visa B2B payment method is selected

**For ISO20022 Testing (Large Amounts ≥ $5,000):**
- Select a PO with amount ≥ $5,000 from the buyer portal
- Create matching BOL with quantities
- Create matching Invoice with amount ≥ $5,000
- Upload BOL first, then Invoice
- Verify ISO20022 payment method is selected

**Testing Progressive Billing:**
- Select any active PO
- Create first partial BOL and Invoice (e.g., 30% of total)
- Process first payment
- Create second partial BOL and Invoice (e.g., another 30%)
- Process second payment
- Verify each payment has unique Payment ID and Tracking Number
