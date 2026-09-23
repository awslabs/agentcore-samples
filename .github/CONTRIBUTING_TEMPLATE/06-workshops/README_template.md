# Template Instructions

When creating a new tutorial in the `06-workshops` folder you **must** follow the following structure to have your PR merged.

Any tutorial must have: 
- a descriptive title 
- an overview section focused on what the tutorial should do including a tutorial architecture diagram
- the prerequisites to execute the example 
- the tutorial setup instructions
- the tutorial execution instructions
- any cleanup instructions

# <Replace me with tutorial sample title\>

## Overview

Add an overview of the tutorial that your agent is addressing. In this overview you should include the tutorial details following this table:

### tutorial details
| Information         | Details                                                                                                                             |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| tutorial type       | conversational or event-driven                                                                                                      |
| Agent type          | Single or multi-agent                                                                                                               |
| tutorial components | any component being demonstrated in your tutorial.<br/> Includes: tools, rag, multi-modality, observability, evaluation, and others |
| tutorial vertical   | the industry of your tutorial                                                                                                       |
| Example complexity  | Easy, intermediate or advance                                                                                                       |
| SDK used            | Amazon Bedrock AgentCore SDK, boto3, AWS CDK, AWS SDK for Java, ...                                                                 |


### tutorial Architecture 

The architecture diagram for your example

### tutorial key Features

Any functionality of your tutorial that you want to highlight

## Prerequisites

Prerequisites to execute your tutorial in a list format. For instance:
* Python 3.10+
* AWS credentials
* boto3 or Amazon Bedrock AgentCore SDK
* ...

## Disclaimer
The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments. Make sure to have Amazon Bedrock Guardrails in place to protect against [prompt injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).
 