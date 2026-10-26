import json
import os
import uuid

import boto3
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel

load_dotenv()

app = FastAPI()

# In-memory transcript storage
transcript_store = {}

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TokenRequest(BaseModel):
    participant_name: str
    room_name: str = None
    agent_type: str = "ec2"  # 'ec2' or 'agentcore'


@app.post("/generate-token")
async def generate_token(request: TokenRequest):
    """Generate LiveKit access token and optionally invoke AgentCore"""
    try:
        room_name = (
            request.room_name or f"interview-{request.participant_name.replace(' ', '-').lower()}"
        )

        # Generate participant token
        token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))

        token.with_identity(request.participant_name)
        token.with_name(request.participant_name)
        token.with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )

        jwt_token = token.to_jwt()

        # If AgentCore selected, invoke it
        if request.agent_type == "agentcore":
            agent_arn = os.getenv("AGENTCORE_ARN")
            print(f"AgentCore selected. ARN: {agent_arn}")
            if agent_arn:
                try:
                    # Generate agent token
                    agent_token_obj = api.AccessToken(
                        os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")
                    )
                    agent_token_obj.with_identity("agentcore-agent")
                    agent_token_obj.with_name("AgentCore Interview Agent")
                    agent_token_obj.with_grants(
                        api.VideoGrants(
                            room_join=True,
                            room=room_name,
                            can_publish=True,
                            can_subscribe=True,
                        )
                    )
                    agent_token = agent_token_obj.to_jwt()

                    # Invoke AgentCore asynchronously (non-blocking)
                    import threading

                    def invoke_agentcore():
                        try:
                            from botocore.config import Config

                            # Set long timeout for AgentCore (can run for hours)
                            config = Config(
                                read_timeout=28800,  # 8 hours
                                connect_timeout=10,
                            )
                            client = boto3.client(
                                "bedrock-agentcore",
                                region_name=os.getenv("AWS_REGION", "us-east-1"),
                                config=config,
                            )
                            session_id = str(uuid.uuid4())
                            payload = {
                                "room_url": os.getenv("LIVEKIT_URL"),
                                "room_token": agent_token,
                                "candidate_name": request.participant_name,
                            }
                            # Store session ID for this room
                            transcript_store[room_name] = {"session_id": session_id}
                            print(f"Invoking AgentCore for room: {room_name}")
                            client.invoke_agent_runtime(
                                agentRuntimeArn=agent_arn,
                                runtimeSessionId=session_id,
                                payload=json.dumps(payload).encode("utf-8"),
                            )
                            print(f"AgentCore invoked successfully with session_id: {session_id}")
                        except Exception as e:
                            print(f"AgentCore background invocation error: {e}")

                    # Start invocation in background thread
                    thread = threading.Thread(target=invoke_agentcore, daemon=True)
                    thread.start()
                    print("AgentCore invocation started in background")
                except Exception as e:
                    print(f"AgentCore invocation error: {e}")
                    import traceback

                    traceback.print_exc()
                    # Continue anyway, return token

        return {
            "token": jwt_token,
            "room_name": room_name,
            "server_url": os.getenv("LIVEKIT_URL"),
            "agent_type": request.agent_type,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/transcript/{room_name}")
async def get_transcript(room_name: str):
    """Get live transcript for a room"""
    return {"transcript": transcript_store.get(room_name, [])}


@app.post("/transcript/{room_name}")
async def add_transcript_message(room_name: str, message: dict):
    """Add a message to room transcript"""
    if room_name not in transcript_store:
        transcript_store[room_name] = []
    transcript_store[room_name].append(message)
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/session-by-room/{room_name}")
async def get_session_by_room(room_name: str):
    """Get session ID by room name from in-memory storage"""
    if room_name in transcript_store:
        return {"session_id": transcript_store[room_name].get("session_id")}
    raise HTTPException(status_code=404, detail="Session not found")


@app.post("/session/{room_name}")
async def store_session(room_name: str, data: dict):
    """Store session ID for a room"""
    if room_name not in transcript_store:
        transcript_store[room_name] = {}
    transcript_store[room_name]["session_id"] = data.get("session_id")
    return {"status": "ok"}


@app.get("/feedback/{session_id}")
async def get_feedback(session_id: str):
    """Get presigned URL for feedback PDF by session ID"""
    try:
        s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
        bucket = os.getenv("TRANSCRIPT_S3_BUCKET", "ai-interview-transcripts-123456789012")

        # Look for PDF file with session_id (format: {name}_{session_id}_feedback.pdf)
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix="feedback/")

        if "Contents" not in response:
            raise HTTPException(
                status_code=404, detail="Feedback not ready yet. Please wait a moment."
            )

        # Find PDF file containing the session_id
        pdf_files = [
            obj
            for obj in response["Contents"]
            if session_id in obj["Key"] and obj["Key"].endswith(".pdf")
        ]
        if not pdf_files:
            raise HTTPException(
                status_code=404, detail="Feedback not ready yet. Please wait a moment."
            )

        # Generate presigned URL (valid for 1 hour)
        pdf_url = s3_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": pdf_files[0]["Key"]}, ExpiresIn=3600
        )

        return {"pdf_url": pdf_url, "filename": pdf_files[0]["Key"].split("/")[-1]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)  # nosec B104 - intentional binding for container use
