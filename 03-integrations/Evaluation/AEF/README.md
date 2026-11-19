# Agentic Evaluation Framework (AEF)

The Agent Evaluation Framework (AEF) is a comprehensive end-to-end evaluation framework designed to replace trial-and-error development with evidence-based measurement. The framework is built to be framework-agnostic, working seamlessly with agents built using AgentCore, LangGraph, etc. 
AEF recognizes that agent success requires excellence across multiple dimensions simultaneously: calling the right tools with correct parameters, generating high-quality responses, and operating efficiently at scale. The framework measures all these aspects independently, giving you the comprehensive visibility needed to build production-ready agents systematically.
AEF evaluates agents across three critical dimensions:
- Tool Calling Evaluation
- Response Quality Evaluation
- Operational Evaluation

AEF provides native integration with Amazon Bedrock AgentCore—Amazon's enterprise-grade platform for deploying AI agents at scale.
AgentCore Integration Features:
- Automatic Agent Discovery: List and evaluate all deployed agents
- Trace Collection: Seamless capture from CloudWatch logs
- Evaluation/ Monitoring: Real-time evaluation of live interactions
- Enterprise Security: Evaluation within AWS security boundaries

## Getting started

1. Install required dependencies in your Python environment
pip install -r requirements.txt
Note: If there is failure related to ragas-evaluation when running the above command, go to ragas-evaluation folder and run `git init`.

2. Run the AgentCore_Evaluation notebook and evaluate your agent or add the code to your pipeline.

Sample Results<br>
{<br>
    'missed_tool_pct': 1.0,        # Tool calling accuracy <br>
    'tools_args_acc': 1.0,         # Parameter precision <br>
    'answer_correctness': 0.9,     # Response quality  <br>
    'answer_precision': 0.0,       # Information focus <br>
    'latency': 2.3                 # Operational performance <br>
}

