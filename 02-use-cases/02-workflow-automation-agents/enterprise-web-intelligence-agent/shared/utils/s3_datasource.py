"""
Unified S3 DataSource combining all fixes from your working code.
Save this as: competitive-intelligence-agent/utils/s3_datasource.py
"""

import json
import time
import tempfile
import shutil
import gzip
import io
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import boto3
from rich.console import Console

console = Console()


class UnifiedS3DataSource:
    """
    Unified S3 data source that combines all fixes:
    - Correct S3 path construction without doubling session ID
    - Proper timestamp parsing from metadata
    - Fallback event creation when recordings are incomplete
    - Support for both direct session access and discovery
    """

    def __init__(self, bucket: str, prefix: str, session_id: Optional[str] = None):
        """
        Initialize S3 data source.

        Args:
            bucket: S3 bucket name
            prefix: S3 prefix (without session ID)
            session_id: Optional session ID. If not provided, will try to discover
        """
        self.s3_client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.session_id = session_id
        self.temp_dir = Path(tempfile.mkdtemp(prefix="bedrock_agentcore_replay_"))

        # Fix: Build the full prefix correctly
        if session_id:
            # Only append session_id if prefix doesn't already contain it
            if prefix and not prefix.endswith(session_id):
                self.full_prefix = f"{prefix}/{session_id}"
            elif prefix:
                # Prefix already contains session_id, don't duplicate
                self.full_prefix = prefix
            else:
                # No prefix, just use session_id
                self.full_prefix = session_id
        else:
            # Try to discover session from prefix
            self.session_id = self._discover_session()
            if self.session_id:
                self.full_prefix = f"{prefix}/{self.session_id}" if prefix else self.session_id
            else:
                self.full_prefix = prefix

        console.print("[cyan]S3 DataSource initialized[/cyan]")
        console.print(f"  Bucket: {bucket}")
        console.print(f"  Prefix: {prefix}")
        console.print(f"  Session: {self.session_id}")
        console.print(f"  Full path: s3://{bucket}/{self.full_prefix}/")

    def cleanup(self):
        """Clean up temp files"""
        if self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass

    def _discover_session(self) -> Optional[str]:
        """Discover the latest session ID from S3 prefix"""
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, Delimiter="/")

            if "CommonPrefixes" in response:
                # Get all session directories
                sessions = []
                for prefix_info in response["CommonPrefixes"]:
                    session_path = prefix_info["Prefix"].rstrip("/")
                    session_id = session_path.split("/")[-1]
                    sessions.append(session_id)

                if sessions:
                    # Return the latest (last) session
                    latest = sorted(sessions)[-1]
                    console.print(f"[green]Discovered session: {latest}[/green]")
                    return latest

            # Alternative: Look for metadata.json files
            response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix)

            if "Contents" in response:
                for obj in response["Contents"]:
                    if "metadata.json" in obj["Key"]:
                        # Extract session ID from path
                        parts = obj["Key"].split("/")
                        for i, part in enumerate(parts):
                            if i > 0 and parts[i - 1] == self.prefix.split("/")[-1]:
                                console.print(f"[green]Found session from metadata: {part}[/green]")
                                return part

        except Exception as e:
            console.print(f"[yellow]Could not discover session: {e}[/yellow]")

        return None

    def list_recordings(self) -> List[Dict]:
        """List recordings with all timestamp parsing fixes"""
        recordings = []

        if not self.session_id:
            console.print("[yellow]No session ID available[/yellow]")
            return recordings

        try:
            # Fetch metadata
            metadata = self._get_metadata()

            # Parse timestamp with all the fixes from your working code
            timestamp = self._parse_timestamp(metadata)

            # Get duration
            duration = metadata.get("duration", 0) or metadata.get("durationMs", 0) or 0

            # Get event count
            event_count = metadata.get("eventCount", 0) or metadata.get("totalEvents", 0) or 0

            # Create recording entry
            recordings.append(
                {
                    "id": self.session_id,
                    "sessionId": self.session_id,
                    "timestamp": timestamp,
                    "date": datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "events": event_count,
                    "duration": duration,
                }
            )

        except Exception as e:
            console.print(f"[yellow]Error listing recordings: {e}[/yellow]")
            # Return fallback recording
            recordings.append(self._create_fallback_recording())

        return recordings

    def download_recording(self, recording_id: str) -> Optional[Dict]:
        """Download and process recording from S3"""
        console.print(f"[cyan]Downloading recording: {recording_id}[/cyan]")

        recording_dir = self.temp_dir / recording_id
        recording_dir.mkdir(exist_ok=True)

        try:
            # Get metadata
            metadata = self._get_metadata()

            # List all objects in session directory
            response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=self.full_prefix)

            if "Contents" not in response:
                console.print("[yellow]No files found in session[/yellow]")
                return self._create_fallback_recording_data()

            # Find batch files
            batch_files = [
                obj["Key"] for obj in response["Contents"] if obj["Key"].endswith(".gz") or "batch-" in obj["Key"]
            ]

            console.print(f"Found {len(batch_files)} batch files")

            # Process batch files
            all_events = []
            for key in batch_files:
                try:
                    console.print(f"[dim]Processing: {key.split('/')[-1]}[/dim]")
                    response = self.s3_client.get_object(Bucket=self.bucket, Key=key)

                    # Read and decompress
                    with gzip.GzipFile(fileobj=io.BytesIO(response["Body"].read())) as gz:
                        content = gz.read().decode("utf-8")

                        # Parse JSON lines
                        for line in content.splitlines():
                            if line.strip():
                                try:
                                    event = json.loads(line)
                                    # Validate event structure
                                    if "type" in event and "timestamp" in event:
                                        all_events.append(event)
                                except json.JSONDecodeError:
                                    continue

                except Exception as e:
                    console.print(f"[yellow]Error processing {key}: {e}[/yellow]")

            console.print(f"[green]✅ Loaded {len(all_events)} events[/green]")

            # If no events or too few, create fallback
            if len(all_events) < 2:
                console.print("[yellow]Insufficient events, using fallback[/yellow]")
                return self._create_fallback_recording_data()

            return {"metadata": metadata, "events": all_events}

        except Exception as e:
            console.print(f"[red]Error downloading recording: {e}[/red]")
            return self._create_fallback_recording_data()

    def _get_metadata(self) -> Dict:
        """Get metadata from S3 with error handling"""
        try:
            metadata_key = f"{self.full_prefix}/metadata.json"
            response = self.s3_client.get_object(Bucket=self.bucket, Key=metadata_key)
            metadata = json.loads(response["Body"].read().decode("utf-8"))
            console.print("[dim]✅ Retrieved metadata[/dim]")
            return metadata
        except Exception as e:
            console.print(f"[yellow]No metadata found: {e}[/yellow]")
            return {}

    def _parse_timestamp(self, metadata: Dict) -> int:
        """Parse timestamp from metadata with all edge cases handled"""
        # Default to current time
        timestamp = int(time.time() * 1000)

        if "startTime" in metadata:
            start_time = metadata["startTime"]

            try:
                if isinstance(start_time, str):
                    # Handle ISO format
                    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    timestamp = int(dt.timestamp() * 1000)
                elif isinstance(start_time, (int, float)):
                    timestamp = int(start_time)
                    # Check if it's in seconds instead of milliseconds
                    if timestamp < 1000000000000:  # Before year 2001 in ms
                        timestamp = timestamp * 1000
            except Exception as e:
                console.print(f"[yellow]Could not parse timestamp: {e}[/yellow]")

        return timestamp

    def _create_fallback_recording(self) -> Dict:
        """Create fallback recording entry"""
        return {
            "id": self.session_id or "unknown",
            "sessionId": self.session_id or "unknown",
            "timestamp": int(time.time() * 1000),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "events": 0,
            "duration": 0,
        }

    def _create_fallback_recording_data(self) -> Dict:
        """
        Create fallback recording data with minimal events when actual recording is unavailable.

        Purpose:
        1. **Graceful Degradation**: When S3 recordings are incomplete or corrupted,
        this ensures the replay viewer doesn't crash completely.

        2. **Development/Testing**: During development, recordings might not be
        available yet. This allows testing the replay viewer interface.

        3. **Partial Failures**: If recording upload partially fails (network issues,
        S3 permissions), users can still access the replay viewer.

        4. **User Experience**: Instead of showing an error, we show a minimal
        interface with a message explaining the recording is unavailable.

        5. **Debugging**: Helps identify when recordings fail - if users see the
        fallback message, they know to check S3 uploads and permissions.

        Returns:
            Dict containing minimal valid rrweb events that create a page showing
            an informative message about the recording being unavailable.
        """
        timestamp = int(time.time() * 1000)

        # Create minimal valid events for rrweb player
        events = [
            {
                "type": 2,  # Meta event
                "timestamp": timestamp,
                "data": {"href": "https://example.com", "width": 1280, "height": 720},
            },
            {
                "type": 4,  # Full snapshot
                "timestamp": timestamp + 100,
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
                                        "attributes": {
                                            "style": "font-family: sans-serif; padding: 40px; text-align: center;"
                                        },
                                        "childNodes": [
                                            {
                                                "type": 3,
                                                "textContent": "Recording data not available - this may be due to upload delays or permissions issues",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
            },
        ]

        return {"metadata": {"fallback": True}, "events": events}
