# Running Commands on Amazon Bedrock AgentCore Code Interpreter - Tutorial

## Overview

This tutorial demonstrates how to use Amazon Bedrock AgentCore Code Interpreter to run commands (shell and AWS CLI). We will interact with AWS services, specifically focusing on S3 operations. We'll walk through:

1. Creating a python based code interpreter
2. Start code interpreter session
3. Run Commands(shell and AWS CLI)
4. Performing S3 operations(create bucket, copy objects, list bucket objects)
5. Cleanup (stop session and delete code interpreter)


### Tutorial Details

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Conversational                                                                   |
| Agent type          | Single                                                                           |
| Agentic Framework   | Langchain & Strands Agents                                                       |
| LLM model           | Anthropic Claude Sonnet 3.5 & 3.7                                                |
| Tutorial components | Amazon Bedrock AgentCore Code Interpreter                                                        |
| Tutorial vertical   | Cross-vertical                                                                   |
| Example complexity  | Easy                                                                             |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                                     |


### Tutorial Architecture

The code execution sandbox enables agents to safely process user queries by creating an isolated environment with a code interpreter, shell, and file system. After a Large Language Model helps with tool selection, code is executed within this session, before being returned to the user or agent for synthesis.

<div style="text-align:left">
    <img src="images/code_interpreter.png" width="100%"/>
</div>
