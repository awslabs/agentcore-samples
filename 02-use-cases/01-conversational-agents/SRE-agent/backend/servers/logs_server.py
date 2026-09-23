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

app = FastAPI(title="Application Logs API", version="1.0.0")

DATA_PATH = Path(__file__).parent.parent / "data" / "logs_data"

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


def _filter_by_time(
    logs: list, start_time: Optional[str] = None, end_time: Optional[str] = None
) -> list:
    """Filter logs by time range"""
    if not start_time and not end_time:
        return logs

    filtered_logs = []
    start_dt = _parse_timestamp(start_time) if start_time else None
    end_dt = _parse_timestamp(end_time) if end_time else None

    for log in logs:
        log_timestamp = log.get("timestamp")
        if not log_timestamp:
            continue

        try:
            log_dt = _parse_timestamp(log_timestamp)

            if start_dt and log_dt < start_dt:
                continue
            if end_dt and log_dt > end_dt:
                continue

            filtered_logs.append(log)
        except:
            # Include logs with unparseable timestamps
            filtered_logs.append(log)

    return filtered_logs


def _parse_log_file(file_path: Path, pattern: Optional[str] = None):
    """Parse log file and filter by pattern"""
    logs = []

    if file_path.suffix == ".json":
        with open(file_path, "r") as f:
            data = json.load(f)
            # Handle both array and object with array
            if isinstance(data, list):
                logs = data
            elif isinstance(data, dict):
                # Get the first array value in the dict
                for key, value in data.items():
                    if isinstance(value, list):
                        logs = value
                        break
    else:
        # Parse text log files
        with open(file_path, "r") as f:
            for line in f:
                if pattern and pattern.lower() not in line.lower():
                    continue
                # Parse log line to extract timestamp, level, and message
                parts = line.strip().split(" ", 3)
                if len(parts) >= 4:
                    timestamp = parts[0]
                    level_part = parts[1]
                    service = parts[2]
                    message = parts[3] if len(parts) > 3 else ""

                    # Extract log level from [LEVEL] format
                    level = "INFO"
                    if "[" in level_part and "]" in level_part:
                        level = level_part.strip("[]")

                    logs.append(
                        {
                            "timestamp": timestamp,
                            "level": level,
                            "service": service,
                            "message": message,
                        }
                    )
                else:
                    logs.append({"message": line.strip()})

    return logs


@app.get("/logs/search")
async def search_logs(
    pattern: str = Query(..., description="Search pattern or keyword"),
    start_time: Optional[str] = Query(None, description="Start time for log search"),
    end_time: Optional[str] = Query(None, description="End time for log search"),
    log_level: Optional[str] = Query(
        None, enum=["ERROR", "WARN", "INFO", "DEBUG"], description="Filter by log level"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Search logs by pattern/timeframe"""
    try:
        application_logs = _parse_log_file(DATA_PATH / "application.log", pattern)

        # Filter by log level if provided
        if log_level:
            application_logs = [
                log for log in application_logs if log.get("level") == log_level
            ]

        # Filter by time range
        application_logs = _filter_by_time(application_logs, start_time, end_time)

        return {"logs": application_logs[:100]}  # Limit results
    except Exception as e:
        logging.error(f"Error searching logs: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/logs/errors")
async def get_error_logs(
    since: Optional[str] = Query(None, description="Get errors since this timestamp"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    api_key: str = Depends(_validate_api_key),
):
    """Retrieve error-specific entries"""
    try:
        with open(DATA_PATH / "error.log", "r") as f:
            error_logs = json.load(f)

        if service:
            error_logs = [log for log in error_logs if log.get("service") == service]

        # Filter by since timestamp
        if since:
            error_logs = _filter_by_time(error_logs, start_time=since)

        return {"errors": error_logs}
    except Exception as e:
        logging.error(f"Error retrieving error logs: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/logs/patterns")
async def analyze_log_patterns(
    time_window: Optional[str] = Query(
        "24h",
        enum=["1h", "6h", "24h", "7d"],
        description="Time window for pattern analysis",
    ),
    min_occurrences: int = Query(
        5, ge=1, description="Minimum occurrences to be considered a pattern"
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Identify recurring issues"""
    try:
        # Read patterns from actual data file
        patterns_file = DATA_PATH / "log_patterns.json"
        if not patterns_file.exists():
            return {"patterns": []}

        with open(patterns_file, "r") as f:
            data = json.load(f)

        patterns = data.get("patterns", [])

        # Filter by min_occurrences
        patterns = [p for p in patterns if p["count"] >= min_occurrences]

        return {"patterns": patterns}
    except Exception as e:
        logging.error(f"Error analyzing log patterns: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/logs/recent")
async def get_recent_logs(
    limit: int = Query(
        100, ge=1, le=1000, description="Number of recent logs to return"
    ),
    service: Optional[str] = Query(None, description="Filter by service name"),
    api_key: str = Depends(_validate_api_key),
):
    """Fetch latest log entries"""
    try:
        all_logs = _parse_log_file(DATA_PATH / "application.log")

        if service:
            all_logs = [log for log in all_logs if service in log.get("service", "")]

        # Return the most recent logs (last N entries)
        recent_logs = all_logs[-limit:] if len(all_logs) > limit else all_logs
        recent_logs.reverse()  # Most recent first

        return {"logs": recent_logs}
    except Exception as e:
        logging.error(f"Error retrieving recent logs: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/logs/count")
async def count_log_events(
    event_type: str = Query(..., description="Type of event to count"),
    time_window: Optional[str] = Query(
        "24h", enum=["1h", "6h", "24h", "7d"], description="Time window for counting"
    ),
    group_by: Optional[str] = Query(
        None,
        enum=["service", "level", "hour"],
        description="Group results by this field",
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Count occurrences of specific events"""
    try:
        # Read counts from actual data file
        counts_file = DATA_PATH / "log_counts.json"
        if not counts_file.exists():
            return {"total_count": 0, "counts": []}

        with open(counts_file, "r") as f:
            data = json.load(f)

        if event_type.lower() == "error":
            error_data = data.get("error_counts", {})
            total_count = error_data.get("total_count", 0)

            if group_by == "service":
                counts = error_data.get("by_service", [])
            elif group_by == "level":
                counts = error_data.get("by_level", [])
            else:
                counts = []
        else:
            all_data = data.get("all_counts", {})
            total_count = all_data.get("total_count", 0)
            counts = all_data.get("by_level", [])

        return {"total_count": total_count, "counts": counts}
    except Exception as e:
        logging.error(f"Error counting log events: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/")
async def health_check(api_key: str = Depends(_validate_api_key)):
    """Health check endpoint"""
    return {"status": "healthy", "service": "logs-api"}


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    import uvicorn

    # Add parent directory to path to import config_utils
    sys.path.append(str(Path(__file__).parent.parent))
    from config_utils import get_server_port

    parser = argparse.ArgumentParser(description="Logs API Server")
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

    port = args.port if args.port else get_server_port("logs")

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

    logging.info(f"Starting Logs server on {protocol}://{args.host}:{port}")
    uvicorn.run(app, host=args.host, port=port, **ssl_config)
