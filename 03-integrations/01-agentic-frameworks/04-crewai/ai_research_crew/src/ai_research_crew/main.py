#!/usr/bin/env python
import sys
import warnings
import os
import json

from datetime import datetime

from ai_research_crew.crew import AiResearchCrew

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information
# Finally write your entrypoint
@app.entrypoint
def run(payload=None):
    """
    Run the crew.
    """
    # Create inputs directly, using payload if provided
    inputs = {
        'topic': payload.get("prompt", "AI LLMs") if payload else "AI LLMs",
        'current_year': str(datetime.now().year)
    }
    
    try:
        # Run the crew
        AiResearchCrew().crew().kickoff(inputs=inputs)
        
        # Try to read the report file
        report_content = None
        report_path = "output/report.md"
        if os.path.exists(report_path):
            with open(report_path, "r") as f:
                report_content = f.read()
        
        # Return the report content if available
        if report_content:
            return json.dumps({"report": report_content})
        else:
            return json.dumps({"status": "Report generated but not available in API response. Check output directory or logs."})
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")


def train(payload=None):
    """
    Train the crew for a given number of iterations.
    """
    # Create inputs directly, using payload if provided
    inputs = {
        "topic": payload.get("prompt", "AI LLMs") if payload else "AI LLMs",
        'current_year': str(datetime.now().year)
    }
    try:
        AiResearchCrew().crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")

def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        AiResearchCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")

def test(payload=None):
    """
    Test the crew execution and returns the results.
    """
    # Create inputs directly, using payload if provided
    inputs = {
        "topic": payload.get("prompt", "AI LLMs") if payload else "AI LLMs",
        "current_year": str(datetime.now().year)
    }
    
    try:
        AiResearchCrew().crew().test(n_iterations=int(sys.argv[1]), eval_llm=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")

if __name__ == "__main__":
    app.run()
