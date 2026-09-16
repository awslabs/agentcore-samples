import json
import boto3
import time
import logging
import os
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    Custom Lambda resource handler for creating AgentCore Policy Engine and Policy
    Handles two resource types:
    - PolicyEngine: Creates/Updates/Deletes a policy engine
    - Policy: Creates/Updates/Deletes a policy within an existing engine
    """
    logger.info(f"Received event: {json.dumps(event)}")

    request_type = event["RequestType"]
    resource_properties = event["ResourceProperties"]
    resource_type = resource_properties.get("ResourceType", "PolicyEngine")

    try:
        if resource_type == "PolicyEngine":
            return handle_policy_engine(event, request_type, resource_properties)
        elif resource_type == "Policy":
            return handle_policy(event, request_type, resource_properties)
        elif resource_type == "PolicyEngineGatewayAssociation":
            # This resource type can be implemented to associate the policy engine with a gateway if needed
            return handle_policy_engine_gateway_association(
                event, request_type, resource_properties
            )
        else:
            raise ValueError(f"Unknown resource type: {resource_type}")

    except Exception as e:
        logger.error(f"Error handling request: {str(e)}")
        return send_response(event, "FAILED", str(e))


# ============================================================================
# POLICY ENGINE HANDLERS FOR GATEWAY ASSOCIATION
# ============================================================================
def handle_policy_engine_gateway_association(
    event: Dict[str, Any], request_type: str, properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle association of Policy Engine with Gateway"""
    logger.info("PolicyEngineGatewayAssociation handling request")

    if request_type == "Delete":
        # For delete, we don't need to make actual API calls
        return send_response(
            event,
            "SUCCESS",
            "Policy engine gateway association deleted",
            physical_resource_id=f"{properties.get('PolicyEngineId')}-association",
        )
    elif request_type in ["Create", "Update"]:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        gateway_id = properties.get("GatewayId", None)
        policy_engine_configuration_mode = properties.get(
            "PolicyEngineConfigurationMode", "LOG_ONLY"
        )
        if gateway_id:
            try:
                response = client.get_gateway(gatewayIdentifier=gateway_id)

                response.pop("ResponseMetadata", None)
                (response.pop("updatedAt", None),)
                response.pop("createdAt", None)
                response.pop("gatewayUrl", None)
                response.pop("status", None)
                response.pop("workloadIdentityDetails", None)
                response.pop("gatewayArn", None)
                response.pop("gatewayId", None)

                gateway_update_object = {}
                gateway_update_object = {
                    "gatewayIdentifier": gateway_id,
                    "name": response.get("name"),
                    "roleArn": response.get("roleArn"),
                    "protocolType": response.get("protocolType"),
                    "protocolConfiguration": response.get("protocolConfiguration"),
                    "authorizerType": response.get("authorizerType"),
                    "authorizerConfiguration": response.get("authorizerConfiguration"),
                    "policyEngineConfiguration": {
                        "arn": properties.get("PolicyEngineArn"),
                        "mode": policy_engine_configuration_mode,
                    },
                    "interceptorConfigurations": response.get(
                        "interceptorConfigurations", []
                    ),
                }

                logger.info(f"Gateway details: {gateway_update_object}")

                response = client.update_gateway(**gateway_update_object)

                logger.info(
                    f"Associated policy engine with gateway successfully: {response}"
                )
            except Exception as e:
                logger.error(f"Error associating policy engine with gateway: {str(e)}")
                return send_response(
                    event,
                    "FAILED",
                    f"Error associating policy engine with gateway: {str(e)}",
                )
        else:
            logger.warning(
                "No GatewayId provided for association, skipping actual association call"
            )
    else:
        raise ValueError(f"Unknown request type: {request_type}")
    return send_response(
        event,
        "SUCCESS",
        "Policy engine associated with gateway successfully",
        physical_resource_id=f"{properties.get('PolicyEngineId')}-association",
    )


# ============================================================================
# POLICY ENGINE HANDLERS
# ============================================================================


def handle_policy_engine(
    event: Dict[str, Any], request_type: str, properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle PolicyEngine resource lifecycle"""
    if request_type == "Create":
        return create_policy_engine(event, properties)
    elif request_type == "Update":
        return update_policy_engine(event, properties)
    elif request_type == "Delete":
        return delete_policy_engine(event, properties)
    else:
        raise ValueError(f"Unknown request type: {request_type}")


def create_policy_engine(
    event: Dict[str, Any], properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a new Policy Engine"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        policy_engine_name = properties.get("PolicyEngineName", "default_policy_engine")
        policy_engine_description = properties.get(
            "Description", f"Policy Engine: {policy_engine_name}"
        )
        logger.info(f"Creating policy engine: {policy_engine_name}")

        create_response = client.create_policy_engine(
            name=policy_engine_name, description=policy_engine_description
        )

        policy_engine_id = create_response["policyEngineId"]
        policy_egine_arn = create_response["policyEngineArn"]
        logger.info(f"Created policy engine with ID: {policy_engine_id}")

        # Wait for policy engine to be active
        wait_for_policy_engine_active(client, policy_engine_id)

        response_data = {
            "PolicyEngineId": policy_engine_id,
            "PolicyEngineArn": policy_egine_arn,
            "Status": "ACTIVE",
        }

        return send_response(
            event,
            "SUCCESS",
            "Policy engine created successfully",
            response_data,
            physical_resource_id=f"{policy_engine_id}",
        )

    except Exception as e:
        logger.error(f"Error creating policy engine: {str(e)}")
        return send_response(event, "FAILED", str(e))


def update_policy_engine(
    event: Dict[str, Any], properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Update an existing Policy Engine"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        physical_resource_id = event.get("PhysicalResourceId", "")
        policy_engine_id = physical_resource_id

        logger.info(f"Update requested for policy engine {policy_engine_id}")

        # Check if policy engine exists
        try:
            response = client.get_policy_engine(policyEngineId=policy_engine_id)
            status = response.get("status", "UNKNOWN")

            logger.info(f"Policy engine exists with status: {status}")

            try:
                response = client.update_policy_engine(
                    policyEngineId=policy_engine_id,
                    description=properties.get(
                        "Description",
                        f"Policy Engine: {properties.get('PolicyEngineName', 'default_policy_engine')}",
                    ),
                )
                wait_for_policy_engine_active(client, policy_engine_id)
                logger.info(f"Policy engine {policy_engine_id} updated successfully")
            except Exception as e:
                logger.error(f"Error updating policy engine: {str(e)}")
                raise Exception(f"Failed to update policy engine: {str(e)}")

            # Return the existing resource
            response_data = {
                "PolicyEngineId": policy_engine_id,
                "PolicyEngineArn": response.get("policyEngineArn", ""),
                "Status": status,
            }

            return send_response(
                event,
                "SUCCESS",
                "Policy engine update",
                response_data,
                physical_resource_id=physical_resource_id,
            )

        except client.exceptions.ResourceNotFoundException:
            # Policy engine doesn't exist, create it
            logger.info(f"Policy engine {policy_engine_id} not found, creating new one")

            policy_engine_name = properties.get(
                "PolicyEngineName", "default-policy-engine"
            )

            create_response = client.create_policy_engine(
                name=policy_engine_name,
                description=f"Policy Engine: {policy_engine_name}",
            )

            new_policy_engine_id = create_response["policyEngineId"]
            logger.info(f"Created new policy engine with ID: {new_policy_engine_id}")

            # Wait for policy engine to be active
            wait_for_policy_engine_active(client, new_policy_engine_id)

            response_data = {"PolicyEngineId": new_policy_engine_id, "Status": "ACTIVE"}

            return send_response(
                event,
                "SUCCESS",
                "Policy engine recreated successfully",
                response_data,
                physical_resource_id=f"{new_policy_engine_id}",
            )

    except Exception as e:
        logger.error(f"Error updating policy engine: {str(e)}")
        return send_response(event, "FAILED", str(e))


def delete_policy_engine(
    event: Dict[str, Any], properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Delete a Policy Engine"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        physical_resource_id = event.get("PhysicalResourceId", "")
        policy_engine_id = physical_resource_id

        if policy_engine_id:
            logger.info(f"Deleting policy engine: {policy_engine_id}")
            client.delete_policy_engine(policyEngineId=policy_engine_id)
            logger.info(f"Policy engine deletion initiated for {policy_engine_id}")

            # Wait for policy engine to be deleted
            wait_for_policy_engine_deleted(client, policy_engine_id)
            logger.info(f"Policy engine {policy_engine_id} deleted successfully")

        return send_response(
            event,
            "SUCCESS",
            "Policy engine deleted successfully",
            physical_resource_id=physical_resource_id,
        )

    except Exception as e:
        logger.error(f"Error deleting policy engine: {str(e)}")
        # Don't fail on delete to avoid blocking stack deletion
        return send_response(
            event,
            "SUCCESS",
            f"Delete completed with warnings: {str(e)}",
            physical_resource_id=event.get("PhysicalResourceId", "unknown"),
        )


# ============================================================================
# POLICY HANDLERS
# ============================================================================


def handle_policy(
    event: Dict[str, Any], request_type: str, properties: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle Policy resource lifecycle"""
    if request_type == "Create":
        return create_policy(event, properties)
    elif request_type == "Update":
        return update_policy(event, properties)
    elif request_type == "Delete":
        return delete_policy(event, properties)
    else:
        raise ValueError(f"Unknown request type: {request_type}")


def create_policy(event: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new Policy in an existing Policy Engine"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        policy_engine_id = properties.get("PolicyEngineId")
        policy_name = properties.get("PolicyName")
        policy_description = properties.get(
            "PolicyDescription", f"Policy: {policy_name}"
        )
        policy_statement = properties.get("PolicyStatement")

        if not policy_engine_id:
            raise ValueError("PolicyEngineId is required for creating a policy")
        if not policy_name:
            raise ValueError("PolicyName is required")
        if not policy_statement:
            raise ValueError("PolicyStatement is required")

        logger.info(f"Creating policy '{policy_name}' in engine {policy_engine_id}")

        definition = {"cedar": {"statement": policy_statement}}

        create_response = client.create_policy(
            policyEngineId=policy_engine_id,
            name=policy_name,
            definition=definition,
            description=policy_description,
            validationMode="IGNORE_ALL_FINDINGS",
        )

        policy_id = create_response["policyId"]
        logger.info(f"Created policy with ID: {policy_id}")

        # Wait for policy to be active
        wait_for_policy_active(client, policy_engine_id, policy_id)

        response_data = {
            "PolicyId": policy_id,
            "PolicyEngineId": policy_engine_id,
            "PolicyDescription": policy_description,
            "PolicyName": policy_name,
            "Status": "ACTIVE",
        }

        return send_response(
            event,
            "SUCCESS",
            "Policy created successfully",
            response_data,
            physical_resource_id=f"{policy_id}",
        )

    except Exception as e:
        logger.error(f"Error creating policy: {str(e)}")
        return send_response(event, "FAILED", str(e))


def update_policy(event: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing Policy"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        physical_resource_id = event.get("PhysicalResourceId", "")
        old_properties = event.get("OldResourceProperties", {})

        policy_engine_id = properties.get("PolicyEngineId")
        policy_statement = properties.get("PolicyStatement")
        policy_name = properties.get("PolicyName")
        policy_description = properties.get(
            "PolicyDescription", f"Policy: {policy_name}"
        )

        # Extract policy ID from physical resource ID
        policy_id = physical_resource_id

        logger.info(f"Updating policy {policy_id} in engine {policy_engine_id}")

        # Check if statement has changed
        old_statement = old_properties.get("PolicyStatement", "")
        if policy_statement != old_statement:
            logger.info("Policy statement changed, updating...")

            definition = {"cedar": {"statement": policy_statement}}

            _update_response = client.update_policy(
                policyEngineId=policy_engine_id,
                policyId=policy_id,
                policy_description=policy_description,
                definition=definition,
                validationMode="IGNORE_ALL_FINDINGS",
            )

            logger.info(f"Policy {policy_id} updated successfully")

        response_data = {
            "PolicyId": policy_id,
            "PolicyEngineId": policy_engine_id,
            "PolicyName": policy_name,
            "PolicyDescription": policy_description,
            "Status": "ACTIVE",
        }

        return send_response(
            event,
            "SUCCESS",
            "Policy updated successfully",
            response_data,
            physical_resource_id=physical_resource_id,
        )

    except Exception as e:
        logger.error(f"Error updating policy: {str(e)}")
        return send_response(event, "FAILED", str(e))


def delete_policy(event: Dict[str, Any], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a Policy"""
    try:
        region = properties.get("Region", os.environ.get("AWS_REGION", "us-east-1"))
        client = boto3.client("bedrock-agentcore-control", region_name=region)

        physical_resource_id = event.get("PhysicalResourceId", "")
        policy_engine_id = properties.get("PolicyEngineId")
        policy_id = physical_resource_id

        if policy_id and policy_engine_id:
            logger.info(f"Deleting policy {policy_id} from engine {policy_engine_id}")
            client.delete_policy(policyEngineId=policy_engine_id, policyId=policy_id)
            logger.info(f"Policy deletion initiated for {policy_id}")

            # Wait for policy to be deleted
            wait_for_policy_deleted(client, policy_engine_id, policy_id)
            logger.info(f"Policy {policy_id} deleted successfully")

        return send_response(
            event,
            "SUCCESS",
            "Policy deleted successfully",
            physical_resource_id=physical_resource_id,
        )

    except Exception as e:
        logger.error(f"Error deleting policy: {str(e)}")
        # Don't fail on delete to avoid blocking stack deletion
        return send_response(
            event,
            "SUCCESS",
            f"Delete completed with warnings: {str(e)}",
            physical_resource_id=event.get("PhysicalResourceId", "unknown"),
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def wait_for_policy_engine_active(
    client, policy_engine_id: str, max_wait_time: int = 300
):
    """Wait for policy engine to become active"""
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        try:
            response = client.get_policy_engine(policyEngineId=policy_engine_id)
            status = response["status"]

            logger.info(f"Policy engine status: {status}")

            if status == "ACTIVE":
                return
            elif status in ["FAILED", "DELETING"]:
                raise Exception(f"Policy engine creation failed with status: {status}")

            time.sleep(10)

        except Exception as e:
            logger.error(f"Error checking policy engine status: {str(e)}")
            raise

    raise Exception(
        f"Policy engine did not become active within {max_wait_time} seconds"
    )


def wait_for_policy_active(
    client, policy_engine_id: str, policy_id: str, max_wait_time: int = 300
):
    """Wait for policy to become active"""
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        try:
            logger.info(f"Checking policy {policy_id} status...")

            response = client.get_policy(
                policyEngineId=policy_engine_id, policyId=policy_id
            )
            status = response.get("status", "UNKNOWN")

            logger.info(f"Policy status: {status}")

            if status == "ACTIVE":
                return
            elif status in ["FAILED", "DELETING", "CREATE_FAILED"]:
                # Create a copy and remove datetime fields that can't be JSON serialized
                response_copy = response.copy()
                response_copy.pop("createdAt", None)
                response_copy.pop("updatedAt", None)
                logger.error(
                    f"Policy creation failed with status: {json.dumps(response_copy, indent=2)}"
                )
                raise Exception(f"Policy creation failed with status: {status}")

            time.sleep(10)

        except client.exceptions.ResourceNotFoundException:
            # Policy might not be immediately available
            logger.info("Policy not found yet, waiting...")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Error checking policy status: {str(e)}")
            raise

    raise Exception(f"Policy did not become active within {max_wait_time} seconds")


def wait_for_policy_deleted(
    client, policy_engine_id: str, policy_id: str, max_wait_time: int = 300
):
    """Wait for policy to be deleted"""
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        try:
            logger.info(f"Checking if policy {policy_id} is deleted...")

            response = client.get_policy(
                policyEngineId=policy_engine_id, policyId=policy_id
            )
            status = response.get("status", "UNKNOWN")

            logger.info(f"Policy still exists with status: {status}")

            if status == "DELETING":
                logger.info("Policy is being deleted, waiting...")
                time.sleep(10)
            else:
                # If status is not DELETING, wait a bit more
                time.sleep(10)

        except client.exceptions.ResourceNotFoundException:
            # Policy has been deleted
            logger.info(f"Policy {policy_id} has been deleted successfully")
            return
        except Exception as e:
            logger.error(f"Error checking policy deletion status: {str(e)}")
            raise

    raise Exception(f"Policy was not deleted within {max_wait_time} seconds")


def wait_for_policy_engine_deleted(
    client, policy_engine_id: str, max_wait_time: int = 300
):
    """Wait for policy engine to be deleted"""
    start_time = time.time()

    while time.time() - start_time < max_wait_time:
        try:
            logger.info(f"Checking if policy engine {policy_engine_id} is deleted...")

            response = client.get_policy_engine(policyEngineId=policy_engine_id)
            status = response.get("status", "UNKNOWN")

            logger.info(f"Policy engine still exists with status: {status}")

            if status == "DELETING":
                logger.info("Policy engine is being deleted, waiting...")
                time.sleep(10)
            else:
                # If status is not DELETING, wait a bit more
                time.sleep(10)

        except client.exceptions.ResourceNotFoundException:
            # Policy engine has been deleted
            logger.info(
                f"Policy engine {policy_engine_id} has been deleted successfully"
            )
            return
        except Exception as e:
            logger.error(f"Error checking policy engine deletion status: {str(e)}")
            raise

    raise Exception(f"Policy engine was not deleted within {max_wait_time} seconds")


def send_response(
    event: Dict[str, Any],
    status: str,
    reason: str,
    response_data: Dict[str, Any] = None,
    physical_resource_id: str = None,
) -> Dict[str, Any]:
    """Send response to CloudFormation"""
    import urllib3

    response_data = response_data or {}

    # Use provided physical_resource_id or generate from event
    if not physical_resource_id:
        physical_resource_id = event.get(
            "PhysicalResourceId", f"failed-{int(time.time())}"
        )

    response_body = {
        "Status": status,
        "Reason": reason,
        "PhysicalResourceId": physical_resource_id,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": response_data,
    }

    logger.info(f"Sending response: {json.dumps(response_body)}")

    # Send response to CloudFormation
    http = urllib3.PoolManager()

    try:
        response = http.request(
            "PUT",
            event["ResponseURL"],
            body=json.dumps(response_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        logger.info(f"Response sent successfully: {response.status}")
    except Exception as e:
        logger.error(f"Error sending response: {str(e)}")

    return response_body
