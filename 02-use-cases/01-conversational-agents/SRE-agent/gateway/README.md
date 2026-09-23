# Gateway Component

This directory contains the MCP (Model Context Protocol) gateway management tools for SRE Agent.

## 📁 Files

- `main.py` - AgentCore Gateway Management Tool for creating and managing AWS AgentCore Gateways
- `mcp_cmds.sh` - Shell script for MCP gateway operations and setup
- `generate_token.py` - JWT token generation for gateway authentication
- `openapi_s3_target_cognito.sh` - Script for adding OpenAPI targets with S3 and Cognito integration
- `config.yaml` - Gateway configuration file
- `config.yaml.example` - Example configuration template
- `.env` - Environment variables for gateway setup
- `.env.example` - Example environment variables template

## 🚀 Gateway Setup

### Step-by-Step Setup

1. **Configure the gateway** (copy and edit config):
   ```bash
   cd gateway
   cp config.yaml.example config.yaml
   cp .env.example .env
   # Edit config.yaml and .env with your specific settings
   ```

2. **Create the gateway**:
   ```bash
   ./create_gateway.sh
   ```

3. **Test the gateway**:
   ```bash
   ./mcp_cmds.sh
   
   # To capture output to a log file for debugging:
   ./mcp_cmds.sh 2>&1 | tee mcp_cmds.log
   ```

This setup process will:
- Configure the MCP gateway infrastructure
- Create the gateway with proper authentication and token management
- Test the gateway functionality and validate the setup

## 🔧 Components

### Gateway Management (`main.py`)
The main gateway management tool provides functionality to:
- Create and manage AWS AgentCore Gateways
- Support MCP protocol integration
- Handle JWT authorization
- Add OpenAPI targets from S3 or inline schemas

### MCP Commands (`mcp_cmds.sh`)
Shell script that orchestrates the gateway setup process including:
- Gateway creation
- Configuration validation
- Service registration
- Health checking

### Token Generation (`generate_token.py`)
Utility for generating JWT tokens for gateway authentication:
```bash
python generate_token.py --config config.yaml
```

### OpenAPI Integration (`openapi_s3_target_cognito.sh`)
Script for integrating OpenAPI specifications with S3 storage and Cognito authentication.

## 🔍 Usage

### Quick Reference
1. Configure your settings in `config.yaml`
2. Create the gateway: `./create_gateway.sh`
3. Test the gateway: `./mcp_cmds.sh`
4. For debugging, capture output: `./mcp_cmds.sh 2>&1 | tee mcp_cmds.log`
5. Verify gateway is running and accessible
6. Generate tokens as needed for client authentication

### Development Mode
For development and testing, you can also run components individually:

```bash
# Generate tokens
python generate_token.py

# Create gateway with specific config
python main.py --config config.yaml

# Add OpenAPI targets
./openapi_s3_target_cognito.sh
```

## ⚠️ Important Notes

- Always run `mcp_cmds.sh` from the gateway directory
- Ensure `config.yaml` is properly configured before setup
- The gateway must be running before starting SRE Agent investigations
- Keep authentication tokens secure and rotate them regularly
- Log files (*.log) are automatically ignored by git - safe to create for debugging

## 🔗 Integration

Once the gateway is set up and running, it provides the MCP endpoint that the SRE Agent core system connects to for accessing infrastructure APIs and tools.