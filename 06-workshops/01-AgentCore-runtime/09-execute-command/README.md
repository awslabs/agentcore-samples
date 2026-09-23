# Execute Commands in Bedrock AgentCore Runtime

This tutorial demonstrates how to execute system commands directly within the Amazon Bedrock AgentCore Runtime environment using the `invoke_agent_runtime_command` API. Learn how to deploy an agent and run shell commands in its containerized runtime with real-time streaming output.

## Prerequisites

Before starting this tutorial, ensure you have:

- **AWS Account** with appropriate permissions for Bedrock AgentCore
- **AWS CLI** configured with credentials
- **Python 3.12+** installed
- **Jupyter Notebook** or JupyterLab
- **Access to Amazon Bedrock AgentCore** in your AWS region

Required Python packages:
```bash
pip install -r requirements.txt
```

## Getting Started

1. **Clone or download this repository**

2. **Open the notebook: [01_exec_command.ipynb](./01_exec_command.ipynb)**


3. **Follow the notebook cells sequentially**
   - The notebook contains step-by-step instructions with detailed comments
   - Remember to restart the kernel after creating the agent files (Step 2)

## What You'll Learn

By completing this tutorial, you will:

1. **Deploy a Bedrock AgentCore Agent** using direct Python code deployment (no Docker required)
2. **Invoke agents** using both high-level SDK methods and direct boto3 calls
3. **Execute shell commands** in the agent runtime environment with `invoke_agent_runtime_command`
4. **Stream command output** in real-time with proper event handling

## Tutorial Details

| **Attribute**         | **Details**                                          |
|-----------------------|------------------------------------------------------|
| **Tutorial Type**     | Command Execution in Agent Runtime                   |
| **Tool Type**         | Bedrock AgentCore Runtime                            |
| **Components**        | Agent deployment, Command execution, Event streaming |
| **Complexity Level**  | Medium                                               |
| **SDKs Used**         | boto3, bedrock-agentcore-starter-toolkit             |


## Tutorial Key Features

### 1. Direct Code Deployment
- No Docker required
- Deploy Python code directly to runtime
- Automatic dependency packaging

### 2. Agent Invocation Methods
- **High-level SDK**: Simplified invoke with toolkit
- **Direct boto3**: Full control with AWS SDK

### 3. Command Execution (⭐ Key Feature)
Execute arbitrary shell commands in the agent runtime:

```python
response = client.invoke_agent_runtime_command(
    agentRuntimeArn=agent_arn,
    body={
        'command': '/bin/bash -c "ls -l /tmp"',
        'timeout': 300
    }
)
```

### 4. Event Stream Handling
Process real-time command outputs.

## Use Cases

This command execution feature is valuable for:

- **Debugging**: Inspect the runtime environment
- **File Operations**: Manage files in the agent container
- **Integration Testing**: Run tests within the agent environment
- **Data Processing**: Execute scripts and process results
- **System Diagnostics**: Check runtime configuration and resources

## Project Structure

```
.
├── 01_exec_command.ipynb        # Main tutorial notebook
├── agents/
│   ├── agent.py                 # Agent entry point
│   └── requirements.txt         # Agent dependencies
└── README.md                    # This file
```

## Cleanup

To avoid ongoing charges, use the cleanup section in the notebook (Step 7):

```python
from bedrock_agentcore_starter_toolkit.operations.runtime.destroy import destroy_bedrock_agentcore

destroy_bedrock_agentcore(
    config_path=Path(".bedrock_agentcore.yaml"),
    agent_name="exec_cmd_sample"
)
```

## Additional Resources

- [Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/)
- [Boto3 API to executes a command in a runtime session container](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime_command.html)
