import json
import boto3
import logging
import uuid
import time
from datetime import datetime
from urllib.parse import urlparse

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


class NotificationHandler:
    """Handles parsing SQS events and retrieving S3 payloads"""

    def __init__(self):
        self.s3_client = boto3.client("s3")

    def process_sqs_event(self, event):
        """Extract job details from SQS event and download S3 payload"""
        if len(event["Records"]) != 1:
            raise ValueError(f"Expected 1 record, got {len(event['Records'])}")

        # Parse SQS message
        record = event["Records"][0]
        message = json.loads(record["body"])
        sqs_message = json.loads(message["Message"])

        logger.info(f"Received message: {json.dumps(sqs_message)}")

        # Extract job metadata
        job_metadata = {
            "job_id": sqs_message["jobId"],
            "memory_id": sqs_message["memoryId"],
            "strategy_id": sqs_message["strategyId"],
            "s3_location": sqs_message["s3PayloadLocation"],
        }

        # Download and parse payload
        payload = self._download_payload(job_metadata["s3_location"])

        return job_metadata, payload

    def _download_payload(self, s3_location):
        """Download payload from S3 location"""
        parsed_url = urlparse(s3_location)
        bucket = parsed_url.netloc
        key = parsed_url.path.lstrip("/")

        logger.info(f"Downloading payload from bucket: {bucket}, key: {key}")

        response = self.s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())


class MemoryExtractor:
    """Extracts memory records from conversation payload"""

    def __init__(self, model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0"):
        self.bedrock_client = boto3.client("bedrock-runtime")
        self.model_id = model_id

    def extract_memories(self, payload, s3_location=None, job_id=None):
        """Extract memories from conversation payload using Bedrock model"""
        conversation_text = self._build_conversation_text(payload)

        prompt = f"""Extract user preferences, interests, and facts from this conversation.
Return ONLY a valid JSON array with this format:
[{{"content": "detailed description", "type": "preference|interest|fact", "confidence": 0.0-1.0}}]

Focus on extracting specific, meaningful pieces of information that would be useful to remember.
Conversation:
{conversation_text}"""

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = self.bedrock_client.invoke_model(modelId=self.model_id, body=json.dumps(request_body))

            response_body = json.loads(response["body"].read())
            extracted_text = response_body["content"][0]["text"]

            # Find JSON in the response
            start_idx = extracted_text.find("[")
            end_idx = extracted_text.rfind("]") + 1

            if start_idx >= 0 and end_idx > start_idx:
                json_str = extracted_text[start_idx:end_idx]
                extracted_data = json.loads(json_str)
                logger.info(f"Extracted {len(extracted_data)} memories")
                return self._format_extracted_memories(extracted_data, payload, s3_location, job_id)
            else:
                logger.error("Could not find JSON in model response")
                return []

        except Exception as e:
            logger.error(f"Error extracting memories: {str(e)}")
            return []

    def _build_conversation_text(self, payload):
        """Build formatted conversation text from payload"""
        text = ""

        # Include historical context if available
        if "historicalContext" in payload:
            text += "Previous conversation:\n"
            for msg in payload["historicalContext"]:
                if "role" in msg and "content" in msg and "text" in msg["content"]:
                    text += f"{msg['role']}: {msg['content']['text']}\n"

        # Add current context
        if "currentContext" in payload:
            text += "\nCurrent conversation:\n"
            for msg in payload["currentContext"]:
                if "role" in msg and "content" in msg and "text" in msg["content"]:
                    text += f"{msg['role']}: {msg['content']['text']}\n"

        return text

    def _format_extracted_memories(self, extracted_data, payload, s3_location=None, job_id=None):
        """Format extracted memories with metadata and citation information"""
        memories = []
        session_id = payload.get("sessionId", "unknown-session")
        actor_id = payload.get("actorId", "unknown-actor")

        # Get timestamp from payload or use current time
        timestamp = payload.get("endingTimestamp", int(time.time()))

        # Get starting timestamp for context window
        starting_timestamp = payload.get("startingTimestamp", timestamp)

        for item in extracted_data:
            if not isinstance(item, dict) or "content" not in item or "type" not in item:
                logger.warning(f"Skipping invalid memory item: {item}")
                continue

            # For this demo we'll focus only on user interests with a hierarchical namespace
            # Format: /interests/actor/{actorId}/session/{sessionId}
            namespace = f"/interests/actor/{actor_id}/session/{session_id}/"

            # Build citation information to link long-term memory back to short-term memory source
            citation_info = {
                "source_type": "short_term_memory",
                "session_id": session_id,
                "actor_id": actor_id,
                "starting_timestamp": starting_timestamp,
                "ending_timestamp": timestamp,
            }

            # Add S3 URI if available
            if s3_location:
                citation_info["s3_uri"] = s3_location
                citation_info["s3_payload_location"] = s3_location

            # Add job ID if available
            if job_id:
                citation_info["extraction_job_id"] = job_id

            # Format citation as readable text to append to content
            citation_text = f"\n\n[Citation: Extracted from session {session_id}, actor {actor_id}"
            if s3_location:
                citation_text += f", source: {s3_location}"
            if job_id:
                citation_text += f", job: {job_id}"
            citation_text += f", timestamp: {timestamp}]"

            # Append citation to content
            content_with_citation = item["content"] + citation_text

            memory = {
                "content": content_with_citation,
                "namespaces": [namespace],
                "memoryStrategyId": None,  # Will be set later
                "timestamp": timestamp,
                "metadata": citation_info,  # Store structured citation metadata
            }

            logger.info(f"Extracted memory with namespace: {namespace}")
            logger.info(f"Extracted memory with citation: {memory}")

            memories.append(memory)

        return memories


class MemoryIngestor:
    """Ingests extracted memories back into AgentCore"""

    def __init__(self):
        self.agentcore_client = boto3.client("bedrock-agentcore")

    def batch_ingest_memories(self, memory_id, memory_records, strategy_id):
        """Ingest memory records using AgentCore batch API"""
        if not memory_records:
            logger.info("No memory records to ingest")
            return {"recordsIngested": 0}

        # Set strategy ID for all records
        for record in memory_records:
            record["memoryStrategyId"] = strategy_id

        # Prepare batch request
        batch_records = []
        for record in memory_records:
            batch_record = {
                "requestIdentifier": str(uuid.uuid4()),
                "content": {"text": record["content"]},
                "namespaces": record["namespaces"],
                "memoryStrategyId": record["memoryStrategyId"],
            }

            # Add timestamp if provided - handle millisecond timestamps
            if "timestamp" in record:
                try:
                    ts_value = record["timestamp"]

                    # Check if timestamp is in milliseconds (13 digits)
                    if isinstance(ts_value, int) and ts_value > 10000000000:  # More than 10 billion = milliseconds
                        # Convert milliseconds to seconds
                        ts_seconds = ts_value / 1000.0
                        batch_record["timestamp"] = datetime.fromtimestamp(ts_seconds)
                        logger.info(f"Converted millisecond timestamp to datetime: {batch_record['timestamp']}")
                    else:
                        # Handle as regular Unix timestamp
                        batch_record["timestamp"] = datetime.fromtimestamp(ts_value)
                except Exception as e:
                    logger.error(f"Error processing timestamp {record['timestamp']}: {str(e)}")
                    # Use current time as fallback
                    batch_record["timestamp"] = datetime.now()
                    logger.info(f"Using fallback timestamp: {batch_record['timestamp']}")

            batch_records.append(batch_record)

        # Execute batch create
        try:
            logger.info(f"Ingesting {len(batch_records)} memory records")

            self.agentcore_client.batch_create_memory_records(
                memoryId=memory_id, records=batch_records, clientToken=str(uuid.uuid4())
            )

            logger.info(f"Successfully ingested {len(batch_records)} memory records")
            return {"recordsIngested": len(batch_records)}

        except Exception as e:
            logger.error(f"Failed to ingest memory records: {str(e)}")
            raise


def lambda_handler(event, context):
    """Main Lambda handler orchestrating the memory processing pipeline"""

    # Initialize components
    notification_handler = NotificationHandler()
    extractor = MemoryExtractor()
    ingestor = MemoryIngestor()

    try:
        # 1. Handle notification and download payload
        job_metadata, payload = notification_handler.process_sqs_event(event)
        logger.info(f"Processing job {job_metadata['job_id']} for memory {job_metadata['memory_id']}")

        # 2. Extract memories using Bedrock model with citation information
        extracted_memories = extractor.extract_memories(
            payload,
            s3_location=job_metadata["s3_location"],
            job_id=job_metadata["job_id"],
        )
        logger.info(f"Extracted {len(extracted_memories)} memories with S3 citation: {job_metadata['s3_location']}")

        # 3. Ingest extracted memories into AgentCore
        if extracted_memories:
            ingest_result = ingestor.batch_ingest_memories(
                job_metadata["memory_id"],
                extracted_memories,
                job_metadata["strategy_id"],
            )

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "jobId": job_metadata["job_id"],
                        "extractedMemories": len(extracted_memories),
                        "ingestedRecords": ingest_result["recordsIngested"],
                    }
                ),
            }
        else:
            logger.info("No memories extracted, nothing to ingest")
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "jobId": job_metadata["job_id"],
                        "extractedMemories": 0,
                        "ingestedRecords": 0,
                    }
                ),
            }

    except Exception as e:
        logger.error(f"Pipeline failed: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
