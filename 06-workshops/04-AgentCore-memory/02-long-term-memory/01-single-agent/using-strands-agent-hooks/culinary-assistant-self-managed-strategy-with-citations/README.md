# Culinary Assistant with Self-Managed Memory Strategy (With Citations)

This sample demonstrates Amazon Bedrock AgentCore's self-managed memory strategy with enhanced citation tracking. This version extends the base culinary assistant example by adding comprehensive citation information to extracted long-term memories.

## What's Different

This sample adds citation functionality to track the source of extracted memories:

### Citation Features

1. **Source Tracking**: Each extracted memory includes metadata about its origin:
   - Session ID and Actor ID
   - Starting and ending timestamps
   - S3 URI where the original short-term memory payload is stored
   - Extraction job ID

2. **Citation Metadata**: Structured citation information is stored in the memory metadata:
   ```python
   citation_info = {
       'source_type': 'short_term_memory',
       'session_id': session_id,
       'actor_id': actor_id,
       'starting_timestamp': starting_timestamp,
       'ending_timestamp': timestamp,
       's3_uri': s3_location,
       's3_payload_location': s3_location,
       'extraction_job_id': job_id
   }
   ```

3. **Human-Readable Citations**: Each memory content includes an appended citation text:
   ```
   [Citation: Extracted from session {session_id}, actor {actor_id}, source: {s3_location}, job: {job_id}, timestamp: {timestamp}]
   ```

### Modified Files

#### `lambda_function.py`

The key changes are in the `MemoryExtractor` class:

- `extract_memories()` method now accepts `s3_location` and `job_id` parameters
- `_format_extracted_memories()` method builds citation information and appends it to memory content
- Enhanced logging to track citation information

**Key Method**: `_format_extracted_memories` (line 97)
This method formats extracted memories with metadata and citation information, creating a traceable link from long-term memories back to their source in short-term memory.

#### `agentcore_self_managed_memory_demo.ipynb`

Updated to demonstrate the citation functionality in action, showing how extracted memories now include source attribution.

## Use Cases

This citation-enhanced version is particularly useful for:

1. **Audit Trails**: Maintaining a complete record of where memories originated
2. **Debugging**: Tracing back to the original conversation context
3. **Compliance**: Meeting requirements for data lineage and source attribution
4. **Memory Verification**: Ability to verify memory content against original source in S3

## Prerequisites

Same as the base culinary assistant example:
- Python 3.11+
- AWS credentials configured
- Amazon Bedrock access with Claude models
- Required AWS services: Lambda, S3, SNS, SQS

## Setup

Follow the same setup process as the base culinary assistant example. The notebook will guide you through:

1. Creating the Lambda function with citation support
2. Setting up the memory strategy with trigger conditions
3. Testing the enhanced citation functionality

## Comparison with Base Sample

| Feature | Base Sample | With Citations |
|---------|------------|----------------|
| Memory extraction | ✅ | ✅ |
| S3 payload tracking | ❌ | ✅ |
| Source attribution | ❌ | ✅ |
| Job ID tracking | ❌ | ✅ |
| Timestamp context | ❌ | ✅ |
| Citation metadata | ❌ | ✅ |

## Documentation

For more information about self-managed memory strategies, see the [Amazon Bedrock AgentCore documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-self-managed-strategies.html).
