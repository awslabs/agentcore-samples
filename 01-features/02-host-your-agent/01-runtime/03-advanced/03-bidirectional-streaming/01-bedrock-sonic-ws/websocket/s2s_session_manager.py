import asyncio
import json
import warnings
import uuid
import logging
from s2s_events import S2sEvent
import time
from aws_sdk_bedrock_runtime.client import (
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from aws_sdk_bedrock_runtime.models import (
    InvokeModelWithBidirectionalStreamInputChunk,
    BidirectionalInputPayloadPart,
)
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

# Suppress warnings
warnings.filterwarnings("ignore")

# Configure logging
logger = logging.getLogger(__name__)


class S2sSessionManager:
    """Manages bidirectional streaming with AWS Bedrock using asyncio"""

    def __init__(self, region, model_id):
        """Initialize the stream manager."""
        self.model_id = model_id
        self.region = region

        # Audio and output queues with size limits to prevent memory issues
        self.audio_input_queue = asyncio.Queue(maxsize=100)  # Limit to 100 audio chunks (~2-3 seconds of audio)
        self.output_queue = asyncio.Queue(maxsize=200)  # Larger output queue for responses

        self.response_task = None
        self.stream = None
        self.is_active = False
        self.bedrock_client = None

        # Session information
        self.prompt_name = None  # Will be set from frontend
        self.content_name = None  # Will be set from frontend
        self.audio_content_name = None  # Will be set from frontend
        self.toolUseContent = ""
        self.toolUseId = ""
        self.toolName = ""

        # Track active tool processing tasks
        self.tool_processing_tasks = set()

    def _initialize_client(self):
        """
        Initialize the Bedrock client using EnvironmentCredentialsResolver.

        Credentials are managed by server.py which either:
        - Uses existing environment variables (local mode)
        - Fetches and refreshes credentials from IMDS (EC2 mode)
        """
        logger.info("Initializing Bedrock client with EnvironmentCredentialsResolver")

        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        self.bedrock_client = BedrockRuntimeClient(config=config)
        logger.info("Bedrock client initialized successfully")

    def reset_session_state(self):
        """Reset session state for a new session."""
        # Cancel any ongoing tool processing tasks
        for task in list(self.tool_processing_tasks):
            if not task.done():
                task.cancel()
        self.tool_processing_tasks.clear()

        # Clear queues
        while not self.audio_input_queue.empty():
            try:
                self.audio_input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Reset tool use state
        self.toolUseContent = ""
        self.toolUseId = ""
        self.toolName = ""

        # Reset session information
        self.prompt_name = None
        self.content_name = None
        self.audio_content_name = None

    async def initialize_stream(self):
        """Initialize the bidirectional stream with Bedrock."""
        try:
            if not self.bedrock_client:
                self._initialize_client()
        except Exception:
            self.is_active = False
            logger.error("Failed to initialize Bedrock client")
            raise

        try:
            # Initialize the stream
            self.stream = await self.bedrock_client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
            )
            self.is_active = True

            # Start listening for responses
            self.response_task = asyncio.create_task(self._process_responses())

            # Start processing audio input
            asyncio.create_task(self._process_audio_input())

            # Wait a bit to ensure everything is set up
            await asyncio.sleep(0.1)

            logger.info("Stream initialized successfully")
            return self
        except Exception:
            self.is_active = False
            logger.error("Failed to initialize stream.")
            raise

    async def send_raw_event(self, event_data):
        """Send a raw event to the Bedrock stream."""
        try:
            if not self.stream or not self.is_active:
                logger.warning("Stream not initialized or closed")
                return

            event_json = json.dumps(event_data)
            # if "audioInput" not in event_data["event"]:
            #    print(event_json)
            event = InvokeModelWithBidirectionalStreamInputChunk(
                value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))
            )
            await self.stream.input_stream.send(event)

            # Close session
            if "sessionEnd" in event_data["event"]:
                await self.close()

        except Exception:
            logger.error("Error sending event to Bedrock")
            # Don't close the stream on send errors, let Bedrock handle it
            # The response processing loop will detect if the stream is broken

    async def _process_audio_input(self):
        """Process audio input from the queue and send to Bedrock."""
        while self.is_active:
            try:
                # Get audio data from the queue
                data = await self.audio_input_queue.get()

                # Extract data from the queue item
                prompt_name = data.get("prompt_name")
                content_name = data.get("content_name")
                audio_bytes = data.get("audio_bytes")

                if not audio_bytes or not prompt_name or not content_name:
                    logger.warning("Missing required audio data properties")
                    continue

                # Create the audio input event
                audio_event = S2sEvent.audio_input(
                    prompt_name,
                    content_name,
                    (audio_bytes.decode("utf-8") if isinstance(audio_bytes, bytes) else audio_bytes),
                )

                # Send the event
                await self.send_raw_event(audio_event)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Error processing audio.")

    def add_audio_chunk(self, prompt_name, content_name, audio_data):
        """Add an audio chunk to the queue."""
        # The audio_data is already a base64 string from the frontend
        try:
            self.audio_input_queue.put_nowait(
                {
                    "prompt_name": prompt_name,
                    "content_name": content_name,
                    "audio_bytes": audio_data,
                }
            )
        except asyncio.QueueFull:
            # Queue is full - drop this chunk to prevent backpressure
            # This is acceptable for real-time audio streaming
            logger.warning("Audio input queue full, dropping audio chunk to prevent backpressure")
            pass

    async def _process_responses(self):
        """Process incoming responses from Bedrock."""
        while self.is_active:
            try:
                output = await self.stream.await_output()
                result = await output[1].receive()

                if result.value and result.value.bytes_:
                    response_data = result.value.bytes_.decode("utf-8")
                    logger.debug(f"Received event: {response_data}")

                    json_data = json.loads(response_data)
                    json_data["timestamp"] = int(time.time() * 1000)  # Milliseconds since epoch

                    event_name = None
                    if "event" in json_data:
                        event_name = list(json_data["event"].keys())[0]

                        # Log contentEnd events for debugging
                        if event_name == "contentEnd":
                            content_end_data = json_data["event"]["contentEnd"]
                            logger.debug(
                                f"Received contentEnd: type={content_end_data.get('type')}, stopReason={content_end_data.get('stopReason')}, role={content_end_data.get('role', 'N/A')}"
                            )

                        # Handle tool use detection
                        if event_name == "toolUse":
                            self.toolUseContent = json_data["event"]["toolUse"]
                            self.toolName = json_data["event"]["toolUse"]["toolName"]
                            self.toolUseId = json_data["event"]["toolUse"]["toolUseId"]
                            logger.info(f"Tool use detected: {self.toolName}, ID: {self.toolUseId}")

                        # Process tool use when content ends
                        elif event_name == "contentEnd" and json_data["event"][event_name].get("type") == "TOOL":
                            prompt_name = json_data["event"]["contentEnd"].get("promptName")
                            logger.debug("Starting tool processing in background")
                            # Process tool in background task to avoid blocking
                            task = asyncio.create_task(
                                self._handle_tool_processing(
                                    prompt_name,
                                    self.toolName,
                                    self.toolUseContent,
                                    self.toolUseId,
                                )
                            )
                            self.tool_processing_tasks.add(task)
                            task.add_done_callback(self.tool_processing_tasks.discard)

                    # Put the response in the output queue for forwarding to the frontend
                    try:
                        # Use put_nowait to avoid blocking, but handle queue full gracefully
                        self.output_queue.put_nowait(json_data)
                    except asyncio.QueueFull:
                        # Queue is full - log warning but don't break the stream
                        # This can happen during high-throughput audio responses
                        logger.warning("Output queue full, dropping response to prevent backpressure")
                        # Continue processing instead of breaking the stream

            except json.JSONDecodeError as ex:
                logger.error(f"JSON decode error in _process_responses: {ex}")
                await self.output_queue.put({"raw_data": response_data})
                # Don't break on JSON errors, continue processing
                continue
            except StopAsyncIteration:
                # Stream has ended normally
                logger.info("Bedrock stream has ended (StopAsyncIteration)")
                break
            except Exception as e:
                # Handle ValidationException and other errors
                error_str = str(e)
                if "ValidationException" in error_str:
                    logger.error(f"Bedrock validation error: {error_str}")
                    # Send error to client but don't break the stream
                    await self.output_queue.put({"event": {"error": {"message": f"Validation error: {error_str}"}}})
                    continue
                else:
                    logger.error(f"Error receiving response from Bedrock: {e}", exc_info=True)
                    # Only break on serious errors
                    break

        logger.info("Bedrock response processing loop ended, closing stream")
        self.is_active = False
        await self.close()

    async def _handle_tool_processing(self, prompt_name, tool_name, tool_use_content, tool_use_id):
        """Handle tool processing in background without blocking event processing"""
        try:
            logger.info(f"[Tool Processing] Starting: {tool_name} with ID: {tool_use_id}")
            toolResult = await self.processToolUse(tool_name, tool_use_content)
            logger.info(f"[Tool Processing] Completed: {tool_name}")

            # Send tool start event
            toolContent = str(uuid.uuid4())
            tool_start_event = S2sEvent.content_start_tool(prompt_name, toolContent, tool_use_id)
            await self.send_raw_event(tool_start_event)

            # Also send tool start event to WebSocket client
            tool_start_event_copy = tool_start_event.copy()
            tool_start_event_copy["timestamp"] = int(time.time() * 1000)
            await self.output_queue.put(tool_start_event_copy)

            # Send tool result event
            if isinstance(toolResult, dict):
                content_json_string = json.dumps(toolResult)
            else:
                content_json_string = toolResult

            tool_result_event = S2sEvent.text_input_tool(prompt_name, toolContent, content_json_string)
            logger.debug(f"Tool result: {tool_result_event}")
            await self.send_raw_event(tool_result_event)

            # Also send tool result event to WebSocket client
            tool_result_event_copy = tool_result_event.copy()
            tool_result_event_copy["timestamp"] = int(time.time() * 1000)
            await self.output_queue.put(tool_result_event_copy)

            # Send tool content end event
            tool_content_end_event = S2sEvent.content_end(prompt_name, toolContent)
            await self.send_raw_event(tool_content_end_event)

            # Also send tool content end event to WebSocket client
            tool_content_end_event_copy = tool_content_end_event.copy()
            tool_content_end_event_copy["timestamp"] = int(time.time() * 1000)
            await self.output_queue.put(tool_content_end_event_copy)

        except Exception as e:
            logger.error(f"Error in tool processing: {e}", exc_info=True)

    async def processToolUse(self, toolName, toolUseContent):
        """Return the tool result"""
        logger.debug(f"Tool Use Content: {toolUseContent}")

        toolName = toolName.lower()
        content, result = None, None
        try:
            if toolUseContent.get("content"):
                # Parse the JSON string in the content field
                content = toolUseContent.get("content")  # Pass the JSON string directly to the agent
                logger.debug(f"Extracted query: {content}")

            # Simple toolUse to get system time in UTC
            if toolName == "getdatetool":
                from datetime import datetime, timezone

                result = datetime.now(timezone.utc).strftime("%A, %Y-%m-%d %H:%M:%S") + " in UTC"

            if not result:
                result = "no result found"

            return {"result": result}
        except Exception as ex:
            logger.error(
                f"[Tool Error] Exception in processToolUse for {toolName}: {ex}",
                exc_info=True,
            )
            return {
                "result": "An error occurred while attempting to retrieve information related to the toolUse event."
            }

    async def close(self):
        """Close the stream properly."""
        if not self.is_active:
            logger.debug("Stream already closed, skipping cleanup")
            return

        logger.info("Closing Bedrock stream and cleaning up resources")
        self.is_active = False

        # Cancel any ongoing tool processing tasks
        for task in list(self.tool_processing_tasks):
            if not task.done():
                task.cancel()

        # Wait for all tool tasks to complete or be cancelled
        if self.tool_processing_tasks:
            await asyncio.gather(*self.tool_processing_tasks, return_exceptions=True)
        self.tool_processing_tasks.clear()

        # Clear audio queue to prevent processing old audio data
        while not self.audio_input_queue.empty():
            try:
                self.audio_input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Clear output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Reset tool use state
        self.toolUseContent = ""
        self.toolUseId = ""
        self.toolName = ""

        # Reset session information
        self.prompt_name = None
        self.content_name = None
        self.audio_content_name = None

        if self.stream:
            try:
                await self.stream.input_stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")

        if self.response_task and not self.response_task.done():
            self.response_task.cancel()
            try:
                await self.response_task
            except asyncio.CancelledError:
                pass

        # Set stream to None to ensure it's properly cleaned up
        self.stream = None
        self.response_task = None

        logger.info("Bedrock stream closed successfully")
