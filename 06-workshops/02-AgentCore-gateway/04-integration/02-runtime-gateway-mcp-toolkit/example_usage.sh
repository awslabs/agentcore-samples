#!/bin/bash
# Example usage of agentcore-mcp-toolkit with command line arguments

agentcore-mcp-toolkit \
  --region us-east-1 \
  --gateway-name "my-gateway-mcp-server" \
  --gateway-description "My AgentCore Gateway" \
  --runtime-configs '[
    {
      "name": "my-calculator-runtime",
      "description": "Calculator MCP Server",
      "entrypoint": "/path/to/calculator/server.py",
      "requirements_file": "/path/to/calculator/requirements.txt",
      "auto_create_execution_role": true,
      "auto_create_ecr": true
    },
    {
      "name": "my-helloworld-runtime", 
      "description": "HelloWorld MCP Server",
      "entrypoint": "/path/to/helloworld/server.py",
      "requirements_file": "/path/to/helloworld/requirements.txt",
      "auto_create_execution_role": true,
      "auto_create_ecr": true
    }
  ]'
