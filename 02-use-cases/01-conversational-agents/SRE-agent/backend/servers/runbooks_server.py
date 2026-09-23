import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
)
from fastapi import (
    Path as PathParam,
)
from fastapi.responses import JSONResponse
from retrieve_api_key import retrieve_api_key

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

app = FastAPI(title="DevOps Runbooks API", version="1.0.0")

DATA_PATH = Path(__file__).parent.parent / "data" / "runbooks_data"

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


@app.get("/runbooks/search")
async def search_runbooks(
    incident_type: Optional[str] = Query(
        None,
        enum=["performance", "availability", "security", "deployment"],
        description="Type of incident",
    ),
    keyword: Optional[str] = Query(
        None, description="Search keyword in runbook content"
    ),
    severity: Optional[str] = Query(
        None,
        enum=["low", "medium", "high", "critical"],
        description="Incident severity level",
    ),
    api_key: str = Depends(_validate_api_key),
):
    """Search runbooks by incident type/keyword"""
    try:
        logging.info(
            f"🔍 RUNBOOKS API: search_runbooks called - incident_type={incident_type}, keyword={keyword}, severity={severity}"
        )

        with open(DATA_PATH / "incident_playbooks.json", "r") as f:
            data = json.load(f)

        runbooks = data.get("playbooks", [])
        original_count = len(runbooks)

        if incident_type:
            runbooks = [r for r in runbooks if r.get("incident_type") == incident_type]
            logging.info(
                f"📋 RUNBOOKS API: Filtered by incident_type '{incident_type}': {len(runbooks)} runbooks"
            )

        if severity:
            runbooks = [r for r in runbooks if r.get("severity") == severity]
            logging.info(
                f"📋 RUNBOOKS API: Filtered by severity '{severity}': {len(runbooks)} runbooks"
            )

        if keyword:
            runbooks = [
                r
                for r in runbooks
                if keyword.lower() in r.get("title", "").lower()
                or keyword.lower() in r.get("description", "").lower()
                or any(keyword.lower() in step.lower() for step in r.get("steps", []))
            ]
            logging.info(
                f"📋 RUNBOOKS API: Filtered by keyword '{keyword}': {len(runbooks)} runbooks"
            )

        response_data = {"runbooks": runbooks}

        # Log detailed response
        logging.info(
            f"📤 RUNBOOKS API: Returning {len(runbooks)} runbooks out of {original_count} total"
        )
        for i, runbook in enumerate(runbooks):
            logging.info(
                f"  📖 Runbook {i + 1}: {runbook.get('title', 'No title')} (ID: {runbook.get('id', 'No ID')})"
            )
            steps = runbook.get("steps", [])
            logging.info(f"     Steps count: {len(steps)}")
            for j, step in enumerate(steps[:3]):  # Show first 3 steps for brevity
                logging.info(f"     Step {j + 1}: {step}")
            if len(steps) > 3:
                logging.info(f"     ... and {len(steps) - 3} more steps")

        logging.info(
            f"📋 RUNBOOKS API: Full response data: {json.dumps(response_data, indent=2)}"
        )
        return response_data
    except Exception as e:
        logging.error(f"❌ Error searching runbooks: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/runbooks/playbook/{playbook_id}")
async def get_incident_playbook(
    playbook_id: str = PathParam(..., description="Playbook ID"),
    api_key: str = Depends(_validate_api_key),
):
    """Retrieve specific incident playbooks"""
    try:
        logging.info(
            f"🔍 RUNBOOKS API: get_incident_playbook called for playbook_id='{playbook_id}'"
        )

        with open(DATA_PATH / "incident_playbooks.json", "r") as f:
            data = json.load(f)

        playbooks = data.get("playbooks", [])

        for playbook in playbooks:
            if playbook.get("id") == playbook_id:
                logging.info(
                    f"📖 RUNBOOKS API: Found playbook '{playbook.get('title', 'No title')}'"
                )
                steps = playbook.get("steps", [])
                logging.info(f"📝 RUNBOOKS API: Playbook has {len(steps)} steps:")
                for i, step in enumerate(steps):
                    logging.info(f"   Step {i + 1}: {step}")

                logging.info(
                    f"📤 RUNBOOKS API: Returning complete playbook data: {json.dumps(playbook, indent=2)}"
                )
                return playbook

        logging.warning(f"❌ RUNBOOKS API: Playbook '{playbook_id}' not found")
        return JSONResponse(status_code=404, content={"error": "Playbook not found"})
    except Exception as e:
        logging.error(f"❌ Error retrieving playbook: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/runbooks/troubleshooting")
async def get_troubleshooting_guide(
    category: Optional[str] = Query(
        None,
        enum=["kubernetes", "performance", "networking", "database"],
        description="Troubleshooting category",
    ),
    issue_type: Optional[str] = Query(None, description="Specific issue type"),
    api_key: str = Depends(_validate_api_key),
):
    """Fetch step-by-step troubleshooting guides"""
    try:
        logging.info(
            f"🔍 RUNBOOKS API: get_troubleshooting_guide called - category={category}, issue_type={issue_type}"
        )

        with open(DATA_PATH / "troubleshooting_guides.json", "r") as f:
            data = json.load(f)

        guides = data.get("guides", [])
        original_count = len(guides)

        if category:
            guides = [g for g in guides if g.get("category") == category]
            logging.info(
                f"📋 RUNBOOKS API: Filtered by category '{category}': {len(guides)} guides"
            )

        if issue_type:
            guides = [
                g
                for g in guides
                if issue_type.lower() in g.get("title", "").lower()
                or issue_type.lower() in g.get("id", "").lower()
            ]
            logging.info(
                f"📋 RUNBOOKS API: Filtered by issue_type '{issue_type}': {len(guides)} guides"
            )

        response_data = {"guides": guides}

        # Log detailed response
        logging.info(
            f"📤 RUNBOOKS API: Returning {len(guides)} guides out of {original_count} total"
        )
        for i, guide in enumerate(guides):
            logging.info(
                f"  📖 Guide {i + 1}: {guide.get('title', 'No title')} (ID: {guide.get('id', 'No ID')})"
            )
            steps = guide.get("steps", [])
            logging.info(f"     Steps count: {len(steps)}")
            for j, step in enumerate(steps[:3]):  # Show first 3 steps for brevity
                logging.info(f"     Step {j + 1}: {step}")
            if len(steps) > 3:
                logging.info(f"     ... and {len(steps) - 3} more steps")

        logging.info(
            f"📋 RUNBOOKS API: Full response data: {json.dumps(response_data, indent=2)}"
        )
        return response_data
    except Exception as e:
        logging.error(f"❌ Error retrieving troubleshooting guides: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/runbooks/escalation")
async def get_escalation_procedures(
    severity: Optional[str] = Query(
        None,
        enum=["low", "medium", "high", "critical"],
        description="Incident severity",
    ),
    incident_type: Optional[str] = Query(None, description="Type of incident"),
    api_key: str = Depends(_validate_api_key),
):
    """Retrieve escalation procedures"""
    try:
        with open(DATA_PATH / "escalation_procedures.json", "r") as f:
            data = json.load(f)

        procedures = data.get("escalation_procedures", [])

        if severity:
            procedures = [p for p in procedures if p.get("severity") == severity]

        if incident_type:
            procedures = [
                p
                for p in procedures
                if incident_type.lower() in p.get("title", "").lower()
                or any(
                    incident_type.lower() in condition.lower()
                    for condition in p.get("trigger_conditions", [])
                )
            ]

        return {"escalation_procedures": procedures}
    except Exception as e:
        logging.error(f"Error retrieving escalation procedures: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/runbooks/resolutions")
async def get_common_resolutions(
    issue: str = Query(..., description="Issue or error type"),
    service: Optional[str] = Query(None, description="Affected service"),
    api_key: str = Depends(_validate_api_key),
):
    """Fetch common resolution steps"""
    try:
        logging.info(
            f"🔍 RUNBOOKS API: get_common_resolutions called - issue='{issue}', service={service}"
        )

        with open(DATA_PATH / "common_resolutions.json", "r") as f:
            data = json.load(f)

        resolutions = data.get("resolutions", [])
        original_count = len(resolutions)

        # Filter by issue
        matching_resolutions = []
        for resolution in resolutions:
            if (
                issue.lower() in resolution.get("issue", "").lower()
                or issue.lower() in resolution.get("id", "").lower()
                or any(
                    issue.lower() in symptom.lower()
                    for symptom in resolution.get("symptoms", [])
                )
            ):
                matching_resolutions.append(resolution)

        logging.info(
            f"📋 RUNBOOKS API: Found {len(matching_resolutions)} matching resolutions for issue '{issue}'"
        )

        # If service specified, prioritize resolutions that mention the service
        if service and matching_resolutions:
            # This is a simple implementation - in real world might have service-specific resolutions
            logging.info(
                f"📋 RUNBOOKS API: Service filter '{service}' applied (basic implementation)"
            )

        response_data = {"resolutions": matching_resolutions}

        # Log detailed response
        logging.info(
            f"📤 RUNBOOKS API: Returning {len(matching_resolutions)} resolutions out of {original_count} total"
        )
        for i, resolution in enumerate(matching_resolutions):
            logging.info(
                f"  📖 Resolution {i + 1}: {resolution.get('issue', 'No issue title')} (ID: {resolution.get('id', 'No ID')})"
            )
            steps = resolution.get("steps", [])
            logging.info(f"     Steps count: {len(steps)}")
            for j, step in enumerate(steps[:3]):  # Show first 3 steps for brevity
                logging.info(f"     Step {j + 1}: {step}")
            if len(steps) > 3:
                logging.info(f"     ... and {len(steps) - 3} more steps")

        logging.info(
            f"📋 RUNBOOKS API: Full response data: {json.dumps(response_data, indent=2)}"
        )
        return response_data
    except Exception as e:
        logging.error(f"❌ Error retrieving common resolutions: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/")
async def health_check(api_key: str = Depends(_validate_api_key)):
    """Health check endpoint"""
    return {"status": "healthy", "service": "runbooks-api"}


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    import uvicorn

    # Add parent directory to path to import config_utils
    sys.path.append(str(Path(__file__).parent.parent))
    from config_utils import get_server_port

    parser = argparse.ArgumentParser(description="Runbooks API Server")
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

    port = args.port if args.port else get_server_port("runbooks")

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

    logging.info(f"Starting Runbooks server on {protocol}://{args.host}:{port}")
    uvicorn.run(app, host=args.host, port=port, **ssl_config)
