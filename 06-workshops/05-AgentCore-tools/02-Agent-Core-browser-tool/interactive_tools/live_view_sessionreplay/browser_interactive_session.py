#!/usr/bin/env python3
"""
Complete Browser Example with Recording and Replay

This example demonstrates a full Bedrock AgentCore browser workflow:
1. Create browser with recording enabled
2. Start browser session
3. View live with take/release control
4. Recordings automatically saved to S3
5. View recordings with session replay viewer

Environment Variables:
    AWS_REGION          - AWS region (default: us-west-2)
    BEDROCK_AGENTCORE_ROLE_ARN    - IAM role ARN for Bedrock AgentCore execution (will use default pattern if not set)
    RECORDING_BUCKET    - S3 bucket for recordings (default: session-record-test-{account_id})
    RECORDING_PREFIX    - S3 prefix for recordings (default: replay-data)
    BEDROCK_AGENTCORE_STAGE       - Bedrock AgentCore stage (default: prod)

Requirements:
    - AWS credentials with permission to create/manage Bedrock AgentCore browsers
    - Execution role with permissions for S3 and browser operations
    - S3 bucket with appropriate permissions
"""

import os
import sys
import time
import json
import uuid
import base64
import secrets
import tempfile
import threading
import webbrowser
import signal
import shutil
import gzip
import io
from pathlib import Path
from datetime import datetime
from typing import Dict
from http.server import HTTPServer

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from rich.console import Console
from rich.panel import Panel

# Import tools
from bedrock_agentcore.tools.browser_client import BrowserClient
from bedrock_agentcore._utils.endpoints import get_control_plane_endpoint
from .browser_viewer_replay import BrowserViewerServer
from .session_replay_viewer import SessionReplayViewer, SessionReplayHandler

# Initialize console
console = Console()

# Configuration from environment variables with defaults
REGION = os.environ.get("AWS_REGION", "us-west-2")
BEDROCK_AGENTCORE_STAGE = os.environ.get("BEDROCK_AGENTCORE_STAGE", "prod")

# Get account ID from STS if not provided
try:
    sts_client = boto3.client("sts")
    ACCOUNT_ID = sts_client.get_caller_identity()["Account"]
    console.print(f"[dim]Using AWS Account ID: {ACCOUNT_ID}[/dim]")
except Exception as e:
    console.print(f"[yellow]Warning: Could not determine AWS Account ID: {e}[/yellow]")
    console.print("[yellow]Please set BEDROCK_AGENTCORE_ROLE_ARN environment variable manually.[/yellow]")
    ACCOUNT_ID = "YOUR_ACCOUNT_ID"  # This will be used only if BEDROCK_AGENTCORE_ROLE_ARN is not set

# Set up role ARN and bucket name
ROLE_ARN = os.environ.get(
    "BEDROCK_AGENTCORE_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/BedrockAgentCoreAdmin",
)
BUCKET_PREFIX = os.environ.get("RECORDING_BUCKET_PREFIX", "session-record-test")
BUCKET_NAME = os.environ.get("RECORDING_BUCKET", f"{BUCKET_PREFIX}-{ACCOUNT_ID}")
S3_PREFIX = os.environ.get("RECORDING_PREFIX", "replay-data")


def create_browser_with_recording():
    """Create a browser with recording enabled using Control Plane API"""

    # Step 1: Get Control Plane endpoint and create client
    control_plane_url = get_control_plane_endpoint(REGION)
    console.print(f"Using Control Plane URL: [dim]{control_plane_url}[/dim]")

    control_client = boto3.client("bedrock-agentcore-control", region_name=REGION, endpoint_url=control_plane_url)

    # Generate a unique browser name and client token
    browser_name = f"Browser_{uuid.uuid4().hex[:8]}"
    client_token = str(uuid.uuid4())

    # Create browser with recording configuration
    console.print("\n🔍 Creating browser with recording enabled")
    console.print(f"  - Name: {browser_name}")
    console.print(f"  - Role ARN: {ROLE_ARN}")
    console.print(f"  - S3 Location: s3://{BUCKET_NAME}/{S3_PREFIX}/")

    try:
        create_response = control_client.create_browser(
            name=browser_name,
            networkConfiguration={"networkMode": "PUBLIC"},
            executionRoleArn=ROLE_ARN,
            recording={
                "enabled": True,
                "s3Location": {"bucket": BUCKET_NAME, "prefix": S3_PREFIX},
            },
            clientToken=client_token,
        )

        browser_id = create_response["browserId"]
        browser_arn = create_response.get("browserArn", "Not available")
        status = create_response.get("status", "Unknown")

        console.print(f"✅ Created browser: {browser_id}")
        console.print(f"  ARN: [dim]{browser_arn}[/dim]")
        console.print(f"  Status: {status}")

        # Print recording config for debugging
        if "recording" in create_response:
            console.print(f"📹 Recording config: {create_response['recording']}")
        else:
            console.print("⚠️ No recording config in response!")

        # Step 2: Create Data Plane client and start a browser session
        console.print("\n📱 Starting browser session with the new browser...")

        # Create the Data Plane client
        data_plane_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com"
        console.print(f"Using Data Plane URL: [dim]{data_plane_url}[/dim]")

        data_client = boto3.client("bedrock-agentcore", region_name=REGION, endpoint_url=data_plane_url)

        # Start browser session using the browser_id
        session_response = data_client.start_browser_session(
            browserIdentifier=browser_id,
            name=f"Session-{uuid.uuid4().hex[:8]}",
            sessionTimeoutSeconds=3600,  # 1 hour
        )

        session_id = session_response["sessionId"]
        console.print(f"✅ Started session: {session_id}")

        # Extract automation stream information
        streams = session_response.get("streams", {})
        automation_stream = streams.get("automationStream")

        if automation_stream:
            console.print("✅ Found automation stream information")
        else:
            console.print("⚠️ No automation stream found in response")

        # Now create a BrowserClient and set its properties
        browser_client = BrowserClient(region=REGION)
        browser_client.identifier = browser_id
        browser_client.session_id = session_id

        # Wait for browser to be fully provisioned
        console.print("⏳ Waiting for browser to become available...")
        time.sleep(5)

        return browser_client, {
            "bucket": BUCKET_NAME,
            "prefix": S3_PREFIX,
            "browser_id": browser_id,
            "session_id": session_id,
        }

    except Exception as e:
        console.print(f"❌ Error creating or starting browser: {str(e)}")
        console.print("📋 Details:")
        import traceback

        traceback.print_exc()
        raise


def get_sigv4_headers(region: str, session_id: str) -> Dict[str, str]:
    """Generate SigV4 authentication headers for WebSocket connection"""
    # Host for WebSocket connection
    host = f"https://bedrock-agentcore-control.{REGION}.amazonaws.com"
    path = f"/browser-streams/aws.browser.v1/sessions/{session_id}/live-view"

    # Get AWS credentials for SigV4 signing
    boto_session = boto3.Session()
    credentials = boto_session.get_credentials().get_frozen_credentials()

    # Generate timestamp for request
    timestamp = datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Create AWS request for signing
    request = AWSRequest(
        method="GET",
        url=f"https://{host}{path}",
        headers={"host": host, "x-amz-date": timestamp},
    )

    # Sign the request with SigV4
    auth = SigV4Auth(credentials, "bedrock-agentcore", region)
    auth.add_auth(request)

    # Generate random WebSocket key
    ws_key = base64.b64encode(secrets.token_bytes(16)).decode()

    # Build WebSocket headers
    headers = {
        "Host": host,
        "X-Amz-Date": timestamp,
        "Authorization": request.headers["Authorization"],
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": ws_key,
        "User-Agent": "Bedrock-AgentCore-BrowserViewer/1.0",
    }

    # Add security token if present
    if credentials.token:
        headers["X-Amz-Security-Token"] = credentials.token

    return headers


def run_live_viewer_with_control(browser_client):
    """Run the live viewer with take/release control"""

    print("\n🖥️  Starting Live Viewer...")
    print("Features available:")
    print("  - 🎮 Take Control: Disable automation and interact manually")
    print("  - 🚫 Release Control: Return control to automation")
    print("  - 📐 Resize display: 720p, 900p, 1080p, 1440p")

    # Start the viewer
    viewer = BrowserViewerServer(browser_client, port=8000)
    viewer_url = viewer.start(open_browser=True)

    print(f"\n✅ Live viewer running at: {viewer_url}")
    print("\nYou can now:")
    print("1. Take control and browse manually")
    print("2. Navigate to different websites")
    print("3. All actions are being recorded to S3")
    print("\nPress Ctrl+C when done to view recordings")

    try:
        # KeeKeep running until user stops
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹️  Stopping live viewer...")


def view_recordings(s3_location):
    """View the recorded sessions directly with custom SessionReplayViewer"""

    print("\n📼 Checking for recordings in S3...")
    print(f"Location: s3://{s3_location['bucket']}/{s3_location['prefix']}/")

    # Create S3 client
    s3 = boto3.client("s3")

    # Wait a bit longer for recordings to be uploaded
    print("⏳ Waiting for recordings to be uploaded to S3 (30 seconds)...")
    time.sleep(30)

    try:
        # First, get a flat list of all objects to find session directories
        response = s3.list_objects_v2(Bucket=s3_location["bucket"], Prefix=s3_location["prefix"])

        if "Contents" not in response:
            print("No objects found in S3 location")
            return

        # Get all unique directory names that contain metadata.json
        session_dirs = set()
        metadata_files = []

        for obj in response["Contents"]:
            key = obj["Key"]
            if "metadata.json" in key:
                # Extract the session directory from the path
                # Example: replay-data/01JZV5RW9FEV3GC5RPG8PYGXFR/metadata.json
                session_dir = key.split("/")[-2]
                session_dirs.add(session_dir)
                metadata_files.append(key)
                print(f"Found session with metadata: {session_dir}")

        if not session_dirs:
            print("No session directories with metadata.json found")
            return

        # Sort the session directories to find the latest one
        # Assuming the session IDs are time-ordered (which they appear to be)
        session_dirs = sorted(list(session_dirs))
        latest_session = session_dirs[-1]
        print(f"Using latest session: {latest_session}")

        # Define the custom S3 data source class first, before using it
        class CustomS3DataSource:
            """Custom data source for S3 recordings with known structure"""

            def __init__(self, bucket, prefix, session_id):
                self.s3_client = boto3.client("s3")
                self.bucket = bucket
                self.prefix = prefix
                self.session_id = session_id
                self.session_prefix = f"{prefix}/{session_id}"
                self.temp_dir = Path(tempfile.mkdtemp(prefix="bedrock_agentcore_replay_"))

            def cleanup(self):
                """Clean up temp files"""
                if self.temp_dir.exists():
                    shutil.rmtree(self.temp_dir)

            def list_recordings(self):
                """List recordings directly"""
                recordings = []

                # Fetch metadata to get details about the recording
                metadata = {}
                try:
                    metadata_key = f"{self.session_prefix}/metadata.json"
                    print(f"Fetching metadata from: {metadata_key}")
                    response = self.s3_client.get_object(Bucket=self.bucket, Key=metadata_key)
                    metadata = json.loads(response["Body"].read().decode("utf-8"))
                    print(f"✅ Found metadata: {metadata}")
                except Exception as e:
                    print(f"⚠️ Could not get metadata: {e}")

                # List batch files to count events
                batch_files = []
                response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=f"{self.session_prefix}/batch-")

                if "Contents" in response:
                    batch_files = [obj["Key"] for obj in response["Contents"]]
                    print(f"✅ Found {len(batch_files)} batch files")

                # Create a recording entry
                timestamp = int(time.time() * 1000)  # Default to now
                duration = 0
                event_count = 0

                # Parse the timestamp correctly
                if "startTime" in metadata:
                    try:
                        # Handle ISO format
                        if isinstance(metadata["startTime"], str):
                            dt = datetime.fromisoformat(metadata["startTime"].replace("Z", "+00:00"))
                            timestamp = int(dt.timestamp() * 1000)
                        else:
                            timestamp = metadata["startTime"]
                    except Exception as e:
                        print(f"⚠️ Error parsing startTime: {e}")

                # Try different duration fields
                if "duration" in metadata:
                    duration = metadata["duration"]
                elif "durationMs" in metadata:
                    duration = metadata["durationMs"]

                # Try different event count fields
                if "eventCount" in metadata:
                    event_count = metadata["eventCount"]
                elif "totalEvents" in metadata:
                    event_count = metadata["totalEvents"]

                # Use correct datetime formatting
                date_string = datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")

                recordings.append(
                    {
                        "id": self.session_id,
                        "sessionId": self.session_id,
                        "timestamp": timestamp,
                        "date": date_string,
                        "events": event_count,
                        "duration": duration,
                    }
                )

                return recordings

            def download_recording(self, recording_id):
                """Download recording from S3"""
                print(f"Downloading recording: {recording_id}")

                recording_dir = self.temp_dir / recording_id
                recording_dir.mkdir(exist_ok=True)

                try:
                    # Get metadata
                    metadata = {}
                    try:
                        metadata_key = f"{self.session_prefix}/metadata.json"
                        response = self.s3_client.get_object(Bucket=self.bucket, Key=metadata_key)
                        metadata = json.loads(response["Body"].read().decode("utf-8"))
                        print(f"✅ Downloaded metadata: {metadata}")
                    except Exception as e:
                        print(f"⚠️ No metadata found: {e}")

                    # Get batch files from metadata ta if possible
                    batch_files = []
                    if "batches" in metadata and isinstance(metadata["batches"], list):
                        for batch in metadata["batches"]:
                            if "file" in batch:
                                batch_files.append(f"{self.session_prefix}/{batch['file']}")

                    # If no batch files found in metadata, look for them directly
                    if not batch_files:
                        response = self.s3_client.list_objects_v2(
                            Bucket=self.bucket, Prefix=f"{self.session_prefix}/batch-"
                        )

                        if "Contents" in response:
                            batch_files = [obj["Key"] for obj in response["Contents"]]

                    all_events = []
                    print(f"Processing {len(batch_files)} batch files: {batch_files}")

                    for key in batch_files:
                        try:
                            print(f"Downloading batch file: {key}")
                            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)

                            # Try to read as gzipped JSON lines
                            with gzip.GzipFile(fileobj=io.BytesIO(response["Body"].read())) as gz:
                                content = gz.read().decode("utf-8")
                                print(f"Read {len(content)} bytes of content")

                                # Process each line as a JSON event
                                for line in content.splitlines():
                                    if line.strip():
                                        try:
                                            event = json.loads(line)
                                            # Validate event
                                            if "type" in event and "timestamp" in event:
                                                all_events.append(event)
                                            else:
                                                print("⚠️ Skipping invalid event (missing required fields)")
                                        except json.JSONDecodeError:
                                            print(f"⚠️ Invalid JSON in line: {line[:50]}...")

                                print(f"  Added {len(all_events)} events")

                        except Exception as e:
                            print(f"⚠️ Error processing file {key}: {e}")
                            import traceback

                            traceback.print_exc()

                    print(f"✅ Loaded {len(all_events)} events")

                    # If no events were loaded, create sample events
                    if len(all_events) < 2:
                        print("⚠️ Insufficient events, creating sample events for testing")
                        all_events = [
                            {
                                "type": 2,
                                "timestamp": timestamp,
                                "data": {
                                    "href": "https://example.com",
                                    "width": 1280,
                                    "height": 720,
                                },
                            }
                            for timestamp in range(
                                int(time.time() * 1000),
                                int(time.time() * 1000) + 10000,
                                1000,
                            )
                        ]
                        # Add a minimal DOM snapshot event
                        all_events.append(
                            {
                                "type": 4,
                                "timestamp": int(time.time() * 1000) + 1000,
                                "data": {
                                    "node": {
                                        "type": 1,
                                        "childNodes": [
                                            {
                                                "type": 2,
                                                "tagName": "html",
                                                "attributes": {},
                                                "childNodes": [
                                                    {
                                                        "type": 2,
                                                        "tagName": "body",
                                                        "attributes": {},
                                                        "childNodes": [
                                                            {
                                                                "type": 3,
                                                                "textContent": "Sample content",
                                                            }
                                                        ],
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                },
                            }
                        )

                    # Return the parsed recording
                    return {"metadata": metadata, "events": all_events}

                except Exception as e:
                    print(f"❌ Error downloading recording: {e}")
                    import traceback

                    traceback.print_exc()
                    return None

        # Create a custom HTTP handler that fixes the JSON response issue
        class CustomSessionReplayHandler(SessionReplayHandler):
            """Custom HTTP request handler for session replay viewer"""

            def serve_recordings_list(self):
                """Return list of recordings - FIX FOR HTML RESPONSE ISSUE"""
                try:
                    recordings = self.data_source.list_recordings()
                    response = json.dumps(recordings)

                    # Debug output to see what we're returning
                    print(f"Serving recordings list: {response[:100]}...")

                    # Ensure proper content type and headers
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    # Add CORS headers to prevent issues
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "*")
                    self.end_headers()

                    # Write the response as bytes
                    self.wfile.write(response.encode("utf-8"))

                except Exception as e:
                    print(f"❌ Error in serve_recordings_list: {e}")
                    import traceback

                    traceback.print_exc()

                    # Return a proper error response as JSON with empty recordings array
                    error_response = json.dumps({"error": str(e), "recordings": []})
                    self.send_response(200)  # Use 200 so client can process the error
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(error_response)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(error_response.encode("utf-8"))

            def download_and_serve_recording(self, recording_id):
                """Download recording and serve it - FIX FOR HTML RESPONSE ISSUE"""
                try:
                    recording_data = self.data_source.download_recording(recording_id)

                    if recording_data:
                        response = json.dumps({"success": True, "data": recording_data})

                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(response)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(response.encode("utf-8"))
                    else:
                        error_response = json.dumps({"success": False, "error": "Recording not found"})
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(error_response)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(error_response.encode("utf-8"))

                except Exception as e:
                    print(f"❌ Error in download_and_serve_recording: {e}")
                    import traceback

                    traceback.print_exc()

                    error_response = json.dumps({"success": False, "error": str(e)})
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(error_response)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(error_response.encode("utf-8"))

            def do_OPTIONS(self):
                """Handle OPTIONS requests for CORS preflight"""
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()

        # Create custom viewer with our fixed handler
        class CustomSessionReplayViewer(SessionReplayViewer):
            def start(self):
                """Start the replay viewer server with custom handler"""
                # Ensure viewer directory exists
                self.viewer_path.mkdir(parents=True, exist_ok=True)

                # Find available port
                port = self.find_available_port()

                # Create request handler
                def handler_factory(*args, **kwargs):
                    return CustomSessionReplayHandler(self.data_source, self.viewer_path, *args, **kwargs)

                # Start server
                self.server = HTTPServer(("", port), handler_factory)

                # Start in thread
                server_thread = threading.Thread(target=self.server.serve_forever)
                server_thread.daemon = True
                server_thread.start()

                url = f"http://localhost:{port}"

                console.print(
                    Panel(
                        f"[bold cyan]Session Replay Viewer Running[/bold cyan]\n\n"
                        f"URL: [link]{url}[/link]\n\n"
                        f"[yellow]Press Ctrl+C to stop[/yellow]",
                        title="Ready",
                        border_style="green",
                    )
                )

                # Open browser
                webbrowser.open(url)

                # Handle shutdown
                def signal_handler(sig, frame):
                    console.print("\n[yellow]Shutting down...[/yellow]")
                    self.server.shutdown()
                    if hasattr(self.data_source, "cleanup"):
                        self.data_source.cleanup()
                    sys.exit(0)

                signal.signal(signal.SIGINT, signal_handler)

                # Keep running
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass

        # Create data source
        data_source = CustomS3DataSource(
            bucket=s3_location["bucket"],
            prefix=s3_location["prefix"],
            session_id=latest_session,
        )

        print(f"🎬 Starting session replay viewer for: {latest_session}")
        viewer = CustomSessionReplayViewer(data_source=data_source, port=8002)
        viewer.start()  # This will block until Ctrl+C

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


def main():
    """Main flow"""

    console.print("🚀 Bedrock AgentCore Browser Complete Example")
    console.print("=" * 50)

    browser_client = None

    try:
        # Step 1: Create browser with recording
        console.print("\n📝 Step 1: Creating browser with recording enabled...")
        browser_client, s3_location = create_browser_with_recording()

        # Step 2: Live viewer with control
        console.print("\n👁️  Step 2: Starting live viewer...")
        run_live_viewer_with_control(browser_client)

        # Step 3: Make sure session is properly stopped to ensure recordings are uploaded
        console.print("\n⏹️  Stopping browser session...")
        browser_client.stop()
        console.print("✅ Browser session stopped")

        # Step 4: View recordings
        console.print("\n🎬 Step 3: Viewing recordings...")
        view_recordings(s3_location)

    except Exception as e:
        console.print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

    finally:
        # Cleanup
        if browser_client:
            try:
                browser_client.stop()
                console.print("\n✅ Browser session stopped")
            except:  # noqa: E722
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n🛑 Process interrupted by user")
        sys.exit(0)
