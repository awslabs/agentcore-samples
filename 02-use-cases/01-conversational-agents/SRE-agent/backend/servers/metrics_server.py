import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
)
from fastapi.responses import JSONResponse
from retrieve_api_key import retrieve_api_key

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

app = FastAPI(title="Application Metrics API", version="1.0.0")

DATA_PATH = Path(__file__).parent.parent / "data" / "metrics_data"

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


def _filter_metrics_by_time(
    metrics: list, start_time: Optional[str] = None, end_time: Optional[str] = None
) -> list:
    """Filter metrics by time range"""
    if not start_time and not end_time:
        return metrics

    filtered_metrics = []
    start_dt = _parse_timestamp(start_time) if start_time else None
    end_dt = _parse_timestamp(end_time) if end_time else None

    for metric in metrics:
        metric_timestamp = metric.get("timestamp")
        if not metric_timestamp:
            continue

        try:
            metric_dt = _parse_timestamp(metric_timestamp)

            if start_dt and metric_dt < start_dt:
                continue
            if end_dt and metric_dt > end_dt:
                continue

            filtered_metrics.append(metric)
        except:
            # Include metrics with unparseable timestamps
            filtered_metrics.append(metric)

    return filtered_metrics


@app.get("/metrics/performance")
async def get_performance_metrics(
    metric_type: Optional[str] = Query(
        None,
        enum=["response_time", "throughput", "cpu_usage", "memory_usage"],
        description="Type of performance metric",
    ),
    start_time: Optional[str] = Query(None, description="Start time for metrics"),
    end_time: Optional[str] = Query(None, description="End time for metrics"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    api_key: str = Depends(_validate_api_key),
):
    """Retrieve performance data"""
    try:
        metrics = []

        if metric_type == "response_time":
            with open(DATA_PATH / "response_times.json", "r") as f:
                data = json.load(f)
                metrics = data.get("metrics", [])
        elif metric_type == "throughput":
            with open(DATA_PATH / "throughput.json", "r") as f:
                data = json.load(f)
                metrics = data.get("metrics", [])
        elif metric_type in ["cpu_usage", "memory_usage"]:
            with open(DATA_PATH / "resource_usage.json", "r") as f:
                data = json.load(f)
                raw_metrics = data.get("metrics", [])
                # Transform resource metrics to match expected format
                metrics = []
                for m in raw_metrics:
                    if metric_type == "cpu_usage":
                        metrics.append(
                            {
                                "timestamp": m["timestamp"],
                                "service": m["service"],
                                "value": m["cpu_usage_percent"],
                                "unit": "percent",
                            }
                        )
                    else:  # memory_usage
                        metrics.append(
                            {
                                "timestamp": m["timestamp"],
                                "service": m["service"],
                                "value": m["memory_usage_mb"],
                                "unit": "MB",
                            }
                        )
        else:
            # Return combined metrics for demo
            with open(DATA_PATH / "resource_usage.json", "r") as f:
                data = json.load(f)
                metrics = data.get("metrics", [])

        if service:
            metrics = [m for m in metrics if m.get("service") == service]

        # Filter by time range
        metrics = _filter_metrics_by_time(metrics, start_time, end_time)

        return {"metrics": metrics}
    except Exception as e:
        logging.error(f"Error retrieving performance metrics: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/metrics/errors")
async def get_error_rates(
    time_window: Optional[str] = Query(
        "24h", enum=["1h", "6h", "24h", "7d"], description="Time window for error rates"
    ),
    service: Optional[str] = Query(None, description="Filter by service name"),
    api_key: str = Depends(_validate_api_key),
):
    """Fetch error rate statistics"""
    try:
        with open(DATA_PATH / "error_rates.json", "r") as f:
            data = json.load(f)

        error_rates = data.get("error_rates", [])

        if service:
            error_rates = [e for e in error_rates if e.get("service") == service]

        # TODO: In real implementation, would filter by time window

        return {"error_rates": error_rates}
    except Exception as e:
        logging.error(f"Error retrieving error rates: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/metrics/resources")
async def get_resource_metrics(
    resource_type: Optional[str] = Query(
        None,
        enum=["cpu", "memory", "disk", "network"],
        description="Type of resource metric",
    ),
    service: Optional[str] = Query(None, description="Filter by service name"),
    time_window: Optional[str] = Query(
        "24h", enum=["1h", "6h", "24h", "7d"], description="Time window for metrics"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Monitor resource utilization"""
    try:
        with open(DATA_PATH / "resource_usage.json", "r") as f:
            data = json.load(f)

        metrics = data.get("metrics", [])

        if service:
            metrics = [m for m in metrics if m.get("service") == service]

        # Filter by resource type if specified
        if resource_type:
            filtered_metrics = []
            for m in metrics:
                filtered = {"timestamp": m["timestamp"], "service": m["service"]}
                if resource_type == "cpu":
                    filtered["cpu_usage_percent"] = m.get("cpu_usage_percent")
                elif resource_type == "memory":
                    filtered["memory_usage_mb"] = m.get("memory_usage_mb")
                    filtered["memory_usage_percent"] = m.get("memory_usage_percent")
                elif resource_type == "disk":
                    filtered["disk_io_read_mb"] = m.get("disk_io_read_mb")
                    filtered["disk_io_write_mb"] = m.get("disk_io_write_mb")
                elif resource_type == "network":
                    filtered["network_in_mb"] = m.get("network_in_mb")
                    filtered["network_out_mb"] = m.get("network_out_mb")
                filtered_metrics.append(filtered)
            metrics = filtered_metrics

        return {"metrics": metrics}
    except Exception as e:
        logging.error(f"Error retrieving resource metrics: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/metrics/availability")
async def get_availability_metrics(
    service: Optional[str] = Query(None, description="Service name"),
    time_window: Optional[str] = Query(
        "24h",
        enum=["1h", "6h", "24h", "7d", "30d"],
        description="Time window for availability calculation",
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Check service availability"""
    try:
        with open(DATA_PATH / "availability.json", "r") as f:
            data = json.load(f)

        availability_metrics = data.get("availability_metrics", [])

        if service:
            availability_metrics = [
                a for a in availability_metrics if a.get("service") == service
            ]

        # TODO: In real implementation, would calculate based on time window

        return {"availability_metrics": availability_metrics}
    except Exception as e:
        logging.error(f"Error retrieving availability metrics: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/metrics/trends")
async def analyze_trends(
    metric_name: str = Query(..., description="Name of the metric to analyze"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    time_window: Optional[str] = Query(
        "24h",
        enum=["1h", "6h", "24h", "7d"],
        description="Time window for trend analysis",
    ),
    anomaly_threshold: float = Query(
        95, ge=0, le=100, description="Percentile threshold for anomaly detection"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Identify metric trends and anomalies"""
    try:
        # Read trends from actual data file
        trends_file = DATA_PATH / "trends.json"
        if not trends_file.exists():
            return {
                "trend": "no_data",
                "average_value": 0,
                "standard_deviation": 0,
                "anomalies": [],
            }

        with open(trends_file, "r") as f:
            data = json.load(f)

        # Determine which trend data to use based on metric name
        if "response" in metric_name.lower():
            trend_data = data.get("response_time_trends", {})
        elif "error" in metric_name.lower():
            trend_data = data.get("error_rate_trends", {})
        elif "cpu" in metric_name.lower():
            trend_data = data.get("cpu_trends", {})
        elif "memory" in metric_name.lower():
            trend_data = data.get("memory_trends", {})
        else:
            trend_data = {}

        # Default values if no data found
        trend = trend_data.get("trend", "no_data")
        average_value = trend_data.get("average_value", 0)
        standard_deviation = trend_data.get("standard_deviation", 0)
        anomalies = trend_data.get("anomalies", [])

        return {
            "trend": trend,
            "average_value": average_value,
            "standard_deviation": standard_deviation,
            "anomalies": anomalies,
        }
    except Exception as e:
        logging.error(f"Error analyzing trends: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/")
async def health_check(api_key: str = Depends(_validate_api_key)):
    """Health check endpoint"""
    return {"status": "healthy", "service": "metrics-api"}


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    import uvicorn

    # Add parent directory to path to import config_utils
    sys.path.append(str(Path(__file__).parent.parent))
    from config_utils import get_server_port

    parser = argparse.ArgumentParser(description="Metrics API Server")
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

    port = args.port if args.port else get_server_port("metrics")

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

    logging.info(f"Starting Metrics server on {protocol}://{args.host}:{port}")
    uvicorn.run(app, host=args.host, port=port, **ssl_config)
