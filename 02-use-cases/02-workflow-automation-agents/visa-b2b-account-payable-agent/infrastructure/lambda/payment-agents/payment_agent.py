"""
Payment Agent - AI-powered payment processing agent

This agent makes intelligent payment decisions and executes payments through
either Visa B2B Virtual Cards or ISO20022 bank transfers.

Architecture:
- Uses Strands Agents framework
- Deployed to AgentCore Runtime
- Calls AgentCore Gateway for Visa B2B payments
- Falls back to ISO20022 for traditional bank transfers
- NO database access - receives data from backend, returns results
"""

import json
import os
import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

# Initialize AWS clients
bedrock_runtime = boto3.client("bedrock-runtime", region_name="us-east-1")
lambda_client = boto3.client("lambda", region_name="us-east-1")

# Environment variables
GATEWAY_URL = os.environ.get("GATEWAY_URL")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
ISO20022_LAMBDA_ARN = os.environ.get("ISO20022_LAMBDA_ARN")

# Create Bedrock Model for agent
bedrock_model = BedrockModel(
    model_id=BEDROCK_MODEL_ID,
    boto_session=boto3.Session(region_name="us-east-1"),
)

# Create AgentCore app
app = BedrockAgentCoreApp()


# ============================================================================
# Gateway Integration Functions
# ============================================================================


def load_gateway_config():
    """Load Gateway configuration"""
    return {
        "gateway_url": GATEWAY_URL,
        "gateway_region": "us-east-1",
        "service": "bedrock-agentcore",
    }


async def call_gateway_tool(tool_name, input_data):
    """
    Call an AgentCore Gateway MCP tool with SigV4 authentication.

    Args:
        tool_name: Name of the MCP tool (e.g., 'VirtualCardRequisition')
        input_data: Dict of input parameters for the tool

    Returns:
        Tool response data
    """
    import requests
    from requests_aws4auth import AWS4Auth
    import traceback

    config = load_gateway_config()

    # Get AWS credentials
    session = boto3.Session()
    credentials = session.get_credentials()

    # Create AWS4Auth for SigV4 signing
    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        config["gateway_region"],
        config["service"],
        session_token=credentials.token,
    )

    print(f"Calling Gateway tool: {tool_name}")
    print(f"Input: {json.dumps(input_data, indent=2)}")

    try:
        # Prepare MCP request
        mcp_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": input_data},
        }

        # Make HTTP POST request with SigV4 auth
        response = requests.post(
            config["gateway_url"],
            json=mcp_request,
            auth=auth,
            headers={"Content-Type": "application/json"},
        )

        # Check response status
        response.raise_for_status()

        # Parse MCP response
        mcp_response = response.json()

        # Check for MCP errors
        if "error" in mcp_response:
            raise Exception(f"MCP error: {mcp_response['error']}")

        # Extract result
        result = mcp_response.get("result")

        # Check if result indicates an error
        if isinstance(result, dict) and result.get("isError"):
            # Extract error message from content
            error_text = result.get("content", [{}])[0].get("text", "Unknown error")
            raise Exception(f"Gateway tool error: {error_text}")

        print(f"Tool result: {result}")

        return result

    except Exception as e:
        error_msg = f"Error calling Gateway tool {tool_name}: {str(e)}"
        print(error_msg)
        print(f"Error type: {type(e).__name__}")
        print(f"Full traceback: {traceback.format_exc()}")

        # Re-raise with more context
        raise Exception(f"Gateway tool call failed: {tool_name} - {str(e)}") from e


def execute_visa_payment_sync(invoice_data, payment_id):
    """
    Synchronous wrapper for Visa B2B payment execution.
    Executes the full Visa B2B payment flow through Gateway.
    """
    import asyncio
    import traceback

    # Run async function in sync context
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(execute_visa_payment(invoice_data, payment_id))
    except Exception as e:
        error_msg = f"Visa B2B payment failed: {str(e)}"
        print(error_msg)
        print(f"Traceback: {traceback.format_exc()}")
        return {"success": False, "error": error_msg}
    finally:
        loop.close()


async def execute_visa_payment(invoice_data, payment_id):
    """
    Execute Visa B2B payment through AgentCore Gateway.

    Flow:
    1. Call VirtualCardRequisition to create virtual card
    2. Call ProcessPayments to execute payment
    3. Call GetPaymentDetails to check status
    4. Return results to backend for database updates
    """
    try:
        # Step 1: Create virtual card
        print("Step 1: Creating virtual card...")

        # Generate unique message ID for this request
        import uuid

        message_id = str(uuid.uuid4())

        card_request = {
            "messageId": message_id,
            "buyerId": 12345,  # Fixed buyer ID for stub API
            "amount": invoice_data["amount"],
            "currency": invoice_data["currency"],
        }

        card_response = await call_gateway_tool("visa-b2b-stub-api-target___VirtualCardRequisition", card_request)

        # Parse response - now it's a dict from MCP result
        if isinstance(card_response, dict) and "content" in card_response:
            # MCP response format
            card_data = json.loads(card_response["content"][0]["text"])
        else:
            # Direct dict response
            card_data = card_response

        # Extract requisitionId from Visa B2B response
        vcard_response = card_data.get("VCardRequistionResponse", {})
        virtual_card_id = vcard_response.get("requisitionId")
        account_number = vcard_response.get("accountNumber")
        expiration_date = vcard_response.get("expirationDate")

        if not virtual_card_id:
            raise Exception(f"Failed to create virtual card: No requisitionId in response. Response: {card_data}")

        print(f"Virtual card created: {virtual_card_id}")
        print(f"Account number: {account_number}")
        print(f"Expiration: {expiration_date}")

        # Step 2: Process payment
        print("Step 2: Processing payment...")

        payment_request = {
            "messageId": str(uuid.uuid4()),
            "buyerId": 12345,  # Fixed buyer ID for stub API
            "virtualCardId": virtual_card_id,
            "amount": invoice_data["amount"],
        }

        payment_response = await call_gateway_tool("visa-b2b-stub-api-target___ProcessPayments", payment_request)

        # Parse response - now it's a dict from MCP result
        if isinstance(payment_response, dict) and "content" in payment_response:
            # MCP response format
            payment_data = json.loads(payment_response["content"][0]["text"])
        else:
            # Direct dict response
            payment_data = payment_response

        # Extract ProcessResponse from Visa B2B response
        # NOTE: CVV2 is NOT returned by ProcessPayments (per Visa B2B API spec)
        process_response = payment_data.get("ProcessResponse", {})
        transaction_id = process_response.get("trackingNumber")
        card_holder_name = process_response.get("cardHolderName")

        if not transaction_id:
            raise Exception(f"Failed to process payment: No trackingNumber in response. Response: {payment_data}")

        print(f"Payment submitted: {transaction_id}")

        # Step 2.5: Get Security Code (CVV2)
        # CVV2 must be retrieved separately via GetSecurityCode API
        print("Step 2.5: Retrieving CVV2...")

        cvv2_request = {
            "messageId": str(uuid.uuid4()),
            "accountNumber": account_number,
            "expirationDate": expiration_date,
        }

        try:
            cvv2_response = await call_gateway_tool("visa-b2b-stub-api-target___GetSecurityCode", cvv2_request)

            # Parse response - now it's a dict from MCP result
            if isinstance(cvv2_response, dict) and "content" in cvv2_response:
                # MCP response format
                cvv2_data = json.loads(cvv2_response["content"][0]["text"])
            else:
                # Direct dict response
                cvv2_data = cvv2_response

            # Extract CVV2 from response (nested under GetSecurityCodeResponse)
            security_code_response = cvv2_data.get("GetSecurityCodeResponse", {})
            cvv = security_code_response.get("cvv2")

            if not cvv:
                print(f"Warning: Failed to retrieve CVV2. Response: {cvv2_data}")
                cvv = None  # Continue without CVV2
            else:
                print("CVV2 retrieved successfully")

        except Exception as cvv_error:
            print(f"Warning: GetSecurityCode failed: {str(cvv_error)}")
            cvv = None  # Continue without CVV2

        # Step 3: Check payment status
        print("Step 3: Checking payment status...")

        status_request = {
            "messageId": str(uuid.uuid4()),
            "buyerId": 12345,  # Fixed buyer ID for stub API
            "trackingNumber": int(transaction_id) if transaction_id.isdigit() else 12345,
        }

        status_response = await call_gateway_tool("visa-b2b-stub-api-target___GetPaymentDetails", status_request)

        # Parse response - now it's a dict from MCP result
        if isinstance(status_response, dict) and "content" in status_response:
            # MCP response format
            status_data = json.loads(status_response["content"][0]["text"])
        else:
            # Direct dict response
            status_data = status_response

        # Extract GetPaymentResponse from Visa B2B response
        get_payment_response = status_data.get("GetPaymentResponse", {})
        status_code = get_payment_response.get("statusCode", "99")
        status_desc = get_payment_response.get("statusDesc", "Unknown")

        # Visa B2B uses statusCode '00' for success
        payment_status = "completed" if status_code == "00" else "failed"

        print(f"Payment status: {payment_status} (statusCode: {status_code}, statusDesc: {status_desc})")

        # Return results for backend to store in database
        return {
            "success": status_code == "00",
            "virtual_card_id": virtual_card_id,
            "transaction_id": transaction_id,
            "status": payment_status,
            "card_details": {
                "account_number": account_number,
                "expiration_date": expiration_date,
                "cvv": cvv,
                "card_holder_name": card_holder_name,
                "requisition_id": virtual_card_id,
                "tracking_number": transaction_id,
            },
        }

    except Exception as e:
        error_msg = f"Visa B2B payment failed: {str(e)}"
        print(error_msg)
        return {"success": False, "error": error_msg}


# ============================================================================
# ISO20022 Integration Functions
# ============================================================================


def execute_iso20022_payment(invoice_data, payment_id):
    """
    Execute ISO20022 bank transfer payment by invoking the ISO20022 Lambda.

    This function calls the comprehensive ISO20022 Lambda function which handles:
    1. Payment data preparation
    2. ISO20022 XML generation with Bedrock
    3. XML validation
    4. S3 file storage

    Returns file information for backend to store in database.
    """
    try:
        print(f"Invoking ISO20022 Lambda: {ISO20022_LAMBDA_ARN}")

        # Prepare payload for ISO20022 Lambda
        lambda_payload = {"action": "generate_payment", "invoice_data": invoice_data}

        # Invoke ISO20022 Lambda function
        response = lambda_client.invoke(
            FunctionName=ISO20022_LAMBDA_ARN,
            InvocationType="RequestResponse",
            Payload=json.dumps(lambda_payload),
        )

        # Parse Lambda response
        response_payload = json.loads(response["Payload"].read())

        # Check for Lambda errors
        if response["StatusCode"] != 200:
            error_msg = f"ISO20022 Lambda invocation failed with status {response['StatusCode']}"
            print(error_msg)
            raise Exception(error_msg)

        # Check for function errors in response
        if "errorMessage" in response_payload:
            error_msg = f"ISO20022 Lambda error: {response_payload['errorMessage']}"
            print(error_msg)
            raise Exception(error_msg)

        # Extract file information from response
        file_key = response_payload.get("file_key")
        file_url = response_payload.get("file_url")

        if not file_key:
            raise Exception("ISO20022 Lambda did not return file_key")

        print(f"ISO20022 file generated: {file_url}")

        # Return file information for backend to store in database
        return {
            "success": True,
            "file_key": file_key,
            "file_url": file_url,
            "status": "completed",
        }

    except Exception as e:
        error_msg = f"ISO20022 payment generation failed: {str(e)}"
        print(error_msg)
        return {"success": False, "error": error_msg}


# ============================================================================
# Payment Agent Tools
# ============================================================================


def decide_payment_method_tool(invoice_data: dict) -> dict:
    """
    Tool for Payment Agent to decide payment method using Bedrock.

    Args:
        invoice_data: Invoice information including amount, supplier preferences

    Returns:
        dict with payment_method ('visa_b2b' or 'iso20022') and reasoning
    """
    prompt = f"""You are a payment processing agent. Analyze this invoice and decide the best payment method.

Invoice Details:
- Amount: ${invoice_data["amount"]:.2f} {invoice_data["currency"]}
- Supplier: {invoice_data["supplier_name"]}
- Due Date: {invoice_data.get("due_date", "Not specified")}

Supplier Preferences:
- Preferred Payment Method: {invoice_data.get("preferred_payment_method", "None specified")}
- Accepts Virtual Cards: {invoice_data.get("accepts_virtual_cards", True)}

Available Payment Methods:
1. Visa B2B Virtual Card (fast, secure, 2% fee, good for amounts < $5,000)
2. ISO20022 Bank Transfer (slower, no fee, better for large amounts)

Decision Criteria (in priority order):
1. If amount >= $5,000: MUST use ISO20022 (Visa B2B has $5,000 limit)
2. If amount < $5,000 AND supplier accepts virtual cards: Use Visa B2B
3. If amount < $5,000 AND supplier does NOT accept virtual cards: Use ISO20022
4. Consider supplier's preferred payment method as a tiebreaker only

Respond with JSON only (no markdown):
{{
    "payment_method": "visa_b2b" or "iso20022",
    "reasoning": "explanation of decision"
}}"""

    try:
        response = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1000,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                }
            ),
        )

        response_body = json.loads(response["body"].read())
        decision_text = response_body["content"][0]["text"]

        # Clean response (remove markdown if present)
        decision_text = decision_text.strip()
        if decision_text.startswith("```json"):
            decision_text = decision_text.replace("```json", "").replace("```", "").strip()
        elif decision_text.startswith("```"):
            decision_text = decision_text.replace("```", "").strip()

        decision = json.loads(decision_text)

        print(f"Payment decision: {decision['payment_method']}")
        print(f"Reasoning: {decision['reasoning']}")

        return decision

    except Exception as e:
        print(f"Error in payment decision: {str(e)}")
        # Default to ISO20022 on error
        return {
            "payment_method": "iso20022",
            "reasoning": f"Defaulting to ISO20022 due to decision error: {str(e)}",
        }


# ============================================================================
# Payment Agent Definition
# ============================================================================

# Create Payment Agent with tools
payment_agent = Agent(
    model=bedrock_model,
    tools=[decide_payment_method_tool],
    system_prompt="""You are a payment processing agent.
    
Your job is to:
1. Analyze invoice data
2. Decide the best payment method (Visa B2B or ISO20022)
3. Execute the payment
4. Return results to the backend

You do NOT access the database directly. The backend handles all database operations.""",
)


# ============================================================================
# AgentCore Entry Point
# ============================================================================


@app.entrypoint
def invoke(payload):
    """
    Main entry point for payment processing.

    Input payload (from backend):
    {
        "invoice_data": {
            "id": "uuid",
            "invoice_number": "INV-001",
            "amount": 1000.00,
            "currency": "USD",
            "supplier_id": "uuid",
            "supplier_name": "Acme Corp",
            "preferred_payment_method": "visa_b2b",
            "accepts_virtual_cards": true
        },
        "payment_id": "uuid"  # Created by backend
    }

    Output (to backend):
    {
        "status": "success" | "failed",
        "payment_method": "visa_b2b" | "iso20022",
        "reasoning": "agent decision explanation",
        "virtual_card_id": "...",  # if Visa B2B
        "transaction_id": "...",   # if Visa B2B
        "card_details": {...},     # if Visa B2B (backend will encrypt)
        "file_key": "...",         # if ISO20022
        "file_url": "...",         # if ISO20022
        "error": "..."             # if failed
    }
    """
    try:
        invoice_data = payload.get("invoice_data")
        payment_id = payload.get("payment_id")

        if not invoice_data:
            return {"status": "failed", "error": "invoice_data is required"}

        if not payment_id:
            return {"status": "failed", "error": "payment_id is required"}

        print(f"Processing payment for invoice: {invoice_data.get('id')}")
        print(f"Payment ID: {payment_id}")

        # Use agent to decide payment method
        decision = decide_payment_method_tool(invoice_data)
        payment_method = decision["payment_method"]
        reasoning = decision["reasoning"]

        # Execute payment based on method
        if payment_method == "visa_b2b":
            print("Executing Visa B2B payment...")
            visa_result = execute_visa_payment_sync(invoice_data, payment_id)

            return {
                "status": "success" if visa_result["success"] else "failed",
                "payment_method": payment_method,
                "reasoning": reasoning,
                **visa_result,  # Include all visa result fields
            }

        elif payment_method == "iso20022":
            print("Executing ISO20022 payment...")
            iso_result = execute_iso20022_payment(invoice_data, payment_id)

            return {
                "status": "success" if iso_result["success"] else "failed",
                "payment_method": payment_method,
                "reasoning": reasoning,
                **iso_result,  # Include all iso result fields
            }

        else:
            error_msg = f"Unknown payment method: {payment_method}"
            print(error_msg)

            return {
                "status": "failed",
                "payment_method": payment_method,
                "reasoning": reasoning,
                "error": error_msg,
            }

    except Exception as e:
        error_msg = f"Error processing payment: {str(e)}"
        print(error_msg)
        return {"status": "failed", "error": error_msg}


if __name__ == "__main__":
    # Run the AgentCore app
    app.run()
