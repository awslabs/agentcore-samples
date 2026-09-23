# AgentCore Memory Implementation

This directory contains the memory management utilities for AgentCore agents, implementing short-term memory capabilities using Amazon Bedrock AgentCore Memory.

## Features

- **Short-term Memory**: Stores conversation context for maintaining conversation flow
- **Session Management**: Tracks conversations by session ID for context continuity
- **Multi-Agent Support**: Works with both DIY and SDK agent implementations
- **Automatic Context Injection**: Seamlessly adds previous conversation context to agent prompts
- **Error Handling**: Gracefully handles missing dependencies and service errors

## Components

### MemoryManager (`memory_manager.py`)

The core memory management class that provides:

- Memory resource setup and management
- Conversation turn storage and retrieval
- Session tracking and context formatting
- Integration with Bedrock AgentCore Memory service

#### Key Methods

- `start_session(session_id)`: Initialize a new conversation session
- `store_conversation_turn()`: Save user-agent exchanges to memory
- `get_conversation_context()`: Retrieve conversation history
- `format_context_for_agent()`: Format context for agent consumption
- `is_memory_available()`: Check if memory functionality is active

## Integration

### DIY Agent Integration

The DIY agent (`src/agents/diy_agent.py`) integrates memory through:

1. **Automatic Context Injection**: Previous conversation context is prepended to user messages
2. **Response Collection**: Agent responses are captured and stored in memory
3. **Session Tracking**: Uses session_id from request payload for conversation continuity

### SDK Agent Integration

The SDK agent (`src/agents/sdk_agent.py`) includes similar memory integration:

1. **BedrockAgentCoreApp Integration**: Memory works alongside the SDK framework
2. **Payload Enhancement**: Supports session_id and actor_id in request payload
3. **Streaming Response Capture**: Collects streaming responses for memory storage

### Chatbot Client Integration

The chatbot client (`chatbot-client/src/client.py`) supports memory through:

1. **Session ID Generation**: Creates unique session IDs for each runtime connection
2. **Payload Enhancement**: Includes session and actor information in requests
3. **Memory Statistics**: Provides memory-stats command for debugging

## Configuration

Memory functionality requires:

1. **Dependencies**: `bedrock-agentcore` Python package
2. **AWS Configuration**: Valid AWS credentials and region configuration
3. **AgentCore Config**: Proper `agentcore-config.yaml` setup

### Config Example

```yaml
aws:
  region: us-east-1
  account_id: '123456789012'

agents:
  payload_formats:
    diy: direct
    sdk: direct
```

## Usage

### Basic Usage

Memory is automatically enabled when:
1. The `bedrock-agentcore` package is installed
2. AWS credentials are configured
3. The memory service is accessible in the specified region

### Testing Memory

Use the chatbot client to test memory functionality:

```bash
cd /path/to/chatbot-client
python src/client.py --interactive
```

Available commands:
- `memory-stats`: View memory and session information
- `history`: View local conversation history
- `switch`: Change runtime (creates new session)

### Memory Flow

1. **Session Start**: Client generates session ID when connecting to runtime
2. **Context Retrieval**: Agent retrieves previous conversation context using session ID
3. **Enhanced Prompt**: Previous context is prepended to current user message
4. **Response Generation**: Agent generates response with full context
5. **Memory Storage**: User message and agent response are stored for future context

## Error Handling

Memory implementation includes comprehensive error handling:

- **Graceful Degradation**: Agents work normally even if memory is unavailable
- **Warning Messages**: Clear warnings when memory features are disabled
- **Dependency Checks**: Validates required packages and configurations
- **Service Resilience**: Continues operation if memory service is temporarily unavailable

## Monitoring

Memory usage can be monitored through:

1. **Agent Logs**: Memory initialization and error messages in agent logs
2. **Client Commands**: `memory-stats` command in chatbot client
3. **AWS CloudWatch**: AgentCore Memory service metrics and logs

## Limitations

Current implementation provides short-term memory only:

- **Session Scope**: Memory is limited to individual session duration
- **No Persistence**: Memory is not persisted across agent restarts
- **No Long-term Storage**: Does not implement user preferences or fact extraction

## Future Enhancements

Potential improvements include:

1. **Long-term Memory**: Implement user preference and semantic fact extraction
2. **Cross-Session Memory**: Persist context across multiple sessions
3. **Memory Analytics**: Advanced memory usage analytics and optimization
4. **Custom Strategies**: Support for custom memory extraction strategies

## Dependencies

- `bedrock-agentcore`: Amazon Bedrock AgentCore Memory client
- `pyyaml`: Configuration file parsing
- `uuid`: Session ID generation
- `datetime`: Timestamp handling
- `boto3`: AWS service integration (indirect)

## Security

Memory implementation follows security best practices:

- **IAM Integration**: Uses existing AgentCore IAM roles and permissions
- **Data Encryption**: Leverages AWS service encryption for stored data
- **Access Control**: Memory access tied to session and actor identification
- **Privacy**: No persistent storage of sensitive conversation data