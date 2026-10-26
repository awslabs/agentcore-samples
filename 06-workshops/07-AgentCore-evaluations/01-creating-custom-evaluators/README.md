# Creating Evaluators

## Overview
In this tutorial you will learn about AgentCore Evaluations built-in and custom metrics.
You'll learn when to use each type and how to create custom evaluators tailored to your specific needs.

## What You'll Learn
- Understanding built-in evaluators and their use cases
- Creating custom evaluators for specialized requirements
- Selecting the right evaluation approach for your agents

## Evaluator Types

### Built-in Evaluators
Built-in evaluators are pre-configured evaluators that use Large Language Models (LLMs) as judges to assess agent performance. 

**Key Characteristics:**
- **Pre-configured**: Come with carefully crafted prompt templates, selected evaluator models, and standardized scoring criteria
- **Ready to use**: No additional configuration required—start evaluating immediately
- **Consistent**: Fixed configurations ensure reliability and consistency across assessments
- **Comprehensive**: Cover 13 critical evaluation dimensions including correctness, helpfulness, and safety

**When to Use Built-in Evaluators:**
- You need to implement quality evaluations quickly
- You want standardized assessment metrics across teams or projects
- Your evaluation needs align with common quality dimensions
- You prioritize consistency and reliability over customization


The following built-in evaluators are available for your use cases:
* Response quality metrics:
  * **Builtin.Correctness**: Evaluates whether the information in the agent's response is factually accurate
  * **Builtin.Faithfulness**: Evaluates whether information in the response is supported by provided context/sources
  * **Builtin.Helpfulness**: Evaluates from user's perspective how useful and valuable the agent's response is
  * **Builtin.ResponseRelevance**: Evaluates whether the response appropriately addresses the user's query
  * **Builtin.Conciseness**: Evaluates whether the response is appropriately brief without missing key information
  * **Builtin.Coherence**: Evaluates whether the response is logically structured and coherent
  * **Builtin.InstructionFollowing**: Measures how well the agent follows the provided system instructions
  * **Builtin.Refusal**: Detects when agent evades questions or directly refuses to answer
* Task completion metrics:
  * **Builtin.GoalSuccessRate**: Evaluates whether the conversation successfully meets the user's goals
* Tool level metrics:
  * **Builtin.ToolSelectionAccuracy**: Evaluates whether the agent selected the appropriate tool for the task
  * **Builtin.ToolParameterAccuracy**: Evaluates how accurately the agent extracts parameters from user queries
* Safety metrics:
  * **Builtin.Harmfulness**: Evaluates whether the response contains harmful content
  * **Builtin.Stereotyping**: Detects content that makes generalizations about individuals or groups

**Note:** Built-in evaluator configurations cannot be modified to maintain evaluation consistency and reliability across all users, but you can create your own evaluator using as base a built-in one.

### Custom Evaluators
Custom evaluators provide maximum flexibility by allowing you to define every aspect of your evaluation process while leveraging LLMs as underlying judges.

**Customization Options:**
- **Evaluator model**: Choose the LLM that best fits your evaluation needs
- **Evaluation prompts**: Craft evaluation instructions specific to your use case
- **Scoring schema**: Design scoring systems that align with your organization's metrics

**When to Use Custom Evaluators:**
- You're evaluating domain-specific agents (e.g., healthcare, finance, legal)
- You have unique quality standards or compliance requirements
- You need specialized scoring systems aligned with organizational KPIs
- Built-in evaluators don't capture your specific evaluation dimensions

**Example Use Cases:**
- Healthcare agents requiring HIPAA compliance evaluation
- Financial agents needing regulatory adherence scoring
- Customer service agents evaluated against brand-specific quality standards
- Technical support agents assessed on troubleshooting methodology

## Next Steps
After completing this tutorial, proceed to [Using On-demand Evaluation](../02-running-evaluations) to learn how to apply these evaluators to your agent traces.
