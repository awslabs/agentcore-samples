# AgentCore Memory Dashboard

A lightweight React + FastAPI dashboard for browsing AWS Bedrock AgentCore Memory data.

**📦 Repository Size**: ~2MB (dependencies excluded - see setup instructions below)

## ✨ Key Features

- **Dynamic Configuration**: Memory ID, Actor ID, and Session ID entered through UI
- **Short-Term Memory**: Query conversation events and turns
- **Long-Term Memory**: Browse facts, preferences, and summaries
- **Real-time Search**: Content filtering with live results



## 📋 Prerequisites

- **Node.js** 16+
- **Python** 3.8+
- **AWS CLI** configured with credentials
- **AWS Bedrock AgentCore Memory** access

## 🔑 AWS Credentials Setup

### Step 1: Install AWS CLI
```bash
# macOS
brew install awscli

# Linux
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Windows
# Download and run the AWS CLI MSI installer from AWS website
```

### Step 2: Configure AWS Credentials
Choose one of these methods:

#### Option A: AWS Configure (Recommended)
```bash
aws configure
```
Enter your:
- AWS Access Key ID
- AWS Secret Access Key  
- Default region (e.g., `us-east-1`)
- Default output format (e.g., `json`)

#### Option B: Environment Variables
```bash
export AWS_ACCESS_KEY_ID=your-access-key-id
export AWS_SECRET_ACCESS_KEY=your-secret-access-key
export AWS_DEFAULT_REGION=us-east-1
```

#### Option C: AWS Credentials File
Create `~/.aws/credentials`:
```ini
[default]
aws_access_key_id = your-access-key-id
aws_secret_access_key = your-secret-access-key
```

Create `~/.aws/config`:
```ini
[default]
region = us-east-1
output = json
```

### Step 3: Verify AWS Access
```bash
# Test AWS connection
aws sts get-caller-identity

# Test Bedrock access
aws bedrock list-foundation-models --region us-east-1
```

### Step 4: Required IAM Permissions
Your AWS user/role needs these permissions:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:ListEvents", 
                "bedrock-agentcore:GetLastKTurns",
                "bedrock-agentcore:RetrieveMemories",
                "bedrock-agentcore:GetMemoryStrategies"
            ],
            "Resource": "*"
        }
    ]
}
```

## 🚀 Quick Start Guide

### Step 1: Clone and Setup
```bash
# Clone the repository
git clone <repository-url>
cd 06-workshops/04-AgentCore-memory/03-advanced-patterns/04-memory-browser

# Install frontend dependencies (this will download ~200MB of packages)
npm install
```

**Note**: 
- 📦 **Dependencies not included**: `node_modules` and `backend/venv` are excluded from the repository
- 🔧 **First-time setup**: Run `npm install` to download all frontend dependencies
- ✅ **Frontend `.env`**: Already configured with default settings
- ❌ **Backend `.env`**: Needs to be created (see Step 2)

### Step 2: Configure Environment Variables

#### Backend Configuration
Copy the example file and customize:
```bash
# Copy the example file
cp backend/.env.example backend/.env

# Edit backend/.env and set your AWS profile (if needed)
# AWS_PROFILE=your-profile-name
```

The `backend/.env` file should contain:
```env
# AWS Configuration (region will be auto-detected from AWS CLI/profile if not set)
# AWS_REGION=us-east-1

# Server Configuration
# Security: Use 127.0.0.1 for local development (recommended)
# Only use 0.0.0.0 if you need to access from other machines on your network
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
DEBUG=true

# CORS Configuration
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# Optional: AWS Profile (if using multiple profiles)
# AWS_PROFILE=your-profile-name
```

**Security Note**: The backend now binds to `127.0.0.1` (localhost only) by default for security. This prevents exposure to all network interfaces. If you need to access the backend from other machines on your network, set `BACKEND_HOST=0.0.0.0` in your `.env` file, but be aware this exposes the service to your entire network.

**Note**: AWS region is automatically detected from your AWS CLI configuration. Only set `AWS_REGION` if you need to override the default.

#### Frontend Configuration  
The frontend `.env` file is already configured with default values. You can modify it if needed:
```env
# Backend API URL
REACT_APP_BACKEND_URL=http://localhost:8000

# Dashboard Settings
REACT_APP_MAX_MEMORY_ENTRIES=50
REACT_APP_REFRESH_INTERVAL=5000
```

### Step 3: Install Backend Dependencies
```bash
# Navigate to backend directory
cd backend

# Create Python virtual environment (isolated Python packages)
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate

# Install Python dependencies (~50MB of packages)
pip install -r requirements.txt

# Return to project root
cd ..
```

**Note**: The virtual environment (`backend/venv/`) is excluded from the repository to keep it lightweight.

### Step 4: Start the Application

#### Option A: Start Both Services Together (Recommended)
```bash
# From project root directory
npm run dev
```
This will start both the backend (FastAPI) and frontend (React) simultaneously.

#### Option B: Start Services Separately
```bash
# Terminal 1: Start backend
npm run start-backend

# Terminal 2: Start frontend  
npm start
```



### Step 5: Access the Dashboard
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs

### Step 6: Configure Memory Access
1. Open the dashboard at http://localhost:3000
2. Enter your **Memory ID** and **Actor ID** in the header
3. Click **Configure** to validate access
4. Start querying your AgentCore Memory data!

## 📊 Dashboard Features

### Short-Term Memory
- Query conversation events and turns
- Filter by content, event type, and role

### Long-Term Memory  
- **User Input Required**: Memory ID and Namespace (entered via UI)
- Namespace-based querying with content filtering
- Browse facts, preferences, and summaries

## 🔧 Troubleshooting

### Common Issues
- **Backend won't start**: Check Python virtual environment is activated
- **Frontend can't connect**: Verify backend is running on port 8000
- **AWS permission errors**: Run `aws sts get-caller-identity` to verify credentials
- **Memory ID not found**: Check Memory ID exists and you have proper permissions

---
