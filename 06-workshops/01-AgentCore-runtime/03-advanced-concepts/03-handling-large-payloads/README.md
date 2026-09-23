# Handling Large Multi-Modal Payloads in AgentCore Runtime

## Overview

This tutorial demonstrates how Amazon Bedrock AgentCore Runtime handles large payloads up to 100MB, including multi-modal content such as Excel files and images. AgentCore Runtime is designed to process rich media content and large datasets seamlessly.

### Tutorial Details

| Information         | Details                                                      |
|:--------------------|:-------------------------------------------------------------|
| Tutorial type       | Large Payload & Multi-Modal Processing                       |
| Agent type          | Single                                                       |
| Agentic Framework   | Strands Agents                                               |
| LLM model           | Anthropic Claude Haiku 4.5                                    |
| Tutorial components | Large File Processing, Image Analysis, Excel Data Processing |
| Tutorial vertical   | Data Analysis & Multi-Modal AI                               |
| Example complexity  | Intermediate                                                 |
| SDK used            | Amazon BedrockAgentCore Python SDK                           |

### Key Features

* **Large Payload Support**: Process files up to 100MB in size
* **Multi-Modal Processing**: Handle Excel files, images, and text simultaneously
* **Data Analysis**: Extract insights from structured data and visual content
* **Base64 Encoding**: Secure transmission of binary data through JSON payloads
