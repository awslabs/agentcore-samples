#!/usr/bin/env python3
"""
Bedrock-AgentCore Browser Live Viewer with proper display sizing support and DCV debugging.
"""

import os
import time
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from rich.console import Console

from bedrock_agentcore.tools.browser_client import BrowserClient

console = Console()


class BrowserViewerServer:
    """Server for viewing Bedrock-AgentCore Browser sessions with configurable display size."""

    def __init__(self, browser_client: BrowserClient, port: int = 8000):
        """Initialize the viewer server."""
        self.browser_client = browser_client
        self.port = port
        self.app = FastAPI(title="Bedrock-AgentCore Browser Viewer")
        self.server_thread = None
        self.is_running = False
        self.has_control = False  # Add control state tracking

        # Setup directory structure
        self.package_dir = Path(__file__).parent
        self.static_dir = self.package_dir / "static"
        self.js_dir = self.static_dir / "js"
        self.css_dir = self.static_dir / "css"
        self.dcv_dir = self.static_dir / "dcvjs"

        # Create all directories
        for directory in [self.static_dir, self.js_dir, self.css_dir, self.dcv_dir]:
            directory.mkdir(parents=True, exist_ok=True)

        # Create the JS and CSS files
        self._create_static_files()

        # Check for DCV SDK
        self._check_dcv_sdk()

        # Mount static files
        self.app.mount("/static", StaticFiles(directory=str(self.static_dir)), name="static")

        # Setup routes
        self._setup_routes()

    def _create_static_files(self):
        """Create the JavaScript and CSS files included with the SDK."""

        # Create bedrock-agentcore-browser-viewer.js with enhanced debugging
        js_content = """// Bedrock-AgentCore Browser Viewer Module with Enhanced Debugging
import dcv from "../dcvjs/dcv.js";
export class BedrockAgentCoreLiveViewer {
    constructor(presignedUrl, containerId = 'dcv-display') {
        this.displayLayoutRequested = false;
        this.presignedUrl = presignedUrl;
        this.containerId = containerId;
        this.connection = null;
        this.desiredWidth = 1600;
        this.desiredHeight = 900;
        console.log('[BedrockAgentCoreLiveViewer] Initialized with URL:', presignedUrl);
    }

    httpExtraSearchParamsCallBack(method, url, body, returnType) {
        console.log('[BedrockAgentCoreLiveViewer] httpExtraSearchParamsCallBack called:', { method, url, returnType });
        const parsedUrl = new URL(this.presignedUrl);
        const params = parsedUrl.searchParams;
        console.log('[BedrockAgentCoreLiveViewer] Returning auth params:', params.toString());
        return params;
    }
    
    displayLayoutCallback(serverWidth, serverHeight, heads) {
        console.log(`[BedrockAgentCoreLiveViewer] Display layout callback: ${serverWidth}x${serverHeight}`);
        
        const display = document.getElementById(this.containerId);
        display.style.width = `${this.desiredWidth}px`;
        display.style.height = `${this.desiredHeight}px`;

        if (this.connection) {
            console.log(`[BedrockAgentCoreLiveViewer] Requesting display layout: ${this.desiredWidth}x${this.desiredHeight}`);
            // Only request display layout once
            if (!this.displayLayoutRequested) {
                console.log('inside this method');
                this.connection.requestDisplayLayout([{
                name: "Main Display",
                rect: {
                    x: 0,
                    y: 0,
                    width: this.desiredWidth,
                    height: this.desiredHeight
                },
                primary: true
                }]);

                this.displayLayoutRequested = true;
            }
        }
    }

    async connect() {
        return new Promise((resolve, reject) => {
            if (typeof dcv === 'undefined') {
                reject(new Error('DCV SDK not loaded'));
                return;
            }

            console.log('[BedrockAgentCoreLiveViewer] DCV SDK loaded, version:', dcv.version || 'Unknown');
            console.log('[BedrockAgentCoreLiveViewer] Available DCV methods:', Object.keys(dcv));
            console.log('[BedrockAgentCoreLiveViewer] Presigned URL:', this.presignedUrl);
            
            // Set debug logging
            if (dcv.setLogLevel) {
                dcv.setLogLevel(dcv.LogLevel.DEBUG);
                console.log('[BedrockAgentCoreLiveViewer] DCV log level set to DEBUG');
            }

            console.log('[BedrockAgentCoreLiveViewer] Starting authentication...');
            
            dcv.authenticate(this.presignedUrl, {
                promptCredentials: () => {
                    console.warn('[BedrockAgentCoreLiveViewer] DCV requested credentials - should not happen with presigned URL');
                },
                error: (auth, error) => {
                    console.error('[BedrockAgentCoreLiveViewer] DCV auth error:', error);
                    console.error('[BedrockAgentCoreLiveViewer] Error details:', {
                        message: error.message || error,
                        code: error.code,
                        statusCode: error.statusCode,
                        stack: error.stack
                    });
                    reject(error);
                },
                success: (auth, result) => {
                    console.log('[BedrockAgentCoreLiveViewer] DCV auth success:', result);
                    if (result && result[0]) {
                        const { sessionId, authToken } = result[0];
                        console.log('[BedrockAgentCoreLiveViewer] Session ID:', sessionId);
                        console.log('[BedrockAgentCoreLiveViewer] Auth token received:', authToken ? 'Yes' : 'No');
                        this.connectToSession(sessionId, authToken, resolve, reject);
                    } else {
                        console.error('[BedrockAgentCoreLiveViewer] No session data in auth result');
                        reject(new Error('No session data in auth result'));
                    }
                },
                httpExtraSearchParams: this.httpExtraSearchParamsCallBack.bind(this)
            });
        });
    }

    connectToSession(sessionId, authToken, resolve, reject) {
        console.log('[BedrockAgentCoreLiveViewer] Connecting to session:', sessionId);
        
        const connectOptions = {
            url: this.presignedUrl,
            sessionId: sessionId,
            authToken: authToken,
            divId: this.containerId,
            baseUrl: "/static/dcvjs",
            callbacks: {
                firstFrame: () => {
                    console.log('[BedrockAgentCoreLiveViewer] First frame received!');
                    resolve(this.connection);
                },
                error: (error) => {
                    console.error('[BedrockAgentCoreLiveViewer] Connection error:', error);
                    reject(error);
                },
                httpExtraSearchParams: this.httpExtraSearchParamsCallBack.bind(this),
                displayLayout: this.displayLayoutCallback.bind(this)
            }
        };
        
        console.log('[BedrockAgentCoreLiveViewer] Connect options:', connectOptions);
        
        dcv.connect(connectOptions)
        .then(connection => {
            console.log('[BedrockAgentCoreLiveViewer] Connection established:', connection);
            this.connection = connection;
        })
        .catch(error => {
            console.error('[BedrockAgentCoreLiveViewer] Connect failed:', error);
            reject(error);
        });
    }

    setDisplaySize(width, height) {
        this.desiredWidth = width;
        this.desiredHeight = height;
        
        if (this.connection) {
            this.displayLayoutCallback(0, 0, []);
        }
    }

    disconnect() {
        if (this.connection) {
            this.connection.disconnect();
            this.connection = null;
        }
    }
}"""

        js_file = self.js_dir / "bedrock-agentcore-browser-viewer.js"
        with open(js_file, "w") as f:
            f.write(js_content)

        # Create viewer.css with added control button styles
        css_content = """/* Bedrock-AgentCore Browser Viewer Styles */
body { 
    margin: 0; 
    padding: 0; 
    height: 100vh; 
    overflow: hidden; 
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
    background: #f5f5f5;
}

.container {
    display: flex;
    flex-direction: column;
    height: 100vh;
}

.header { 
    background: #232f3e; 
    color: white; 
    padding: 10px 20px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.header h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 400;
}

.viewer-wrapper {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
    overflow: auto;
    background: #e9ecef;
}

#dcv-display { 
    background: black;
    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    border-radius: 4px;
    position: relative;
}

.controls {
    background: white;
    padding: 12px 20px;
    border-top: 1px solid #dee2e6;
    display: flex;
    gap: 10px;
    align-items: center;
}

.size-selector {
    display: flex;
    gap: 8px;
    align-items: center;
}

.size-selector span {
    font-size: 14px;
    color: #495057;
    margin-right: 8px;
}

button {
    padding: 6px 16px;
    border: 1px solid #dee2e6;
    background: white;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: all 0.2s;
}

button:hover {
    background: #f8f9fa;
    border-color: #adb5bd;
}

button.active {
    background: #0073bb;
    color: white;
    border-color: #0073bb;
}

/* Added control button styles */
.control-group {
    display: flex;
    gap: 8px;
    align-items: center;
    padding-right: 20px;
    border-right: 1px solid #dee2e6;
}

.btn-take-control {
    background: #28a745;
    color: white;
    border-color: #28a745;
}

.btn-take-control:hover {
    background: #218838;
}

.btn-release-control {
    background: #dc3545;
    color: white;
    border-color: #dc3545;
}

.btn-release-control:hover {
    background: #c82333;
}

.control-indicator {
    font-size: 13px;
    color: #6c757d;
}

#status {
    margin-left: auto;
    font-size: 14px;
    color: #6c757d;
}

.error-display {
    padding: 40px;
    text-align: center;
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.error-display h3 {
    color: #dc3545;
    margin-bottom: 16px;
}

.error-display p {
    color: #6c757d;
    line-height: 1.5;
}

#debug-info {
    position: fixed;
    bottom: 10px;
    right: 10px;
    background: rgba(0,0,0,0.8);
    color: white;
    padding: 10px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 12px;
    max-width: 400px;
    max-height: 200px;
    overflow: auto;
}"""

        css_file = self.css_dir / "viewer.css"
        with open(css_file, "w") as f:
            f.write(css_content)

    def _check_dcv_sdk(self):
        """Check if DCV SDK is present."""
        dcv_dir = self.static_dir / "dcvjs"
        dcv_dir.mkdir(parents=True, exist_ok=True)

        dcv_js_path = dcv_dir / "dcv.js"

        if not dcv_js_path.exists():
            console.print("\n[bold yellow]⚠️  DCV SDK Not Found[/bold yellow]")
            console.print("The Amazon DCV Web Client SDK is required but not found.")
            console.print(f"[dim]Expected location: {dcv_dir}[/dim]\n")
            console.print("[bold]To obtain the DCV SDK:[/bold]")
            console.print(
                "1. Download from: https://d1uj6qtbmh3dt5.cloudfront.net/webclientsdk/nice-dcv-web-client-sdk-1.9.100-952.zip"
            )
            console.print("2. Extract and copy dcvjs-umd/* files to the directory above")
            console.print("3. Ensure the following structure:")
            console.print("   dcvjs/")
            console.print("   ├── dcv.js")
            console.print("   ├── dcv/")
            console.print("   │   ├── broadwayh264decoder-worker.js")
            console.print("   │   ├── jsmpegdecoder-worker.js")
            console.print("   │   ├── lz4decoder-worker.js")
            console.print("   │   └── microphoneprocessor.js")
            console.print("   └── lib/")
            console.print("       ├── broadway/")
            console.print("       ├── jsmpeg/")
            console.print("       └── lz4/")
            console.print("\n[red]The viewer will not work until DCV SDK is installed![/red]\n")
        else:
            # Check if it's a real DCV file or placeholder
            file_size = dcv_js_path.stat().st_size
            if file_size < 10000:  # Real DCV SDK is much larger
                console.print("\n[bold yellow]⚠️  DCV SDK file appears to be a placeholder[/bold yellow]")
                console.print(f"File size: {file_size} bytes (expected > 100KB)")
                console.print("Please replace with the real DCV SDK files\n")
            else:
                console.print(f"[green]✅ DCV SDK found ({file_size:,} bytes)[/green]")

    def _setup_routes(self):
        """Setup FastAPI routes."""

        @self.app.get("/", response_class=HTMLResponse)
        async def root():
            """Serve the main viewer page."""
            if not self.browser_client.session_id:
                raise HTTPException(status_code=400, detail="No active browser session")

            try:
                presigned_url = self.browser_client.generate_live_view_url(expires=300)

                # Debug logging
                console.print("\n[cyan]Generated presigned URL:[/cyan]")
                console.print(f"[dim]{presigned_url}[/dim]\n")

                html = self._generate_html(presigned_url)
                return HTMLResponse(content=html)
            except Exception as e:
                console.print(f"[red]Error generating viewer: {str(e)}[/red]")
                raise HTTPException(status_code=500, detail=str(e))

        # ADD TAKE CONTROL ROUTE
        @self.app.post("/api/take-control")
        async def take_control():
            """Take control of the browser session."""
            try:
                self.browser_client.take_control()
                self.has_control = True
                console.print("[green]✅ Took control of browser session[/green]")
                return JSONResponse(
                    {
                        "status": "success",
                        "message": "Control taken",
                        "has_control": True,
                    }
                )
            except Exception as e:
                console.print(f"[red]❌ Failed to take control: {e}[/red]")
                return JSONResponse(
                    {
                        "status": "error",
                        "message": "An error occurred while taking control. See server logs for details.",
                        "has_control": self.has_control,
                    },
                    status_code=500,
                )

        # ADD RELEASE CONTROL ROUTE
        @self.app.post("/api/release-control")
        async def release_control():
            """Release control of the browser session."""
            try:
                self.browser_client.release_control()
                self.has_control = False
                console.print("[yellow]✅ Released control of browser session[/yellow]")
                return JSONResponse(
                    {
                        "status": "success",
                        "message": "Control released",
                        "has_control": False,
                    }
                )
            except Exception as e:
                console.print(f"[red]❌ Failed to release control: {e}[/red]")
                return JSONResponse(
                    {
                        "status": "error",
                        "message": "An error occurred while releasing control. See server logs for details.",
                        "has_control": self.has_control,
                    },
                    status_code=500,
                )

        @self.app.get("/api/session-info")
        async def session_info():
            """Get session information."""
            return {
                "session_id": self.browser_client.session_id,
                "identifier": self.browser_client.identifier,
                "region": self.browser_client.region,
                "stage": os.environ.get("BEDROCK_AGENTCORE_STAGE", "gamma"),
                "display_sizes": [
                    {"width": 1280, "height": 720, "label": "HD"},
                    {"width": 1600, "height": 900, "label": "HD+"},
                    {"width": 1920, "height": 1080, "label": "Full HD"},
                    {"width": 2560, "height": 1440, "label": "2K"},
                ],
            }

        @self.app.get("/api/debug-info")
        async def debug_info():
            """Get debug information."""
            return {
                "dcv_files": self._check_dcv_files(),
                "session": {
                    "id": self.browser_client.session_id,
                    "identifier": self.browser_client.identifier,
                    "region": self.browser_client.region,
                    "stage": os.environ.get("BEDROCK_AGENTCORE_STAGE", "gamma"),
                },
                "server": {
                    "static_dir": str(self.static_dir),
                    "dcv_dir": str(self.dcv_dir),
                },
            }

    def _check_dcv_files(self):
        """Check which DCV files are present."""
        dcv_files = {}
        dcv_dir = self.static_dir / "dcvjs"

        required_files = [
            "dcv.js",
            "dcv/broadwayh264decoder-worker.js",
            "dcv/jsmpegdecoder-worker.js",
            "dcv/lz4decoder-worker.js",
            "dcv/microphoneprocessor.js",
        ]

        for file_path in required_files:
            full_path = dcv_dir / file_path
            if full_path.exists():
                dcv_files[file_path] = {
                    "exists": True,
                    "size": full_path.stat().st_size,
                }
            else:
                dcv_files[file_path] = {"exists": False}

        return dcv_files

    def _generate_html(self, presigned_url: str) -> str:
        """Generate the viewer HTML with enhanced debugging."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bedrock-AgentCore Browser Viewer</title>
    <link rel="stylesheet" href="/static/css/viewer.css">
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>Bedrock-AgentCore Browser Viewer - Session: {self.browser_client.session_id}</h2>
        </div>
        
        <div class="viewer-wrapper">
            <div id="dcv-display"></div>
        </div>
        
        <div class="controls">
            <div class="control-group">
                <span>Control:</span>
                <button id="take-control" class="btn-take-control" onclick="takeControl()">
                    🎮 Take Control
                </button>
                <button id="release-control" class="btn-release-control" onclick="releaseControl()" style="display: none;">
                    🚫 Release Control
                </button>
                <span id="control-indicator" class="control-indicator">Automation Active</span>
            </div>
            
            <div class="size-selector">
                <span>Display Size:</span>
                <button onclick="setSize(1280, 720)" id="size-720">1280×720</button>
                <button onclick="setSize(1600, 900)" id="size-900" class="active">1600×900</button>
                <button onclick="setSize(1920, 1080)" id="size-1080">1920×1080</button>
                <button onclick="setSize(2560, 1440)" id="size-1440">2560×1440</button>
            </div>
            <span id="status">Initializing...</span>
        </div>
    </div>
    
    <!-- Debug info panel -->
    <div id="debug-info"></div>
    
    <!-- Configure DCV worker path -->
    <script>
        // Tell DCV where to find its worker files
        window.dcvWorkerPath = '/static/dcvjs/dcv/';
        console.log('[Main] DCV worker path set to:', window.dcvWorkerPath);
    </script>
    
    <!-- Load DCV SDK -->
    <script src="/static/dcvjs/dcv.js"></script>
    
    <!-- Main viewer logic -->
    <script type="module">
        import {{ BedrockAgentCoreLiveViewer }} from '/static/js/bedrock-agentcore-browser-viewer.js';
        
        // Debug logging
        const debugInfo = document.getElementById('debug-info');
        function log(message) {{
            console.log(message);
            debugInfo.innerHTML += message + '<br>';
            debugInfo.scrollTop = debugInfo.scrollHeight;
        }}
        
        // Global viewer instance
        let viewer = null;
        
        // Display the presigned URL for debugging
        log('[Main] Presigned URL: ' + '{presigned_url}'.substring(0, 100) + '...');
        
        // Check DCV SDK
        if (typeof dcv !== 'undefined') {{
            log('[Main] DCV SDK loaded successfully');
            log('[Main] DCV methods: ' + Object.keys(dcv).join(', '));
            
            // Configure DCV
            if (dcv.setWorkerPath) {{
                dcv.setWorkerPath('/static/dcvjs/dcv/');
                log('[Main] Set DCV worker path');
            }}
        }} else {{
            log('[Main] ERROR: DCV SDK not found!');
        }}
        
        // ADD CONTROL FUNCTIONS
        window.takeControl = async function() {{
            try {{
                updateStatus('Taking control...');
                const response = await fetch('/api/take-control', {{ method: 'POST' }});
                const data = await response.json();
                
                if (data.status === 'success') {{
                    document.getElementById('take-control').style.display = 'none';
                    document.getElementById('release-control').style.display = 'inline-block';
                    document.getElementById('control-indicator').textContent = '🎮 You Have Control';
                    updateStatus('You have control');
                    log('[Main] Control taken successfully');
                }} else {{
                    updateStatus('Failed to take control: ' + data.message);
                    log('[Main] ERROR: ' + data.message);
                }}
            }} catch (error) {{
                updateStatus('Error: ' + error.message);
                log('[Main] ERROR taking control: ' + error.message);
            }}
        }};
        
        window.releaseControl = async function() {{
            try {{
                updateStatus('Releasing control...');
                const response = await fetch('/api/release-control', {{ method: 'POST' }});
                const data = await response.json();
                
                if (data.status === 'success') {{
                    document.getElementById('take-control').style.display = 'inline-block';
                    document.getElementById('release-control').style.display = 'none';
                    document.getElementById('control-indicator').textContent = 'Automation Active';
                    updateStatus('Control released');
                    log('[Main] Control released successfully');
                }} else {{
                    updateStatus('Failed to release control: ' + data.message);
                    log('[Main] ERROR: ' + data.message);
                }}
            }} catch (error) {{
                updateStatus('Error: ' + error.message);
                log('[Main] ERROR releasing control: ' + error.message);
            }}
        }};
        
        // Size control function
        window.setSize = function(width, height) {{
            if (viewer) {{
                viewer.setDisplaySize(width, height);
                
                document.querySelectorAll('.size-selector button').forEach(btn => {{
                    btn.classList.remove('active');
                }});
                event.target.classList.add('active');
                
                updateStatus(`Display size: ${{width}}×${{height}}`);
            }}
        }};
        
        function updateStatus(message) {{
            document.getElementById('status').textContent = message;
            log('[Main] Status: ' + message);
        }}
        
        async function initialize() {{
            try {{
                updateStatus('Initializing DCV viewer...');
                
                // Fetch debug info
                try {{
                    const debugResponse = await fetch('/api/debug-info');
                    const debugData = await debugResponse.json();
                    log('[Main] Debug info: ' + JSON.stringify(debugData, null, 2));
                }} catch (e) {{
                    log('[Main] Could not fetch debug info: ' + e.message);
                }}
                
                viewer = new BedrockAgentCoreLiveViewer('{presigned_url}', 'dcv-display');
                viewer.setDisplaySize(1600, 900);
                
                updateStatus('Connecting to browser session...');
                await viewer.connect();
                
                updateStatus('Connected - Display: 1600×900');
                
            }} catch (error) {{
                console.error('Failed to initialize viewer:', error);
                updateStatus('Error: ' + error.message);
                log('[Main] ERROR: ' + error.message);
                log('[Main] Stack: ' + (error.stack || 'No stack trace'));
                
                if (error.message && error.message.includes('DCV SDK not loaded')) {{
                    document.getElementById('dcv-display').innerHTML = `
                        <div class="error-display">
                            <h3>DCV SDK Not Found</h3>
                            <p>The Amazon DCV Web Client SDK is required but not found.</p>
                            <p>Please ensure all DCV files are properly installed.</p>
                            <p>Check the console and debug panel for more information.</p>
                        </div>
                    `;
                }}
            }}
        }}
        
        // Start initialization when page loads
        document.addEventListener('DOMContentLoaded', () => {{
            log('[Main] DOM loaded, starting initialization...');
            initialize();
        }});
    </script>
</body>
</html>"""

    def start(self, open_browser: bool = True) -> str:
        """Start the viewer server."""

        def run_server():
            uvicorn.run(self.app, host="0.0.0.0", port=self.port, log_level="error")  # nosec B104

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        self.is_running = True

        time.sleep(1)

        viewer_url = f"http://localhost:{self.port}"
        console.print(f"\n[green]✅ Viewer server running at: {viewer_url}[/green]")
        console.print("[dim]Check browser console (F12) for detailed debug information[/dim]\n")

        if open_browser:
            console.print("[cyan]Opening browser...[/cyan]")
            webbrowser.open(viewer_url)

        return viewer_url
