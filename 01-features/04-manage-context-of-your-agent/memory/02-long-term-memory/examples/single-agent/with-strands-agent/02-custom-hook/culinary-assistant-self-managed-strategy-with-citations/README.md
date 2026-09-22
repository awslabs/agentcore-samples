# Culinary Assistant with Self-Managed memory Strategy (With Citations)

This sample demonstrates Amazon Bedrock AgentCore's self-managed memory strategy with enhanced citation tracking. This version extends the base culinary assistant example by adding comprehensive citation information to extracted long-term memories.

## Self-Managed Strategy Pipeline

The self-managed strategy replaces AgentCore's built-in extraction logic with a pipeline you control. The core flow:

1. **Trigger conditions** — you configure thresholds (message count, idle timeout, or token count) on the memory resource. When a threshold is reached, AgentCore publishes a notification.
2. **SNS → SQS → Lambda** — AgentCore publishes to your SNS topic; an SQS queue subscribes and triggers your Lambda function.
3. **S3 payload delivery** — AgentCore delivers the raw conversation payload to an S3 bucket. Your Lambda downloads it, runs extraction logic (using Bedrock or any model), and stores structured memory records back into AgentCore via `BatchCreateMemoryRecords`.
4. **Citations** — this variant extends the base pattern by attaching citation metadata (session ID, actor ID, S3 URI, extraction job ID, timestamps) to each extracted record, providing full data lineage.

```
Events → AgentCore memory → SNS notification
                                  │
                              SQS queue
                                  │
                            Lambda function
                           /             \
                       S3 payload      Bedrock model
                       (conversation)   (extraction)
                                  \
                          BatchCreateMemoryRecords
                          (with citation metadata)
```

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

#### `agentcore_self_managed_memory_demo.py`

Updated to demonstrate the citation functionality in action, showing how extracted memories now include source attribution.

## Use Cases

This citation-enhanced version is particularly useful for:

1. **Audit Trails**: Maintaining a complete record of where memories originated
2. **Debugging**: Tracing back to the original conversation context
3. **Compliance**: Meeting requirements for data lineage and source attribution
4. **memory Verification**: Ability to verify memory content against original source in S3

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
| memory extraction | ✅ | ✅ |
| S3 payload tracking | ❌ | ✅ |
| Source attribution | ❌ | ✅ |
| Job ID tracking | ❌ | ✅ |
| Timestamp context | ❌ | ✅ |
| Citation metadata | ❌ | ✅ |

## Documentation

For more information about self-managed memory strategies, see the [Amazon Bedrock AgentCore documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-self-managed-strategies.html).

## AgentCore CLI

The self-managed memory strategy requires infrastructure (SNS, SQS, Lambda) that must be provisioned
separately. For standard built-in strategies, add memory to a runtime project with:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore add memory --name culinarymemory --strategies SEMANTIC,USER_PREFERENCE --expiry 30
agentcore deploy
```

Self-managed strategies are configured programmatically via the boto3 SDK as shown in this tutorial.

## Running the Python Scripts

Install dependencies (if a `requirements.txt` is present):

```bash
pip install -r requirements.txt
```

Run each script directly:

```bash
python agentcore_self_managed_memory_demo.py
python aws_utils.py
python lambda_function.py
```
