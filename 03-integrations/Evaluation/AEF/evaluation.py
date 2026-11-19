import pandas as pd
import numpy as np
from collections import Counter
import boto3
import json
from datasets import Dataset
import sys
import time
from typing import List, Dict

from langchain_aws import ChatBedrock
from langchain_community.embeddings import BedrockEmbeddings

sys.path.append("../ragas-evaluation/src/")

from ragas import evaluate
from ragas.metrics._answer_precision import answer_precision
from ragas.metrics._answer_recall import answer_recall
from ragas.metrics._answer_correctness import answer_correctness
from ragas.metrics._answer_similarity import answer_similarity


def get_answers_detail_langgraph(
    user_input, graph, agent_node_name, tool_node_name, config
):
    """
    Extract detailed information from graph execution including tools used and responses.

    Args:
        user_input: Input text/message to process
        graph: The processing graph object
        agent_node_name: Name of the agent node in the graph
        tool_node_name: Name of the tool node in the graph
        config: agent config includong thread id

    Returns:
        tuple: Lists of called tools, their arguments, answers, responses, and token counts
    """
    try:
        # Initialize tracking lists
        called_tools_list = []
        called_tools_args = []
        called_tools_ans = []
        responses = []
        input_tokens = 0
        output_tokens = 0

        # Process input through graph
        for output in graph.stream({"messages": user_input}, config):
            for key, value in output.items():
                if key == agent_node_name:
                    if isinstance(value["messages"], list):
                        value_messages = value["messages"][-1]
                    else:
                        value_messages = value["messages"]
                    # Extract tool calls and token usage from agent node
                    current_tool = value_messages.tool_calls
                    input_tokens += value_messages.usage_metadata["input_tokens"]
                    output_tokens += value_messages.usage_metadata["output_tokens"]

                    # Record tool usage or response
                    if len(current_tool) > 0:
                        called_tools_list.append(current_tool[0]["name"])
                        called_tools_args.append(current_tool[0]["args"])
                    else:
                        responses.append(value_messages.content)
                elif key == tool_node_name:
                    # Record tool answers
                    called_tools_ans.append(value)

        return (
            called_tools_list,
            called_tools_args,
            called_tools_ans,
            responses,
            input_tokens,
            output_tokens,
        )

    except KeyError as e:
        raise ValueError(f"Missing expected key in graph output: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Error processing graph output: {str(e)}")


def get_answers_detail_bedrock(user_input, agent_id, alias_id, session_id, config):
    """
    Process user input through a Bedrock agent and return detailed information about the interaction.

    Args:
        user_input (str): The user's input text
        agentId (str): The ID of the Bedrock agent
        alias_id (str): The alias ID for the agent
        session_id (str): The session ID for the interaction
        config (dict): Configuration parameters for the session

    Returns:
        tuple: Contains lists of called tools, their arguments, answers, responses,
               and the total input and output tokens used.

    Raises:
        ValueError: If expected keys are missing in the agent's response
        RuntimeError: For any other processing errors
    """
    try:
        # Initialize tracking lists and counters
        called_tools_list = []
        called_tools_args = []
        called_tools_ans = []
        responses = []
        input_tokens = 0
        output_tokens = 0

        # Create Bedrock Agent Runtime client
        bedrock_agent_runtime_client = boto3.client("bedrock-agent-runtime")

        # Invoke the Bedrock agent with the user input
        agent_response = bedrock_agent_runtime_client.invoke_agent(
            inputText=user_input,
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=session_id,
            enableTrace=True,
            endSession=False,
            sessionState=config,
        )

        # Process the event stream from the agent's response
        event_stream = agent_response["completion"]
        for event in event_stream:
            if "chunk" in event:
                # Decode and store text chunks from the response
                data = event["chunk"]["bytes"]
                responses.append(data.decode("utf8"))
            elif "trace" in event:
                # Process trace information
                cur_trace = event["trace"]["trace"]["orchestrationTrace"]
                if "invocationInput" in cur_trace.keys():
                    # Extract information about called tools and their parameters
                    tool_info = cur_trace["invocationInput"][
                        "actionGroupInvocationInput"
                    ]
                    called_tools_list.append(tool_info["function"])
                    called_tools_args.extend(
                        [params["value"] for params in tool_info["parameters"]]
                    )
                if "modelInvocationOutput" in cur_trace.keys():
                    # Accumulate token usage information
                    input_tokens += cur_trace["modelInvocationOutput"]["metadata"][
                        "usage"
                    ]["inputTokens"]
                    output_tokens += cur_trace["modelInvocationOutput"]["metadata"][
                        "usage"
                    ]["outputTokens"]

        # Return collected information
        return (
            called_tools_list,
            called_tools_args,
            called_tools_ans,
            responses,
            input_tokens,
            output_tokens,
        )

    except KeyError as e:
        # Raise an error if expected keys are missing in the response
        raise ValueError(f"Missing expected key in agent response: {str(e)}")
    except Exception as e:
        # Catch and re-raise any other errors
        raise RuntimeError(f"Error processing agent response: {str(e)}")


def save_agent_responses(
    agent_type="langgraph",
    agent_params={},
    config=None,
    output_path=None,
    gt_df=None,
    question_col="Questions",
):
    """
    Process inputs through an agent and save results to a CSV file.

    Args:
        agent_type (str): Type of agent to use ("langgraph" or "bedrock")
        agent_params (dict): Parameters specific to the chosen agent type
        config: Configuration object for the agent
        output_path (str): Path to save the output CSV file
        gt_df (pd.DataFrame): Input DataFrame containing questions
        question_col (str): Column name containing questions in gt_df

    Raises:
        TypeError: If gt_df is not a pandas DataFrame
        ValueError: If question_col is not found in gt_df or if agent_type is invalid
        IOError: If there's an error saving the CSV file
        RuntimeError: For any other processing errors
    """

    try:
        # Validate inputs
        if not isinstance(gt_df, pd.DataFrame):
            raise TypeError("gt_df must be a pandas DataFrame")
        if question_col not in gt_df.columns:
            raise ValueError(f"Column '{question_col}' not found in DataFrame")

        # Initialize tracking lists
        tools_list = []
        args_list = []
        ans_list = []
        response_list = []
        latency_list = []
        input_tokens_list = []
        output_tokens_list = []

        # Process each question
        for idx, row in gt_df.iterrows():
            user_input = row[question_col]
            start_time = time.time()

            # Get detailed responses based on agent type
            if agent_type == "langgraph":
                (
                    called_tools,
                    called_tools_args,
                    called_tools_ans,
                    responses,
                    input_tokens,
                    output_tokens,
                ) = get_answers_detail_langgraph(
                    user_input,
                    agent_params["agent"],
                    agent_params["agent_node_name"],
                    agent_params["tool_node_name"],
                    config,
                )
            elif agent_type == "bedrock":
                (
                    called_tools,
                    called_tools_args,
                    called_tools_ans,
                    responses,
                    input_tokens,
                    output_tokens,
                ) = get_answers_detail_bedrock(
                    user_input,
                    agent_params["agentId"],
                    agent_params["alias_id"],
                    agent_params["session_id"],
                    config,
                )
            else:
                raise ValueError(
                    f"Invalid agent type. Expected 'langgraph' or 'bedrock', got '{agent_type}'"
                )

            end_time = time.time()
            time.sleep(10)

            # Record results
            tools_list.append(called_tools)
            args_list.append(called_tools_args)
            ans_list.append(called_tools_ans)
            response_list.append(responses)
            latency_list.append(end_time - start_time)
            input_tokens_list.append(input_tokens)
            output_tokens_list.append(output_tokens)

        # Add results to DataFrame
        gt_df["called_tools"] = tools_list
        gt_df["called_tools_args"] = args_list
        gt_df["called_tools_ans"] = ans_list
        gt_df["responses"] = response_list
        gt_df["final_answer"] = gt_df["responses"].apply(lambda x: x[-1] if x else "")
        gt_df["latency"] = latency_list
        gt_df["input_tokens"] = input_tokens_list
        gt_df["output_tokens"] = output_tokens_list

        # Save to CSV
        gt_df.to_csv(output_path, index=False)

    except (IOError, PermissionError) as e:
        raise IOError(f"Error saving to {output_path}: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Error processing responses: {str(e)}")


def incorrect_tool_pct(gt_tools, called_tools):
    """
    Calculate the percentage of incorrectly called tools.

    Args:
        gt_tools (list): Ground truth tools list
        called_tools (list): Actually called tools list

    Returns:
        float: Percentage of incorrect tools

    Raises:
        ValueError: If input lists are empty
        TypeError: If inputs are not lists
    """
    try:
        # Input validation
        if not isinstance(gt_tools, list) or not isinstance(called_tools, list):
            raise TypeError("Inputs must be lists")
        if len(called_tools) == 0:
            raise ValueError("Called tools list cannot be empty")

        incorrect = 0
        gt_count = Counter(gt_tools)
        tools_count = Counter(called_tools)

        # Calculate incorrect tools count
        for t in tools_count.keys():
            if t not in gt_count:
                incorrect += tools_count[t]
            else:
                incorrect += max(0, tools_count[t] - gt_count[t])

        return incorrect / len(called_tools)

    except Exception as e:
        print(f"Error in incorrect_tool_pct: {str(e)}")
        return None


def missed_tool_pct(gt_tools, called_tools):
    """
    Calculate the percentage of missed tools.

    Args:
        gt_tools (list): Ground truth tools list
        called_tools (list): Actually called tools list

    Returns:
        float: Percentage of missed tools

    Raises:
        ValueError: If ground truth list is empty
        TypeError: If inputs are not lists
    """
    try:
        # Input validation
        if not isinstance(gt_tools, list) or not isinstance(called_tools, list):
            raise TypeError("Inputs must be lists")
        if len(gt_tools) == 0:
            raise ValueError("Ground truth tools list cannot be empty")

        missed = 0
        gt_count = Counter(gt_tools)
        tools_count = Counter(called_tools)

        # Calculate missed tools count
        for t in gt_count.keys():
            if t not in tools_count:
                missed += gt_count[t]
            else:
                missed += max(0, gt_count[t] - tools_count[t])

        return missed / len(gt_tools)

    except Exception as e:
        print(f"Error in missed_tool_pct: {str(e)}")
        return None


def args_acc(gt_tools, gt_args, called_tools, called_tools_args):
    """
    Calculate the accuracy of tool arguments.

    Args:
        gt_tools (list): Ground truth tools list
        gt_args (list): Ground truth arguments list
        called_tools (list): Actually called tools list
        called_tools_args (list): Actually called tools arguments

    Returns:
        float: Argument accuracy score

    Raises:
        ValueError: If input lists are empty or of unequal lengths
        TypeError: If inputs are not in correct format
    """
    try:
        # Create ground truth arguments dictionary
        gt_args_dict = {}
        for i in range(len(gt_tools)):
            if gt_tools[i] in gt_args_dict:
                gt_args_dict[gt_tools[i]].extend(gt_args[i])
            else:
                gt_args_dict[gt_tools[i]] = gt_args[i]

        # Create called arguments dictionary
        args_dict = {}
        for i in range(len(called_tools)):
            cur = (
                ",".join([str(ii) for ii in called_tools_args[i].values()])
                .replace(" ", "")
                .split(",")
            )
            if called_tools[i] in args_dict:
                args_dict[called_tools[i]].extend(cur)
            else:
                args_dict[called_tools[i]] = cur

        # Calculate accuracy
        total = 0
        correct = 0
        for t in args_dict:
            if t in gt_args_dict:
                diff = Counter(gt_args_dict[t]) - Counter(args_dict[t])
                diff.pop("None", None)
                correct += len(gt_args_dict[t]) - len(list(diff.elements()))
                total += len(gt_args_dict[t])
        if total == 0:
            return 1
        return correct / total

    except Exception as e:
        print(f"Error in args_acc: {str(e)}")
        return None


def llm_as_judge_score(
    question: str,
    reference: str,
    response: str,
    judge_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    max_tokens: int = 4096,
    top_k: int = 50,
    top_p: float = 0.1,
    temperature: float = 0.1,
) -> float:
    """
    Evaluate response quality using an LLM judge.

    Args:
        question (str): The original question
        reference (str): The reference/ground truth answer
        response (str): The response to evaluate
        judge_id (str): The LLM model ID to use as judge
        max_tokens (int): Maximum tokens for response
        top_k (int): Top K parameter for sampling
        top_p (float): Top P parameter for sampling
        temperature (float): Temperature parameter for sampling

    Returns:
        float: Evaluation score between 0 and 1

    Raises:
        ValueError: If inputs are invalid
        BotoClientError: If there's an AWS Bedrock API error
    """
    try:
        # Input validation
        if not all(isinstance(x, str) for x in [question, reference, response]):
            raise ValueError("Question, reference and response must be strings")
        if not all(len(x.strip()) > 0 for x in [question, reference, response]):
            raise ValueError("Question, reference and response cannot be empty")

        # Initialize AWS Bedrock client
        try:
            bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
        except Exception as e:
            raise ConnectionError(f"Failed to initialize Bedrock client: {str(e)}")

        # Prepare prompt
        resp_fmt = """{
                       "score":float,
                       "reasoning": str
                   }
               """

        user_prompt = """
            You are an AI evaluator that helps in evaluating final response from LLM agent. 
            Please act as an impartial judge and evaluate the correctness and format of the response
            provided by an AI agent to the user question displayed below. You will be given a reference answer 
            and the agent's answer. Begin your evaluation by comparing the agents's answer with the reference answer. 
            Identify any mistakes or missing information. After providing your explanation in the "reasoning" tab, 
            you must score the response on a scale of 0 to 1 in the "score" tab. 
            Strictly follow the below json format:{resp_fmt}.
            \n\n[Question]\n{question}\n\n[The Start of Reference Answer]\n{reference}\n
            [The End of Reference Answer]\n\n[The Start of Agent's Answer]\n{response}\n[The End of Agents's Answer]"""

        prompt = user_prompt.format(
            resp_fmt=resp_fmt, question=question, reference=reference, response=response
        )

        # Prepare request body
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "top_k": top_k,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop_sequences": ["Human"],
            }
        )

        # Make API call
        try:
            response = bedrock_client.invoke_model(
                modelId=judge_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
        except Exception as e:
            raise RuntimeError(f"Bedrock API call failed: {str(e)}")

        # Parse response
        response_body = json.loads(response.get("body").read())
        response_score = json.loads(
            response_body.get("content")[0]["text"], strict=False
        )["score"]

        # Validate score
        if not (0 <= response_score <= 1):
            raise ValueError(f"Invalid score received: {response_score}")

        return response_score

    except Exception as e:
        print(f"Error in llm_as_judge_score: {str(e)}")
        return None


def llm_as_judge_score_answer_relevancy(
    question: str,
    response: str,
    judge_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    max_tokens: int = 4096,
    top_k: int = 50,
    top_p: float = 0.1,
    temperature: float = 0.1,
) -> float:
    """
    Evaluate response quality using an LLM judge.

    Args:
        question (str): The original question
        response (str): The response to evaluate
        judge_id (str): The LLM model ID to use as judge
        max_tokens (int): Maximum tokens for response
        top_k (int): Top K parameter for sampling
        top_p (float): Top P parameter for sampling
        temperature (float): Temperature parameter for sampling

    Returns:
        float: Evaluation score between 0 and 1

    Raises:
        ValueError: If inputs are invalid
        BotoClientError: If there's an AWS Bedrock API error
    """
    try:
        # Input validation
        if not all(isinstance(x, str) for x in [question, response]):
            raise ValueError("Question and response must be strings")
        if not all(len(x.strip()) > 0 for x in [question, response]):
            raise ValueError("Question and response cannot be empty")

        # Initialize AWS Bedrock client
        try:
            bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
        except Exception as e:
            raise ConnectionError(f"Failed to initialize Bedrock client: {str(e)}")

        # Prepare prompt
        resp_fmt = """{
                       "score":float,
                       "reasoning": str
                   }
               """

        user_prompt = """
            You are an AI evaluator that helps in evaluating final response from LLM agent. 
            Please act as an impartial judge and evaluate the correctness and format of the response
            provided by an AI agent to the user question displayed below. You will be given the question and 
            the agent's answer. In your evaluation, check if the agent answer is relevant to the user question. 
            Identify any mistakes or missing information. After providing your explanation in the "reasoning" tab, 
            you must score the response on a scale of 0 to 1 in the "score" tab. 
            Strictly follow the below json format:{resp_fmt}.
            \n\n[Question]\n{question}\n\n[The Start of Agent's Answer]\n{response}\n[The End of Agents's Answer]"""

        prompt = user_prompt.format(
            resp_fmt=resp_fmt, question=question, response=response
        )

        # Prepare request body
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "top_k": top_k,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop_sequences": ["Human"],
            }
        )

        # Make API call
        try:
            response = bedrock_client.invoke_model(
                modelId=judge_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
        except Exception as e:
            raise RuntimeError(f"Bedrock API call failed: {str(e)}")

        # Parse response
        response_body = json.loads(response.get("body").read())
        response_score = json.loads(
            response_body.get("content")[0]["text"], strict=False
        )["score"]

        # Validate score
        if not (0 <= response_score <= 1):
            raise ValueError(f"Invalid score received: {response_score}")

        return response_score

    except Exception as e:
        print(f"Error in llm_as_judge_score: {str(e)}")
        raise e
        return None


def llm_as_judge_score_tool_calling(
    question: str,
    called_tools: str,
    all_tools: str,
    judge_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    max_tokens: int = 4096,
    top_k: int = 50,
    top_p: float = 0.1,
    temperature: float = 1,
) -> float:
    """
    Evaluate response quality using an LLM judge.

    Args:
        question (str): The original question
        called_tools (str): comma seperated list of tools agent calls in order to generate response
        all_tools (str): comma seperated list of tools that agent has access
        judge_id (str): The LLM model ID to use as judge
        max_tokens (int): Maximum tokens for response
        top_k (int): Top K parameter for sampling
        top_p (float): Top P parameter for sampling
        temperature (float): Temperature parameter for sampling

    Returns:
        float: Evaluation score between 0 and 1

    Raises:
        ValueError: If inputs are invalid
        BotoClientError: If there's an AWS Bedrock API error
    """
    try:
        # Initialize AWS Bedrock client
        try:
            bedrock_client = boto3.client("bedrock-runtime", region_name="us-east-1")
        except Exception as e:
            raise ConnectionError(f"Failed to initialize Bedrock client: {str(e)}")

        # Prepare prompt
        resp_fmt = """{
                       "score":float,
                       "reasoning": str
                   }
               """

        user_prompt = """
            You are an AI evaluator that helps in evaluating tool calling process in LLM agent.
            Please act as an impartial judge and evaluate the called tools and their order called 
            by an AI agent to find the answer of user query. You will be given user query and list of all tools that 
            the agent has access to, as well as list of tools agent called in order to answer the query. Begin your 
            evaluation by finding what tools among list of all tools agent needs to answer user query. Identify any 
            wrong tool calling, missing tools or missing orders.
            Note that you have to penalize the agent if it calles any tool that is not available in the list of 
            available tools provided here.
            If list of available tool is empty, it means agent should not call any tool, so any called tool is a 
            mistake.
            After providing your explanation in the "reasoning" tab, 
            you must score the called tools on a scale of 0 to 1 in the "score" tab. 
            Strictly follow the below json format:{resp_fmt}.
            \n\n[Query]\n{question}\n\n[The Start of List of Available Tools]\n{all_tools}\n
            [The End of List of Available Tools]\n\n[The Start of Tools Agent called]\n{called_tools}\n[The End of 
            Tools Agent called]"""

        prompt = user_prompt.format(
            resp_fmt=resp_fmt,
            question=question,
            all_tools=all_tools,
            called_tools=called_tools,
        )

        # Prepare request body
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": prompt}]}
                ],
                "top_k": top_k,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stop_sequences": ["Human"],
            }
        )

        # Make API call
        try:
            response = bedrock_client.invoke_model(
                modelId=judge_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
        except Exception as e:
            raise RuntimeError(f"Bedrock API call failed: {str(e)}")

        # Parse response
        response_body = json.loads(response.get("body").read())
        response_score = json.loads(
            response_body.get("content")[0]["text"], strict=False
        )["score"]

        # Validate score
        if not (0 <= response_score <= 1):
            raise ValueError(f"Invalid score received: {response_score}")

        return response_score

    except Exception as e:
        print(f"Error in llm_as_judge_score: {str(e)}")
        return None


def calc_metrics(
    data_df: pd.DataFrame,
    metric_list: List[str],
    tool_calling_weight: List[str] = [1, 1, 1],
    column_map: Dict[str, str] = None,
    available_tools: str = None,
    eval_embedId: str = "amazon.titan-embed-text-v2:0",
    eval_modelId: str = "anthropic.claude-3-haiku-20240307-v1:0",
    # running using claude 3 haiku, put in config
) -> pd.DataFrame:
    """
    Calculate multiple evaluation metrics for a dataset.

    Args:
        data_df (pd.DataFrame): Input dataframe containing evaluation data
        metric_list (List[str]): List of metrics to calculate. Supported metrics:
            - incorrect_tool_pct
            - missed_tool_pct
            - tools_args_acc
            - tool_calling_perf
            - response_acc_llm_judge
            - answer_precision
            - answer_recall
            - answer_correctness
            - answer_similarity
            - answer_relevancy (no GT)
            - tool_calling_accuracy (no GT)
        tool_calling_weight (List[str]): Weights for tool calling performance calculation
        column_map (Dict[str, str], optional): Mapping of column names for Ragas metrics
        available_tools (str): Comma-separated list of all available tools for tool_calling_accuracy
        eval_embedId (str): Embedding model ID for evaluation
        eval_modelId (str): LLM model ID for evaluation

    Returns:
        pd.DataFrame: Original dataframe with additional columns for calculated metrics

    Raises:
        ValueError: If inputs are invalid
        KeyError: If required columns are missing
        RuntimeError: If metric calculation fails
    """

    print("##### available_tools", available_tools)
    try:
        # Input validation
        if not isinstance(data_df, pd.DataFrame):
            raise ValueError("data_df must be a pandas DataFrame")
        if not isinstance(metric_list, list) or len(metric_list) == 0:
            raise ValueError("metric_list must be a non-empty list")

        # Check if tool_calling_accuracy is requested but available_tools is not provided
        if "tool_calling_accuracy" in metric_list and not available_tools:
            raise ValueError(
                "available_tools parameter must be provided when tool_calling_accuracy metric is requested"
            )

        if "tool_calling_perf" in metric_list:
            if "incorrect_tool_pct" not in metric_list:
                metric_list.insert(0, "incorrect_tool_pct")
            if "missed_tool_pct" not in metric_list:
                metric_list.insert(0, "missed_tool_pct")
            if "tools_args_acc" not in metric_list:
                metric_list.insert(0, "tools_args_acc")

        # Create copy of input dataframe
        result_df = data_df.copy()

        # Define supported metrics
        tool_metrics = {
            "incorrect_tool_pct": {
                "required_cols": ["Tools", "called_tools"],
                "func": incorrect_tool_pct,
            },
            "missed_tool_pct": {
                "required_cols": ["Tools", "called_tools"],
                "func": missed_tool_pct,
            },
            "tools_args_acc": {
                "required_cols": [
                    "Tools",
                    "Arguments",
                    "called_tools",
                    "called_tools_args",
                ],
                "func": args_acc,
            },
            "response_acc_llm_judge": {
                "required_cols": ["Questions", "Expected Output", "final_answer"],
                "func": llm_as_judge_score,
            },
            "answer_relevancy": {
                "required_cols": ["Questions", "final_answer"],
                "func": llm_as_judge_score_answer_relevancy,
            },
            "tool_calling_accuracy": {
                "required_cols": ["Questions", "called_tools"],
                "func": llm_as_judge_score_tool_calling,
            },
        }

        ragas_metrics = [
            "answer_precision",
            "answer_recall",
            "answer_correctness",
            "answer_similarity",
        ]

        # Process non-Ragas metrics
        for metric in metric_list:
            if metric in tool_metrics:
                # Verify required columns exist
                required_cols = tool_metrics[metric]["required_cols"]
                missing_cols = [
                    col for col in required_cols if col not in result_df.columns
                ]
                if missing_cols:
                    raise KeyError(
                        f"Missing required columns for {metric}: {missing_cols}"
                    )

                # Calculate metric
                if metric == "incorrect_tool_pct":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Tools"], row["called_tools"]
                        ),
                        axis=1,
                    )
                elif metric == "missed_tool_pct":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Tools"], row["called_tools"]
                        ),
                        axis=1,
                    )
                elif metric == "tools_args_acc":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Tools"],
                            row["Arguments"],
                            row["called_tools"],
                            row["called_tools_args"],
                        ),
                        axis=1,
                    )
                elif metric == "response_acc_llm_judge":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Questions"],
                            row["Expected Output"],
                            row["final_answer"],
                        ),
                        axis=1,
                    )
                # Add handlers for new metrics
                elif metric == "answer_relevancy":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Questions"], row["final_answer"], eval_modelId
                        ),
                        axis=1,
                    )
                elif metric == "tool_calling_accuracy":
                    result_df[metric] = result_df.apply(
                        lambda row: tool_metrics[metric]["func"](
                            row["Questions"],
                            ",".join(row["called_tools"])
                            if isinstance(row["called_tools"], list)
                            else str(row["called_tools"]),
                            available_tools,  # Use the function parameter instead of dataframe column
                            eval_modelId,
                        ),
                        axis=1,
                    )

        if "tool_calling_perf" in metric_list:
            result_df["1-incorrect_tool_pct"] = 1 - result_df["incorrect_tool_pct"]
            result_df["1-missed_tool_pct"] = 1 - result_df["missed_tool_pct"]
            result_df["tool_calling_perf"] = np.average(
                result_df[
                    ["1-incorrect_tool_pct", "1-missed_tool_pct", "tools_args_acc"]
                ],
                weights=tool_calling_weight,
                axis=1,
            )

        # Process Ragas metrics
        ragas_metric_list = [m for m in metric_list if m in ragas_metrics]

        # Initiate LLMs
        # bedrock_config = Config(connect_timeout=120, read_timeout=120, retries={'max_attempts': 2})
        bedrock_client = boto3.client("bedrock-runtime")
        bedrock_model = ChatBedrock(model_id=eval_modelId, client=bedrock_client)

        # init the embeddings
        bedrock_embeddings = BedrockEmbeddings(model_id=eval_embedId)

        if ragas_metric_list:
            try:
                # Set default column mapping if none provided
                if column_map is None:
                    column_map = {
                        "question": "Questions",
                        "answer": "final_answer",
                        "ground_truth": "Expected Output",
                    }

                # Verify required columns exist
                missing_cols = [
                    col for col in column_map.values() if col not in result_df.columns
                ]
                if missing_cols:
                    raise KeyError(
                        f"Missing required columns for Ragas metrics: {missing_cols}"
                    )
                # Calculate Ragas metrics
                # Use a dictionary to map metric names to their corresponding function objects
                ragas_metric_functions = {
                    "answer_precision": answer_precision,
                    "answer_recall": answer_recall,
                    "answer_correctness": answer_correctness,
                    "answer_similarity": answer_similarity,
                }

                # Get the actual metric functions based on names
                metric_functions = [
                    ragas_metric_functions[m] for m in ragas_metric_list
                ]
                input_ds = Dataset.from_pandas(
                    result_df[["Questions", "final_answer", "Expected Output"]]
                )
                eval_result = evaluate(
                    input_ds,
                    metrics=metric_functions,
                    llm=bedrock_model,
                    embeddings=bedrock_embeddings,
                    column_map=column_map,
                )

                # Merge results
                eval_result_df = eval_result.to_pandas()
                result_df = result_df.reset_index(drop=True)
                eval_result_df = eval_result_df.reset_index(drop=True)
                result_df = result_df.merge(
                    eval_result_df[ragas_metric_list],
                    how="left",
                    left_index=True,
                    right_index=True,
                )

            except Exception as e:
                raise RuntimeError(f"Failed to calculate Ragas metrics: {str(e)}")

        return result_df

    except Exception as e:
        print(f"Error in calc_metrics: {str(e)}")
        return data_df  # Return original dataframe on error


def calc_metrics_online(
    question: str,
    called_tools: str,
    metric_list: List[str],
    response: str,
    available_tools: str = None,
    eval_modelId: str = "anthropic.claude-3-haiku-20240307-v1:0",
) -> Dict:
    result = {}
    print("##### available_tools", available_tools)
    if "tool_calling_accuracy" in metric_list:
        result["tool_calling_accuracy"] = llm_as_judge_score_tool_calling(
            question, called_tools, available_tools
        )
    if "answer_relevancy" in metric_list:
        result["answer_relevancy"] = llm_as_judge_score_answer_relevancy(
            question, response
        )
    return result


def get_online_metrics(user_input, agent_params, config, metric_list, available_tools):
    start_time = time.time()
    agent_eval_data = {}
    (
        called_tools,
        called_tools_args,
        called_tools_ans,
        responses,
        input_tokens,
        output_tokens,
    ) = get_answers_detail_langgraph(
        user_input,
        agent_params["agent"],
        agent_params["agent_node_name"],
        agent_params["tool_node_name"],
        config,
    )

    end_time = time.time()

    agent_eval_data["Questions"] = user_input
    agent_eval_data["Response"] = responses
    agent_eval_data["called_tools"] = [called_tools]
    agent_eval_data["called_tools_args"] = [called_tools_args]
    agent_eval_data["called_tools_ans"] = [called_tools_ans]
    agent_eval_data["responses"] = [responses]
    agent_eval_data["final_answer"] = (
        agent_eval_data["responses"][-1] if agent_eval_data["responses"] else ""
    )
    agent_eval_data["latency"] = [end_time - start_time]
    agent_eval_data["input_tokens"] = [input_tokens]
    agent_eval_data["output_tokens"] = [output_tokens]

    df = pd.DataFrame(agent_eval_data)

    res = calc_metrics(df, metric_list, available_tools=available_tools)
    return res
