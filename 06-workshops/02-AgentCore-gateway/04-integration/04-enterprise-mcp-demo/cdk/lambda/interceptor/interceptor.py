import logging
import json
import os
import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", None)
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "1.0")
MCP_METADATA_KEY = os.getenv("MCP_METADATA_KEY", "com.example/target")

client = boto3.client("bedrock-runtime")


def lambda_handler(event, context):
    mcp_method = None
    try:
        """
        Lambda function that handles both REQUEST and RESPONSE interceptor types.

        For REQUEST interceptors: logs the MCP method and passes request through unchanged
        For RESPONSE interceptors: passes response through unchanged
        """
        # Extract the MCP data from the event
        mcp_data = event.get("mcp", {})

        logger.info(f"Received event: {json.dumps(event, indent=2)}")

        # Check if this is a REQUEST or RESPONSE interceptor based on presence of gatewayResponse
        if "gatewayResponse" in mcp_data and mcp_data["gatewayResponse"] is not None:
            logger.info("This is a RESPONSE interceptor")

            # Get the request body to check the method (method is in the request, not response)
            request_body = mcp_data.get("gatewayRequest", {}).get("body", {})
            response_body = mcp_data.get("gatewayResponse", {}).get("body", {}) or {}

            if request_body:
                mcp_method = request_body.get("method", "unknown")
                logger.info(f"Gateway request method: {mcp_method}")

            if response_body:
                logger.info(f"Gateway response body: {json.dumps(response_body, indent=2)}")

            logger.info(f"Processing RESPONSE interceptor - MCP method: {mcp_method}")

            # === HANDLE TOOLS/LIST FILTERING BASED ON _meta ===
            if mcp_method == "tools/list" and response_body:
                logger.info("tools/list response detected in RESPONSE interceptor")

                # Extract target filter from MCP _meta (spec-compliant)
                target_filter = None
                meta = request_body.get("_meta", {})
                if isinstance(meta, dict):
                    target_filter = meta.get(MCP_METADATA_KEY)

                if target_filter:
                    logger.info(f"Target filter from _meta: {MCP_METADATA_KEY} = '{target_filter}'")
                    logger.info(f"Will filter tools to only those starting with '{target_filter}___'")
                else:
                    logger.info("No target filter in _meta - returning ALL tools (no filtering)")

                # Filter tools if target filter is specified
                if "result" in response_body and "tools" in response_body.get("result", {}):
                    result = response_body["result"]
                    original_tools = result.get("tools", [])

                    logger.info(f"Original tools count: {len(original_tools)}")

                    if target_filter:
                        # Filter by gateway target name prefix (format: "target___tool")
                        filtered_tools = [
                            tool for tool in original_tools if tool.get("name", "").startswith(f"{target_filter}___")
                        ]

                        logger.info(f"Filtered to {len(filtered_tools)} tools for target '{target_filter}'")

                        # Log matched tools
                        if filtered_tools:
                            logger.info("Matched tools:")
                            for tool in filtered_tools:
                                logger.info(f"  - {tool.get('name')}")
                        else:
                            logger.warning(f"No tools matched target '{target_filter}'")

                        # Log filtering summary
                        removed = len(original_tools) - len(filtered_tools)
                        if removed > 0:
                            logger.info(f"Filtered out {removed} tools not matching target")

                        # Create filtered response
                        filtered_body = {
                            "jsonrpc": response_body.get("jsonrpc", "2.0"),
                            "id": response_body.get("id"),
                            "result": {"tools": filtered_tools},
                        }

                        # Preserve _meta from response if present
                        if "_meta" in response_body:
                            filtered_body["_meta"] = response_body["_meta"]

                        response = {
                            "interceptorOutputVersion": "1.0",
                            "mcp": {
                                "transformedGatewayResponse": {
                                    "body": filtered_body,
                                    "statusCode": 200,
                                }
                            },
                        }
                        logger.info("Returning filtered tools/list response")
                        return response
                    else:
                        # No filtering - log all tools and return unchanged
                        logger.info(f"No filtering applied - returning all {len(original_tools)} tools")
                        logger.info("Available tools:")
                        for tool in original_tools:
                            logger.info(f"  - {tool.get('name')}")

            if mcp_method == "tools/call" and response_body:
                logger.info("tools/call response detected in RESPONSE interceptor")
                content = (
                    response_body.get("result", {}).get("content", [])[0].get("text", {}) if response_body else None
                )
                if GUARDRAIL_ID:
                    gr_response = client.apply_guardrail(
                        guardrailIdentifier=GUARDRAIL_ID,
                        guardrailVersion=GUARDRAIL_VERSION,
                        source="INPUT",
                        content=[
                            {
                                "text": {
                                    "text": content,
                                    "qualifiers": ["guard_content"],
                                },
                            },
                        ],
                        outputScope="FULL",
                    )
                    if gr_response.get("action", None) == "GUARDRAIL_INTERVENED":
                        logger.warning("Guardrail intervened on the content. Details:")
                        guardrail_text = gr_response.get("outputs", [{}])[0].get("text", "")
                        logger.warning(guardrail_text)
                        body_transformed = response_body
                        body_transformed["result"]["content"][0] = {
                            "type": "text",
                            "text": guardrail_text,
                        }
                        statusCode = 403
                        response = {
                            "interceptorOutputVersion": "1.0",
                            "mcp": {
                                "transformedGatewayResponse": {
                                    "body": body_transformed,
                                    "statusCode": statusCode,
                                }
                            },
                        }
                        logger.info(
                            f"Interceptor response after guardrail intervention: {json.dumps(response, indent=2)}"
                        )
                        return response
                    else:
                        logger.info("Guardrail did not intervene. Passing through original response.")
                else:
                    logger.warning("GUARDRAIL_ID environment variable not set. Skipping guardrail application.")
            else:
                logger.info("Non tools/call method detected in RESPONSE interceptor. Passing through unchanged.")

            # This is a RESPONSE interceptor
            logger.info("Processing RESPONSE interceptor - passing through unchanged")

            # Pass through the original request and response unchanged
            response = {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "body": mcp_data.get("gatewayResponse", {}).get("body", {}) or {},
                        "statusCode": mcp_data.get("gatewayResponse", {}).get("statusCode", 200),
                    }
                },
            }
            logger.info(f"Interceptor response: {json.dumps(response, indent=2)}")
            return response
        else:
            # This is a REQUEST interceptor
            gateway_request = mcp_data.get("gatewayRequest", {})
            request_body = gateway_request.get("body", {})
            mcp_method = request_body.get("method", "unknown")

            # Log the MCP method
            logger.info(f"Processing REQUEST interceptor - MCP method: {mcp_method}")

            if mcp_method == "tools/call" and request_body:
                # This is a REQUEST interceptor
                if GUARDRAIL_ID:
                    gr_response = client.apply_guardrail(
                        guardrailIdentifier=GUARDRAIL_ID,
                        guardrailVersion=GUARDRAIL_VERSION,
                        source="INPUT",
                        content=[
                            {
                                "text": {
                                    "text": json.dumps(request_body),
                                    "qualifiers": ["guard_content"],
                                },
                            },
                        ],
                        outputScope="FULL",
                    )
                    logger.info(f"Guardrail response: {gr_response}")

                    if gr_response.get("action", None) == "GUARDRAIL_INTERVENED":
                        logger.warning("Guardrail intervened on the content. Details:")
                        guardrail_text = gr_response.get("outputs", [{}])[0].get("text", "{}")
                        logger.warning(guardrail_text)

                        # Parse the guardrail output back to a dict since the gateway
                        # expects body to be a JSON object, not a string
                        try:
                            transformed_body = json.loads(guardrail_text)
                        except (json.JSONDecodeError, TypeError):
                            # If guardrail output isn't valid JSON, pass through original request
                            logger.error("Guardrail output is not valid JSON, passing through original request")
                            transformed_body = request_body

                        response = {
                            "interceptorOutputVersion": "1.0",
                            "mcp": {
                                "transformedGatewayRequest": {
                                    "body": transformed_body,
                                }
                            },
                        }
                        logger.info(f"Interceptor response after guardrail intervention: {response}")
                        return response
                    else:
                        logger.info("Guardrail did not intervene. Passing through original request.")
                else:
                    logger.warning("GUARDRAIL_ID environment variable not set. Skipping guardrail application.")
            else:
                logger.info("Non tools/call method detected in REQUEST interceptor. Passing through unchanged.")

            # Pass through the original request unchanged
            response = {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayRequest": {
                        "body": request_body,
                    }
                },
            }

        logger.info(f"Interceptor response: {json.dumps(response, indent=2)}")
        return response
    except Exception as e:
        logger.error(f"Error processing interceptor: {str(e)}")
        raise e
