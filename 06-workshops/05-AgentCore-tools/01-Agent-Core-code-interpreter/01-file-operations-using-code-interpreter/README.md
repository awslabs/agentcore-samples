# Amazon AgentCore Bedrock Code Interpreter - Getting Started Tutorial

## Overview

In this tutorial you will learn how to use AgentCore Bedrock Code Interpreter to:

1. Set up a sandbox environment
2. Load and analyze data
3. Execute code in a sandbox environment
4. Process and retrieve results


### Tutorial Details

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Conversational                                                                   |
| Tutorial components | Bedrock AgentCore Code Interpreter                                               |
| Tutorial vertical   | Cross-vertical                                                                   |
| Example complexity  | Easy                                                                             |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                                     |


### Tutorial Architecture

The code execution sandbox enables agents to safely process user queries by creating an isolated environment with a code interpreter, shell, and file system. After a Large Language Model helps with tool selection, code is executed within this session, before being returned to the user or agent for synthesis.

<div style="text-align:left">
    <img src="images/code_interpreter.png" width="100%"/>
</div>
