# Project Structure

## 📁 Organized File Structure

```
strands-agents/agent-core/code-interpreter/
├── 📂 backend/                    # FastAPI Backend
│   ├── main.py                   # Main application server
│   └── requirements.txt          # Python dependencies
│
├── 📂 frontend/                   # React Frontend
│   ├── 📂 src/
│   │   ├── App.js               # Main React application
│   │   └── 📂 components/       # React components
│   │       ├── CodeEditor.js    # Monaco code editor
│   │       ├── CodeDisplay.js   # Code output display
│   │       ├── ExecutionResults.js # Execution results
│   │       └── SessionHistory.js   # Session management
│   ├── 📂 public/               # Static assets
│   ├── package.json             # Node.js dependencies
│   └── package-lock.json        # Dependency lock file
│
├── 📂 tests/                      # Test Suite
│   ├── run_all_tests.py         # Comprehensive test runner
│   ├── verify_setup.py          # Setup verification
│   ├── test_model_fallback.py   # Model fallback testing
│   ├── test_execution_fix.py    # Execution result testing
│   └── debug_code_generation.py # Code generation debugging
│
├── 📂 docs/                       # Documentation
│   ├── ARCHITECTURE.md          # System architecture
│   ├── SETUP.md                 # Setup instructions
│   ├── OVERVIEW.md              # Project overview
│   └── [historical docs]        # Previous iteration docs
│
├── 📂 venv/                       # Python Virtual Environment
│   ├── bin/                     # Executables
│   ├── lib/                     # Python packages
│   └── ...
│
├── 🔧 Configuration Files
│   ├── .env                     # Environment variables
│   ├── .env.example             # Environment template
│   └── .gitignore               # Git ignore rules
│
├── 🚀 Scripts
│   ├── setup.sh                 # Automated setup
│   ├── start.sh                 # Application launcher
│   └── cleanup.sh               # Cleanup script
│
├── 📋 Documentation
│   ├── README.md                # Main documentation
│   └── PROJECT_STRUCTURE.md     # This file
│
└── 📊 Logs (Generated)
    ├── backend.log              # Backend logs
    ├── frontend.log             # Frontend logs
    └── *.pid                    # Process ID files
```

## 🎯 Key Directories

### `/backend/`
**Purpose**: FastAPI server with Strands-Agents integration
- **main.py**: Core application with REST APIs and WebSocket handlers
- **requirements.txt**: Python dependencies including strands-agents and bedrock-agentcore

### `/frontend/`
**Purpose**: React application with AWS Cloudscape UI
- **src/App.js**: Main application with tabbed interface
- **src/components/**: Reusable React components
- **package.json**: Node.js dependencies and scripts

### `/tests/`
**Purpose**: Comprehensive testing and verification
- **run_all_tests.py**: Full test suite for all components
- **verify_setup.py**: Quick setup verification
- **test_*.py**: Specific component tests
- **debug_*.py**: Debugging utilities

### `/docs/`
**Purpose**: Project documentation
- **ARCHITECTURE.md**: System design and component details
- **SETUP.md**: Detailed setup instructions
- **OVERVIEW.md**: Project overview and use cases

## 🔧 Configuration

### Environment Variables (`.env`)
```bash
# AWS Configuration
AWS_PROFILE=default
AWS_REGION=us-east-1

# Application Configuration
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
REACT_APP_API_URL=http://localhost:8000
```

### Scripts
- **setup.sh**: Automated environment setup
- **start.sh**: Launch backend and frontend
- **cleanup.sh**: Clean up processes and temporary files

## 🧪 Testing Structure

### Test Categories
1. **Environment Tests**: Dependencies, AWS config, file structure
2. **Model Tests**: AI model initialization and fallback
3. **Agent Tests**: Strands-Agents integration
4. **API Tests**: REST endpoints and WebSocket handlers
5. **Integration Tests**: End-to-end functionality

### Running Tests
```bash
# Quick verification
python tests/verify_setup.py

# Comprehensive testing
python tests/run_all_tests.py

# Specific component testing
python tests/test_model_fallback.py
```

## 📊 Logging and Monitoring

### Log Files
- **backend.log**: FastAPI server logs, agent responses, errors
- **frontend.log**: React development server logs
- ***.pid**: Process ID files for cleanup

### Health Monitoring
- **Health Endpoint**: `GET /health` - System status
- **Agent Status**: `GET /api/agents/status` - Agent information
- **Model Info**: Current model and fallback status

## 🚀 Quick Commands

### Setup and Start
```bash
./setup.sh          # Initial setup
./start.sh           # Start application
./cleanup.sh         # Clean up and reset
```

### Testing
```bash
python tests/verify_setup.py      # Verify setup
python tests/run_all_tests.py     # Run all tests
```

### Development
```bash
# Backend only
source venv/bin/activate
python backend/main.py

# Frontend only
cd frontend && npm start
```

## 📋 File Organization Benefits

### ✅ **Improved Maintainability**
- Clear separation of concerns
- Logical grouping of related files
- Easy navigation and discovery

### ✅ **Better Testing**
- Centralized test suite
- Comprehensive coverage
- Easy test execution

### ✅ **Enhanced Documentation**
- Organized documentation structure
- Clear setup instructions
- Architecture overview

### ✅ **Streamlined Development**
- Automated setup and cleanup
- Consistent project structure
- Easy onboarding for new developers

This organized structure provides a solid foundation for development, testing, and deployment of the AgentCore Code Interpreter.
