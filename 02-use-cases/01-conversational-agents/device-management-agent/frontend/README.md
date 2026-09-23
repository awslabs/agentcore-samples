# Frontend Module

## Architecture & Overview

### What is the Frontend Module?

The Frontend Module provides a web-based user interface for the Device Management System. Built with FastAPI and WebSockets, it offers a chat-like interface where users can interact with their IoT devices using natural language queries.

### Key Responsibilities
- **Web Interface**: Serve responsive HTML interface for device management
- **Real-time Communication**: Handle WebSocket connections for live chat experience
- **User Authentication**: Integrate with Amazon Cognito for secure user login
- **Response Formatting**: Display device data in user-friendly formats
- **Session Management**: Maintain user sessions and conversation context

### Architecture Components
- **FastAPI Application**: Web framework serving the interface and API endpoints
- **WebSocket Handler**: Real-time communication with Agent Runtime
- **Authentication System**: Amazon Cognito integration for user management
- **Template Engine**: Jinja2 for dynamic HTML rendering
- **Static Assets**: CSS, JavaScript, and images for the user interface

## Prerequisites

### Required Software
- **Python 3.10+**
- **Web Browser** (Chrome, Firefox, Safari, Edge)
- **Node.js** (optional, for advanced frontend development)

### AWS Services Access
- **Amazon Cognito** User Pool for authentication
- **Agent Runtime** endpoint for device operations

### Required Dependencies
- **FastAPI**: Web framework
- **Uvicorn**: ASGI server
- **WebSockets**: Real-time communication
- **Jinja2**: Template engine
- **Python-Jose**: JWT token handling

## Deployment Steps

### Option 1: Automated Setup (Recommended)

```bash
# From the frontend directory
chmod +x setup_and_run.sh
./setup_and_run.sh
```

### Option 2: Manual Deployment

#### Step 1: Environment Configuration
```bash
# Create .env file
cp .env.example .env
# Edit with your values:
# - MCP_SERVER_URL (from Gateway module)
# - COGNITO_* variables for authentication
# - HOST and PORT settings
```

#### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

#### Step 3: Run Development Server
```bash
# Local development
python main.py

# Or with uvicorn directly
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

#### Step 4: Docker Deployment (Optional)
```bash
# Build container
docker build -t device-management-frontend .

# Run container
docker run -p 5001:5001 --env-file .env device-management-frontend
```

### Deployment Verification

```bash
# Test local server
curl http://localhost:8000/

# Test health endpoint
curl http://localhost:8000/health

# Test WebSocket connection (requires browser or WebSocket client)
# Open browser to http://localhost:8000 and try the chat interface
```

## Sample Queries

Once the frontend is running, users can interact through the web interface with these types of queries:

### Device Management Queries
```
"Show me all my devices"
"List devices that are offline"
"What's the status of device DG-10016?"
"How many devices do I have?"
```

### Device Configuration Queries
```
"Get the settings for device DG-10005"
"Show me the WiFi networks for device DG-10016"
"What's the firmware version of device DG-10022?"
```

### WiFi Management Queries
```
"Update the WiFi SSID for device DG-10016 to 'HomeNetwork-5G'"
"Change the security type for device DG-10005 to WPA3"
"Show me all WiFi networks"
```

### User and Activity Queries
```
"Who logged in today?"
"Show me recent activity"
"List all users in the system"
```

### Expected User Experience
- **Real-time Responses**: Messages stream in real-time as the agent processes them
- **Formatted Output**: Device information displayed in readable tables and lists
- **Error Handling**: User-friendly error messages for failed operations
- **Session Persistence**: Login state maintained across browser sessions

## Cleanup Instructions

### Stop Running Services

```bash
# Stop development server
# Press Ctrl+C if running in foreground

# Stop Docker container
docker stop device-management-frontend
docker rm device-management-frontend
```

### Remove Docker Resources

```bash
# Remove built image
docker rmi device-management-frontend

# Clean up unused images
docker image prune
```

### Clean Up Local Files

```bash
# Remove environment file (contains sensitive data)
rm .env

# Remove session data and cache
rm -rf __pycache__/
rm -rf .pytest_cache/

# Remove log files if any
rm -f *.log
```

## Configuration

### Environment Variables

```bash
# Server Configuration
HOST=127.0.0.1  # Use 0.0.0.0 for Docker
PORT=8000

# Agent Runtime Connection
MCP_SERVER_URL=https://gateway-id.gateway.bedrock-agentcore.us-west-2.amazonaws.com
AGENT_RUNTIME_URL=http://localhost:8080  # If using local agent runtime

# Amazon Cognito Configuration (for user authentication)
COGNITO_USERPOOL_ID=your-frontend-userpool-id
COGNITO_APP_CLIENT_ID=your-frontend-client-id
COGNITO_DOMAIN=your-frontend-domain.auth.us-west-2.amazoncognito.com
COGNITO_CLIENT_SECRET=your-frontend-client-secret

# CORS Configuration
CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# Session Configuration
SESSION_SECRET_KEY=your-secret-key-for-sessions
SESSION_MAX_AGE=3600  # 1 hour
```

### Web Interface Features

#### Authentication Options
- **Amazon Cognito Login**: Full OAuth flow with hosted UI
- **Simple Login**: Basic username/password for development/demo
- **Session Management**: Secure session cookies with CSRF protection

#### Chat Interface
- **WebSocket Communication**: Real-time bidirectional communication
- **Message History**: Conversation history maintained during session
- **Typing Indicators**: Visual feedback during agent processing
- **Error Recovery**: Automatic reconnection on connection loss

#### Response Formatting
- **Device Tables**: Formatted device listings with status indicators
- **Configuration Displays**: Structured settings and network information
- **Activity Logs**: Chronological user activity with timestamps
- **Error Messages**: User-friendly error descriptions and suggestions

## Troubleshooting

### Common Issues

**Frontend won't start**:
- Check if port 8000 is already in use
- Verify Python dependencies are installed
- Ensure .env file has correct configuration

**Authentication failures**:
- Verify Amazon Cognito configuration
- Check if User Pool and App Client exist
- Ensure CORS origins include your domain

**WebSocket connection failures**:
- Check if Agent Runtime is running and accessible
- Verify MCP_SERVER_URL is correct
- Test network connectivity to backend services

**Chat interface not responding**:
- Check browser console for JavaScript errors
- Verify WebSocket connection is established
- Test backend services independently

### Debug Commands

```bash
# Test FastAPI server
curl -v http://localhost:8000/

# Check WebSocket endpoint
curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" \
     http://localhost:8000/ws/test-client

# Test authentication endpoints
curl -v http://localhost:8000/simple-login

# Check static file serving
curl -v http://localhost:8000/static/style.css
```

### Browser Developer Tools

1. **Console Tab**: Check for JavaScript errors
2. **Network Tab**: Monitor WebSocket connections and HTTP requests
3. **Application Tab**: Inspect session storage and cookies
4. **Elements Tab**: Debug HTML/CSS rendering issues

## Integration with Other Modules

- **Agent Runtime Module**: Communicates via WebSocket for real-time chat functionality
- **Gateway Module**: Indirectly accessed through Agent Runtime for device operations
- **Device Management Module**: Operations executed through the full stack (Frontend → Agent Runtime → Gateway → Lambda)