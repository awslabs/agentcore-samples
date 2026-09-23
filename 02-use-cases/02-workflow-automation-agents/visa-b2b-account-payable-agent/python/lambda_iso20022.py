"""
ISO20022 Lambda - Refactored for Visa B2B Payment Integration

CHANGES (Task 4 - Refactoring):
1. Invoice flow (PDF/images): Extract data → Save to DB → STOP (Payment Agent decides payment method)
2. CSV/JSON flow: Generate ISO20022 file immediately (preserved for bulk processing)
3. New function: generate_iso20022_payment() - Callable by Payment Agent when it decides to use ISO20022
4. Helper function: _process_and_generate_payment_file() - Reusable ISO20022 generation logic

FLOWS:
- Invoice Upload (PDF/image) → Extract → Save to DB → STOP (no payment file)
- CSV/JSON Upload → Generate ISO20022 file immediately (bulk processing preserved)
- Payment Agent → Calls generate_iso20022_payment() if ISO20022 chosen for invoice
"""

import json
import boto3
from datetime import datetime
import uuid
import os
import base64
import urllib3
# import urllib.parse
# XML imports removed - Bedrock generates XML directly

s3 = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")
secrets_client = boto3.client("secretsmanager")
http = urllib3.PoolManager()

# Configuration from environment variables
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "iso20022-output-bucket")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "YOUR COMPANY")
COMPANY_ID = os.environ.get("COMPANY_ID", "1234567890")
RTP_API_URL = os.environ.get("RTP_API_URL", "https://api.rtp-overlay.com")
API_KEY_SECRET_NAME = os.environ.get("API_KEY_SECRET_NAME", "rtp-overlay/api-key")
# Bedrock Model Configuration - Sonnet 4 supports PDF natively
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# --- ACH-specific configuration (REQUIRED for US domestic) ---
COMPANY_BANK_NAME = os.environ.get("COMPANY_BANK_NAME", "Bank of America, N.A.")
COMPANY_ROUTING_ABA = os.environ.get("COMPANY_ROUTING_ABA", "026009593")  # 9-digit ABA
COMPANY_ACCOUNT_NUM = os.environ.get("COMPANY_ACCOUNT_NUM", "001234567890")  # Debtor account number

# Optional for wires/cross-border (leave blank by default for ACH)
COMPANY_BIC = os.environ.get("COMPANY_BIC", "")
COMPANY_IBAN = os.environ.get("COMPANY_IBAN", "")


# No schema needed - Bedrock generates XML directly


def get_api_key():
    """Retrieve API key from AWS Secrets Manager"""
    try:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_NAME)
        secret = json.loads(response["SecretString"])
        return secret.get("apiKey")
    except Exception as e:
        print(f"Error retrieving API key: {str(e)}")
        raise


def extract_job_id_from_key(file_key):
    """Extract job ID from S3 file key metadata or filename pattern"""
    # Job ID might be in the filename or we'll need to query by source_file_key
    # For now, return None and we'll handle it in the API
    return None


def update_job_status(job_id, status, result_id=None, error_message=None):
    """Update document job status via API"""
    if not job_id:
        return

    try:
        payload = {"status": status}

        if result_id:
            payload["resultId"] = result_id

        if error_message:
            payload["errorMessage"] = error_message

        headers = {"Content-Type": "application/json"}

        url = f"{RTP_API_URL}/api/document-jobs/{job_id}"

        response = http.request("PATCH", url, body=json.dumps(payload), headers=headers)

        if response.status == 200:
            print(f"Job {job_id} status updated to {status}")
        else:
            print(f"Warning: Failed to update job status: {response.status}")

    except Exception as e:
        print(f"Warning: Error updating job status: {str(e)}")


def transform_extracted_data(bedrock_data):
    """Transform Bedrock extracted data into the format expected by the backend API"""

    # The backend expects this structure:
    # {
    #   supplier: { name, address_lines, iban, routing_aba, account_number },
    #   invoice: { number, date, due_date, currency, total, subtotal, tax_amount },
    #   line_items: [{ description, quantity, unit_price, amount }],
    #   bill_to: { name, address_lines }
    # }
    # All numeric fields (total, subtotal, tax_amount, quantities, prices) must be floats

    # Helper function to safely convert to float
    def safe_float(value, default=0.0):
        """Convert value to float, handling strings and invalid values"""
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    # Helper function to clean vendor names
    def clean_vendor_name(name):
        """
        Strip vendor codes in parentheses from vendor names.
        Example: "Global Parts Ltd (GLOBAL003)" -> "Global Parts Ltd"
        """
        import re

        if not name:
            return name
        # Remove anything in parentheses at the end of the string
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
        return cleaned

    # Extract invoice data with proper type conversion
    invoice_data = bedrock_data.get("invoice", {})

    # Get supplier name and clean it
    supplier_name = bedrock_data.get("supplier", {}).get("name", "")
    cleaned_supplier_name = clean_vendor_name(supplier_name)

    if supplier_name != cleaned_supplier_name:
        print(f"Cleaned vendor name: '{supplier_name}' -> '{cleaned_supplier_name}'")

    transformed = {
        "supplier": {
            "name": cleaned_supplier_name,
            "address_lines": bedrock_data.get("supplier", {}).get("address_lines", []),
            "iban": bedrock_data.get("supplier", {}).get("iban", ""),
            "routing_aba": bedrock_data.get("supplier", {}).get("routing_aba", ""),
            "account_number": bedrock_data.get("supplier", {}).get("account_number", ""),
        },
        "invoice": {
            "number": invoice_data.get("number", ""),
            "date": invoice_data.get("date", ""),
            "due_date": invoice_data.get("due_date", ""),
            "currency": invoice_data.get("currency", "USD"),
            "total": safe_float(invoice_data.get("total", 0)),
            "subtotal": safe_float(invoice_data.get("subtotal", 0)),
            "tax_amount": safe_float(invoice_data.get("tax_amount", 0)),
            "line_items": [
                {
                    "description": item.get("description", ""),
                    "quantity": safe_float(item.get("quantity", 0)),
                    "unit_price": safe_float(item.get("unit_price", 0)),
                    "amount": safe_float(item.get("amount", 0)),
                }
                for item in bedrock_data.get("invoice", {}).get("line_items", [])
            ],
        },
        "bill_to": {
            "name": bedrock_data.get("bill_to", {}).get("name", ""),
            "address_lines": bedrock_data.get("bill_to", {}).get("address_lines", []),
        },
    }

    return transformed


def send_invoice_to_api(extracted_data, source_file_key, iso20022_file_key=None, job_id=None):
    """Send extracted invoice data to RTP API"""
    try:
        # Transform the extracted data into the expected format
        transformed_data = transform_extracted_data(extracted_data)

        # Debug: Log the transformed data to see what we're sending
        print(f"Transformed invoice data: {json.dumps(transformed_data.get('invoice', {}), indent=2)}")

        payload = {"extractedData": transformed_data, "sourceFileKey": source_file_key}

        # Include ISO 20022 file key if provided
        if iso20022_file_key:
            payload["iso20022FileKey"] = iso20022_file_key

        # Include job ID for tracking
        if job_id:
            payload["jobId"] = job_id

        headers = {"Content-Type": "application/json"}

        url = f"{RTP_API_URL}/api/invoices"

        print(f"Sending invoice data to API: {url}")
        print(
            f"DEBUG - Payload invoice.total value: {payload.get('extractedData', {}).get('invoice', {}).get('total')}"
        )
        print(
            f"DEBUG - Payload invoice.total type: {type(payload.get('extractedData', {}).get('invoice', {}).get('total'))}"
        )

        response = http.request("POST", url, body=json.dumps(payload), headers=headers)

        if response.status == 201:
            print("Invoice successfully created in database")
            return json.loads(response.data.decode("utf-8"))
        elif response.status == 409:
            print("Invoice already exists in database")
            # Parse the response to get the existing invoice ID if available
            try:
                error_response = json.loads(response.data.decode("utf-8"))
                invoice_id = error_response.get("invoiceId")
                if invoice_id:
                    print(f"Found existing invoice ID: {invoice_id}")
                    return {
                        "status": "duplicate",
                        "id": invoice_id,
                        "message": "Invoice already exists",
                    }
                else:
                    return {"status": "duplicate", "message": "Invoice already exists"}
            except Exception as e:
                print(f"duplicate: Invoice already exists {str(e)}")
                return {"status": "duplicate", "message": "Invoice already exists"}
        else:
            error_msg = f"API returned status {response.status}: {response.data.decode('utf-8')}"
            print(f"Error creating invoice: {error_msg}")
            raise Exception(error_msg)

    except Exception as e:
        print(f"Error sending invoice to API: {str(e)}")
        raise


def extract_invoice_data_with_vision(file_content, file_type, bucket=None, key=None):
    """Use Bedrock (Claude Sonnet 4) to extract invoice data from PDF or images"""

    # Handle both PDF and images with Claude Sonnet 4 vision
    # Encode file content to base64
    file_base64 = base64.b64encode(file_content).decode("utf-8")

    # Determine media type (Sonnet 4 supports PDF + images)
    media_type_map = {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    media_type = media_type_map.get(file_type.lower(), "image/jpeg")

    prompt = """
You are extracting PAYMENT fields from a U.S. supplier invoice image or PDF.

Return ONLY JSON with this schema (no markdown, no commentary):

{
  "supplier": {
    "name": "...",
    "address_lines": ["...", "...", "..."],
    "email": "...",
    "phone": "..."
  },
  "invoice": {
    "number": "...",
    "date": "YYYY-MM-DD",
    "due_date": "YYYY-MM-DD",
    "currency": "USD",
    "line_items": [
      {"description":"...", "quantity": 1, "unit_price": 0.00, "amount": 0.00}
    ],
    "subtotal": 0.00,
    "tax_amount": 0.00,
    "total": 0.00
  },
  "payment": {
    "bank_name": "...",
    "account_name": "...",
    "account_number": "...",
    "routing_aba": "...",            // REQUIRED for U.S. domestic
    "swift_bic": "...",              // OPTIONAL for wires; may be blank
    "iban": ""                       // MUST be blank for U.S. domestic
  },
  "bill_to": {
    "name": "ACME Corporation",
    "address_lines": ["456 Business Plaza", "New York, NY 10001", "US"]
  }
}

Rules:
- If the invoice looks U.S. domestic, set "iban" to "" and provide "routing_aba" (9 digits).
- Use currency "USD" unless the invoice clearly shows another currency.
- If any field is missing on the page, set it to "" (do NOT invent values).
"""

    # Build content based on file type
    if file_type.lower() == "pdf":
        # PDF uses document type for Sonnet 4
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": file_base64},
        }
    else:
        # Images use image type
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": file_base64},
        }

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [content_block, {"type": "text", "text": prompt}],
            }
        ],
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            body=json.dumps(payload),
        )

        response_body = json.loads(response.get("body").read().decode("utf-8"))
        extracted_data = response_body.get("content", [])[0].get("text", "")

        return extracted_data

    except Exception as e:
        print(f"Error extracting invoice data: {str(e)}")
        raise


def generate_iso20022_xml_with_bedrock(payment_data, is_csv=False, is_invoice=False):
    """Use Bedrock to generate ISO 20022 XML directly"""

    if is_invoice:
        data_section = f"""Extracted Invoice Data:
{payment_data}

Use this extracted invoice information to create the payment."""
    elif is_csv:
        data_section = f"""Payment Details (CSV format):
{payment_data}

Parse this CSV data where each row represents a payment transaction."""
    else:
        data_section = f"""Payment Details (JSON format):
{json.dumps(payment_data, indent=2)}"""

    prompt = (
        f"""
Generate a complete ISO 20022 pain.001.001.03 XML (ACH — U.S. domestic) for the following payment instruction.

Input Data (JSON):
{json.dumps(payment_data, indent=2) if not is_invoice and not is_csv else data_section}

Fixed Configuration (ACH only):
- Initiating Party (Dbtr/InitgPty.Nm): "{COMPANY_NAME}"
- Initiating Party ID: {COMPANY_ID}
- Debtor Bank:
  - Name: {COMPANY_BANK_NAME}
  - ABA routing (…Agt/FinInstnId/MmbId): {COMPANY_ROUTING_ABA}
- Debtor Account (…Acct/Id/Othr/Id): {COMPANY_ACCOUNT_NUM}
- Currency: USD
- Message version: pain.001.001.03
- Dates must be ISO (YYYY-MM-DD or YYYY-MM-DDThh:mm:ssZ)

**ACH RULES (MANDATORY)**
1) DO NOT use IBAN in any element. Never create "US…" IBANs.
2) DO NOT use SEPA, SCT, or <SvcLvl><Cd>SEPA</Cd>.
3) Use <PmtMtd>TRF</PmtMtd>.
4) Use <PmtTpInf> with:
   <LclInstrm><Prtry>ACH</Prtry></LclInstrm>
   <CtgyPurp><Prtry>SUPP</Prtry></CtgyPurp>
5) For agents (DbtrAgt, CdtrAgt):
   <FinInstnId>
     <ClrSysId><Prtry>ABA</Prtry></ClrSysId>
     <MmbId>9-digit ABA</MmbId>
     <Nm>Bank Name</Nm>
   </FinInstnId>
   (You may include BIC as well ONLY in addition, not instead, of ABA.)
6) For accounts:
   - Debtor account (DbtrAcct): <Id><Othr><Id>"""
        + COMPANY_ACCOUNT_NUM
        + """</Id><SchmeNm><Prtry>ACCT</Prtry></SchmeNm></Othr></Id>
   - Creditor account (CdtrAcct): use the supplier account from input JSON at payment.account_number with the same ACCT scheme.
   NEVER use <IBAN> for U.S. domestic.
7) Include:
   - <ChrgBr>SLEV</ChrgBr>
   - Optional <BtchBookg>true</BtchBookg> if a batch is implied
**REMITTANCE (RmtInf)**
Include BOTH unstructured and structured remittance:

A) Unstructured (multiple <Ustrd> lines):
- First line: the invoice number (e.g., "INV-2025-102").
- Then one <Ustrd> per line item using this exact format:
  "<Description> | Qty <quantity> x <unit_price> = <line_amount>"
  Example: "CNC Spindle Assembly | Qty 2 x 8,200.00 = 16,400.00"
- Final line: "Subtotal <amount> | Tax <amount> | Total <amount>"
- Keep each <Ustrd> under ~120 characters. If necessary, truncate descriptions with "…".
- Use ASCII-safe characters only (no curly quotes or special symbols).

B) Structured:
<Strd>
  <RfrdDocInf>
    <Tp><CdOrPrtry><Cd>CINV</Cd></CdOrPrtry></Tp>
    <Nb>INV-…</Nb>
    <RltdDt>YYYY-MM-DD</RltdDt>
  </RfrdDocInf>
  <CdtrRefInf>
    <Tp><CdOrPrtry><Cd>SCOR</Cd></CdOrPrtry></Tp>
    <Ref>INV-…</Ref>
  </CdtrRefInf>
</Strd>

**IDs**
- <GrpHdr>:
  - <MsgId>MSG-YYYYMMDD-HHMMSS</MsgId>
  - <CreDtTm>current ISO datetime</CreDtTm>
  - <NbOfTxs> and <CtrlSum> must match content
- <PmtInf>:
  - <PmtInfId>PMT-YYYYMMDD</PmtInfId>
- <CdtTrfTxInf>/<PmtId>/<EndToEndId>: E2E-YYYYMMDD-XXX

**Output**
- Return ONLY the XML (no markdown fences, no commentary).
- The root element must be: <Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03" ...>
- The XML must be well-formed and indented.
"""
    )

    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "temperature": 0,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }

    try:
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            body=json.dumps(payload),
        )

        response_body = json.loads(response.get("body").read().decode("utf-8"))
        xml_content = response_body.get("content", [])[0].get("text", "")

        # Clean XML output (remove markdown if present)
        xml_content = xml_content.strip()
        if xml_content.startswith("```xml"):
            xml_content = xml_content.replace("```xml", "").replace("```", "").strip()
        elif xml_content.startswith("```"):
            xml_content = xml_content.replace("```", "").strip()

        # Clean up common XML issues
        # Replace smart quotes with regular quotes
        xml_content = xml_content.replace('"', '"').replace('"', '"')
        xml_content = xml_content.replace(""", "'").replace(""", "'")
        # Replace em dashes and other special characters
        xml_content = xml_content.replace("—", "-").replace("–", "-")
        # Remove any non-printable characters except newlines and tabs
        xml_content = "".join(char for char in xml_content if char.isprintable() or char in "\n\t")

        return xml_content

    except Exception as e:
        print(f"Error calling Bedrock: {str(e)}")
        raise


# XML formatting now handled by Bedrock


def validate_iso20022_xml(xml_content):
    """Basic validation of ISO 20022 XML"""
    errors = []

    # Check if it's valid XML
    try:
        from xml.etree import ElementTree as ET

        ET.fromstring(xml_content.encode("utf-8"))  # nosec: Internal xml being build
    except Exception as e:
        error_msg = f"Invalid XML: {str(e)}"
        errors.append(error_msg)

        # Try to show the problematic line
        try:
            lines = xml_content.split("\n")
            # Extract line number from error message
            import re

            match = re.search(r"line (\d+)", str(e))
            if match:
                line_num = int(match.group(1))
                if 0 < line_num <= len(lines):
                    print(f"Problematic line {line_num}: {lines[line_num - 1]}")
                    # Show context (2 lines before and after)
                    start = max(0, line_num - 3)
                    end = min(len(lines), line_num + 2)
                    print("Context:")
                    for i in range(start, end):
                        marker = ">>>" if i == line_num - 1 else "   "
                        print(f"{marker} {i + 1}: {lines[i]}")
        except Exception as e:
            print(f"Exception: {str(e)}")
            pass

        return False, errors

    # Check for required elements
    required_elements = [
        "Document",
        "CstmrCdtTrfInitn",
        "GrpHdr",
        "PmtInf",
        "CdtTrfTxInf",
    ]

    for elem in required_elements:
        if elem not in xml_content:
            errors.append(f"Missing required element: {elem}")

    return len(errors) == 0, errors


def generate_unique_filename():
    """Generate unique filename for ISO 20022 file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    return f"{timestamp}_pain001_{unique_id}.xml"


def generate_iso20022_payment(invoice_data):
    """
    Callable function to generate ISO20022 payment file from invoice data.
    This function can be called by the Payment Agent when it decides to use ISO20022.

    Args:
        invoice_data: Dict containing invoice and payment information

    Returns:
        Dict with:
            - file_key: S3 key where the ISO20022 file was saved
            - file_url: S3 URL to the file
            - transaction_count: Number of transactions
            - total_amount: Total payment amount
    """
    try:
        print("Generating ISO20022 payment file from invoice data...")

        # Generate ISO 20022 XML
        xml_content = generate_iso20022_xml_with_bedrock(invoice_data, is_csv=False, is_invoice=True)

        # Validate XML
        is_valid, errors = validate_iso20022_xml(xml_content)

        if not is_valid:
            error_msg = f"ISO 20022 validation failed: {', '.join(errors)}"
            print(error_msg)
            raise Exception(error_msg)

        print("ISO 20022 XML validated successfully")

        # Generate output filename
        filename = generate_unique_filename()
        output_key = f"iso20022/{filename}"

        # Calculate total amount
        import re

        amounts = re.findall(r"<InstdAmt[^>]*>([0-9.]+)</InstdAmt>", xml_content)
        total_amount = sum(float(amt) for amt in amounts) if amounts else 0

        # Save ISO 20022 file to S3
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=output_key,
            Body=xml_content,
            ContentType="application/xml",
            Metadata={
                "source_format": "invoice",
                "transaction_count": "1",
                "total_amount": str(total_amount),
                "generated_at": datetime.now().isoformat(),
                "generated_by": "payment-agent-iso20022",
                "message_standard": "ISO20022-pain.001.001.03",
                "validation_status": "passed",
            },
        )

        print(f"ISO 20022 file saved to: s3://{OUTPUT_BUCKET}/{output_key}")

        return {
            "file_key": output_key,
            "file_url": f"s3://{OUTPUT_BUCKET}/{output_key}",
            "transaction_count": 1,
            "total_amount": total_amount,
            "filename": filename,
        }

    except Exception as e:
        error_msg = f"Error generating ISO20022 payment: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)


def _process_and_generate_payment_file(payment_data, is_csv, is_invoice, key, job_id):
    """
    Internal helper to generate ISO20022 payment file.
    Separated for reuse by both S3 trigger and callable function.
    """
    transaction_count = (
        1
        if is_invoice
        else (
            len([line for line in payment_data.split("\n") if line.strip()]) - 1
            if is_csv
            else len(payment_data.get("transactions", []))
        )
    )

    print(f"Payment data loaded: ~{transaction_count} transactions")

    # Generate ISO 20022 XML directly with Bedrock
    print("Calling Bedrock to generate ISO 20022 XML...")
    xml_content = generate_iso20022_xml_with_bedrock(payment_data, is_csv=is_csv, is_invoice=is_invoice)

    print("XML content received from Bedrock")

    # Validate XML
    is_valid, errors = validate_iso20022_xml(xml_content)

    if not is_valid:
        error_msg = f"ISO 20022 validation failed: {', '.join(errors)}"
        print(error_msg)
        raise Exception(error_msg)

    print("ISO 20022 XML validated successfully")

    # Generate output filename
    filename = generate_unique_filename()
    output_key = f"iso20022/{filename}"

    # Calculate totals for metadata
    if is_invoice or is_csv:
        # For invoices and CSV, extract from generated XML
        import re

        amounts = re.findall(r"<InstdAmt[^>]*>([0-9.]+)</InstdAmt>", xml_content)
        total_amount = sum(float(amt) for amt in amounts) if amounts else 0
    else:
        # For JSON, extract from payment_data
        transactions = payment_data.get("transactions", [])
        total_amount = sum(float(t.get("amount", 0)) for t in transactions)

    # Save ISO 20022 file to S3
    s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=output_key,
        Body=xml_content,
        ContentType="application/xml",
        Metadata={
            "source_file": key,
            "source_format": "invoice" if is_invoice else ("csv" if is_csv else "json"),
            "transaction_count": str(transaction_count),
            "total_amount": str(total_amount),
            "generated_at": datetime.now().isoformat(),
            "generated_by": "bedrock-iso20022-vision" if is_invoice else "bedrock-iso20022",
            "message_standard": "ISO20022-pain.001.001.03",
            "validation_status": "passed",
        },
    )

    print(f"ISO 20022 file saved to: s3://{OUTPUT_BUCKET}/{output_key}")

    return {
        "output_key": output_key,
        "filename": filename,
        "transaction_count": transaction_count,
        "total_amount": total_amount,
    }


def lambda_handler(event, context):
    """
    Lambda handler for payment file processing.

    Supports two invocation modes:
    1. S3 Event-driven (automatic processing of uploaded files)
    2. Direct invocation from Payment Agent (action: 'generate_payment')

    REFACTORED BEHAVIOR:
    - Invoices (PDF/images): Extract data → Save to DB → NO payment file (Payment Agent decides)
    - CSV/JSON: Generate ISO20022 file immediately (bulk payment processing)
    - Direct call: Generate ISO20022 file from invoice data (Payment Agent decision)
    """
    job_id = None

    try:
        # Check if this is a direct invocation from Payment Agent
        if "action" in event and event["action"] == "generate_payment":
            print("Direct invocation from Payment Agent detected")
            invoice_data = event.get("invoice_data")

            if not invoice_data:
                raise Exception("invoice_data is required for generate_payment action")

            # Call the generate_iso20022_payment function
            result = generate_iso20022_payment(invoice_data)

            # Return result directly (not wrapped in body) for Lambda-to-Lambda invocation
            return result

        # Otherwise, handle as S3 event
        for record in event.get("Records", []):
            bucket = record["s3"]["bucket"]["name"]
            key = record["s3"]["object"]["key"]

            # URL decode the key (S3 events URL encode special characters)
            from urllib.parse import unquote_plus

            key = unquote_plus(key)

            print(f"Processing file: s3://{bucket}/{key}")

            # Detect file type
            file_ext = key.lower().split(".")[-1]
            is_csv = file_ext == "csv"
            is_json = file_ext == "json"
            is_invoice = file_ext in ["pdf", "png", "jpg", "jpeg", "gif", "webp"]

            if not (is_csv or is_json or is_invoice):
                raise Exception(f"Unsupported file type. Supported: .json, .csv, .pdf, .png, .jpg, .jpeg. Got: {key}")

            # Read payment instruction file and get metadata
            response = s3.get_object(Bucket=bucket, Key=key)

            # Get job ID from S3 object metadata
            metadata = response.get("Metadata", {})
            job_id = metadata.get("jobid")  # S3 lowercases metadata keys

            if job_id:
                print(f"Found job ID in metadata: {job_id}")
                update_job_status(job_id, "PROCESSING")
            else:
                print("Warning: No job ID found in S3 metadata")

            if is_invoice:
                # INVOICE FLOW: Extract → Save to DB → STOP (no payment file)
                print(f"Invoice file detected ({file_ext}), using vision to extract data")
                file_content = response["Body"].read()  # Binary content for images/PDFs

                # Extract invoice data using vision
                extracted_data_str = extract_invoice_data_with_vision(file_content, file_ext)

                # Clean the response - remove markdown code blocks if present
                extracted_data_str = extracted_data_str.strip()
                if extracted_data_str.startswith("```json"):
                    extracted_data_str = extracted_data_str.replace("```json", "").replace("```", "").strip()
                elif extracted_data_str.startswith("```"):
                    extracted_data_str = extracted_data_str.replace("```", "").strip()

                # Parse JSON
                try:
                    payment_data = json.loads(extracted_data_str)
                    print(f"DEBUG - Bedrock returned invoice.total: {payment_data.get('invoice', {}).get('total')}")
                    print(
                        f"DEBUG - Bedrock returned invoice.total type: {type(payment_data.get('invoice', {}).get('total'))}"
                    )
                except json.JSONDecodeError as json_err:
                    print(f"JSON parsing error: {str(json_err)}")
                    print(f"Raw response from Bedrock: {extracted_data_str[:500]}")  # Log first 500 chars
                    raise Exception(f"Failed to parse invoice data as JSON: {str(json_err)}")

                print("Invoice data extracted successfully")

                # Send invoice data to RTP API for database storage
                # NO ISO20022 file generation here - Payment Agent will decide payment method
                print("Sending invoice data to RTP API...")

                try:
                    api_response = send_invoice_to_api(payment_data, key, iso20022_file_key=None, job_id=job_id)
                    invoice_id = api_response.get("id", "N/A")

                    # Handle duplicate case - still mark as completed since processing succeeded
                    if api_response.get("status") == "duplicate":
                        print("Invoice already exists - marking job as completed with existing invoice ID")
                        # For duplicates, use the existing invoice ID if available
                        if job_id:
                            if invoice_id and invoice_id != "N/A":
                                update_job_status(job_id, "COMPLETED", result_id=invoice_id)
                            else:
                                update_job_status(
                                    job_id,
                                    "COMPLETED",
                                    error_message="Invoice already exists",
                                )
                    else:
                        print(f"Invoice stored in database: {invoice_id}")
                        # Update job to COMPLETED with result ID
                        if job_id:
                            update_job_status(job_id, "COMPLETED", result_id=invoice_id)

                except Exception as api_error:
                    print(f"Error: Failed to store invoice in database: {str(api_error)}")
                    # Mark job as failed if we have job_id
                    if job_id:
                        update_job_status(job_id, "FAILED", str(api_error))
                    raise

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "message": "Invoice extracted and saved successfully",
                            "invoice_id": invoice_id,
                            "source_file": key,
                            "model_used": BEDROCK_MODEL_ID,
                            "note": "Payment file will be generated by Payment Agent based on payment method decision",
                        }
                    ),
                }

            elif is_csv:
                # CSV FLOW: Generate ISO20022 file immediately (bulk processing)
                print("CSV file detected, passing raw content to Bedrock")
                file_content = response["Body"].read().decode("utf-8")
                payment_data = file_content

                result = _process_and_generate_payment_file(
                    payment_data, is_csv=True, is_invoice=False, key=key, job_id=job_id
                )

                if job_id:
                    update_job_status(job_id, "COMPLETED")

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "message": "ISO 20022 pain.001 file generated successfully",
                            "source_format": "csv",
                            "output_file": f"s3://{OUTPUT_BUCKET}/{result['output_key']}",
                            "filename": result["filename"],
                            "transaction_count": result["transaction_count"],
                            "total_amount": result["total_amount"],
                            "message_standard": "ISO20022-pain.001.001.03",
                            "model_used": BEDROCK_MODEL_ID,
                            "validation_status": "passed",
                        }
                    ),
                }

            else:
                # JSON FLOW: Generate ISO20022 file immediately (bulk processing)
                print("JSON file detected, parsing structure")
                file_content = response["Body"].read().decode("utf-8")
                payment_data = json.loads(file_content)

                result = _process_and_generate_payment_file(
                    payment_data, is_csv=False, is_invoice=False, key=key, job_id=job_id
                )

                if job_id:
                    update_job_status(job_id, "COMPLETED")

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "message": "ISO 20022 pain.001 file generated successfully",
                            "source_format": "json",
                            "output_file": f"s3://{OUTPUT_BUCKET}/{result['output_key']}",
                            "filename": result["filename"],
                            "transaction_count": result["transaction_count"],
                            "total_amount": result["total_amount"],
                            "message_standard": "ISO20022-pain.001.001.03",
                            "model_used": BEDROCK_MODEL_ID,
                            "validation_status": "passed",
                        }
                    ),
                }

    except Exception as e:
        error_msg = f"Error processing payment instruction: {str(e)}"
        print(error_msg)

        # Update job to FAILED if we have a job ID
        if job_id:
            update_job_status(job_id, "FAILED", error_message=error_msg)

        return {"statusCode": 500, "body": json.dumps({"error": error_msg})}
