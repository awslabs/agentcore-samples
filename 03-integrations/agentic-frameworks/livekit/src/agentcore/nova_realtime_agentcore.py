"""
Interview Agent with Bedrock AgentCore + LiveKit + Nova Sonic
"""

import asyncio
import json
import os
from datetime import datetime

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.memory.session import MemorySessionManager
from dotenv import load_dotenv

# Try to import LiveKit with better error handling
try:
    from livekit import rtc
    from livekit.agents import Agent, AgentSession, RoomInputOptions, RoomOutputOptions, llm
    from livekit.plugins import aws

    LIVEKIT_AVAILABLE = True
except ImportError as e:
    print(f"WARNING: LiveKit import failed: {e}")
    if "ReadWriteLogRecord" in str(e):
        print("This is an OpenTelemetry compatibility issue.")
        print("The container has been updated to use compatible versions.")
        print("Please redeploy the agent to fix this issue.")
    else:
        print("This is likely due to missing system dependencies in the container.")
        print("Make sure the following packages are installed:")
        print("- libglib2.0-0")
        print("- libgobject-2.0-0")
        print("- libgirepository-1.0-1")
        print("- gir1.2-glib-2.0")
    LIVEKIT_AVAILABLE = False

    # Create dummy classes to prevent further import errors
    class rtc:
        class Room:
            pass

    class AgentSession:
        pass

    class Agent:
        pass

    class llm:
        class ChatContext:
            @staticmethod
            def empty():
                return None

    class RoomInputOptions:
        pass

    class RoomOutputOptions:
        pass

    class aws:
        class realtime:
            class RealtimeModel:
                pass


import random

import boto3
import requests
from chat_history import ChatHistory

load_dotenv()
app = BedrockAgentCoreApp()

# Memory configuration
MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", "nova_realtime_agentcore_mem-NMIvaLCjpm")

REGION = os.getenv("AWS_REGION", "us-east-1")
NOVA_SONIC_MODEL_ID = os.getenv("NOVA_SONIC_MODEL_ID", "amazon.nova-sonic-v1:0")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))  # Nova Sonic max limit
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "3600"))
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "24000"))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", "1"))
S3_BUCKET = os.getenv("TRANSCRIPT_S3_BUCKET", "ai-interview-transcripts-123456789012")


# Load gateway configuration from JSON file
def load_gateway_config():
    """Load gateway credentials from config file"""
    config_paths = [
        "/app/config/gateway_config.json",
        "config/gateway_config.json",
        "src/agentcore/config/gateway_config.json",
    ]

    for path in config_paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                    print(f"Gateway config loaded from {path}")
                    return config
            except Exception as e:
                print(f"Warning: Could not load gateway config from {path}: {e}")

    print("ERROR: Gateway config file not found. Gateway functionality will not work.")
    return None


GATEWAY_CONFIG = load_gateway_config()


def fetch_gateway_token():
    """Fetch OAuth access token for gateway"""
    if not GATEWAY_CONFIG:
        return None

    try:
        response = requests.post(
            GATEWAY_CONFIG["token_url"],
            data=f"grant_type=client_credentials&client_id={GATEWAY_CONFIG['client_id']}&client_secret={GATEWAY_CONFIG['client_secret']}&scope={GATEWAY_CONFIG['scope']}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        result = response.json()
        return result.get("access_token")
    except Exception as e:
        print(f"Failed to fetch gateway token: {e}")
        return None


def invoke_gateway_tool(tool_name, arguments):
    """Invoke gateway tool using JSON-RPC protocol"""
    if not GATEWAY_CONFIG:
        print("Gateway config not loaded, cannot invoke tool")
        return None

    access_token = fetch_gateway_token()
    if not access_token:
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "invoke-tool-request",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    response = requests.post(
        GATEWAY_CONFIG["gateway_url"], headers=headers, json=payload, timeout=30
    )
    return response.json()


def load_interview_questions():
    """Load and randomly select interview questions for each session"""
    # Try multiple paths for local vs container
    for path in [
        "/app/data/leadership_principles.json",
        "../../data/leadership_principles.json",
        "data/leadership_principles.json",
    ]:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    all_principles = json.load(f)
                # Select 4 random principles and pick 1 main + 2 follow-up questions for each
                selected_principles = random.sample(all_principles, 4)  # nosec B311 - not used for security
                principles_list = []
                for principle in selected_principles:
                    questions = random.sample(  # nosec B311 - not used for security
                        principle["questions"], min(3, len(principle["questions"]))
                    )
                    principles_list.append({"name": principle["name"], "questions": questions})
                print(f"Loaded {len(selected_principles)} leadership principles from {path}")
                return principles_list
            except Exception as e:
                print(f"Warning: Could not load leadership principles from {path}: {e}")

    # Fallback
    return [
        {"name": "Teamwork", "questions": []},
        {"name": "Problem Solving", "questions": []},
        {"name": "Communication", "questions": []},
        {"name": "Adaptability", "questions": []},
    ]


def get_interview_instructions(principles_list):
    """Generate interview instructions with loaded principles"""
    principles_names = "\n".join([f"{i + 1}. {p['name']}" for i, p in enumerate(principles_list)])

    return f"""You are Lexis, an AI Interview agent from Amazon. 

CRITICAL RULES:
- Ask ONLY ONE SHORT question per response
- NEVER ask multi-part questions
- STOP immediately after asking ONE question

INTERVIEW STRUCTURE:
1. Introduce yourself (1 sentence), WAIT
2. Ask candidate to introduce themselves, WAIT
3. Cover these 4 principles:
{principles_names}

For EACH principle, you MUST ask EXACTLY 3 questions:
   Question 1: Main behavioral question about the principle
   WAIT for answer
   Question 2: First follow-up (dig deeper into their answer)
   WAIT for answer
   Question 3: Second follow-up (explore impact or learnings)
   WAIT for answer
   
After completing ALL 3 questions for a principle, move to the next principle.

4. After all 4 principles (12 questions total), thank candidate and end.

You MUST ask 3 questions per principle. Do NOT skip follow-ups.
"""


@app.entrypoint
async def invoke(payload, context):
    """Interview agent entrypoint with LiveKit + Nova Sonic"""

    # Check if LiveKit is available
    if not LIVEKIT_AVAILABLE:
        return {
            "error": "LiveKit is not available due to import issues. Please check container configuration and redeploy.",
            "details": "This may be due to OpenTelemetry compatibility issues or missing system dependencies",
            "session_id": getattr(context, "session_id", "default"),
        }

    # Get session ID and room info from payload
    session_id = getattr(context, "session_id", "default")
    room_url = payload.get("room_url")
    room_token = payload.get("room_token")

    if not room_url or not room_token:
        return {"error": "Missing room_url or room_token in payload", "session_id": session_id}

    # Create LiveKit room
    room = rtc.Room()
    session = None

    try:
        # Connect to LiveKit room
        await room.connect(room_url, room_token)
        print("Agent connected to room, waiting for participants...")

        # Wait for a participant to join
        participant_future = asyncio.Future()

        def on_participant_connected(participant: rtc.RemoteParticipant):
            if not participant_future.done():
                print(f"Participant joined: {participant.identity}")
                participant_future.set_result(participant)

        room.on("participant_connected", on_participant_connected)

        # Check if participant already in room
        if len(room.remote_participants) > 0:
            participant_future.set_result(next(iter(room.remote_participants.values())))

        # Wait for participant (with timeout)
        try:
            await asyncio.wait_for(participant_future, timeout=SESSION_TIMEOUT)
        except TimeoutError:
            return {"error": "No participant joined within timeout", "session_id": session_id}

        print("Starting Nova Sonic session...")

        # Load interview questions for this session
        principles_list = load_interview_questions()
        interview_instructions = get_interview_instructions(principles_list)

        # Initialize chat context
        chat_context = llm.ChatContext.empty()
        chat_context.add_message(
            role="user",
            content="Please introduce yourself and explain what this interview is about.",
        )

        # Create Nova Sonic session
        session = AgentSession(
            llm=aws.realtime.RealtimeModel(
                tool_choice="auto",
                max_tokens=MAX_TOKENS,
            )
        )

        # Create agent with interview instructions
        agent = Agent(instructions=interview_instructions, chat_ctx=chat_context)

        # Start the session
        await session.start(
            room=room,
            agent=agent,
            room_input_options=RoomInputOptions(close_on_disconnect=False),
            room_output_options=RoomOutputOptions(
                audio_enabled=True,
                audio_sample_rate=AUDIO_SAMPLE_RATE,
                audio_num_channels=AUDIO_CHANNELS,
                transcription_enabled=True,
            ),
        )

        print("Session started, interview in progress...")
        print(f"S3 Bucket configured: {S3_BUCKET}")

        # Send session ID to UI immediately after session starts
        try:
            session_id_msg = json.dumps({"type": "session_id", "session_id": session_id})
            await room.local_participant.publish_data(
                payload=session_id_msg.encode("utf-8"), reliable=True
            )
            print(f"Session ID sent to UI: {session_id}")
        except Exception as e:
            print(f"Failed to send session ID to UI: {e}")

        # Initialize chat history
        chat_history = ChatHistory()

        # Initialize memory session and store metadata
        memory_session = None
        start_time = datetime.utcnow()
        candidate_name = payload.get("candidate_name", "Unknown")
        last_message_count = 0

        try:
            session_manager = MemorySessionManager(memory_id=MEMORY_ID, region_name=REGION)
            memory_session = session_manager.create_memory_session(
                actor_id=f"candidate_{session_id}", session_id=session_id
            )

            # Store interview start metadata
            metadata_msg = (
                f"Interview started at {start_time.isoformat()} for candidate: {candidate_name}"
            )
            memory_session.add_turns(
                messages=[ConversationalMessage(metadata_msg, MessageRole.ASSISTANT)]
            )
            print(f"Memory session created with start metadata: {session_id}")
        except Exception as e:
            print(f"Failed to create memory session: {e}")

        # Monitor chat context and store in memory
        async def monitor_chat():
            nonlocal last_message_count
            print("Monitor chat task started")
            while True:
                await asyncio.sleep(2)
                try:
                    if session and hasattr(session, "_agent") and session._agent:
                        ctx = session._agent.chat_ctx
                        messages = list(ctx.items)
                        current_count = len(messages)

                        if current_count > last_message_count:
                            print(f"New messages: {current_count - last_message_count}")
                            for msg in messages[last_message_count:]:
                                role = getattr(msg, "role", "unknown")
                                content = str(getattr(msg, "content", ""))

                                if content and role in ["user", "assistant"]:
                                    chat_history.add_message(role.upper(), content)
                                    print(f"{role.upper()}: {content[:100]}...")

                                    # Send to UI via data channel
                                    try:
                                        transcript_msg = json.dumps(
                                            {
                                                "type": "transcript",
                                                "role": role.upper(),
                                                "content": content.replace("['", "")
                                                .replace("']", "")
                                                .replace("\\n", ""),
                                                "timestamp": datetime.utcnow().isoformat(),
                                            }
                                        )
                                        await room.local_participant.publish_data(
                                            payload=transcript_msg.encode("utf-8"), reliable=True
                                        )
                                        print(f"Sent to UI: {role.upper()}")
                                    except Exception as e:
                                        print(f"Failed to send to UI: {e}")
                                        import traceback

                                        traceback.print_exc()

                                    if memory_session:
                                        try:
                                            mem_role = (
                                                MessageRole.USER
                                                if role == "user"
                                                else MessageRole.ASSISTANT
                                            )
                                            memory_session.add_turns(
                                                messages=[ConversationalMessage(content, mem_role)]
                                            )
                                        except Exception as e:
                                            print(f"Failed to store in memory: {e}")

                            last_message_count = current_count
                except Exception as e:
                    print(f"Monitor error: {e}")
                    import traceback

                    traceback.print_exc()

        monitor_task = asyncio.create_task(monitor_chat())

        # Wait for participant to disconnect
        disconnect_future = asyncio.Future()

        def on_participant_disconnected(participant: rtc.RemoteParticipant):
            if not disconnect_future.done():
                print(f"Participant disconnected: {participant.identity}")
                disconnect_future.set_result(participant.identity)

        room.on("participant_disconnected", on_participant_disconnected)

        # Wait for disconnect or timeout
        try:
            participant_id = await asyncio.wait_for(disconnect_future, timeout=SESSION_TIMEOUT)
            print("Interview ended - participant disconnected")
        except TimeoutError:
            participant_id = "timeout"
            print("Interview ended - timeout reached")
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        # Store end metadata in memory
        end_time = datetime.utcnow()
        duration_seconds = (end_time - start_time).total_seconds()

        if memory_session:
            try:
                end_metadata = f"Interview ended at {end_time.isoformat()}. Duration: {duration_seconds:.0f} seconds. Participant: {participant_id}"
                memory_session.add_turns(
                    messages=[ConversationalMessage(end_metadata, MessageRole.ASSISTANT)]
                )
                print("Memory session updated with end metadata")
            except Exception as e:
                print(f"Failed to update memory with end metadata: {e}")

        # Save transcript and metadata to S3
        try:
            s3_client = boto3.client("s3", region_name=REGION)
            timestamp = end_time.strftime("%Y%m%d_%H%M%S")

            # Save transcript
            transcript_data = {
                "session_id": session_id,
                "candidate_name": candidate_name,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": duration_seconds,
                "transcript": chat_history.to_dict(),
            }

            transcript_key = f"transcripts/{candidate_name}_{timestamp}_{session_id}.json"
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=transcript_key,
                Body=json.dumps(transcript_data, indent=2),
                ContentType="application/json",
            )
            print(f"Transcript saved to S3: s3://{S3_BUCKET}/{transcript_key}")

            # Save metadata
            metadata = {
                "session_id": session_id,
                "candidate_name": candidate_name,
                "participant_id": participant_id,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "duration_seconds": duration_seconds,
                "message_count": len(chat_history.messages),
                "source": "agentcore_memory",
            }

            metadata_key = f"metadata/{candidate_name}_{timestamp}_{session_id}.json"
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=metadata_key,
                Body=json.dumps(metadata, indent=2),
                ContentType="application/json",
            )
            print(f"Metadata saved to S3: s3://{S3_BUCKET}/{metadata_key}")
        except Exception as e:
            print(f"Failed to save to S3: {e}")

        # Invoke evaluation via AgentCore Gateway
        try:
            print("Invoking evaluation via AgentCore Gateway...")
            result = invoke_gateway_tool(
                tool_name="evaluate-interview___evaluate_interview",
                arguments={"session_id": session_id},
            )
            if result:
                print(f"Gateway invocation result: {result}")
            else:
                print("Gateway not configured, falling back to direct Lambda invocation")
                lambda_client = boto3.client("lambda", region_name=REGION)
                response = lambda_client.invoke(
                    FunctionName="EvaluateInterview",
                    InvocationType="Event",
                    Payload=json.dumps({"session_id": session_id}),
                )
                print(f"Lambda invoked: StatusCode={response['StatusCode']}")
        except Exception as e:
            print(f"Failed to invoke evaluation: {e}")
            import traceback

            traceback.print_exc()

        return {
            "status": "completed",
            "session_id": session_id,
            "candidate_name": candidate_name,
            "duration_seconds": duration_seconds,
            "message_count": len(chat_history.messages),
            "transcript_saved": True,
        }

    except Exception as e:
        import traceback

        traceback.print_exc()
        return {"error": str(e), "session_id": session_id}
    finally:
        if session:
            await session.aclose()
        await room.disconnect()


if __name__ == "__main__":
    app.run()
