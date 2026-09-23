# AgentCore Code Interpreter

A Python code execution environment built with a **hybrid architecture** using **strands-agents** for code generation and **Amazon Bedrock AgentCore** for code execution, featuring a React frontend with AWS Cloudscape design system.

## Architecture

This application uses a **hybrid multi-agent architecture** with **correct AgentCore integration**:

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────────┐
│   React UI      │    │   FastAPI        │    │   Hybrid Agent System   │
│   (Cloudscape)  │◄──►│   Backend        │◄──►│                         │
│                 │    │                  │    │  ┌─────────────────┐    │
└─────────────────┘    └──────────────────┘    │  │ Strands Agent   │    │
                                                │  │ Code Generator  │    │
                                                │  │ Claude 3.7      │    │
                                                │  └─────────────────┘    │
                                                │           │             │
                                                │           ▼             │
                                                │  ┌─────────────────┐    │
                                                │  │ Strands Agent   │    │
                                                │  │ + AgentCore     │    │
                                                │  │ CodeInterpreter │    │
                                                │  │ Tool            │    │
                                                │  └─────────────────┘    │
                                                └─────────────────────────┘
```

## Model Hierarchy

The application uses an intelligent model fallback system with **inference profiles**:

1. **Primary**: Claude Haiku 4.5 (`global.anthropic.claude-haiku-4-5-20251001-v1:0`)
2. **Fallback**: Nova Premier (`us.amazon.nova-premier-v1:0`) 
3. **Last Resort**: Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6-20250514-v1:0`)

The system automatically detects model availability and selects the best available option.

1. **Code Generator Agent** (Strands-Agents Framework):
   - Uses **Claude Haiku 4.5** (primary) or **Nova Premier** (fallback) via strands-agents
   - Generates clean, executable Python code from natural language
   - Optimized for code generation tasks
   - Returns pure Python code without explanations

2. **Code Executor Agent** (Hybrid Strands-Agents + AgentCore):
   - **Primary Mode**: Strands-Agents Agent with AgentCore CodeInterpreter tool
   - **Model**: Same as Code Generator (Claude Haiku 4.5 → Nova Premier → Claude Sonnet 4.6)
   - Uses `code_session` from AgentCore for real code execution
   - Executes Python code in AWS-managed sandboxed environment

### AgentCore Integration Pattern

The application follows the **official AgentCore samples pattern**:

```python
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands import Agent, tool  # strands-agents package
import json

@tool
def execute_python(code: str, description: str = "") -> str:
    """Execute Python code using AgentCore - following official sample"""
    if description:
        code = f"# {description}\n{code}"
    
    with code_session(aws_region) as code_client:
        response = code_client.invoke("executeCode", {
            "code": code,
            "language": "python",
            "clearContext": False
        })
    
    for event in response["stream"]:
        return json.dumps(event["result"])

# Strands-Agents agent with AgentCore tool
agent = Agent(
    model=bedrock_model,
    tools=[execute_python],
    system_prompt="Execute code using the tool"
)
```

## Prerequisites

- Python 3.8 or higher
- Node.js 16 or higher
- AWS Account with Bedrock access
- AWS CLI configured or AWS credentials

## Installation

1. **Clone and navigate to the project directory**:
   ```bash
   cd /Users/nmurich/strands-agents/agent-core/code-interpreter
   ```

2. **Run the setup script**:
   ```bash
   ./setup.sh
   ```

3. **Configure AWS credentials**:
   Edit the `.env` file with your AWS credentials:
   ```bash
   AWS_REGION=us-east-1
   AWS_ACCESS_KEY_ID=your_access_key_here
   AWS_SECRET_ACCESS_KEY=your_secret_key_here
   ```

## Usage

### Quick Start

Run both backend and frontend with a single command:
```bash
./start.sh
```

This will start:
- Backend API server on `http://localhost:8000`
- React frontend on `http://localhost:3000`

### Manual Start

**Backend**:
```bash
source venv/bin/activate
cd backend
python main.py
```

**Frontend**:
```bash
cd frontend
npm start
```

## Application Features

### 1. Code Generation
- Enter a natural language prompt describing what you want the code to do
- Click "Generate Code" to create Python code using Claude Haiku 4.5 via Strands Agent
- **Generated code is automatically loaded into the Code Editor**
- Preview the generated code before editing or executing
- Choose to execute immediately or edit before execution

### 2. Code Editor
- Monaco Editor with Python syntax highlighting
- **Automatically populated with generated code**
- Upload existing Python files
- Edit generated or uploaded code
- Visual indicators when code has been generated
- **Interactive execution support** with input handling
- Execute code with a single click

### 3. Interactive Code Execution
- **Automatic detection** of interactive code (input() calls)
- **Code analysis** to identify required inputs and suggest values
- **Interactive Execution Modal** for providing inputs before execution
- **Pre-provided input simulation** for testing interactive code
- Support for complex interactive scenarios (loops, conditionals)
- **Visual indicators** showing interactive execution details

### 4. Execution Results
- View execution output in a formatted display with syntax highlighting
- Error handling with detailed error messages and proper styling
- Copy output to clipboard with built-in copy functionality
- **Interactive execution details** showing provided inputs
- Re-execute code easily
- Clear distinction between successful execution and errors

### 5. Session History
- Track all generated code and prompts with agent attribution
- View execution history with timestamps
- **Interactive execution tracking** with input details
- Re-execute previous code snippets
- Session persistence during application use
- See which agent (Strands vs AgentCore) handled each operation

## API Endpoints

### Code Generation
```http
POST /api/generate-code
Content-Type: application/json

{
  "prompt": "Create a function to calculate fibonacci numbers",
  "session_id": "optional-session-id"
}
```

### Code Execution
```http
POST /api/execute-code
Content-Type: application/json

{
  "code": "print('Hello, World!')",
  "session_id": "optional-session-id",
  "interactive": false,
  "inputs": ["input1", "input2"]
}
```

### Interactive Code Analysis
```http
POST /api/analyze-code
Content-Type: application/json

{
  "code": "name = input('Your name: ')",
  "session_id": "optional-session-id"
}
```

### File Upload
```http
POST /api/upload-file
Content-Type: application/json

{
  "filename": "script.py",
  "content": "# Python code content",
  "session_id": "optional-session-id"
}
```

### Session History
```http
GET /api/session/{session_id}/history
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_PROFILE` | AWS CLI profile name (recommended) | `default` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `AWS_ACCESS_KEY_ID` | AWS access key (fallback) | Required if no profile |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key (fallback) | Required if no profile |
| `BACKEND_HOST` | Backend server host | `0.0.0.0` |
| `BACKEND_PORT` | Backend server port | `8000` |
| `REACT_APP_API_URL` | Frontend API URL | `http://localhost:8000` |

### AWS Authentication Priority

The application uses the following authentication priority:

1. **AWS Profile** (Recommended): Uses `AWS_PROFILE` from `.env` file
2. **Access Keys** (Fallback): Uses `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`

**Example .env configuration:**
```bash
# Option 1: Use AWS Profile (Recommended)
AWS_PROFILE=default
AWS_REGION=us-east-1

# Option 2: Use Access Keys (only if no profile)
# AWS_ACCESS_KEY_ID=your_access_key_here
# AWS_SECRET_ACCESS_KEY=your_secret_key_here
```

### AgentCore Configuration

The application supports two execution modes:

- **AgentCore Mode**: Full code execution with Amazon Bedrock AgentCore
- **Strands Simulation**: Code analysis and simulation when AgentCore is not available

AgentCore requires additional permissions (`bedrock-agentcore:*`). If not available, the application automatically falls back to Strands simulation.

## Security Considerations

- Code execution happens in AgentCore's sandboxed environment
- Session data is stored in memory (not persistent across restarts)
- AWS credentials should be properly secured
- Consider implementing authentication for production use

## Troubleshooting

### Common Issues

1. **"No response from server" Error**:
   ```bash
   # Check if backend is running
   curl http://localhost:8000/health
   
   # Run diagnostics
   python diagnose_backend.py
   
   # Start backend manually for debugging
   ./start_manual.sh
   ```

2. **Backend fails to start**:
   - Check AWS credentials in `.env` file
   - Ensure Bedrock access is enabled in your AWS account
   - Verify Python dependencies are installed
   - Run: `python test_strands.py` to check strands-agents framework

3. **Strands-Agents framework not found**:
   ```bash
   # Check if strands-agents is available
   python test_strands.py
   
   # If not installed, install it
   pip install strands-agents
   
   # Or check if it exists locally
   ls -la ../strands*
   ```

4. **Frontend can't connect to backend**:
   - Ensure backend is running on port 8000
   - Check CORS configuration
   - Verify API URL in frontend configuration

5. **Code execution fails**:
   - Check AgentCore initialization
   - Verify Bedrock model access
   - Review execution logs for specific errors

### Diagnostic Tools

1. **Full Diagnostics**:
   ```bash
   python diagnose_backend.py
   ```

2. **AWS Authentication Test**:
   ```bash
   python test_aws_auth.py
   ```

3. **Strands-Agents Framework Test**:
   ```bash
   python test_strands.py
   ```

4. **AgentCore Integration Test**:
   ```bash
   python test_agentcore_integration.py
   ```

5. **Frontend Component Test**:
   ```bash
   node test_frontend.js
   ```

6. **Interactive Execution Test**:
   ```bash
   python test_interactive.py
   ```

### Manual Startup

If the automatic start script fails, use manual startup:

1. **Backend Only**:
   ```bash
   ./start_manual.sh
   ```

2. **Frontend Only** (in separate terminal):
   ```bash
   cd frontend
   npm start
   ```

### Logs

Backend logs are printed to console. For debugging:
```bash
# Run backend with verbose logging
cd backend
python main.py --log-level debug

# Or check log files (if using improved start.sh)
tail -f backend.log
tail -f frontend.log
```

## Development

### Project Structure
```
├── backend/
│   └── main.py              # FastAPI backend server
├── frontend/
│   ├── src/
│   │   ├── components/      # React components
│   │   ├── services/        # API services
│   │   └── App.js          # Main React app
│   └── package.json
├── requirements.txt         # Python dependencies
├── setup.sh                # Setup script
├── start.sh                # Start script
└── README.md
```

### Adding New Features

1. **Backend**: Add new endpoints in `backend/main.py`
2. **Frontend**: Add new components in `frontend/src/components/`
3. **API**: Update `frontend/src/services/api.js` for new endpoints

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review AWS Bedrock AgentCore documentation
3. Open an issue in the repository
