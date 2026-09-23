# Getting Started with harness in AgentCore 

This folder contains the introductory tutorial for harness in Amazon Bedrock AgentCore.

## What is harness in AgentCore?

Harness helps developers experiment and ship agents faster by letting them define and run agents in one API call, skipping framework setup, orchestration code, and deployment. Developers specify the model, system prompt, and tools in a single API call.

## Getting Started Guide

### With AgentCore CLI.

The markdown file [CLI](CLI.md) contains a complete walkthrough that demonstrates the harness workflow using AgentCore CLI.

**What you'll learn:**
1. Creating an agent and invoking it, using Bedrock model provider
2. Creating an agent and invoking it, using OpenAI model provider

### Getting Started using Jupyter notebook and boto3.

The jupyter notebook [01_getting_started_bedrock.ipynb](01_getting_started_bedrock.ipynb) contains a complete walkthrough that demonstrates the core Harness workflow:

**What you'll learn:**
1. Creating an IAM execution role with necessary permissions
2. Creating a Harness agent
3. Invoking the agent with prompts
4. Running shell commands on the agent's isolated microVM
5. Cleaning up resources

## Important Notes

- Each Harness session runs in an isolated Firecracker microVM
- Use `execute_command` to run shell commands on the agent's VM (not local `!` commands)
- The agent has `file_operations` and `shell` tools available by default
- Sessions are identified by a unique `session_id`

## Next Steps

After completing this tutorial, explore:
- [**01-advanced-examples/**](../01-advanced-examples/) — Custom containers, CLI scripts, and advanced patterns
- [**02-use-cases/**](../02-use-cases/) — Real-world applications like travel agents and webapp testing
