#!/usr/bin/env python3
"""
Session Replay Viewer for Bedrock Agentcore Browser Sessions

Views session recordings stored in standard rrweb-{timestamp}-{sessionid} format.
Supports both local sample recordings and S3 streaming.
"""

import os
import sys
import json
import time
import tempfile
import threading
import webbrowser
import socket
import signal
import shutil
import gzip
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import mimetypes
from datetime import datetime

import boto3
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


class SessionReplayHandler(BaseHTTPRequestHandler):
    """HTTP request handler for session replay viewer"""

    def __init__(self, data_source, viewer_path, *args, **kwargs):
        self.data_source = data_source
        self.viewer_path = viewer_path
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to reduce server logging noise"""
        pass

    def do_GET(self):
        """Handle GET requests"""
        try:
            path = self.path.split("?")[0]

            if path == "/":
                self.serve_file("index.html")
            elif path == "/api/recordings":
                self.serve_recordings_list()
            elif path.startswith("/api/download/"):
                recording_id = path.split("/")[-1]
                self.download_and_serve_recording(recording_id)
            else:
                self.serve_file(path.lstrip("/"))

        except Exception as e:
            console.print(f"[red]Error handling request: {e}[/red]")
            self.send_error(500, str(e))

    def serve_file(self, file_path):
        """Serve static files"""
        full_path = self.viewer_path / file_path

        if not full_path.exists():
            # Create index.html on the fly
            if file_path == "index.html":
                self._create_index_html(full_path)
            else:
                self.send_error(404, f"File not found: {file_path}")
                return

        content_type, _ = mimetypes.guess_type(str(full_path))
        if content_type is None:
            content_type = "application/octet-stream"

        with open(full_path, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _create_index_html(self, path):
        """Create the viewer HTML interface"""
        print("\n======= DEBUGGING =======")
        print(f"Creating index.html at: {path}")
        print(f"Path exists: {os.path.exists(path)}")
        print(f"Parent directory exists: {os.path.exists(path.parent)}")

        # Ensure the directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        html_content = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bedrock Agentcore Session Replay Viewer</title>
    <style>
        * { box-sizing: border-box; }
        
        body {
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5;
        }
        
        .header {
            background: #232f3e;
            color: white;
            padding: 15px 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .header h1 {
            margin: 0;
            font-size: 20px;
            font-weight: 400;
        }
        
        .container {
            display: flex;
            height: calc(100vh - 60px);
        }
        
        .sidebar {
            width: 350px;
            background: white;
            border-right: 1px solid #e0e0e0;
            display: flex;
            flex-direction: column;
        }
        
        .sidebar-header {
            padding: 15px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
            font-weight: 500;
        }
        
        .recordings-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }
        
        .recording-item {
            padding: 12px;
            margin: 5px 0;
            background: #f8f9fa;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            border: 1px solid transparent;
        }
        
        .recording-item:hover {
            background: #e9ecef;
            border-color: #dee2e6;
        }
        
        .recording-item.active {
            background: #0073bb;
            color: white;
            border-color: #0073bb;
        }
        
        .recording-item .date {
            font-size: 12px;
            opacity: 0.8;
            margin-bottom: 4px;
        }
        
        .recording-item .session-id {
            font-family: monospace;
            font-size: 13px;
            margin-bottom: 4px;
        }
        
        .recording-item .stats {
            font-size: 12px;
            opacity: 0.7;
        }
        
        .viewer {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #e9ecef;
        }
        
        .player-container {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        #player {
            width: 100%;
            max-width: 1200px;
            height: 100%;
            max-height: 800px;
            background: white;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            border-radius: 8px;
        }
        
        .empty-state {
            text-align: center;
            color: #6c757d;
            padding: 40px;
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #0073bb;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .error {
            color: #dc3545;
            padding: 20px;
            background: #f8d7da;
            border: 1px solid #f5c6cb;
            border-radius: 4px;
            margin: 20px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Bedrock Agentcore Session Replay Viewer</h1>
    </div>
    
    <div class="container">
        <div class="sidebar">
            <div class="sidebar-header">
                Available Recordings
                <span id="recordingCount" style="float: right; opacity: 0.7;"></span>
            </div>
            <div class="recordings-list" id="recordingsList">
                <div class="empty-state">
                    <div class="loading"></div>
                    Loading recordings...
                </div>
            </div>
        </div>
        
        <div class="viewer">
            <div class="player-container">
                <div id="player">
                    <div class="empty-state">
                        <h2>Select a Recording</h2>
                        <p>Choose a recording from the list to begin playback</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/rrweb@latest/dist/rrweb.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/rrweb-player@latest/dist/index.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/rrweb-player@latest/dist/style.css">
    
    <script>
        let currentPlayer = null;
        let recordings = [];
        
        async function loadRecordings() {
            try {
                const response = await fetch('/api/recordings');
                const data = await response.json();
                
                // Check if response has error
                if (data.error) {
                    console.error('Server returned error:', data.error);
                    document.getElementById('recordingsList').innerHTML = 
                        '<div class="error">Failed to load recordings: ' + data.error + '</div>';
                    document.getElementById('recordingCount').textContent = '(0)';
                    return;
                }
                
                // Make sure recordings is an array
                recordings = Array.isArray(data) ? data : (data.recordings || []);
                
                const listEl = document.getElementById('recordingsList');
                const countEl = document.getElementById('recordingCount');
                
                if (recordings.length === 0) {
                    listEl.innerHTML = '<div class="empty-state">No recordings found</div>';
                    countEl.textContent = '(0)';
                    return;
                }
                
                // CHANGED: Use string concatenation instead of template literals
                countEl.textContent = '(' + recordings.length + ')';
                
                // CHANGED: Create HTML using map with string concatenation
                listEl.innerHTML = recordings.map(function(recording, index) {
                    return '<div class="recording-item" data-index="' + index + '" onclick="loadRecording(' + index + ')">' +
                        '<div class="date">' + recording.date + '</div>' +
                        '<div class="session-id">' + recording.sessionId + '</div>' +
                        '<div class="stats">' + 
                            recording.events + ' events • ' + formatDuration(recording.duration) +
                        '</div>' +
                    '</div>';
                }).join('');
                
            } catch (e) {
                console.error('Failed to load recordings:', e);
                document.getElementById('recordingsList').innerHTML = 
                    '<div class="error">Failed to load recordings: ' + e.message + '</div>';
                document.getElementById('recordingCount').textContent = '(0)';
            }
        }
        
        function formatDuration(ms) {
            const seconds = Math.floor(ms / 1000);
            const minutes = Math.floor(seconds / 60);
            const hours = Math.floor(minutes / 60);
            
            // CHANGED: Use string concatenation instead of template literals
            if (hours > 0) {
                return hours + 'h ' + (minutes % 60) + 'm';
            } else if (minutes > 0) {
                return minutes + 'm ' + (seconds % 60) + 's';
            } else {
                return seconds + 's';
            }
        }
        
        async function loadRecording(index) {
            const recording = recordings[index];
            
            document.querySelectorAll('.recording-item').forEach(el => {
                el.classList.remove('active');
            });
            // CHANGED: Use string concatenation
            document.querySelector('[data-index="' + index + '"]').classList.add('active');
            
            const playerEl = document.getElementById('player');
            playerEl.innerHTML = '<div class="empty-state"><div class="loading"></div>Downloading recording...</div>';
            
            try {
                // Safely dispose of the existing player first
                if (currentPlayer) {
                    try {
                        if (typeof currentPlayer.destroy === 'function') {
                            currentPlayer.destroy();
                        } else {
                            console.warn('Current player does not have a destroy method');
                        }
                    } catch (e) {
                        console.error('Error destroying player:', e);
                    }
                    currentPlayer = null;
                }
                
                // CHANGED: Use string concatenation
                const response = await fetch('/api/download/' + recording.id);
                const result = await response.json();
                
                if (!result.success || !result.data) {
                    throw new Error(result.error || 'Failed to download recording');
                }
                
                const { events } = result.data;
                
                if (!events || events.length === 0) {
                    throw new Error('Recording contains no events');
                }
                
                // CHANGED: Use string concatenation for logging
                console.log('Loaded ' + events.length + ' events. First event type: ' + events[0].type);
                
                playerEl.innerHTML = '';
                
                if (typeof rrwebPlayer !== 'function') {
                    throw new Error('rrwebPlayer not found - make sure the library is loaded');
                }
                
                const width = Math.min(playerEl.offsetWidth, 1200);
                const height = Math.min(playerEl.offsetHeight, 800);
                
                // CHANGED: Use string concatenation
                console.log('Creating player with dimensions ' + width + 'x' + height);
                
                currentPlayer = new rrwebPlayer({
                    target: playerEl,
                    props: {
                        events: events,
                        width: width,
                        height: height,
                        autoPlay: true,
                        showController: true
                    }
                });
                
                console.log('Player created:', currentPlayer);
                
            } catch (e) {
                console.error('Failed to load recording:', e);
                // CHANGED: Use string concatenation
                playerEl.innerHTML = '<div class="error">Error: ' + e.message + '</div>';
            }
        }
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            console.log('Session Replay Viewer loaded');
            loadRecordings();
        });
        
        // Auto-refresh recordings list periodically
        setInterval(loadRecordings, 30000);
    </script>
</body>
</html>"""

        print(f"Original content length: {len(html_content)}")
        print(f"Contains 'DOLLAR': {'DOLLAR' in html_content}")

        # Try to replace DOLLAR with $ just in case
        if "DOLLAR" in html_content:
            html_content = html_content.replace("DOLLAR", "$")
            print(f"After replacement, contains 'DOLLAR': {'DOLLAR' in html_content}")

        print(f"Writing to file: {path}")

        try:
            with open(path, "w") as f:
                f.write(html_content)

            # Verify the file was written correctly
            file_size = os.path.getsize(path)
            print(f"File written successfully, size: {file_size} bytes")

            # Verify content in file
            with open(path, "r") as f:
                first_100 = f.read(100)
            print(f"First 100 chars of file: {first_100}")

        except Exception as e:
            print(f"ERROR writing file: {e}")

        print("======= END DEBUGGING =======\n")

    def serve_recordings_list(self):
        """Return list of recordings with proper headers"""
        try:
            recordings = self.data_source.list_recordings()
            response = json.dumps(recordings)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.end_headers()

            self.wfile.write(response.encode("utf-8"))

        except Exception as e:
            console.print(f"[red]Error in serve_recordings_list: {e}[/red]")

            error_response = json.dumps({"error": str(e), "recordings": []})
            self.send_response(200)  # Use 200 to ensure client gets the error
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_response)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(error_response.encode("utf-8"))

    def download_and_serve_recording(self, recording_id):
        """Download recording and serve it with proper headers"""
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
            console.print(f"[red]Error in download_and_serve_recording: {e}[/red]")
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


class DataSource:
    """Base class for data sources"""

    def list_recordings(self):
        raise NotImplementedError

    def download_recording(self, recording_id):
        raise NotImplementedError


class LocalDataSource(DataSource):
    """Local file system data source"""

    def __init__(self, recordings_dir):
        self.recordings_dir = Path(recordings_dir)
        console.print(f"[cyan]Using local recordings from:[/cyan] {self.recordings_dir}")

    def list_recordings(self):
        """List local recordings"""
        recordings = []

        if not self.recordings_dir.exists():
            return recordings

        # Look for rrweb-* directories
        for item in self.recordings_dir.iterdir():
            if item.is_dir() and item.name.startswith("rrweb-"):
                recording_id = item.name

                # Parse recording ID
                parts = recording_id.split("-")
                if len(parts) >= 3:
                    timestamp = parts[1]
                    session_id = "-".join(parts[2:])

                    # Try to get metadata
                    metadata = {}
                    metadata_file = item / "metadata.json"
                    if metadata_file.exists():
                        with open(metadata_file, "r") as f:
                            metadata = json.load(f)

                    recordings.append(
                        {
                            "id": recording_id,
                            "sessionId": session_id,
                            "timestamp": timestamp,
                            "date": datetime.fromtimestamp(int(timestamp) / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                            "events": metadata.get("totalEvents", 0),
                            "duration": metadata.get("duration", 0),
                        }
                    )

        recordings.sort(key=lambda x: x["timestamp"], reverse=True)
        return recordings

    def download_recording(self, recording_id):
        """Load recording from local files"""
        recording_dir = self.recordings_dir / recording_id

        if not recording_dir.exists():
            return None

        all_events = []
        metadata = {}

        # Load metadata if exists
        metadata_file = recording_dir / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                metadata = json.load(f)

        # Load batch files
        batch_files = sorted(recording_dir.glob("batch-*.ndjson.gz"))

        for batch_file in batch_files:
            with gzip.open(batch_file, "rt") as f:
                for line in f:
                    if line.strip():
                        all_events.append(json.loads(line))

        return {"metadata": metadata, "events": all_events}


class S3DataSource(DataSource):
    """S3 data source"""

    def __init__(self, bucket, prefix=""):
        self.s3_client = boto3.client("s3")
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.temp_dir = Path(tempfile.mkdtemp(prefix="bedrock_agentcore_replay_"))

        console.print("[cyan]Using S3 location:[/cyan]")
        console.print(f"  Bucket: {bucket}")
        console.print(f"  Prefix: {prefix}")

    def cleanup(self):
        """Clean up temp files"""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def list_recordings(self):
        """List recordings from S3"""
        recordings = []

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")

            # Look for any directories (not just rrweb-*)
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix, Delimiter="/"):
                if "CommonPrefixes" in page:
                    console.print(f"Found {len(page['CommonPrefixes'])} directories in prefix {self.prefix}")

                    for prefix_info in page["CommonPrefixes"]:
                        prefix = prefix_info["Prefix"]
                        recording_id = prefix.rstrip("/").split("/")[-1]

                        # Check if it's a recording directory by looking for metadata.json
                        metadata = self._get_metadata(recording_id)
                        if metadata:
                            # This is a valid recording directory
                            session_id = recording_id  # Use the folder name as the session ID
                            timestamp = int(metadata.get("startTime", time.time() * 1000))

                            recordings.append(
                                {
                                    "id": recording_id,
                                    "sessionId": session_id,
                                    "timestamp": timestamp,
                                    "date": datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                                    "events": metadata.get("eventCount", 0),
                                    "duration": metadata.get("duration", 0),
                                }
                            )
                            console.print(f"✅ Found recording: {recording_id}")
                        else:
                            console.print(f"⚠️ Directory without metadata: {recording_id}")

                recordings.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                console.print(f"[green]Found {len(recordings)} recordings[/green]")

            # If no recordings found with CommonPrefixes, try a direct list
            if not recordings:
                console.print("Trying alternative method to find recordings...")

                # Get a flat list of all objects
                all_objects = []
                for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                    if "Contents" in page:
                        all_objects.extend(page["Contents"])

                # Extract unique directory names
                dirs = set()
                for obj in all_objects:
                    key = obj["Key"]
                    if "/" in key[len(self.prefix) :]:
                        dir_name = key.split("/", 1)[0] if not self.prefix else key[len(self.prefix) :].split("/", 1)[0]
                        dirs.add(dir_name)

                console.print(f"Found directories: {dirs}")

                # Check each directory for metadata
                for dir_name in dirs:
                    metadata = self._get_metadata(dir_name)
                    if metadata:
                        session_id = dir_name
                        timestamp = int(metadata.get("startTime", time.time() * 1000))

                        recordings.append(
                            {
                                "id": dir_name,
                                "sessionId": session_id,
                                "timestamp": timestamp,
                                "date": datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                                "events": metadata.get("eventCount", 0),
                                "duration": metadata.get("duration", 0),
                            }
                        )

                recordings.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
                console.print(f"[green]Found {len(recordings)} recordings with alternative method[/green]")

        except Exception as e:
            console.print(f"[red]Error listing recordings: {e}[/red]")
            import traceback

            traceback.print_exc()

        return recordings

    def _get_metadata(self, recording_id):
        """Get metadata for a recording"""
        try:
            # Try both possible metadata paths
            keys_to_try = [
                f"{self.prefix}/{recording_id}/metadata.json",
                f"{recording_id}/metadata.json",
                f"{self.prefix}{recording_id}/metadata.json",
            ]

            for key in keys_to_try:
                try:
                    console.print(f"[dim]Trying metadata path: {key}[/dim]")
                    response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
                    data = json.loads(response["Body"].read().decode("utf-8"))
                    console.print(f"[dim]Found metadata at: {key}[/dim]")
                    return data
                except Exception as e:
                    console.print(f"[dim]No metadata at: {key} ({str(e)})[/dim]")
                    continue

            return {}
        except Exception as e:
            console.print(f"[dim]Error getting metadata: {e}[/dim]")
            return {}

    def download_recording(self, recording_id):
        """Download recording from S3"""
        console.print(f"[cyan]Downloading recording: {recording_id}[/cyan]")

        recording_dir = self.temp_dir / recording_id
        recording_dir.mkdir(exist_ok=True)

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                # List files for this recording
                prefix = f"{self.prefix}/{recording_id}/" if self.prefix else f"{recording_id}/"
                console.print(f"Looking for files with prefix: {prefix}")

                paginator = self.s3_client.get_paginator("list_objects_v2")

                files_to_download = []
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    if "Contents" in page:
                        for obj in page["Contents"]:
                            files_to_download.append(obj["Key"])

                # Download files
                console.print(f"Downloading {len(files_to_download)} files")
                task = progress.add_task(
                    f"Downloading {len(files_to_download)} files...",
                    total=len(files_to_download),
                )

                all_events = []
                metadata = {}

                for key in files_to_download:
                    filename = key.split("/")[-1]
                    local_path = recording_dir / filename

                    self.s3_client.download_file(self.bucket, key, str(local_path))
                    progress.advance(task)

                    # Process file
                    if filename == "metadata.json":
                        with open(local_path, "r") as f:
                            metadata = json.load(f)
                    elif filename.startswith("batch-") and (
                        filename.endswith(".ndjson.gz") or filename.endswith(".jsonl.gz")
                    ):
                        try:
                            with gzip.open(local_path, "rt") as f:
                                for line in f:
                                    if line.strip():
                                        try:
                                            event_data = json.loads(line)
                                            # Validate event structure for rrweb
                                            if "type" in event_data and "timestamp" in event_data:
                                                all_events.append(event_data)
                                            else:
                                                console.print(
                                                    "[yellow]Skipping invalid event: missing required fields[/yellow]"
                                                )
                                        except json.JSONDecodeError:
                                            console.print(
                                                f"[yellow]Warning: Invalid JSON in line: {line[:50]}...[/yellow]"
                                            )
                        except Exception as e:
                            console.print(f"[yellow]Warning: Error processing batch file {filename}: {e}[/yellow]")

            console.print(f"[green]✓ Downloaded {len(all_events)} events[/green]")

            # If no events were parsed, check the files
            if len(all_events) == 0:
                console.print("[yellow]Warning: No events were parsed from the batch files[/yellow]")

                # Create sample events to prevent viewer from breaking
                console.print("[yellow]Creating sample events to allow viewer to function[/yellow]")
                timestamp = int(time.time() * 1000)

                # Create a minimal set of events for rrweb
                all_events = [
                    {
                        "type": 2,  # Meta event
                        "timestamp": timestamp,
                        "data": {
                            "href": "https://example.com",
                            "width": 1280,
                            "height": 720,
                        },
                    },
                    {
                        "type": 4,  # DOM snapshot
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
                                                "attributes": {},
                                                "childNodes": [
                                                    {
                                                        "type": 3,
                                                        "textContent": "No recording data found - placeholder content",
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

                # List downloaded files for debugging
                console.print("Downloaded files:")
                for path in recording_dir.iterdir():
                    console.print(f"  - {path.name} ({path.stat().st_size} bytes)")

            return {"metadata": metadata, "events": all_events}

        except Exception as e:
            console.print(f"[red]Error downloading recording: {e}[/red]")
            import traceback

            traceback.print_exc()
            return None


class SessionReplayViewer:
    """Main session replay viewer"""

    def __init__(self, data_source, port=8080):
        self.data_source = data_source
        self.port = port
        self.viewer_path = Path(__file__).parent.parent / "static" / "replay-viewer"
        self.server = None

    def find_available_port(self):
        """Find an available port"""
        for port in range(self.port, self.port + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        raise RuntimeError("No available ports found")

    def start(self):
        """Start the replay viewer server"""
        # Ensure viewer directory exists
        self.viewer_path.mkdir(parents=True, exist_ok=True)

        # Find available port
        port = self.find_available_port()

        # Create request handler
        def handler_factory(*args, **kwargs):
            return SessionReplayHandler(self.data_source, self.viewer_path, *args, **kwargs)

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
            console.print("\n[yellow]Shutting down...[/yellow]")
            self.server.shutdown()
            if hasattr(self.data_source, "cleanup"):
                self.data_source.cleanup()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Session Replay Viewer - View browser session recordings")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--local", help="Path to local recordings directory")
    group.add_argument("--s3", help="S3 path to recordings (e.g., s3://bucket/prefix/)")

    parser.add_argument("--port", type=int, default=8080, help="Port to run server on (default: 8080)")

    args = parser.parse_args()

    # Create data source
    if args.local:
        data_source = LocalDataSource(args.local)
    else:
        # Parse S3 path
        if not args.s3.startswith("s3://"):
            console.print("[red]S3 path must start with s3://[/red]")
            sys.exit(1)

        path_parts = args.s3[5:].split("/", 1)
        bucket = path_parts[0]
        prefix = path_parts[1] if len(path_parts) > 1 else ""

        data_source = S3DataSource(bucket, prefix)

    # Start viewer
    viewer = SessionReplayViewer(data_source, port=args.port)
    viewer.start()


if __name__ == "__main__":
    main()
