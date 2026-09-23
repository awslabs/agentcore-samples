# Payment Agent - AgentCore Runtime

AI-powered payment processing agent that intelligently decides between Visa B2B Virtual Cards and ISO20022 bank transfers.

## Architecture

- **Framework**: Strands Agents
- **Runtime**: Amazon Bedrock AgentCore Runtime
- **Model**: Claude Sonnet 4 (via Bedrock)
- **Gateway**: AgentCore Gateway for Visa B2B API calls

## Files

- `payment_agent.py` - Main agent implementation
- `streamable_http_sigv4.py` - SigV4 authentication helper for Gateway calls
- `requirements.txt` - Python dependencies

## Environment Variables

Required environment variables:

```bash
GATEWAY_URL=<AgentCore Gateway URL>
DATABASE_SECRET_ARN=<RDS credentials ARN>
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0
RTP_API_URL=<Backend API URL>
```

## Agent Flow

1. **Receive Request**: Invoice ID from backend Lambda
2. **Get Invoice Data**: Query database for invoice and supplier info
3. **AI Decision**: Use Bedrock to decide payment method
4. **Create Payment Record**: Write to DB (status='pending', includes reasoning)
5. **Update Invoice**: Link invoice to payment
6. **Execute Payment**: 
   - **Visa B2B**: Call Gateway tools with SigV4 auth
     - Step 1: `VirtualCardRequisition` - Create virtual card
     - Step 2: Update DB with virtual_card_id
     - Step 3: `ProcessPayments` - Execute payment
     - Step 4: Update DB status to 'submitted'
     - Step 5: `GetPaymentDetails` - Check payment status
     - Step 6: Update DB status to 'completed' or 'failed'
   - **ISO20022**: Call ISO20022 generation function
7. **Update Status**: Mark payment as completed/failed

## Gateway Integration

The agent uses **AgentCore Gateway** to call Visa B2B APIs as MCP tools:

### Authentication
- **Method**: AWS SigV4 (IAM-based)
- **Service**: `bedrock-agentcore`
- **Helper**: `streamable_http_sigv4.py` (from AgentCore samples)

### MCP Tools Available
1. **VirtualCardRequisition** - Creates virtual card for payment
   - Input: `{amount, currency, supplier_id}`
   - Output: `{virtual_card: {card_id, card_number, cvv, expiry_date}}`

2. **ProcessPayments** - Executes payment with virtual card
   - Input: `{virtual_card_id, amount, currency, supplier_account}`
   - Output: `{payment_id, transaction_id, status}`

3. **GetPaymentDetails** - Checks payment status
   - Input: `{payment_id}`
   - Output: `{payment_id, status, completion_date}`

### Gateway Configuration
Gateway URL and credentials are loaded from:
- Environment variable: `GATEWAY_URL`
- AWS credentials from Lambda execution role
- Region: `us-east-1`

## ISO20022 Integration

The agent can fall back to **ISO20022 bank transfers** when:
- Supplier doesn't accept virtual cards
- Payment amount is large (>= $5,000)
- Visa B2B payment fails

### ISO20022 Flow
1. **Prepare Payment Data** - Convert invoice to ISO20022 format
2. **Generate XML** - Use Bedrock to create pain.001.001.03 XML
3. **Validate XML** - Ensure compliance with ISO20022 standard
4. **Save to S3** - Store XML file in `iso20022/` folder
5. **Update Database** - Record file location and mark as completed

### File Output
- **Bucket**: Configured via `OUTPUT_BUCKET` environment variable
- **Path**: `s3://{bucket}/iso20022/{timestamp}_pain001_{uuid}.xml`
- **Format**: ISO20022 pain.001.001.03 (ACH - U.S. domestic)
- **Metadata**: Includes payment_id, invoice_id, amount, timestamp

### Integration Method
The agent generates ISO20022 XML directly using Bedrock:
- Uses the same Bedrock prompt as the ISO20022 Lambda
- Generates pain.001.001.03 XML format (ACH - U.S. domestic)
- Validates and saves to S3
- No cross-Lambda dependencies required

## Database Write Sequence (CRITICAL)

The agent MUST write to the database BEFORE executing the payment:

```
1. INSERT into payments (status='pending', agent_reasoning=...)
2. UPDATE invoices (payment_method=..., payment_id=...)
3. Execute payment (Visa B2B or ISO20022)
4. UPDATE payments (status='submitted' → 'completed'/'failed')
```

This ensures:
- Audit trail of agent decisions
- Payment lifecycle visibility from the start
- Manual review capability for failed payments

## Deployment

### Prerequisites

1. **Install AgentCore Starter Toolkit**:
   ```bash
   pip install bedrock-agentcore-starter-toolkit
   ```

2. **Set Environment Variables**:
   ```bash
   # Copy template and fill in values
   cp .env.template .env
   
   # Or export directly
   export GATEWAY_URL=<from gateway config>
   export DATABASE_SECRET_ARN=<your-secret-arn>
   export OUTPUT_BUCKET=<your-bucket-name>
   export RTP_API_URL=<your-api-url>
   ```

3. **Ensure Gateway is Deployed**:
   ```bash
   # Check gateway status
   python3 ../scripts/test-gateway-tools.py
   ```

### Deploy to AgentCore Runtime

Run the deployment script:

```bash
./infrastructure/deploy-payment-agent.sh
```

Or deploy manually:

```bash
cd infrastructure/lambda/payment-agents

# Configure agent
agentcore configure \
  --entrypoint payment_agent.py \
  --requirements requirements.txt \
  --region us-east-1 \
  --agent-name visa-b2b-payment-agent \
  --auto-create-execution-role \
  --auto-create-ecr

# Launch to AgentCore Runtime
agentcore launch

# Test invocation
agentcore invoke '{
  "invoice_id": "test-invoice-id",
  "action": "process_payment"
}'
```

### Verify Deployment

1. **Check Runtime Status**:
   ```bash
   agentcore list
   ```

2. **Test Payment Processing**:
   ```bash
   agentcore invoke '{
     "invoice_id": "existing-invoice-id",
     "action": "process_payment"
   }'
   ```

3. **Check Logs**:
   ```bash
   # View CloudWatch logs for the agent
   aws logs tail /aws/bedrock-agentcore/visa-b2b-payment-agent --follow
   ```

## Implementation Status

### ✅ Completed (Task 5.1-5.6)
- [x] Lambda structure created
- [x] Strands Agent initialized
- [x] Bedrock decision logic implemented
- [x] Database operations (create, update payment records)
- [x] Gateway integration with SigV4 authentication
- [x] Visa B2B payment flow (VirtualCardRequisition → ProcessPayments → GetPaymentDetails)
- [x] ISO20022 fallback implementation

### 🚧 In Progress
- [ ] Error handling and retry logic (Task 5.7)
- [ ] Environment configuration (Task 5.8)
- [ ] Deployment script (Task 5.9)

## Testing

Local testing (requires AWS credentials):

```python
from payment_agent import invoke

result = invoke({
    'invoice_id': 'test-invoice-id',
    'action': 'process_payment'
})

print(result)
```

## Next Steps

1. Implement Gateway integration (Task 5.5)
2. Add ISO20022 fallback (Task 5.6)
3. Add error handling and retry logic (Task 5.7)
4. Configure environment variables (Task 5.8)
5. Create deployment script (Task 5.9)


## ISO20022 Integration

The agent can fall back to ISO20022 bank transfers when Visa B2B is not suitable:

### Implementation
- Uses Bedrock to generate ISO20022 pain.001.001.03 XML directly
- Saves XML file to S3 bucket
- Updates payment record with file location
- Marks payment as completed

### When ISO20022 is Used
- Large amounts (>= $5,000)
- Supplier doesn't accept virtual cards
- Supplier prefers ISO20022
- Visa B2B payment fails (fallback)

### Generated File
- Format: `YYYYMMDD_HHMMSS_pain001_XXXXXXXX.xml`
- Location: `s3://{OUTPUT_BUCKET}/iso20022/{filename}`
- Standard: ISO20022 pain.001.001.03 (ACH - U.S. domestic)

## Implementation Status

### ✅ Completed (Task 5.1-5.6, 5.8-5.9)
- [x] Lambda structure created (Task 5.1)
- [x] Strands Agent initialized (Task 5.2)
- [x] Bedrock decision logic implemented (Task 5.3)
- [x] Database operations (create, update payment records) (Task 5.4)
- [x] Gateway integration with SigV4 authentication (Task 5.5)
- [x] Visa B2B payment flow (VirtualCardRequisition → ProcessPayments → GetPaymentDetails) (Task 5.5)
- [x] ISO20022 fallback implementation (generates XML with Bedrock, saves to S3) (Task 5.6)
- [x] Environment configuration template (.env.template) (Task 5.8)
- [x] Deployment script (deploy-payment-agent.sh) (Task 5.9)

### ⏭️ Skipped
- Error handling and retry logic (Task 5.7) - Skipped per user request

### 🎯 Ready for Deployment
The Payment Agent is complete and ready to deploy to AgentCore Runtime!
