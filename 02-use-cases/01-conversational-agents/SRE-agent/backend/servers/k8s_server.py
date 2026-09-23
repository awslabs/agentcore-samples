import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
)
from pydantic import BaseModel, Field
from retrieve_api_key import retrieve_api_key

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

app = FastAPI(title="Kubernetes Analysis API", version="1.0.0")

# Base path for fake data
DATA_PATH = Path(__file__).parent.parent / "data" / "k8s_data"

# API Key for authentication
CREDENTIAL_PROVIDER_NAME = "sre-agent-api-key-credential-provider"

# Retrieve API key from credential provider at startup
try:
    EXPECTED_API_KEY = retrieve_api_key(CREDENTIAL_PROVIDER_NAME)
    if not EXPECTED_API_KEY:
        logging.error("Failed to retrieve API key from credential provider")
        raise RuntimeError(
            "Cannot start server without valid API key from credential provider"
        )
except Exception as e:
    logging.error(f"Error retrieving API key: {e}")
    raise RuntimeError(f"Cannot start server: {e}") from e


def _validate_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    """Validate API key from header"""
    if not x_api_key or x_api_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


def _parse_timestamp(timestamp_str: str) -> datetime:
    """Parse ISO timestamp string to datetime object"""
    try:
        # Handle both with and without timezone
        if timestamp_str.endswith("Z"):
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        elif "+" in timestamp_str or timestamp_str.endswith("0"):
            return datetime.fromisoformat(timestamp_str)
        else:
            return datetime.fromisoformat(timestamp_str + "+00:00")
    except:
        # Fallback: assume current time if parsing fails
        return datetime.now(timezone.utc)


def _filter_events_by_time(events: list, since: Optional[str] = None) -> list:
    """Filter events by since timestamp"""
    if not since:
        return events

    filtered_events = []
    since_dt = _parse_timestamp(since)

    for event in events:
        event_timestamp = event.get("timestamp")
        if not event_timestamp:
            continue

        try:
            event_dt = _parse_timestamp(event_timestamp)

            if event_dt >= since_dt:
                filtered_events.append(event)
        except:
            # Include events with unparseable timestamps
            filtered_events.append(event)

    return filtered_events


# Pydantic Models
class PodStatus(str, Enum):
    """Pod status enumeration"""

    RUNNING = "Running"
    PENDING = "Pending"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"
    CRASHLOOPBACKOFF = "CrashLoopBackOff"


class PodPhase(str, Enum):
    """Pod phase enumeration"""

    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


class ResourceUsage(BaseModel):
    """Resource usage information"""

    cpu: str = Field(..., description="CPU request/limit", example="250m")
    memory: str = Field(..., description="Memory request/limit", example="512Mi")
    cpu_utilization: str = Field(
        ..., description="CPU utilization percentage", example="75%"
    )
    memory_utilization: str = Field(
        ..., description="Memory utilization percentage", example="85%"
    )


class Pod(BaseModel):
    """Pod information model"""

    name: str = Field(
        ..., description="Pod name", example="web-app-deployment-5c8d7f9b6d-k2n8p"
    )
    namespace: str = Field(
        ..., description="Kubernetes namespace", example="production"
    )
    status: PodStatus = Field(..., description="Pod status")
    phase: PodPhase = Field(..., description="Pod phase")
    node: str = Field(..., description="Node where pod is running", example="node-1")
    created_at: str = Field(..., description="Pod creation timestamp")
    resource_usage: ResourceUsage = Field(..., description="Resource usage metrics")


class PodStatusResponse(BaseModel):
    """Response model for pod status endpoint"""

    pods: List[Pod] = Field(..., description="List of pods")


class DeploymentStatus(str, Enum):
    """Deployment status enumeration"""

    HEALTHY = "Healthy"
    DEGRADED = "Degraded"
    FAILED = "Failed"


class Deployment(BaseModel):
    """Deployment information model"""

    name: str = Field(..., description="Deployment name", example="web-app-deployment")
    namespace: str = Field(
        ..., description="Kubernetes namespace", example="production"
    )
    replicas: int = Field(..., description="Desired number of replicas", example=3)
    available_replicas: int = Field(
        ..., description="Number of available replicas", example=2
    )
    unavailable_replicas: int = Field(
        ..., description="Number of unavailable replicas", example=1
    )
    status: DeploymentStatus = Field(..., description="Deployment status")


class DeploymentStatusResponse(BaseModel):
    """Response model for deployment status endpoint"""

    deployments: List[Deployment] = Field(..., description="List of deployments")


class EventType(str, Enum):
    """Event type enumeration"""

    NORMAL = "Normal"
    WARNING = "Warning"
    ERROR = "Error"


class Event(BaseModel):
    """Kubernetes event model"""

    type: EventType = Field(..., description="Event type")
    reason: str = Field(..., description="Event reason", example="FailedScheduling")
    object: str = Field(
        ...,
        description="Kubernetes object reference",
        example="pod/web-app-deployment-5c8d7f9b6d-k2n8p",
    )
    message: str = Field(
        ...,
        description="Event message",
        example="0/3 nodes are available: 3 Insufficient memory",
    )
    timestamp: str = Field(..., description="Event timestamp")
    namespace: str = Field(
        ..., description="Kubernetes namespace", example="production"
    )
    count: int = Field(..., description="Number of occurrences", example=5)


class EventsResponse(BaseModel):
    """Response model for events endpoint"""

    events: List[Event] = Field(..., description="List of events")


class ErrorResponse(BaseModel):
    """Error response model"""

    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Detailed error information")


@app.get("/pods/status", response_model=PodStatusResponse)
async def get_pod_status(
    namespace: Optional[str] = Query(
        None, description="Kubernetes namespace to filter pods"
    ),
    pod_name: Optional[str] = Query(None, description="Specific pod name to retrieve"),
    api_key: str = Depends(_validate_api_key),
):
    """
    Retrieve pod information from the Kubernetes cluster.

    This endpoint provides detailed information about pods including their status,
    resource usage, and location within the cluster. Results can be filtered by
    namespace and specific pod name.

    Args:
        namespace: Optional Kubernetes namespace to filter pods
        pod_name: Optional specific pod name to retrieve
        api_key: Required API key for authentication

    Returns:
        PodStatusResponse: List of pods with detailed status information

    Raises:
        HTTPException: 401 if API key is invalid
        HTTPException: 500 if data retrieval fails
    """
    try:
        with open(DATA_PATH / "pods.json", "r") as f:
            data = json.load(f)

        pods = data.get("pods", [])

        # Filter by namespace if provided
        if namespace:
            pods = [p for p in pods if p.get("namespace") == namespace]

        # Filter by pod name if provided
        if pod_name:
            pods = [p for p in pods if p.get("name") == pod_name]

        return PodStatusResponse(pods=pods)
    except Exception as e:
        logging.error(f"Error retrieving pod status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/deployments/status", response_model=DeploymentStatusResponse)
async def get_deployment_status(
    namespace: Optional[str] = Query(None, description="Kubernetes namespace"),
    deployment_name: Optional[str] = Query(
        None, description="Specific deployment name"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """
    Check deployment health and replica status.

    This endpoint provides comprehensive information about deployments including
    their current status, replica counts, and health metrics. Results can be
    filtered by namespace and specific deployment name.

    Args:
        namespace: Optional Kubernetes namespace to filter deployments
        deployment_name: Optional specific deployment name to retrieve
        api_key: Required API key for authentication

    Returns:
        DeploymentStatusResponse: List of deployments with health status

    Raises:
        HTTPException: 401 if API key is invalid
        HTTPException: 500 if data retrieval fails
    """
    try:
        with open(DATA_PATH / "deployments.json", "r") as f:
            data = json.load(f)

        deployments = data.get("deployments", [])

        if namespace:
            deployments = [d for d in deployments if d.get("namespace") == namespace]

        if deployment_name:
            deployments = [d for d in deployments if d.get("name") == deployment_name]

        return DeploymentStatusResponse(deployments=deployments)
    except Exception as e:
        logging.error(f"Error retrieving deployment status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/events", response_model=EventsResponse)
async def get_cluster_events(
    since: Optional[str] = Query(
        None, description="Filter events since this timestamp"
    ),
    severity: Optional[str] = Query(
        None,
        enum=["Warning", "Error", "Normal"],
        description="Filter by event severity",
    ),
    api_key: str = Depends(_validate_api_key),
):
    """
    Fetch recent Kubernetes cluster events.

    This endpoint retrieves cluster events with filtering capabilities by timestamp
    and severity level. Events provide insights into cluster operations, scheduling
    decisions, and potential issues.

    Args:
        since: Optional ISO 8601 timestamp to filter events from
        severity: Optional severity filter (Warning, Error, Normal)
        api_key: Required API key for authentication

    Returns:
        EventsResponse: List of cluster events with timestamps and details

    Raises:
        HTTPException: 401 if API key is invalid
        HTTPException: 500 if data retrieval fails
    """
    try:
        with open(DATA_PATH / "events.json", "r") as f:
            data = json.load(f)

        events = data.get("events", [])

        if severity:
            events = [e for e in events if e.get("type") == severity]

        # Filter by since timestamp
        events = _filter_events_by_time(events, since)

        return EventsResponse(events=events)
    except Exception as e:
        logging.error(f"Error retrieving cluster events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/resource_usage")
async def get_resource_usage(
    namespace: Optional[str] = Query(None, description="Filter by namespace"),
    resource_type: Optional[str] = Query(
        None, enum=["cpu", "memory", "pods"], description="Type of resource to monitor"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """
    Monitor cluster resource consumption and utilization.

    This endpoint provides detailed metrics about resource usage across the cluster,
    including CPU, memory, and pod consumption. Data can be filtered by namespace
    and specific resource types.

    Args:
        namespace: Optional namespace to filter resource usage data
        resource_type: Optional resource type filter (cpu, memory, pods)
        api_key: Required API key for authentication

    Returns:
        Dict: Resource usage metrics with cluster and namespace breakdowns

    Raises:
        HTTPException: 401 if API key is invalid
        HTTPException: 500 if data retrieval fails
    """
    try:
        with open(DATA_PATH / "resource_usage.json", "r") as f:
            data = json.load(f)

        resource_usage = data.get("resource_usage", {})

        # Filter by namespace if provided
        if namespace and "namespace_usage" in resource_usage:
            namespace_data = resource_usage["namespace_usage"].get(namespace, {})
            if resource_type:
                return {
                    "resource_usage": {resource_type: namespace_data.get(resource_type)}
                }
            return {"resource_usage": {"namespace": namespace, "usage": namespace_data}}

        return {"resource_usage": resource_usage}
    except Exception as e:
        logging.error(f"Error retrieving resource usage: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/nodes/status")
async def get_node_status(
    node_name: Optional[str] = Query(None, description="Specific node name"),
    api_key: str = Depends(_validate_api_key),
):
    """
    Check cluster node health and status.

    This endpoint provides comprehensive information about cluster nodes including
    their health status, capacity, allocatable resources, and current usage.
    Results can be filtered by specific node name.

    Args:
        node_name: Optional specific node name to retrieve
        api_key: Required API key for authentication

    Returns:
        Dict: Node status information with health and resource metrics

    Raises:
        HTTPException: 401 if API key is invalid
        HTTPException: 500 if data retrieval fails
    """
    try:
        with open(DATA_PATH / "nodes.json", "r") as f:
            data = json.load(f)

        nodes = data.get("nodes", [])

        if node_name:
            nodes = [n for n in nodes if n.get("name") == node_name]

        return {"nodes": nodes}
    except Exception as e:
        logging.error(f"Error retrieving node status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def health_check(api_key: str = Depends(_validate_api_key)):
    """
    Health check endpoint for the Kubernetes API service.

    This endpoint provides a simple health check to verify that the API service
    is running and accessible. It requires authentication like all other endpoints.

    Args:
        api_key: Required API key for authentication

    Returns:
        Dict: Service health status information

    Raises:
        HTTPException: 401 if API key is invalid
    """
    return {"status": "healthy", "service": "k8s-api"}


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    import uvicorn

    # Add parent directory to path to import config_utils
    sys.path.append(str(Path(__file__).parent.parent))
    from config_utils import get_server_port

    parser = argparse.ArgumentParser(description="K8s API Server")
    parser.add_argument(
        "--host",
        type=str,
        required=True,
        help="Host to bind to (REQUIRED - must match SSL certificate hostname if using SSL)",
    )
    parser.add_argument("--ssl-keyfile", type=str, help="Path to SSL private key file")
    parser.add_argument("--ssl-certfile", type=str, help="Path to SSL certificate file")
    parser.add_argument("--port", type=int, help="Port to bind to (overrides config)")

    args = parser.parse_args()

    port = args.port if args.port else get_server_port("k8s")

    # Configure SSL if both cert files are provided
    ssl_config = {}
    if args.ssl_keyfile and args.ssl_certfile:
        ssl_config = {
            "ssl_keyfile": args.ssl_keyfile,
            "ssl_certfile": args.ssl_certfile,
        }
        protocol = "HTTPS"
        logging.warning(
            f"⚠️  SSL CERTIFICATE HOSTNAME WARNING: Ensure your SSL certificate is valid for hostname '{args.host}'"
        )
        logging.warning(
            f"⚠️  If using self-signed certificates, generate with: openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN={args.host}'"
        )
    else:
        protocol = "HTTP"

    logging.info(f"Starting K8s server on {protocol}://{args.host}:{port}")
    uvicorn.run(app, host=args.host, port=port, **ssl_config)
