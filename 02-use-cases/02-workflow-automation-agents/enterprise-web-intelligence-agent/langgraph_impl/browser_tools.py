"""Browser automation tools using BedrockAgentCore SDK with Playwright and CDP enhancements."""

import asyncio
import uuid
import json
from typing import Dict, List, Optional
from datetime import datetime

import boto3
from playwright.async_api import (
    async_playwright,
)
from langchain_core.messages import HumanMessage
from rich.console import Console

# Import from BedrockAgentCore SDK
from bedrock_agentcore.tools.browser_client import BrowserClient
from bedrock_agentcore._utils.endpoints import get_control_plane_endpoint

console = Console()


class BrowserTools:
    """Enhanced browser automation tools with CDP capabilities."""

    def __init__(self, config):
        self.config = config
        self.browser_client = None
        self.browser_id = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.cdp_session = None
        self.recording_path = None
        self.llm = None
        self._screenshots_taken = []
        self._discovered_apis = []
        self._performance_metrics = {}

    def create_browser_with_recording(self) -> str:
        """Create a browser with recording configuration using Control Plane API."""
        console.print("[cyan]🔧 Creating browser with recording configuration...[/cyan]")

        # Create control plane client
        control_plane_url = get_control_plane_endpoint(self.config.region)
        control_client = boto3.client(
            "bedrock-agentcore-control",
            region_name=self.config.region,
            endpoint_url=control_plane_url,
        )

        # Create browser with recording
        browser_name = f"competitive_intel_{uuid.uuid4().hex[:8]}"

        console.print(f"  Browser name: {browser_name}")
        console.print(f"  S3 location: s3://{self.config.s3_bucket}/{self.config.s3_prefix}")
        console.print(f"  Role ARN: {self.config.recording_role_arn}")

        response = control_client.create_browser(
            name=browser_name,
            executionRoleArn=self.config.recording_role_arn,
            networkConfiguration={"networkMode": "PUBLIC"},
            recording={
                "enabled": True,
                "s3Location": {
                    "bucket": self.config.s3_bucket,
                    "prefix": self.config.s3_prefix,
                },
            },
        )

        self.browser_id = response["browserId"]

        # NEW: Store the structured recording configuration
        self.recording_config = response.get("recording", {})

        # Build recording path for display (but keep structured config)
        s3_location = self.recording_config.get("s3Location", {})
        self.recording_path = f"s3://{s3_location.get('bucket')}/{s3_location.get('prefix')}"

        console.print(f"✅ Browser created: {self.browser_id}")
        console.print(f"📹 Recording to: {self.recording_path}")

        return self.browser_id

    async def initialize_browser_session(self, llm):
        """Initialize browser session with enhanced CDP capabilities."""
        self.llm = llm

        # Create BrowserClient from SDK
        self.browser_client = BrowserClient(region=self.config.region)
        self.browser_client.identifier = self.browser_id

        # Start a session
        session_id = self.browser_client.start(
            identifier=self.browser_id,
            name=f"competitive_intel_session_{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            session_timeout_seconds=self.config.browser_session_timeout,
        )

        console.print(f"✅ Session started: {session_id}")

        # Get WebSocket headers
        ws_url, headers = self.browser_client.generate_ws_headers()
        console.print(f"[dim]WebSocket URL: {ws_url}[/dim]")

        # Wait for browser initialization
        console.print("[yellow]⏳ Waiting for browser to initialize...[/yellow]")
        await asyncio.sleep(10)

        # Initialize Playwright with CDP
        console.print("[cyan]🎭 Connecting Playwright with CDP support...[/cyan]")
        self.playwright = await async_playwright().start()

        # Connect to the browser via CDP
        self.browser = await self.playwright.chromium.connect_over_cdp(ws_url, headers=headers)

        # Get context and page
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0]

        # Create CDP session for advanced features
        try:
            self.cdp_session = await self.context.new_cdp_session(self.page)
            await self._setup_cdp_domains()
            console.print("✅ CDP session initialized")
        except Exception as e:
            console.print(f"[yellow]⚠️ CDP setup partial: {e}[/yellow]")
            self.cdp_session = None

        # Set up network interception
        await self._setup_network_interception()

        console.print("✅ Playwright connected with enhancements")

        # Set recording path
        self.recording_path = f"s3://{self.config.s3_bucket}/{self.config.s3_prefix}{session_id}/"
        console.print(f"📹 Recording to: {self.recording_path}")

        return self.page

    async def _setup_cdp_domains(self):
        """Enable CDP domains for advanced features."""
        if not self.cdp_session:
            return

        try:
            # Enable required CDP domains
            await self.cdp_session.send("Network.enable")
            await self.cdp_session.send("Performance.enable")
            console.print("[dim]✅ CDP domains enabled[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠️ Some CDP domains failed: {e}[/yellow]")

    async def _setup_network_interception(self):
        """Set up network request interception for API discovery."""

        def handle_response(response):
            """Handle network responses to discover APIs."""
            try:
                url = response.url

                # Skip ad networks and analytics
                skip_domains = [
                    "doubleclick.net",
                    "googletagmanager.com",
                    "google-analytics.com",
                    "facebook.com",
                    "twitter.com",
                    "linkedin.com",
                    "pinterest.com",
                    "amazon-adsystem.com",
                    "googleadservices.com",
                ]
                if any(domain in url.lower() for domain in skip_domains):
                    return

                # Track relevant APIs
                if any(keyword in url.lower() for keyword in ["api", "price", "pricing", "tier", "plan"]):
                    self._discovered_apis.append(
                        {
                            "url": url[:100],  # Truncate long URLs
                            "status": response.status,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    if len(self._discovered_apis) <= 5:  # Limit console output
                        console.print(f"[dim]🔍 Found API: {url[:60]}...[/dim]")
            except Exception:
                pass

        # Set up response handler
        self.page.on("response", handle_response)

    async def navigate_to_url(self, url: str) -> Dict:
        """Navigate to URL with enhanced visual feedback."""
        try:
            console.print(f"[cyan]🌐 Navigating to: {url}[/cyan]")

            # Navigate with proper timeout
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait for dynamic content
            await self.page.wait_for_timeout(3000)

            # Get page metrics if CDP is available
            if self.cdp_session:
                try:
                    metrics = await self.cdp_session.send("Performance.getMetrics")
                    self._performance_metrics = {m["name"]: m["value"] for m in metrics.get("metrics", [])}
                except Exception:
                    pass

            title = await self.page.title()

            return {
                "status": "success",
                "url": url,
                "title": title,
                "metrics": self._performance_metrics,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            console.print(f"[red]❌ Navigation error: {e}[/red]")
            return {"status": "error", "url": url, "error": str(e)}

    async def analyze_forms_and_inputs(self) -> Dict:
        """NEW: Analyze forms and input fields on the page."""
        console.print("[cyan]📝 Analyzing forms and inputs...[/cyan]")

        try:
            # Find all forms on the page
            forms_data = await self.page.evaluate("""
                () => {
                    const forms = Array.from(document.querySelectorAll('form'));
                    return {
                        forms: forms.map(form => ({
                            action: form.action,
                            method: form.method,
                            id: form.id,
                            className: form.className,
                            inputs: Array.from(form.querySelectorAll('input, select, textarea')).map(input => ({
                                type: input.type || input.tagName.toLowerCase(),
                                name: input.name,
                                id: input.id,
                                placeholder: input.placeholder,
                                required: input.required,
                                value: input.type === 'password' ? '[hidden]' : input.value
                            }))
                        })),
                        total_inputs: document.querySelectorAll('input, select, textarea').length,
                        has_file_upload: document.querySelectorAll('input[type="file"]').length > 0,
                        has_password_field: document.querySelectorAll('input[type="password"]').length > 0
                    };
                }
            """)

            console.print(
                f"[green]Found {len(forms_data['forms'])} forms with {forms_data['total_inputs']} inputs[/green]"
            )

            if forms_data["has_file_upload"]:
                console.print("[yellow]📎 Page has file upload capability[/yellow]")

            if forms_data["has_password_field"]:
                console.print("[yellow]🔐 Page has authentication form[/yellow]")

            return {"status": "success", **forms_data}

        except Exception as e:
            console.print(f"[yellow]⚠️ Form analysis error: {e}[/yellow]")
            return {"status": "error", "error": str(e)}

    async def handle_authentication(self, username: str, password: str, form_selector: Optional[str] = None) -> Dict:
        """NEW: Handle authentication on login pages."""
        console.print("[cyan]🔐 Handling authentication...[/cyan]")

        try:
            # Find login form
            if not form_selector:
                # Common selectors for login forms
                possible_selectors = [
                    'form[action*="login"]',
                    'form[action*="signin"]',
                    "form#loginForm",
                    "form.login-form",
                    "form",
                ]

                for selector in possible_selectors:
                    form = await self.page.query_selector(selector)
                    if form:
                        form_selector = selector
                        break

            if not form_selector:
                return {"status": "error", "error": "No login form found"}

            # Fill in credentials
            await self.page.fill('input[type="email"], input[type="text"], input[name*="user"]', username)
            await self.page.fill('input[type="password"]', password)

            # Submit form
            await self.page.click('button[type="submit"], input[type="submit"]')

            # Wait for navigation or response
            await self.page.wait_for_timeout(3000)

            # Check if login was successful (simple heuristic)
            current_url = self.page.url

            return {
                "status": "success",
                "logged_in": "login" not in current_url.lower(),
                "current_url": current_url,
            }

        except Exception as e:
            console.print(f"[red]❌ Authentication error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    async def upload_file_to_form(self, file_path: str, selector: str = 'input[type="file"]') -> Dict:
        """NEW: Upload a file to a form."""
        console.print(f"[cyan]📤 Uploading file: {file_path}[/cyan]")

        try:
            # Find file input
            file_input = await self.page.query_selector(selector)
            if not file_input:
                return {"status": "error", "error": "No file input found"}

            # Set the file
            await file_input.set_input_files(file_path)

            # Wait for any upload progress
            await self.page.wait_for_timeout(2000)

            return {"status": "success", "file_uploaded": file_path}

        except Exception as e:
            console.print(f"[red]❌ File upload error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    async def explore_multi_page_workflow(self, target_pages: List[str]) -> List[Dict]:
        """NEW: Explore multiple pages in a workflow."""
        console.print(f"[cyan]🔄 Exploring {len(target_pages)} additional pages...[/cyan]")

        explored_pages = []
        base_url = self.page.url

        for target in target_pages:
            try:
                # Try to find and navigate to the page
                console.print(f"[dim]Looking for: {target}[/dim]")

                # Look for links containing the target keyword
                link_found = False
                selectors = [
                    f'a[href*="{target}"]',
                    f'a:has-text("{target}")',
                    f'nav a:has-text("{target}")',
                    f'[class*="menu"] a:has-text("{target}")',
                ]

                for selector in selectors:
                    try:
                        link = await self.page.query_selector(selector)
                        if link:
                            await link.click()
                            await self.page.wait_for_load_state("domcontentloaded")
                            await self.page.wait_for_timeout(2000)

                            # Capture information about this page
                            page_info = {
                                "target": target,
                                "url": self.page.url,
                                "title": await self.page.title(),
                                "found": True,
                                "timestamp": datetime.now().isoformat(),
                            }

                            # Take a screenshot
                            await self.take_annotated_screenshot(f"Explored - {target}")

                            explored_pages.append(page_info)
                            console.print(f"[green]✅ Found and explored: {target}[/green]")
                            link_found = True

                            # Go back to base URL for next exploration
                            await self.page.goto(base_url, wait_until="domcontentloaded")
                            break
                    except Exception:
                        continue

                if not link_found:
                    explored_pages.append(
                        {
                            "target": target,
                            "found": False,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    console.print(f"[yellow]⚠️ Could not find: {target}[/yellow]")

            except Exception as e:
                console.print(f"[yellow]⚠️ Error exploring {target}: {e}[/yellow]")
                explored_pages.append({"target": target, "found": False, "error": str(e)})

        return explored_pages

    async def execute_javascript_analysis(self, custom_script: Optional[str] = None) -> Dict:
        """NEW: Execute custom JavaScript for advanced analysis."""
        console.print("[cyan]⚡ Executing JavaScript analysis...[/cyan]")

        try:
            if custom_script:
                result = await self.page.evaluate(custom_script)
            else:
                # Default analysis script
                result = await self.page.evaluate("""
                    () => {
                        // Analyze page structure
                        const analysis = {
                            // Count different element types
                            tables: document.querySelectorAll('table').length,
                            forms: document.querySelectorAll('form').length,
                            images: document.querySelectorAll('img').length,
                            videos: document.querySelectorAll('video').length,
                            iframes: document.querySelectorAll('iframe').length,
                            
                            // Check for specific technologies
                            hasReact: window.React !== undefined,
                            hasJQuery: window.jQuery !== undefined,
                            hasAngular: window.angular !== undefined,
                            
                            // Page metrics
                            documentHeight: document.documentElement.scrollHeight,
                            viewportHeight: window.innerHeight,
                            
                            // Interactive elements
                            buttons: document.querySelectorAll('button').length,
                            links: document.querySelectorAll('a').length,
                            
                            // Meta information
                            metaDescription: document.querySelector('meta[name="description"]')?.content,
                            metaKeywords: document.querySelector('meta[name="keywords"]')?.content
                        };
                        
                        return analysis;
                    }
                """)

            console.print("[green]JavaScript analysis complete[/green]")
            return {"status": "success", "analysis": result}

        except Exception as e:
            console.print(f"[red]❌ JavaScript execution error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    async def intelligent_scroll_and_discover(self) -> List[Dict]:
        """Perform intelligent scrolling to discover content sections."""
        console.print("[cyan]🔍 Discovering page content...[/cyan]")
        discovered_sections = []

        try:
            # Get page height
            page_height = await self.page.evaluate("document.body.scrollHeight")
            await self.page.evaluate("window.innerHeight")

            # Calculate scroll positions (0%, 25%, 50%, 75%, 100%)
            scroll_positions = [0, 0.25, 0.5, 0.75, 1.0]

            for position in scroll_positions:
                current_position = int(page_height * position)

                # Smooth scroll
                await self.page.evaluate(f"window.scrollTo({{top: {current_position}, behavior: 'smooth'}})")
                await asyncio.sleep(1)  # Pause to load content

                # Look for important sections at this position
                important_selectors = [
                    ('[class*="pric"]', "Pricing"),
                    ('[class*="tier"]', "Tiers"),
                    ('[class*="plan"]', "Plans"),
                    ('[class*="feature"]', "Features"),
                    ("table", "Table"),
                    ("form", "Form"),
                    ('[class*="testimonial"]', "Testimonials"),
                    ('[class*="faq"]', "FAQ"),
                ]

                for selector, label in important_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        if elements:
                            discovered_sections.append(
                                {
                                    "selector": selector,
                                    "label": label,
                                    "count": len(elements),
                                    "position": position,
                                }
                            )
                            console.print(f"[dim]Found: {label} ({len(elements)} elements)[/dim]")
                    except Exception:
                        pass

            # Scroll back to top
            await self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            await asyncio.sleep(1)

        except Exception as e:
            console.print(f"[yellow]⚠️ Discovery error: {e}[/yellow]")

        return discovered_sections

    async def smart_navigation(self, target: str) -> bool:
        """Try to navigate to specific page sections (pricing, features, etc)."""
        console.print(f"[cyan]🎯 Looking for {target} page...[/cyan]")

        nav_patterns = {
            "pricing": ["pricing", "price", "plans", "cost", "subscription"],
            "features": ["features", "capabilities", "benefits", "solutions"],
            "docs": ["docs", "documentation", "api", "developers"],
            "about": ["about", "company", "team", "story"],
        }

        keywords = nav_patterns.get(target.lower(), [target.lower()])

        for keyword in keywords:
            try:
                # Try to find and click a link
                selectors = [
                    f'a[href*="{keyword}"]',
                    f'a:has-text("{keyword}")',
                    f'nav a:has-text("{keyword}")',
                ]

                for selector in selectors:
                    try:
                        element = await self.page.query_selector(selector)
                        if element:
                            await element.click()
                            await self.page.wait_for_load_state("domcontentloaded")
                            console.print(f"[green]✅ Found and clicked {target} link[/green]")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        console.print(f"[yellow]⚠️ Could not find {target} link[/yellow]")
        return False

    async def extract_pricing_info(self) -> Dict:
        """Extract pricing information with visual feedback."""
        try:
            console.print("[cyan]💰 Extracting pricing information...[/cyan]")

            # First do intelligent scroll to find pricing sections
            discovered = await self.intelligent_scroll_and_discover()

            # Find pricing elements
            pricing_selectors = [
                '[class*="price"], [class*="Price"]',
                '[class*="pricing"], [class*="Pricing"]',
                '[class*="tier"], [class*="Tier"]',
                '[class*="plan"], [class*="Plan"]',
            ]

            found_elements = []
            for selector in pricing_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    for element in elements[:5]:
                        text = await element.text_content()
                        if text and len(text.strip()) > 0:
                            found_elements.append(text.strip())
                except Exception:
                    pass

            # Get text content - LIMIT TO PREVENT TOKEN OVERFLOW
            text_content = await self.page.evaluate("() => document.body.innerText")

            # Truncate to avoid token limits
            max_chars = 10000
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars]
                console.print(f"[yellow]⚠️ Truncated content to {max_chars} chars[/yellow]")

            # LLM extraction
            extraction_prompt = f"""
            Analyze this webpage and extract pricing information.
            
            URL: {self.page.url}
            Found elements: {json.dumps(found_elements[:20])}
            
            Text (truncated):
            {text_content}
            
            Extract: prices, tiers, features per tier, billing cycles.
            Return as concise JSON.
            """

            response = await self.llm.ainvoke([HumanMessage(content=extraction_prompt)])

            return {
                "status": "success",
                "data": response.content,
                "visual_elements": found_elements[:20],
                "discovered_sections": discovered,
                "url": self.page.url,
                "extracted_at": datetime.now().isoformat(),
            }

        except Exception as e:
            error_msg = str(e)
            if "ThrottlingException" in error_msg or "ValidationException" in error_msg:
                console.print("[yellow]⚠️ LLM limit hit, returning partial data[/yellow]")
                return {
                    "status": "partial",
                    "visual_elements": found_elements[:20] if "found_elements" in locals() else [],
                    "url": self.page.url,
                }
            console.print(f"[red]❌ Extraction error: {e}[/red]")
            return {"status": "error", "error": str(e)}

    async def extract_product_features(self) -> Dict:
        """Extract product features from the page."""
        try:
            console.print("[cyan]🔍 Extracting product features...[/cyan]")

            # Get text content - LIMITED
            text_content = await self.page.evaluate("() => document.body.innerText")

            max_chars = 8000
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars]

            extraction_prompt = f"""
            Extract key product features from this page.
            URL: {self.page.url}
            
            Content:
            {text_content}
            
            List top 10 features as JSON. Be concise.
            """

            response = await self.llm.ainvoke([HumanMessage(content=extraction_prompt)])

            return {
                "status": "success",
                "data": response.content,
                "url": self.page.url,
                "extracted_at": datetime.now().isoformat(),
            }

        except Exception as e:
            if "ThrottlingException" in str(e) or "ValidationException" in str(e):
                return {
                    "status": "partial",
                    "error": "Rate limited",
                    "url": self.page.url,
                }
            return {"status": "error", "error": str(e)}

    async def take_annotated_screenshot(self, description: str = "") -> Dict:
        """Take screenshot with annotation overlay."""
        try:
            console.print(f"[cyan]📸 Taking screenshot: {description}[/cyan]")

            # Add annotation to the page (safe way without innerHTML)
            if description:
                await self.page.evaluate(f"""
                    () => {{
                        const annotation = document.createElement('div');
                        annotation.id = 'screenshot-annotation';
                        annotation.style.cssText = 'position: fixed; top: 10px; left: 10px; background: rgba(0,0,0,0.8); color: white; padding: 10px; border-radius: 8px; z-index: 99999; font-family: monospace;';
                        annotation.textContent = '{description} - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}';
                        document.body.appendChild(annotation);
                    }}
                """)

            # Take screenshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = f"screenshot_{timestamp}.png"

            await self.page.screenshot(path=screenshot_path, full_page=False)

            # Remove annotation
            if description:
                await self.page.evaluate("""
                    () => {
                        const annotation = document.getElementById('screenshot-annotation');
                        if (annotation) annotation.remove();
                    }
                """)

            screenshot_info = {
                "description": description,
                "timestamp": datetime.now().isoformat(),
                "url": self.page.url,
                "path": screenshot_path,
            }
            self._screenshots_taken.append(screenshot_info)

            # Clean up local file
            import os

            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)

            return {
                "status": "success",
                "screenshot": screenshot_info,
                "total_screenshots": len(self._screenshots_taken),
            }

        except Exception as e:
            console.print(f"[yellow]⚠️ Screenshot error: {e}[/yellow]")
            return {"status": "error", "error": str(e)}

    async def capture_performance_metrics(self) -> Dict:
        """Capture performance metrics using CDP."""
        if not self.cdp_session:
            return {}

        try:
            metrics = await self.cdp_session.send("Performance.getMetrics")
            return {m["name"]: m["value"] for m in metrics.get("metrics", [])}
        except Exception:
            return {}

    def take_control(self):
        """Take manual control of the browser."""
        if self.browser_client:
            console.print("[yellow]🎮 Taking manual control...[/yellow]")
            self.browser_client.take_control()
            console.print("✅ Manual control enabled")

    def release_control(self):
        """Release manual control."""
        if self.browser_client:
            console.print("[yellow]🤖 Releasing control...[/yellow]")
            self.browser_client.release_control()
            console.print("✅ Automation restored")

    async def cleanup(self):
        """Clean up browser resources."""
        if self.cdp_session:
            try:
                await self.cdp_session.detach()
            except Exception:
                pass

        if self.browser:
            console.print("[yellow]🎭 Closing browser...[/yellow]")
            await self.browser.close()

        if self.playwright:
            console.print("[yellow]🎭 Stopping Playwright...[/yellow]")
            await self.playwright.stop()

        if self.browser_client:
            console.print("[yellow]🛑 Stopping session...[/yellow]")
            self.browser_client.stop()
            console.print("✅ Cleanup complete")
