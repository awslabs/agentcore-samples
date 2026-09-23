import base64
import boto3
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session
from langfuse import get_client
from utils.aws import get_ssm_parameter

boto_session = Session()
region = boto_session.region_name

agentcore_runtime = Runtime()


class ExistingAgentLaunchResult:
    """Mock launch result object for already-deployed agents to maintain API compatibility."""

    def __init__(self, agent_arn, agent_id, ecr_uri=None, status="ACTIVE"):
        self.agent_arn = agent_arn
        self.agent_id = agent_id
        self.ecr_uri = ecr_uri
        self.status = status
        self.already_deployed = True


LANGFUSE_PROJECT_NAME = get_ssm_parameter("/langfuse/LANGFUSE_PROJECT_NAME")
LANGFUSE_SECRET_KEY = get_ssm_parameter("/langfuse/LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = get_ssm_parameter("/langfuse/LANGFUSE_PUBLIC_KEY")
LANGFUSE_HOST = get_ssm_parameter("/langfuse/LANGFUSE_HOST")

# Langfuse configuration
otel_endpoint = f"{LANGFUSE_HOST}/api/public/otel"
langfuse_project_name = LANGFUSE_PROJECT_NAME
langfuse_secret_key = LANGFUSE_SECRET_KEY
langfuse_public_key = LANGFUSE_PUBLIC_KEY
langfuse_auth_token = base64.b64encode(f"{langfuse_public_key}:{langfuse_secret_key}".encode()).decode()
otel_auth_header = f"Authorization=Basic {langfuse_auth_token}"


def deploy_agent(model, system_prompt, force_redeploy=False, environment="DEV"):
    """
    Deploys an Amazon Bedrock AgentCore Runtime agent with the specified configuration.

    Parameters:
    - model (dict): Dictionary containing model name and model_id
    - system_prompt (dict): Dictionary containing prompt name and prompt text
    - force_redeploy (bool): If True, redeploys the agent even if it already exists (default: False)

    Returns:
    - dict: The launch result from AgentCore Runtime, or existing agent info if already deployed
    """
    agent_name = f"strands_{model['name']}_{system_prompt['name']}_{environment}"

    # Check if the agent already exists
    try:
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=region)

        # List all agent runtimes to check if this agent already exists
        list_response = agentcore_control_client.list_agent_runtimes()
        existing_agents = list_response.get("agentRuntimes", [])
        # Check if an agent with this name already exists
        existing_agent = None
        for agent_summary in existing_agents:
            if agent_summary.get("agentRuntimeName") == agent_name:
                existing_agent = agent_summary
                break

        # If agent exists and force_redeploy is False, return existing agent info
        if existing_agent and not force_redeploy:
            print(f"Agent '{agent_name}' already exists. Skipping deployment.")
            print(f"Agent Runtime ARN: {existing_agent.get('agentRuntimeArn')}")
            print(f"Status: {existing_agent.get('status')}")

            # Get full agent runtime details to extract ECR URI
            agent_runtime_id = existing_agent.get("agentRuntimeId")
            agent_runtime_arn = existing_agent.get("agentRuntimeArn")

            try:
                get_response = agentcore_control_client.get_agent_runtime(agentRuntimeId=agent_runtime_id)
                ecr_uri = get_response.get("ecrUri", "")
            except Exception as e:
                print(f"Warning: Could not retrieve ECR URI: {str(e)}")
                ecr_uri = ""

            # Create a compatible launch result object
            launch_result = ExistingAgentLaunchResult(
                agent_arn=agent_runtime_arn,
                agent_id=agent_runtime_id,
                ecr_uri=ecr_uri,
                status=existing_agent.get("status", "ACTIVE"),
            )

            return {
                "agent_name": agent_name,
                "launch_result": launch_result,
                "model_id": model["model_id"],
                "system_prompt_id": system_prompt["name"],
            }

        # If agent exists and force_redeploy is True, inform the user
        if existing_agent and force_redeploy:
            print(f"Agent '{agent_name}' already exists. Force redeploying...")

    except Exception as e:
        print(f"Error checking existing agents: {str(e)}")
        print("Proceeding with deployment...")

    # Proceed with deployment
    response = agentcore_runtime.configure(
        entrypoint="./agents/strands_claude.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="./agents/requirements.txt",
        region=region,
        agent_name=agent_name,
        disable_otel=True,
        memory_mode="NO_MEMORY",
    )

    print(response)

    # Agent configuration
    bedrock_model_id = model["model_id"]
    system_prompt_value = system_prompt["prompt"]

    launch_result = agentcore_runtime.launch(
        env_vars={
            "BEDROCK_MODEL_ID": bedrock_model_id,
            "LANGFUSE_PROJECT_NAME": langfuse_project_name,
            "LANGFUSE_TRACING_ENVIRONMENT": environment,
            "OTEL_EXPORTER_OTLP_ENDPOINT": otel_endpoint,  # Use Langfuse OTEL endpoint
            "OTEL_EXPORTER_OTLP_HEADERS": otel_auth_header,  # Add Langfuse OTEL auth header
            "DISABLE_ADOT_OBSERVABILITY": "true",
            "SYSTEM_PROMPT": system_prompt_value,
        }
    )

    print(launch_result)

    return {
        "agent_name": agent_name,
        "launch_result": launch_result,
        "model_id": model["model_id"],
        "system_prompt_id": system_prompt["name"],
    }


def invoke_agent(agent_arn, prompt, session_id=None, environment=None):
    """
    Invokes an Amazon Bedrock AgentCore Runtime agent with the given prompt.

    Parameters:
    - agent_arn (str): The ARN of the deployed agent runtime
    - prompt (str): The input prompt for the agent
    - session_id (str, optional): A unique identifier for the session

    Returns:
    - dict: The agent's response
    """
    import json
    import uuid

    try:
        # Initialize the Bedrock AgentCore client
        agent_core_client = boto3.client("bedrock-agentcore", region_name=region)

        if environment == "DEV":
            trace_id = get_client().get_current_trace_id()
            obs_id = get_client().get_current_observation_id()

            payload = json.dumps({"prompt": prompt, "trace_id": trace_id, "parent_obs_id": obs_id}).encode()
        else:
            payload = json.dumps({"prompt": prompt}).encode()

        # Generate session_id if not provided
        if session_id is None:
            session_id = str(uuid.uuid4())

        # Invoke the agent
        response = agent_core_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn, runtimeSessionId=session_id, payload=payload
        )

        # Process the response based on content type
        content_type = response.get("contentType", "")

        if "text/event-stream" in content_type:
            # Handle streaming response
            content = []
            for line in response["response"].iter_lines(chunk_size=10):
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        line = line[6:]
                        content.append(line)

            return {
                "response": "\n".join(content),
                "session_id": session_id,
                "content_type": content_type,
            }

        elif content_type == "application/json":
            # Handle standard JSON response
            content = []
            for chunk in response.get("response", []):
                content.append(chunk.decode("utf-8"))

            return {
                "response": json.loads("".join(content)),
                "session_id": session_id,
                "content_type": content_type,
            }

        else:
            # Return raw response for other content types
            return {
                "response": response,
                "session_id": session_id,
                "content_type": content_type,
            }

    except Exception as e:
        return {"error": str(e), "agent_arn": agent_arn}


def delete_agent(agent_runtime_id, ecr_uri):
    """
    Deletes an Amazon Bedrock AgentCore Runtime agent and its ECR repository.

    Parameters:
    - agent_runtime_id (str): The agent runtime ID to delete
    - ecr_uri (str): The ECR URI of the agent's container repository

    Returns:
    - dict: The status of the deletion operation
    """
    try:
        # Initialize the Bedrock AgentCore Control client
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=region)

        # Initialize the ECR client
        ecr_client = boto3.client("ecr", region_name=region)

        # Delete the agent runtime
        runtime_delete_response = agentcore_control_client.delete_agent_runtime(
            agentRuntimeId=agent_runtime_id,
        )

        print(f"ECR repository: {ecr_uri}")

        # Delete the ECR repository
        repository_name_tmp = ecr_uri.split("/")[1] if "/" in ecr_uri else ecr_uri

        print(f"Repository name 1: {repository_name_tmp}")

        repository_name = repository_name_tmp.split(":")[0] if ":" in repository_name_tmp else repository_name_tmp

        print(f"Repository name 1: {repository_name}")

        print(f"Deleting ECR repository: {repository_name}")

        ecr_delete_response = ecr_client.delete_repository(repositoryName=repository_name, force=True)

        return {
            "status": "success",
            "agent_runtime_id": agent_runtime_id,
            "runtime_delete_response": runtime_delete_response,
            "ecr_delete_response": ecr_delete_response,
        }

    except Exception as e:
        return {
            "status": "error",
            "agent_runtime_id": agent_runtime_id,
            "error": str(e),
        }
