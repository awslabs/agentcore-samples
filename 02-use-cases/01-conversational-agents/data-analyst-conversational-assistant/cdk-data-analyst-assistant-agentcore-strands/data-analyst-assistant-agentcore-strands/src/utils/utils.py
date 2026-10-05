"""
Utility Functions for Data Analyst Conversational Assistant

Provides functions for storing analysis query results to DynamoDB.
"""

import json
import os
from datetime import datetime, timezone

import boto3


def save_raw_query_result(user_prompt_uuid, user_prompt, sql_query, sql_query_description, result, message):
    """
    Save analysis query results to DynamoDB for audit trail.

    Args:
        user_prompt_uuid (str): Unique identifier for the user prompt
        user_prompt (str): The original user question
        sql_query (str): The executed SQL query
        sql_query_description (str): Human-readable description of the query
        result (dict): The query results and metadata
        message (str): Additional information about the result

    Returns:
        dict: Response with success status and DynamoDB response or error details
    """
    try:
        question_answers_table = os.environ.get("QUESTION_ANSWERS_TABLE", "")
        if not question_answers_table:
            return {"success": False, "error": "QUESTION_ANSWERS_TABLE not configured"}

        dynamodb_client = boto3.client("dynamodb")

        response = dynamodb_client.put_item(
            TableName=question_answers_table,
            Item={
                "id": {"S": user_prompt_uuid},
                "my_timestamp": {"N": str(int(datetime.now(timezone.utc).timestamp()))},
                "datetime": {"S": str(datetime.now(timezone.utc))},
                "user_prompt": {"S": user_prompt},
                "sql_query": {"S": sql_query},
                "sql_query_description": {"S": sql_query_description},
                "data": {"S": json.dumps(result)},
                "message_result": {"S": message},
            },
        )

        print(f"✅ Analysis data saved to DynamoDB ({question_answers_table}), session: {user_prompt_uuid}")
        return {"success": True, "response": response}

    except Exception as e:
        print(f"❌ DynamoDB save error: {e!s}")
        return {"success": False, "error": str(e)}
