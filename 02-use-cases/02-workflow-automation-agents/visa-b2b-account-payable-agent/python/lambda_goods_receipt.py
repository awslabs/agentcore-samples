import json
import boto3

# from datetime import datetime
import os
import base64
import urllib3

s3 = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")
http = urllib3.PoolManager()

# Configuration from environment variables
RTP_API_URL = os.environ.get("RTP_API_URL", "https://api.rtp-overlay.com")
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


def extract_gr_data_with_vision(file_content, file_type):
    """Use Bedrock (Claude Sonnet 4) to extract goods receipt data from photos"""

    # Encode file content to base64
    file_base64 = base64.b64encode(file_content).decode("utf-8")

    # Determine media type
    media_type_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "pdf": "application/pdf",
    }
    media_type = media_type_map.get(file_type.lower(), "image/jpeg")

    prompt = """
You are extracting data from a Goods Receipt or Bill of Lading (BOL) document.
This may be a photo taken with a mobile phone and may contain handwritten text.

Return ONLY JSON with this schema (no markdown, no commentary):

{
  "bol_number": "...",
  "po_reference": "...",
  "vendor": {
    "name": "...",
    "address_lines": ["...", "..."]
  },
  "delivery_date": "YYYY-MM-DD",
  "material_description": "...",
  "quantity_received": 0,
  "unit": "...",
  "received_by": "...",
  "delivery_location": "...",
  "notes": "...",
  "confidence_scores": {
    "bol_number": 0.95,
    "po_reference": 0.90,
    "quantity_received": 0.85,
    "material_description": 0.92,
    "received_by": 0.70
  }
}

Rules:
- Extract handwritten quantities carefully
- If text is unclear, lower the confidence score
- PO reference might be labeled as "PO#", "Purchase Order", or similar
- Quantity might be handwritten
- If any field is missing, set it to "" or 0
- Confidence scores should reflect OCR quality (0.0 to 1.0)
"""

    # Build content based on file type
    if file_type.lower() == "pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": file_base64},
        }
    else:
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
        print(f"Error extracting GR data: {str(e)}")
        raise


def validate_gr_completeness(extracted_data):
    """Check if GR has all critical fields and determine if manual review is needed"""
    critical_fields = [
        "bol_number",
        "po_reference",
        "quantity_received",
        "material_description",
    ]
    missing_fields = []
    needs_review = False

    for field in critical_fields:
        value = extracted_data.get(field)
        if not value or value == "" or value == 0:
            missing_fields.append(field)
            needs_review = True

    # Check confidence scores - flag for review if any critical field has low confidence
    confidence_scores = extracted_data.get("confidence_scores", {})
    low_confidence_fields = []

    for field in critical_fields:
        confidence = confidence_scores.get(field, 0)
        if confidence < 0.7:  # Threshold for manual review
            low_confidence_fields.append(field)
            needs_review = True

    return {
        "needs_review": needs_review,
        "missing_fields": missing_fields,
        "low_confidence_fields": low_confidence_fields,
        "status": "pending_review" if needs_review else "approved",
    }


def send_gr_to_api(extracted_data, source_file_key, job_id=None):
    """Send extracted goods receipt data to RTP API"""
    try:
        # Clean vendor name if present (strip codes in parentheses)
        if "vendor" in extracted_data and "name" in extracted_data["vendor"]:
            import re

            vendor_name = extracted_data["vendor"]["name"]
            cleaned_name = re.sub(r"\s*\([^)]*\)\s*$", "", vendor_name).strip()
            if vendor_name != cleaned_name:
                print(f"Cleaned vendor name: '{vendor_name}' -> '{cleaned_name}'")
                extracted_data["vendor"]["name"] = cleaned_name

        # Validate completeness
        validation = validate_gr_completeness(extracted_data)

        payload = {
            "extractedData": extracted_data,
            "sourceFileKey": source_file_key,
            "status": validation["status"],
            "needsReview": validation["needs_review"],
            "missingFields": validation["missing_fields"],
            "lowConfidenceFields": validation["low_confidence_fields"],
        }

        # Include job ID for tracking
        if job_id:
            payload["jobId"] = job_id

        headers = {"Content-Type": "application/json"}

        url = f"{RTP_API_URL}/api/goods-receipts/process"

        print(f"Sending GR data to API: {url}")

        response = http.request("POST", url, body=json.dumps(payload), headers=headers)

        if response.status == 201:
            print("Goods receipt successfully created in database")
            return json.loads(response.data.decode("utf-8"))
        elif response.status == 409:
            print("Goods receipt already exists in database")
            return {"status": "duplicate", "message": "GR already exists"}
        else:
            error_msg = f"API returned status {response.status}: {response.data.decode('utf-8')}"
            print(f"Error creating GR: {error_msg}")
            raise Exception(error_msg)

    except Exception as e:
        print(f"Error sending GR to API: {str(e)}")
        raise


def update_job_status(job_id, status, result_id=None, error_message=None):
    """Update document job status via API"""
    try:
        payload = {"status": status}

        if result_id:
            payload["resultId"] = result_id

        if error_message:
            payload["errorMessage"] = error_message

        headers = {"Content-Type": "application/json"}

        url = f"{RTP_API_URL}/api/document-jobs/{job_id}"

        print(f"Updating job {job_id} to status {status}")

        response = http.request("PATCH", url, body=json.dumps(payload), headers=headers)

        if response.status == 200:
            print(f"Job {job_id} updated successfully")
        else:
            print(f"Warning: Failed to update job status: {response.status}")

    except Exception as e:
        print(f"Error updating job status: {str(e)}")


def lambda_handler(event, context):
    """
    S3 Event-driven Lambda handler for Goods Receipt processing
    """
    job_id = None

    try:
        for record in event.get("Records", []):
            bucket = record["s3"]["bucket"]["name"]
            key = record["s3"]["object"]["key"]

            # URL decode the key
            from urllib.parse import unquote_plus

            key = unquote_plus(key)

            print(f"Processing file: s3://{bucket}/{key}")

            # Detect file type
            file_ext = key.lower().split(".")[-1]

            if file_ext not in ["pdf", "png", "jpg", "jpeg", "gif", "webp"]:
                raise Exception(f"Unsupported file type. Supported: .pdf, .png, .jpg, .jpeg. Got: {key}")

            # Read file and get metadata
            response = s3.get_object(Bucket=bucket, Key=key)
            file_content = response["Body"].read()

            # Get job ID from S3 object metadata
            metadata = response.get("Metadata", {})
            job_id = metadata.get("jobid")  # S3 lowercases metadata keys

            if job_id:
                print(f"Found job ID in metadata: {job_id}")
                update_job_status(job_id, "PROCESSING")
            else:
                print("Warning: No job ID found in S3 metadata")

            print(f"GR file detected ({file_ext}), using vision to extract data")

            # Extract GR data using vision
            extracted_data_str = extract_gr_data_with_vision(file_content, file_ext)

            # Clean the response (remove markdown if present)
            extracted_data_str = extracted_data_str.strip()
            if extracted_data_str.startswith("```json"):
                extracted_data_str = extracted_data_str.replace("```json", "").replace("```", "").strip()
            elif extracted_data_str.startswith("```"):
                extracted_data_str = extracted_data_str.replace("```", "").strip()

            print(f"Extracted data string (first 200 chars): {extracted_data_str[:200]}")

            try:
                gr_data = json.loads(extracted_data_str)
            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {str(e)}")
                print(f"Full extracted string: {extracted_data_str}")
                raise Exception(f"Failed to parse Bedrock response as JSON: {str(e)}")

            print("GR data extracted successfully")
            print(f"BOL: {gr_data.get('bol_number', 'N/A')}, PO: {gr_data.get('po_reference', 'N/A')}")

            # Send to API
            print("Sending GR data to RTP API...")

            try:
                api_response = send_gr_to_api(gr_data, key, job_id)
                gr_id = api_response.get("id", "N/A")
                print(f"GR stored in database: {gr_id}")

                # Update job to COMPLETED with result ID
                if job_id:
                    update_job_status(job_id, "COMPLETED", result_id=gr_id)

            except Exception as api_error:
                print(f"Warning: Failed to store GR in database: {str(api_error)}")
                if job_id:
                    update_job_status(job_id, "FAILED", error_message=str(api_error))
                raise

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": "Goods receipt processed successfully",
                        "bol_number": gr_data.get("bol_number"),
                        "po_reference": gr_data.get("po_reference"),
                        "quantity_received": gr_data.get("quantity_received"),
                        "model_used": BEDROCK_MODEL_ID,
                    }
                ),
            }

    except Exception as e:
        error_msg = f"Error processing goods receipt: {str(e)}"
        print(error_msg)

        # Update job to FAILED if we have a job ID
        if job_id:
            update_job_status(job_id, "FAILED", error_message=error_msg)

        return {"statusCode": 500, "body": json.dumps({"error": error_msg})}
