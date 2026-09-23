#!/usr/bin/env python

# # Multi-Agent Healthcare System with Episodic Memory
#
# ## Introduction
#
# This notebook demonstrates how to implement a **multi-agent healthcare system with episodic memory** using the AgentCore Memory SDK and Strands memory hooks. This approach provides automatic memory management without manual API calls.
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:----------------------------------------------------------------------------------|
# | Tutorial type       | Episodic Memory with Multi-Agent Coordination                                    |
# | Agent type          | Healthcare Assistant System                                                      |
# | Agentic Framework   | Strands Agents with Memory Hooks                                                 |
# | LLM model           | Anthropic Claude Opus 4.6                                                        |
# | Tutorial components | Episodic Memory, Memory Hooks, HealthLake Integration                           |
# | Example complexity  | Intermediate                                                                     |
#
# You will learn:
#
# - How to use the MemoryClient SDK for episodic memory
# - Creating memory hooks for automatic memory management
# - Implementing specialized agents with shared episodic memory
# - Integrating real-time HealthLake FHIR queries
#
# ## How Episodic Memory Helps This Healthcare Assistant
#
# The **EpisodicStrategy** captures interactions as structured episodes and generates meaningful insights across sessions. This goes beyond recording "what happened" to understand "why" and "how" interactions unfolded.
#
# ### Three-Step Process
#
# 1. **Extraction** – Identifies useful insights from short-term memory (events) and places them into long-term memory as structured episodes
# 2. **Consolidation** – Determines whether to write information to a new episode or update an existing one
# 3. **Reflection** – Generates insights across multiple episodes to identify patterns and improvements
#
# ### Episode Structure
#
# Each episode captures:
# - **Situation**: What the healthcare professional was trying to accomplish
# - **Intent**: The primary goal of the interaction
# - **Assessment**: Whether the goal was successfully achieved
# - **Justification**: Why the assessment was made
# - **Turn-by-turn analysis**: Detailed breakdown showing agent routing, tool usage, and decision-making
# - **Episode-level reflection**: Insights about what worked well in this specific session
#
# ### Patient-Level Reflections
#
# Reflections consolidate across multiple episodes to extract broader insights:
# - **Successful strategies**: Patterns that consistently work (e.g., routing protocol, data presentation)
# - **Common use cases**: Types of inquiries frequently made for this patient
# - **Potential improvements**: Areas where the assistant could be more effective
# - **Lessons learned**: Insights that span multiple interactions
#
# ### Benefits for Healthcare Workflows
#
# 1. **Improved routing**: Learn which agent handles which types of questions most effectively
# 2. **Better data presentation**: Understand how to format complex healthcare data for quick comprehension
# 3. **Pattern recognition**: Identify common inquiry patterns for specific patients
# 4. **Quality improvement**: Track what works and what doesn't across multiple sessions
# 5. **Contextual awareness**: Future interactions benefit from lessons learned in past sessions
#
# In this tutorial, you'll see how episodes capture the complete flow of multi-agent interactions, and how reflections provide actionable insights for improving the healthcare assistant over time.
#
# ---
# ## Scenario Context
#
# We'll create a **Healthcare Assistant System** with:
# 1. A **Supervisor Agent** that routes patient questions
# 2. A **Claims Agent** for insurance and billing
# 3. A **Demographics Agent** for patient information
# 4. A **Medication Agent** for prescriptions
#
# All agents use memory hooks to automatically save conversations to episodic memory.
#
# ## Architecture
# <div style="text-align:left">
#     <img src="architecture.png" width="75%" />
# </div>
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with Bedrock and AgentCore Memory permissions
# - Amazon HealthLake datastore (optional)
#
# Let's get started!

# ## Step 1: Environment Setup
# Let's begin importing all the necessary libraries and defining the clients to make this notebook work.


import logging
from datetime import datetime
from strands import Agent, tool
from strands.hooks import HookProvider, HookRegistry
from bedrock_agentcore.memory import MemoryClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("healthcare-assistant")


# Let's define the user inputs for Memory Configuration


MEMORY_NAME = "healthcare_episodic_memory"
PATIENT_ID = "b2055b4d-ac17-4d94-8c5b-3395e4c334dd"
region = "us-east-1"  # Replace with your AWS region
SESSION_ID = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
MODEL_ID = "global.anthropic.claude-opus-4-6-v1"  # Replace with your Model ID

print("Memory Configuration:")
print(f"  Memory Name: {MEMORY_NAME}")
print(f"  Patient ID: {PATIENT_ID}")  # codeql[py/clear-text-logging-sensitive-data]
print(f"  Region: {region}")
print(f"  Session ID: {SESSION_ID}")
print(f"  Model ID: {MODEL_ID}")


# ## Step 2: Configure HealthLake Datastore
#
# Set up HealthLake FHIR datastore with patient data for the healthcare agents to query.


import boto3  # noqa: E402
import requests  # noqa: E402
import time  # noqa: E402
from botocore.auth import SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402

# HealthLake configuration
HEALTHLAKE_REGION = "us-east-1"
DATASTORE_ID = ""

healthlake_client = boto3.client("healthlake", region_name=HEALTHLAKE_REGION)

# Create new datastore if not provided
if not DATASTORE_ID:
    create_new = "yes"

    if create_new == "yes":
        print("\nCreating HealthLake datastore...")

        # Create datastore
        create_response = healthlake_client.create_fhir_datastore(
            DatastoreName=f"healthcare-demo-{int(time.time())}",
            DatastoreTypeVersion="R4",
            PreloadDataConfig={"PreloadDataType": "SYNTHEA"},
        )

        DATASTORE_ID = create_response["DatastoreId"]
        print(f"✅ Datastore created: {DATASTORE_ID}")
        print("⏳ Waiting for datastore to become ACTIVE (this may take 10-15 minutes)...")

        # Wait for ACTIVE status
        while True:
            status_response = healthlake_client.describe_fhir_datastore(DatastoreId=DATASTORE_ID)
            status = status_response["DatastoreProperties"]["DatastoreStatus"]

            if status == "ACTIVE":
                print("✅ Datastore is ACTIVE")
                break
            elif status in ["FAILED", "DELETING"]:
                print(f"❌ Datastore creation failed with status: {status}")
                raise Exception(f"Datastore creation failed: {status}")

            print(f"   Status: {status}...")
            time.sleep(30)

        print(
            f"\n✅ Synthea data loaded. Using default patient ID: {PATIENT_ID}"
        )  # codeql[py/clear-text-logging-sensitive-data]


# Get HealthLake endpoint
datastore = healthlake_client.describe_fhir_datastore(DatastoreId=DATASTORE_ID)
HEALTHLAKE_ENDPOINT = datastore["DatastoreProperties"]["DatastoreEndpoint"]


def query_healthlake(resource_type, search_params=None, resource_id=None):
    """Query HealthLake FHIR API"""
    if resource_id:
        url = f"{HEALTHLAKE_ENDPOINT}/{resource_type}/{resource_id}"
    else:
        url = f"{HEALTHLAKE_ENDPOINT}/{resource_type}"
        if search_params:
            params = "&".join([f"{k}={v}" for k, v in search_params.items()])
            url += f"?{params}"

    session = boto3.Session()
    credentials = session.get_credentials()

    request = AWSRequest(method="GET", url=url, headers={"Accept": "application/fhir+json"})
    SigV4Auth(credentials, "healthlake", HEALTHLAKE_REGION).add_auth(request)

    response = requests.get(url, headers=dict(request.headers), timeout=30)

    if response.status_code == 200:
        return response.json()
    else:
        return {"error": f"Failed to fetch: {response.text}"}


print(f"\n{'=' * 70}")
print("HealthLake Configuration:")
print(f"  Datastore ID: {DATASTORE_ID}")
print(f"  Endpoint:     {HEALTHLAKE_ENDPOINT}")
print(f"  Region:       {HEALTHLAKE_REGION}")
print(f"  Patient ID:   {PATIENT_ID}")  # codeql[py/clear-text-logging-sensitive-data]
print(f"{'=' * 70}")


# ## Step 3: Create Memory with Episodic Strategy
#
# We'll create a single memory resource that will support multiple branches - one for each agent. This shared memory resource acts as the foundation, while branches provide isolated contexts for each agent's conversations.
#
# Think of it like a Git repository: one repository (memory resource) with multiple branches (agent contexts).


client = MemoryClient(region_name=region)

# NOTE: although this tutorial is framed around "episodes", it is configured with a
# SEMANTIC strategy. True episodic memory requires an `episodicMemoryStrategy` with a
# `reflectionConfiguration` (and conversations that conclude so the service detects
# episode completion). See 02-long-term-memory/01-built-in-strategies/episodic.py.
strategies = [
    {
        "semanticMemoryStrategy": {
            "name": "HealthcareEpisodes",
            "description": "Captures healthcare interactions as episodes",
            "namespaceTemplates": ["/healthcare/{actorId}/{sessionId}/"],
        }
    }
]

# Initialize before the try so the error handler can reference it safely even if
# creation fails before assignment (otherwise `if memory_id` raises NameError).
memory_id = None
try:
    # create_or_get_memory is idempotent: creates the memory (waiting for ACTIVE) or
    # returns the existing one on a re-run, replacing the manual list-and-match fallback.
    memory = client.create_or_get_memory(
        name=MEMORY_NAME,
        strategies=strategies,
        description="Healthcare system with episodic memory",
        event_expiry_days=7,  # Short-term conversation expires after 7 days
    )
    memory_id = memory["id"]
    logger.info(f"Memory ready with ID: {memory_id}")
except Exception as e:
    # Handle any errors during memory creation
    print(f"❌ ERROR: {e}")
    import traceback

    traceback.print_exc()

    # Cleanup on error - delete the memory if it was partially created
    if memory_id:
        try:
            client.delete_memory_and_wait(memory_id=memory_id)
            logger.info(f"Cleaned up memory: {memory_id}")
        except Exception as cleanup_error:
            logger.info(f"Failed to clean up memory: {cleanup_error}")


# ### Understanding Memory Branching for Healthcare Multi-Agent Systems
#
# The memory resource we've created supports **branching** - a critical feature for healthcare multi-agent architectures. Here's how it works:
#
# **Single Memory Resource, Multiple Branches:**
# - All agents share the same `memory_id` and `session_id`
# - Each agent gets its own `branch_name` for isolated context
#
# **Key Benefits for Healthcare Multi-Agent Systems:**
#
# 1. **Context Isolation**: Each agent maintains its own conversation history without interference
#    - Claims agent only sees insurance and billing conversations
#    - Demographics agent only sees patient information conversations
#    - Medication agent only sees prescription-related conversations
#    - Supervisor sees the main routing and coordination flow
#
# 2. **Parallel Execution Safety**: Multiple agents can execute simultaneously
#    - No memory conflicts when agents run in parallel
#    - Each branch is independently accessible
#    - Critical for healthcare workflows that require concurrent processing
#
# 3. **Clear Audit Trail**: Each agent's interactions are traceable
#    - Inspect what each healthcare agent discussed
#    - Debug agent-specific issues
#    - Understand the flow of patient care conversations
#    - Maintain compliance and documentation requirements
#
# **Healthcare Branch Structure:**
# - `main` branch: Supervisor routing decisions
# - `claims_agent` branch: Insurance and billing conversations
# - `demographics_agent` branch: Patient information updates
# - `medication_agent` branch: Prescription discussions

# ## Step 4: Create Memory Hook Provider with Branch Support


from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole  # noqa: E402
from strands.hooks import AgentInitializedEvent, MessageAddedEvent  # noqa: E402
from bedrock_agentcore.memory import MemorySessionManager  # noqa: E402


class HealthcareMemoryHooks(HookProvider):
    def __init__(self, memory_id: str, region_name: str = None, branch_name: str = "main"):
        """Initialize the hook with a MemorySessionManager.

        Args:
            memory_id: The AgentCore Memory ID
            region_name: AWS region for the memory service
            branch_name: Branch name for this agent's memory (default: "main")
        """
        if region_name is None:
            region_name = region  # Use global region variable

        self.memory_manager = MemorySessionManager(memory_id=memory_id, region_name=region_name)
        self.memory_id = memory_id
        self.branch_name = branch_name
        self._sessions = {}  # Cache session objects per actor/session combo
        self._branch_initialized = False  # Track if branch has been created

    def _get_or_create_session(self, actor_id: str, session_id: str):
        """Get or create a MemorySession for the given actor/session."""
        key = f"{actor_id}:{session_id}"
        if key not in self._sessions:
            self._sessions[key] = self.memory_manager.create_memory_session(actor_id=actor_id, session_id=session_id)
        return self._sessions[key]

    def _initialize_branch(self, actor_id: str, session_id: str):
        """Initialize a branch if it doesn't exist and this is not the main branch."""
        if self._branch_initialized or self.branch_name == "main":
            return

        try:
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Check if branch already exists
            branches = memory_session.list_branches()
            branch_exists = any(b.name == self.branch_name for b in branches)

            if not branch_exists:
                # Get the last event from main branch to fork from
                main_events = memory_session.list_events(branch_name="main")
                if not main_events:
                    # Create initial event in main branch
                    memory_session.add_turns(
                        [ConversationalMessage("Healthcare system initialized", MessageRole.ASSISTANT)]
                    )
                    main_events = memory_session.list_events(branch_name="main")

                if main_events:
                    last_event = main_events[-1]
                    # Create the branch
                    memory_session.fork_conversation(
                        root_event_id=last_event.eventId,
                        branch_name=self.branch_name,
                        messages=[
                            ConversationalMessage(
                                f"Starting {self.branch_name} healthcare branch",
                                MessageRole.ASSISTANT,
                            )
                        ],
                    )
                    logger.info(f"✅ Created healthcare branch: {self.branch_name}")

            self._branch_initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize healthcare branch {self.branch_name}: {e}")

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when healthcare agent starts"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in healthcare agent state")
                return

            # Initialize branch if needed (for non-main branches)
            if self.branch_name != "main":
                self._initialize_branch(actor_id, session_id)

            # Get the memory session
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Get last 5 conversation turns from this branch
            recent_turns = memory_session.get_last_k_turns(
                k=5, branch_name=self.branch_name, include_parent_branches=False
            )

            if recent_turns:
                # Add context to agent's system prompt
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message.get("role", "unknown").lower()
                        text = message.get("content", {}).get("text", "")
                        if text:
                            context_messages.append(f"{role.title()}: {text}")

                if context_messages:
                    context = "\n".join(context_messages[-10:])  # Last 10 messages
                    event.agent.system_prompt += (
                        f"\n\nRecent healthcare conversation history:\n{context}\n\n"
                        "Continue the conversation naturally based on this context."
                    )
                    logger.info(f"✅ Loaded healthcare context from branch '{self.branch_name}'")

        except Exception as e:
            logger.error(f"Failed to load healthcare conversation history: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Store healthcare conversation turns in memory on the appropriate branch"""
        try:
            # Get session info from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Missing actor_id or session_id in healthcare agent state")
                return

            # Get the memory session
            memory_session = self._get_or_create_session(actor_id, session_id)

            # Get the last message
            messages = event.agent.messages
            if not messages:
                return

            last_message = messages[-1]
            role_str = last_message.get("role", "").upper()
            content_text = last_message.get("content", [{}])[0].get("text", "")

            if not content_text:
                logger.debug("Skipping empty healthcare message")
                return

            # Map role string to MessageRole enum
            role_mapping = {
                "USER": MessageRole.USER,
                "ASSISTANT": MessageRole.ASSISTANT,
                "TOOL": MessageRole.TOOL,
            }
            message_role = role_mapping.get(role_str, MessageRole.USER)

            # Store the message on the appropriate branch
            if self.branch_name == "main":
                # Main branch - just add turns normally
                memory_session.add_turns(messages=[ConversationalMessage(content_text, message_role)])
            else:
                # Non-main branch - need to append to existing branch
                # Initialize branch if it doesn't exist
                if not self._branch_initialized:
                    self._initialize_branch(actor_id, session_id)

                # Add to existing branch
                memory_session.add_turns(
                    messages=[ConversationalMessage(content_text, message_role)],
                    branch={"name": self.branch_name},
                )

            logger.info(f"Memory saved to healthcare branch: {self.branch_name}")

        except Exception as e:
            logger.error(f"Failed to store healthcare message: {e}")

    def get_session(self, actor_id: str, session_id: str):
        """Get the memory session object for direct access."""
        return self._get_or_create_session(actor_id, session_id)

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register healthcare memory hooks with the registry."""
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


print("✅ Healthcare memory hook provider defined")


# ## Step 5: Create Multi-Agent Healthcare Architecture with Memory Branching
#
# In this section, we'll create specialized healthcare agents that use **different memory branches** to demonstrate the branching capability:
#
# ### Healthcare Branching Strategy:
# - **Main Branch**: Stores the supervisor's routing decisions and serves as the base conversation thread
# - **claims_agent Branch**: A separate branch for insurance, billing, and claims conversations
# - **demographics_agent Branch**: A separate branch for patient information and contact details
# - **medication_agent Branch**: A separate branch for prescription and medication conversations
#
# Each specialized healthcare agent operates on its own branch, which is automatically forked from the main conversation when first used. This allows:
#
# - Independent conversation flows for different healthcare specializations
# - Isolation of domain-specific medical context
# - Preservation of the main supervisor conversation thread
# - Compliance with healthcare data separation requirements
# - Clear audit trails for different types of patient interactions
#
# ### Healthcare Agent Roles:
# - **Supervisor Agent**: Routes patient questions to appropriate specialists
# - **Claims Agent**: Handles insurance claims, billing inquiries, and coverage questions
# - **Demographics Agent**: Manages patient demographic information and contact updates
# - **Medication Agent**: Processes prescription questions, dosage information, and medication management
#
# This architecture ensures that sensitive healthcare conversations remain properly isolated while maintaining a cohesive patient care experience.

# ### Creating Agents with Branched Memory
#
# Next, we'll define system prompts and create agents that use different memory branches. Notice how we use the same `actor_id` and `session_id` but different `branch_name` values to create isolated conversation contexts:


# System prompt for the healthcare supervisor
SUPERVISOR_PROMPT = """You are a healthcare supervisor agent. Route patient questions to:
    - Claims Agent: for insurance, billing, claims questions
    - Demographics Agent: for personal info, contact details  
    - Medication Agent: for prescriptions, medications, dosage
    
    Respond briefly and indicate which agent you're routing to."""

# System prompt for the claims specialist
CLAIMS_PROMPT = """You handle insurance claims. Use the get_patient_claims tool to fetch 
    current claim data from HealthLake. Answer questions about claims, billing, and coverage."""

# System prompt for the demographics specialist
DEMOGRAPHICS_PROMPT = """You handle patient demographics. Use the get_patient_demographics tool to 
    fetch current patient data from HealthLake. Answer questions about contact details and personal information."""

# System prompt for the medication specialist
MEDICATION_PROMPT = """You handle medications. Use the get_patient_medications tool to fetch 
    current medication data from HealthLake. Answer questions about prescriptions and dosages."""


# ## Step 6:  Setting Up Healthcare Tools and Memory Hooks
#
# First, we'll create the HealthLake data tools and memory hooks for our specialized healthcare agents. Each agent gets its own memory hook configured with a specific branch name:
# - Claims agent uses the `claims_agent` branch
# - Demographics agent uses the `demographics_agent` branch
# - Medication agent uses the `medication_agent` branch
# - Supervisor agent uses the `main` branch
#
# When these healthcare agents are invoked:
# 1. The hook checks if the branch exists
# 2. If not, it forks a new branch from the main conversation
# 3. The agent's healthcare conversation is stored on its dedicated branch
# 4. Each agent maintains isolated context for patient data privacy
# 5. HealthLake data tools provide real-time patient information access
# 6. Healthcare compliance is maintained through proper data isolation
#
# **HealthLake Data Tools:**
# - `get_patient_claims` - Retrieves insurance claims and billing information
# - `get_patient_demographics` - Fetches patient contact and demographic data
# - `get_patient_medications` - Gets current prescriptions and medication data
#
# **Memory Branch Structure:**
# - Each agent operates independently with its own memory context
# - Patient conversations are properly isolated by healthcare domain
# - Supervisor coordinates between agents while maintaining separation
#


# Initialize healthcare memory hooks as None
supervisor_hooks = None
claims_hooks = None
demographics_hooks = None
medication_hooks = None


@tool
def get_patient_claims(patient_id: str = PATIENT_ID) -> dict:
    """Get patient insurance claims from HealthLake"""
    return query_healthlake("Claim", {"patient": patient_id})


@tool
def get_patient_medications(patient_id: str = PATIENT_ID) -> dict:
    """Get patient medications from HealthLake"""
    return query_healthlake("MedicationRequest", {"patient": patient_id})


@tool
def get_patient_demographics(patient_id: str = PATIENT_ID) -> dict:
    """Get patient demographic information from HealthLake"""
    return query_healthlake("Patient", resource_id=patient_id)


# Create memory hooks for each agent
supervisor_hooks = HealthcareMemoryHooks(memory_id, region, "main")
claims_hooks = HealthcareMemoryHooks(memory_id, region, "claims_agent")
demographics_hooks = HealthcareMemoryHooks(memory_id, region, "demographics_agent")
medication_hooks = HealthcareMemoryHooks(memory_id, region, "medication_agent")

print("✅ HealthLake tools and memory hooks created")


# ### Creating Healthcare Agents
#
# Now we'll create the healthcare agents using the tools and memory hooks we set up:
#
# - **Supervisor Agent**: Routes questions (main branch)
# - **Claims Agent**: Insurance & billing (claims_agent branch)
# - **Demographics Agent**: Patient info (demographics_agent branch)
# - **Medication Agent**: Prescriptions (medication_agent branch)
#
# Each agent gets its specialized system prompt, relevant tools, and memory hooks for isolated conversations.


# Create specialized healthcare agents with memory branching
supervisor = Agent(
    model=MODEL_ID,
    system_prompt=SUPERVISOR_PROMPT,
    hooks=[supervisor_hooks],
    state={"actor_id": PATIENT_ID, "session_id": SESSION_ID},
)

claims_agent = Agent(
    model=MODEL_ID,
    system_prompt=CLAIMS_PROMPT,
    tools=[get_patient_claims],
    hooks=[claims_hooks],
    state={"actor_id": PATIENT_ID, "session_id": SESSION_ID},
)

demographics_agent = Agent(
    model=MODEL_ID,
    system_prompt=DEMOGRAPHICS_PROMPT,
    tools=[get_patient_demographics],
    hooks=[demographics_hooks],
    state={"actor_id": PATIENT_ID, "session_id": SESSION_ID},
)

medication_agent = Agent(
    model=MODEL_ID,
    system_prompt=MEDICATION_PROMPT,
    tools=[get_patient_medications],
    hooks=[medication_hooks],
    state={"actor_id": PATIENT_ID, "session_id": SESSION_ID},
)

print("✅ Healthcare agents created with HealthLake tools and memory branching")


# #### Your Healthcare Multi-Agent System with Episodic Memory is ready !!
#
# ## Let's test the Healthcare Assistant.
#
# Let's test our healthcare multi-agent system with a patient care scenario:
#
# **Sample questions to try:**
# - "What's the status of my insurance claims?"
# - "Can you update my contact information?"
# - "What medications am I currently taking?"
# - "Do I have any pending billing issues?"
# - "What's my current address on file?"
# - "Are there any drug interactions with my prescriptions?"
# - "How much do I owe for my recent visit?"
# - "Can you tell me about my coverage details?"


# Demo queries for non-interactive testing
demo_queries = [
    "What's the status of my insurance claims?",
    "What medications am I currently taking?",
]

print("Healthcare Assistant - Running demo queries\n")
for user_input in demo_queries:
    print(f"You: {user_input}")

    # Supervisor handles routing
    routing = str(supervisor(user_input))
    print(f"\nSupervisor: {routing}")

    # Route to appropriate agent based on supervisor's decision
    if "claims agent" in routing.lower():
        response = str(claims_agent(user_input))
        print(f"\nClaims Agent: {response}\n")
    elif "demographics agent" in routing.lower():
        response = str(demographics_agent(user_input))
        print(f"\nDemographics Agent: {response}\n")
    elif "medication agent" in routing.lower():
        response = str(medication_agent(user_input))
        print(f"\nMedication Agent: {response}\n")


# ## Inspecting Healthcare Memory Branches
#
# One of the key advantages of AgentCore Memory Branching is the ability to inspect each healthcare agent's conversation history independently. This is crucial for:
#
# **Debugging Healthcare Multi-Agent Systems:**
# - See exactly what each healthcare agent discussed with the patient
# - Identify which agent handled which medical inquiry
# - Trace the flow of patient information through the healthcare system
#
# **Understanding Healthcare Agent Coordination:**
# - Verify that agents maintained separate medical contexts
# - Confirm no patient data conflicts occurred during concurrent execution
# - Audit the timeline of healthcare agent interactions
# - Ensure HIPAA compliance through isolated conversation tracking
#
# **Healthcare-Specific Benefits:**
# - **Claims Agent**: Track all insurance and billing discussions
# - **Demographics Agent**: Monitor patient information updates and changes
# - **Medication Agent**: Audit all prescription and medication conversations
# - **Supervisor Agent**: Review routing decisions and patient triage
#
# Let's explore the healthcare branches that were created during our patient consultation:


print("\n=== Viewing Healthcare Memory Branches ===")

if claims_hooks or demographics_hooks or medication_hooks:
    # Get any memory session to list branches (they all point to the same session)
    hook = claims_hooks if claims_hooks else (demographics_hooks if demographics_hooks else medication_hooks)
    if hook:
        memory_session = hook.get_session(actor_id=PATIENT_ID, session_id=SESSION_ID)

        # List all branches in the session
        branches = memory_session.list_branches()
        print(f"\n📊 Session has {len(branches)} branches total:")
        for branch in branches:
            events = memory_session.list_events(branch_name=branch.name)
            print(f"  - Branch: {branch.name}")
            print(f"    └─ Events: {len(events)}")
            print(f"    └─ Created: {branch.created}")

            # Print recent conversations from this branch
            if events:
                print("    └─ Recent conversations:")
                for event in events[-100:]:  # Show last 10 events
                    for payload in event.payload:
                        if "conversational" in payload:
                            role = payload["conversational"]["role"]
                            text = payload["conversational"]["content"]["text"]
                            print(f"        {role}: {text[:500]}...")

        print("\n💡 Each branch represents a different agent's memory:")
        print("  • 'main' = Supervisor agent conversations")
        print("  • 'claims_agent' = Claims assistant conversations")
        print("  • 'demographics_agent' = Demographics assistant conversations")
        print("  • 'medication_agent' = Medication assistant conversations")
else:
    print("No memory hooks found. Make sure to run the cell that creates the hooks first.")


# ## Validating Long-Term Healthcare Memory: Episodes and Reflections
#
# Let's examine how our healthcare system transformed short-term conversations into structured long-term patient insights using the **EpisodicStrategy**.
#
# ### Healthcare Episodes
# Episodes capture consolidated patient interactions with:
# - **Clinical Context**: Patient care goals and outcomes
# - **Agent Coordination**: How supervisor and specialist agents worked together
# - **Data Integration**: HealthLake information retrieval and presentation
#
# ### Patient Reflections
# Reflections provide cross-episode insights about:
# - **Care Patterns**: Patient communication preferences and recurring needs
# - **Effective Strategies**: What approaches work best for this patient
# - **Optimization Opportunities**: Areas for improving future care
#
# Episodes and reflections are processed asynchronously.


print("=== HEALTHCARE LONG-TERM MEMORY: EPISODES ===")
actor_id = PATIENT_ID
session_id = SESSION_ID
# Define namespace for healthcare episodes
episode_namespace = f"healthcare/{actor_id}/{session_id}"
print(f"\n📋 Episode namespace: {episode_namespace}")  # codeql[py/clear-text-logging-sensitive-data]

try:
    print("\n📖 HEALTHCARE EPISODES (Session-specific patient interactions)")
    episodes = client.retrieve_memories(
        memory_id=memory_id,
        namespace=episode_namespace,
        query="patient healthcare interactions",
        top_k=10,
    )
    print(f"Found {len(episodes)} healthcare episode(s)")

    for i, episode in enumerate(episodes, 1):
        print(f"\n🏥 Healthcare Episode {i}:")
        content = episode.get("content", {})
        if isinstance(content, dict):
            text = content.get("text", "")
            # Show more content for healthcare context
            print(f"   {text[:300]}..." if len(text) > 300 else f"   {text}")
        print(f"   Score: {episode.get('score', 'N/A')}")

    if not episodes:
        print("   No episodes found yet. Episodic processing happens asynchronously.")

except Exception as e:
    print(f"❌ Error retrieving healthcare episodes: {e}")

print("\n💡 TIP: Use the memory browser for interactive healthcare memory visualization")
print("   Episodes show individual patient consultation summaries")
print("\n⏱️  NOTE: Episode generation takes a few minutes after conversations")
print("   Check back later if no episodes appear immediately")


# ## Summary
#
# ### What We Built:
# 1. **Supervisor Agent** - Orchestrates on main branch
# 2. **Claims Agent** - Handles insurance on claims_agent branch
# 3. **Demographics Agent** - Manages patient info on demographics_agent branch
# 4. **Medication Agent** - Handles medications on medication_agent branch
#
# ### Memory Architecture:
# - **Short-term**: Each agent has isolated branch
# - **Episodes**: Stored per session `healthcare/{actorId}/{sessionId}/`
# - **Reflections**: Shared across all sessions `healthcare/{actorId}/`
#
# ### Benefits:
# - ✅ Agents don't interfere with each other's conversations
# - ✅ All agents contribute to same session's long-term memory
# - ✅ Learned patterns (reflections) shared across all patient sessions
# - ✅ Complete conversation history maintained per agent

# ## Cleanup (Optional)
#
# Run this cell to delete the memory and IAM role created in this tutorial.


# import boto3

# print("Cleanup Options:")
# delete_memory = input("Delete memory? (yes/no): ").strip().lower()
# if delete_memory == 'yes':
#   try:
#       print(f"Deleting memory: {memory_id}")
#       client.delete_memory_and_wait(memory_id=memory_id)
#       print("Memory deleted")
#   except Exception as e:
#       print(f"Error deleting memory: {e}")
# else:
#   print(f"Memory preserved: {memory_id}")

# delete_healthlake = input("Delete HealthLake datastore? (yes/no): ").strip().lower()
# if delete_healthlake == 'yes':
#     try:
#         print(f"Deleting HealthLake datastore: {DATASTORE_ID}")
#         healthlake_client.delete_fhir_datastore(DatastoreId=DATASTORE_ID)
#         print("HealthLake datastore deletion initiated")
#     except Exception as e:
#         print(f"Error deleting HealthLake datastore: {e}")
# else:
#     print(f"HealthLake datastore preserved: {DATASTORE_ID}")

# print("Cleanup complete")
