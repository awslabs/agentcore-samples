# Architecture Overview

## System Architecture

The AgentCore Code Interpreter follows a modern three-tier architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                         │
│                    Frontend (React + AWS Cloudscape)           │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐   │
│  │  Code Generator │ │   Code Editor   │ │ Execution Results│   │
│  │      Tab        │ │      Tab        │ │      Tab        │   │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘   │
└─────────────────────────────┬───────────────────────────────────┘
                              │ HTTP/WebSocket
┌─────────────────────────────┴───────────────────────────────────┐
│                     APPLICATION LAYER                          │
│                    Backend (FastAPI + Strands-Agents)          │
│  ┌─────────────────┐                    ┌─────────────────┐     │
│  │ Code Generator  │                    │ Code Executor   │     │
│  │     Agent       │                    │     Agent       │     │
│  │ (Strands-Agents)│                    │ (Strands-Agents)│     │
│  └─────────┬───────┘                    └─────────┬───────┘     │
└────────────┼────────────────────────────────────────┼───────────┘
             │                                        │
┌────────────┴────────────────────────────────────────┴───────────┐
│                      SERVICE LAYER                             │
│                     AWS Bedrock Services                       │
│  ┌─────────────────┐                    ┌─────────────────┐     │
│  │ Claude Sonnet3.7│                    │   AgentCore     │     │
│  │ (Inference      │                    │ CodeInterpreter │     │
│  │  Profile)       │                    │    (Sandbox)    │     │
│  └─────────────────┘                    └─────────────────┘     │
│  ┌─────────────────┐                                            │
│  │  Nova Premier   │                                            │
│  │ (Inference      │                                            │
│  │  Profile)       │                                            │
│  └─────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Component Details

### Frontend Layer (React)
- **Technology**: React 18 + AWS Cloudscape Design System
- **Purpose**: User interface for code generation and execution
- **Components**:
  - Code Generator: Natural language input and AI code generation
  - Code Editor: Monaco-based Python code editor
  - Execution Results: Formatted output display with error handling
- **Communication**: HTTP REST APIs and WebSocket for real-time updates

### Backend Layer (FastAPI)
- **Technology**: FastAPI + Strands-Agents Framework
- **Purpose**: Business logic and AI agent orchestration
- **Components**:
  - **Code Generator Agent**: Converts natural language to Python code
  - **Code Executor Agent**: Executes Python code safely
  - **Session Manager**: Handles user sessions and conversation history
  - **API Gateway**: RESTful endpoints and WebSocket handlers
- **Features**:
  - Intelligent model fallback
  - Session persistence
  - Error handling and logging

### Service Layer (AWS Bedrock)
- **Technology**: AWS Bedrock + AgentCore
- **Purpose**: AI model inference and code execution
- **Components**:
  - **Claude Haiku 4.5**: Primary AI model (inference profile)
  - **Nova Premier**: Fallback AI model (inference profile)
  - **Claude 3.5 Sonnet**: Safety net model
  - **AgentCore**: Sandboxed Python execution environment

## Data Flow

### Code Generation Flow
```
User Input → Frontend → Backend API → Strands Agent → Bedrock Model → Response
    ↓
Generated Code → Code Editor → Ready for Execution
```

### Code Execution Flow
```
Python Code → Backend API → Strands Agent → AgentCore Sandbox → Execution Results
    ↓
Results → Frontend → Formatted Display
```

## Key Design Principles

### 1. **Separation of Concerns**
- Frontend handles UI/UX only
- Backend manages business logic
- AWS services provide AI and execution capabilities

### 2. **Fault Tolerance**
- Multiple AI model fallbacks
- Graceful error handling
- Session recovery mechanisms

### 3. **Security**
- Sandboxed code execution
- AWS IAM-based access control
- Input validation and sanitization

### 4. **Scalability**
- Stateless backend design
- Cloud-native services
- Horizontal scaling capabilities

### 5. **Extensibility**
- Plugin-based agent architecture
- Configurable model selection
- Modular component design

## Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Frontend** | React 18 | User interface framework |
| | AWS Cloudscape | Design system and components |
| | Monaco Editor | Code editing capabilities |
| **Backend** | FastAPI | High-performance API framework |
| | Strands-Agents | AI agent orchestration |
| | Python 3.8+ | Runtime environment |
| **AI Services** | AWS Bedrock | AI model hosting |
| | Claude Haiku 4.5 | Primary language model |
| | Nova Premier | Fallback language model |
| **Execution** | AgentCore | Sandboxed code execution |
| **Infrastructure** | AWS | Cloud platform |

## Security Architecture

### Authentication & Authorization
- AWS IAM roles and policies
- Profile-based or access key authentication
- Least privilege access principles

### Code Execution Security
- Isolated sandbox environments
- Resource limits and timeouts
- Network isolation
- No persistent storage access

### Data Protection
- Encrypted communication (HTTPS/WSS)
- No sensitive data logging
- Session-based data isolation

## Deployment Architecture

### Development Environment
```
Local Machine
├── Frontend (localhost:3000)
├── Backend (localhost:8000)
└── AWS Services (Remote)
```

### Production Environment
```
AWS Cloud
├── Frontend (S3 + CloudFront)
├── Backend (ECS/Lambda)
├── Load Balancer (ALB)
└── Bedrock Services
```

## Performance Considerations

### Optimization Strategies
- **Model Caching**: Reuse initialized models
- **Connection Pooling**: Efficient AWS service connections
- **Async Processing**: Non-blocking I/O operations
- **Response Streaming**: Real-time result delivery

### Monitoring & Observability
- Health check endpoints
- Comprehensive logging
- Error tracking and alerting
- Performance metrics collection

This architecture provides a robust, scalable, and secure platform for AI-powered code generation and execution.
