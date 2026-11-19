# Creating a Private Enterprise Agent Registry Using AWS Agent Registry and GoDaddy Agent Name Service (ANS)

## Overview

This tutorial demonstrates integrating **AWS Agent Registry** (private/enterprise agent catalog) with **GoDaddy's Agent Name Service (ANS)** (public DNS-based agent discovery and trust verification). The integration enables enterprises to:

1. **Register** agents in AWS Agent Registry with GoDaddy ANS public identity metadata
2. **Discover** agents via AWS Registry semantic search with trust scores visible
3. **Verify** agent identity via GoDaddy ANS (DNS + Transparency Log + PKI fingerprint matching)
4. **Connect** to verified agents via A2A protocol
5. **Sync** GoDaddy ANS metadata automatically via Lambda + EventBridge (every 5 minutes)

### Why Integrate GoDaddy ANS with AWS Agent Registry?

- **AWS Agent Registry** = private/enterprise discovery with governance (approval workflows, semantic search, access control)
- **GoDaddy ANS** = public discovery with cryptographic trust verification (domain validation, Transparency Log, cert fingerprints)
- Together: enterprise agents get governed internal discovery AND public trust verification

### Tutorial Details

| Information | Details |
|:---|:---|
| Tutorial type | Registry Integration with External Trust Service |
| AgentCore components | AWS Agent Registry |
| External services | GoDaddy ANS (DNS, Transparency Log), A2A Agent |
| Inbound Auth | IAM |
| LLM model | N/A (no LLM required) |
| Tutorial components | AWS Agent Registry, Lambda, EventBridge, CloudWatch Logs |
| Tutorial vertical | Cross-vertical (Agent Identity & Trust) |
| Example complexity | Advanced |
| SDK used | boto3, dnspython, requests |

## Architecture

![Architecture Overview](diagrams/1-architecture-overview.drawio.png)

## Sequence Diagrams

### Flow 1: Publishing — Register Agent with GoDaddy ANS Metadata

The publisher registers an agent in GoDaddy ANS (public), fetches the metadata, and creates a governed record in AWS Agent Registry (private).

```mermaid
sequenceDiagram
    participant Publisher
    participant ANS as GoDaddy ANS<br/>(Public)
    participant DNS as DNS<br/>(_ans-badge TXT)
    participant TL as Transparency Log
    participant Registry as AWS Agent Registry<br/>(Private)

    Note over Publisher,Registry: Agent Publication Flow

    Publisher->>ANS: Register agent (domain, certs)
    ANS->>DNS: Create _ans-badge TXT record
    ANS->>TL: Seal registration event (certs, attestations)
    ANS-->>Publisher: ANS Name: ans://v1.0.0.agent.example.com

    Publisher->>DNS: Resolve _ans-badge.agent.example.com TXT
    DNS-->>Publisher: url=https://transparency.ans.godaddy.com/v1/agents/...

    Publisher->>TL: GET badge URL
    TL-->>Publisher: {status: ACTIVE, certs, trustVector, attestations}

    Publisher->>Publisher: Build A2A Agent Card with ANS extension<br/>(capabilities.extensions[0].params = ANS metadata)

    Publisher->>Registry: create_registry_record(A2A, agentCard)
    Note over Registry: Validates:<br/>1. Liveness (DNS + TL + TLS)<br/>2. Uniqueness (ANS name)<br/>3. Version alignment<br/>4. Host alignment
    Registry-->>Publisher: Record created (DRAFT)

    Publisher->>Registry: submit_for_approval(recordId)
    Registry-->>Publisher: Status: PENDING_APPROVAL

    Publisher->>Registry: approve_record(recordId)
    Registry-->>Publisher: Status: APPROVED ✅
```

### Flow 2: Discovery + Verification + Connection (Consumer)

The consumer discovers an agent via AWS Registry semantic search, verifies it via GoDaddy ANS, and connects via A2A.

```mermaid
sequenceDiagram
    participant Consumer
    participant Registry as AWS Agent Registry<br/>(Private)
    participant DNS as DNS<br/>(_ans-badge TXT)
    participant TL as Transparency Log
    participant Agent as A2A Agent<br/>(Provider)

    Note over Consumer,Agent: Consumer Discovery + Verification + Call Flow

    Consumer->>Registry: search_registry_records("customer support")
    Registry-->>Consumer: [{name, descriptorType: A2A, agentCard with ANS extension}]

    Consumer->>Consumer: Extract ANS name from<br/>capabilities.extensions[].params.ansName

    Note over Consumer,TL: ANS Verification (4 checks)

    Consumer->>DNS: Resolve _ans-badge.{host} TXT
    DNS-->>Consumer: url=https://transparency.ans.godaddy.com/v1/agents/...

    Consumer->>TL: GET badge URL
    TL-->>Consumer: {status: ACTIVE, serverCert.fingerprint, identityCert}

    Consumer->>Agent: TLS handshake (port 443)
    Agent-->>Consumer: Server certificate (DER)

    Consumer->>Consumer: SHA256(live cert) == TL serverCert.fingerprint?
    Note over Consumer: ✅ MATCH — Agent is who they claim to be

    Consumer->>Consumer: Compute Trust Vector<br/>(integrity, identity, solvency, behavior, safety)

    Note over Consumer,Agent: A2A Connection (JSON-RPC 2.0)

    Consumer->>Agent: POST /a2a<br/>{"method": "message/send", "params": {message}}
    Agent-->>Consumer: {"result": {status: "completed", artifacts: [{text: "..."}]}}
```

### Flow 3: GoDaddy ANS Metadata Sync (Lambda + EventBridge)

The sync poller keeps AWS Registry records up-to-date with the latest GoDaddy ANS state (status, cert fingerprints).

```mermaid
sequenceDiagram
    participant EB as EventBridge<br/>(every 5 min)
    participant Lambda as Sync Lambda
    participant Registry as AWS Agent Registry
    participant DNS as DNS
    participant TL as Transparency Log

    Note over EB,TL: Automated ANS Sync Flow

    EB->>Lambda: Trigger (rate: 5 minutes)

    Lambda->>Registry: list_registry_records(registryId)
    Registry-->>Lambda: [record1, record2, record3, ...]

    loop For each record with ANS data
        Lambda->>Lambda: Extract ANS name from record<br/>(A2A extension / MCP x-ans-* / CUSTOM ans.*)

        Lambda->>DNS: Resolve _ans-badge.{host} TXT
        DNS-->>Lambda: badge URL

        Lambda->>TL: GET badge URL
        TL-->>Lambda: {status, serverCert.fingerprint, identityCert.fingerprint}

        Lambda->>Lambda: Compare source-of-truth fields only:<br/>status, serverCert.fingerprint, identityCert.fingerprint

        alt No changes detected
            Lambda->>Lambda: Skip (no DRAFT reset)
        else Changes detected (cert rotation, revocation, etc.)
            Lambda->>Registry: update_registry_record(recordId, updated descriptors)
            Note over Registry: Record status → DRAFT<br/>(requires re-approval)
        end
    end

    Lambda-->>EB: {checked: N, updated: M}
```

### Flow 4: Liveness Validation (Pre-Registration)

Before any record is created or updated, the system validates the GoDaddy ANS name is live and valid.

```mermaid
sequenceDiagram
    participant Client as Registry Client
    participant DNS as DNS
    participant TL as Transparency Log
    participant Agent as Agent Host

    Note over Client,Agent: Liveness Validation (4 checks)

    Client->>Client: Check 1: ANS name format<br/>ans://v{semver}.{host} ✓

    Client->>DNS: Check 2: Resolve _ans-badge.{host} TXT
    alt Record exists
        DNS-->>Client: ✅ Found badge URL
    else NXDOMAIN
        DNS-->>Client: ❌ Agent not registered in ANS
        Client->>Client: REJECT — AnsLivenessError
    end

    Client->>TL: Check 3: GET badge URL
    alt Status is ACTIVE/WARNING/DEPRECATED
        TL-->>Client: ✅ Status: ACTIVE
    else Status is REVOKED/EXPIRED
        TL-->>Client: ❌ Status: REVOKED
        Client->>Client: REJECT — AnsLivenessError
    end

    Client->>Agent: Check 4: TLS connect to {host}:443
    alt Connection succeeds
        Agent-->>Client: ✅ Valid TLS certificate
    else Connection fails
        Agent-->>Client: ❌ Unreachable
        Client->>Client: REJECT — AnsLivenessError
    end

    Client->>Client: All 4 checks passed → LIVE ✅
```

### Flow 5: PKI Fingerprint Anti-Impersonation

The fingerprint matching mechanism prevents DNS hijacking and man-in-the-middle attacks.

```mermaid
sequenceDiagram
    participant Consumer
    participant DNS as DNS<br/>(could be hijacked)
    participant TL as Transparency Log<br/>(immutable, append-only)
    participant RealAgent as Real Agent<br/>(legitimate)
    participant Imposter as Imposter<br/>(attacker)

    Note over Consumer,Imposter: Scenario: Attacker hijacks DNS to redirect traffic

    Consumer->>DNS: Resolve agent.example.com
    Note over DNS: ⚠️ DNS hijacked!<br/>Points to imposter IP

    Consumer->>TL: GET badge (independent of DNS)
    TL-->>Consumer: serverCert.fingerprint = SHA256:abc123...<br/>(sealed at registration time, immutable)

    Consumer->>Imposter: TLS handshake
    Imposter-->>Consumer: Imposter's certificate (different key pair)

    Consumer->>Consumer: SHA256(imposter cert) = SHA256:xyz789...<br/>≠ TL fingerprint SHA256:abc123...

    Note over Consumer: ❌ MISMATCH DETECTED<br/>Imposter cannot forge the original cert's private key

    Consumer->>Consumer: REJECT connection — possible impersonation

    Note over Consumer,RealAgent: Normal flow (no attack)
    Consumer->>RealAgent: TLS handshake (correct IP)
    RealAgent-->>Consumer: Real certificate
    Consumer->>Consumer: SHA256(real cert) = SHA256:abc123...<br/>== TL fingerprint SHA256:abc123...
    Note over Consumer: ✅ MATCH — Safe to proceed
```

### Flow 6: MCP Server Registration + Discovery with GoDaddy ANS

MCP servers use `x-ans-*` custom fields in the server schema instead of A2A extensions.

```mermaid
sequenceDiagram
    participant Publisher
    participant ANS as GoDaddy ANS
    participant DNS as DNS
    participant TL as Transparency Log
    participant Registry as AWS Agent Registry
    participant Consumer
    participant MCP as MCP Server

    Note over Publisher,MCP: MCP Server with ANS Trust Metadata

    rect rgb(230, 245, 255)
        Note over Publisher,Registry: Phase 1: Publish MCP Server with ANS
        Publisher->>ANS: Register MCP server in ANS
        ANS-->>Publisher: ANS Name: ans://v1.0.0.mcp-server.example.com

        Publisher->>DNS: Resolve _ans-badge.mcp-server.example.com
        DNS-->>Publisher: badge URL

        Publisher->>TL: GET badge
        TL-->>Publisher: {status: ACTIVE, certs, trust}

        Publisher->>Publisher: Build MCP server schema with x-ans-* fields:<br/>x-ans-name, x-ans-status, x-ans-trust-profile,<br/>x-ans-server-cert-fingerprint, x-ans-liveness-valid

        Publisher->>Registry: create_registry_record(MCP, serverSchema)
        Note over Registry: Validates liveness + uniqueness<br/>via x-ans-name field
        Registry-->>Publisher: Record created (DRAFT → APPROVED)
    end

    rect rgb(230, 255, 230)
        Note over Consumer,MCP: Phase 2: Consumer Discovers + Verifies + Connects
        Consumer->>Registry: search_registry_records("data processing")
        Registry-->>Consumer: MCP record with x-ans-* fields

        Consumer->>Consumer: Extract x-ans-name from server schema

        Consumer->>DNS: Resolve _ans-badge.mcp-server.example.com
        DNS-->>Consumer: badge URL

        Consumer->>TL: GET badge
        TL-->>Consumer: {status: ACTIVE, serverCert.fingerprint}

        Consumer->>MCP: TLS handshake
        MCP-->>Consumer: Server certificate

        Consumer->>Consumer: SHA256(live cert) == x-ans-server-cert-fingerprint?
        Note over Consumer: ✅ MATCH — MCP server verified

        Consumer->>MCP: MCP protocol (JSON-RPC tools/list, tools/call)
        MCP-->>Consumer: Tool results
    end
```

### Flow 7: ACP (Commerce Agent) via CUSTOM Type

Agents with commerce credentials (Visa TAP, Mastercard Agent Pay) use CUSTOM type with an `ans.*` nested object.

```mermaid
sequenceDiagram
    participant Publisher
    participant ANS as GoDaddy ANS
    participant PayNet as Payment Network<br/>(Visa TAP / Mastercard)
    participant Registry as AWS Agent Registry
    participant Consumer
    participant Agent as Commerce Agent

    Note over Publisher,Agent: ACP Agent with ANS + Commerce Credentials

    Publisher->>ANS: Register commerce agent in ANS
    ANS-->>Publisher: ANS Name + trust metadata

    Publisher->>PayNet: Register for Visa TAP / Mastercard Agent Pay
    PayNet-->>Publisher: credentialId, network capabilities

    Publisher->>Publisher: Build CUSTOM record:<br/>{protocolType: "ACP",<br/> ans: {ansName, status, trustVector},<br/> commerceCredentials: [{network, credentialId}]}

    Publisher->>Registry: create_registry_record(CUSTOM, inlineContent)
    Note over Registry: Validates ANS liveness via ans.ansName
    Registry-->>Publisher: Record APPROVED

    Consumer->>Registry: search("commerce payment agent")
    Registry-->>Consumer: CUSTOM record with ans.* + commerceCredentials

    Consumer->>Consumer: Extract ans.ansName
    Consumer->>ANS: Verify via DNS + TL + PKI fingerprint
    ANS-->>Consumer: ✅ Verified

    Consumer->>Consumer: Check commerceCredentials:<br/>Visa TAP: REGISTERED ✅<br/>Forter TACP: ACTIVE ✅

    Consumer->>Agent: A2A message/send (purchase request)
    Agent-->>Consumer: {status: completed, transaction confirmation}
```

### Flow 8: End-to-End Overview (All Actors)

Complete lifecycle showing publication, sync, discovery, verification, and connection.

```mermaid
sequenceDiagram
    participant Publisher
    participant ANS as GoDaddy ANS
    participant Registry as AWS Agent Registry
    participant Lambda as Sync Lambda<br/>(every 5 min)
    participant Consumer
    participant Agent as A2A Agent

    Note over Publisher,Agent: Complete Lifecycle

    rect rgb(230, 245, 255)
        Note over Publisher,Registry: Phase 1: Publication
        Publisher->>ANS: Register agent in ANS
        ANS-->>Publisher: ANS Name + badge URL
        Publisher->>Publisher: Fetch ANS metadata + build A2A card
        Publisher->>Registry: Create record (A2A + ANS extension)
        Registry->>Registry: Validate (liveness, uniqueness, version, host)
        Publisher->>Registry: Submit + Approve
    end

    rect rgb(255, 245, 230)
        Note over Lambda,Registry: Phase 2: Continuous Sync
        Lambda->>Registry: List all records
        Lambda->>ANS: Fetch fresh metadata for each ANS name
        Lambda->>Lambda: Compare source-of-truth fields
        alt Changed
            Lambda->>Registry: Update record
        end
    end

    rect rgb(230, 255, 230)
        Note over Consumer,Agent: Phase 3: Discovery + Verification + Call
        Consumer->>Registry: Semantic search("customer support")
        Registry-->>Consumer: Record with ANS extension
        Consumer->>Consumer: Extract ANS name
        Consumer->>ANS: DNS + TL badge + TLS fingerprint check
        ANS-->>Consumer: Verified ✅ (fingerprint MATCH)
        Consumer->>Agent: A2A JSON-RPC message/send
        Agent-->>Consumer: Response with artifacts
    end
```

## Key Features

- **ANS Extension in A2A Agent Card** — GoDaddy ANS metadata stored as a standard A2A protocol extension (`capabilities.extensions`), passing schema validation
- **Liveness validation** — Before registration, verifies the GoDaddy ANS name is live (DNS resolvable, TL badge reachable, TLS connectable)
- **Duplicate detection** — GoDaddy ANS name is treated as a primary key; rejects duplicate registrations
- **Version alignment** — GoDaddy ANS version (from `ans://v1.0.0.host`) must match the registry record version
- **Host alignment** — Agent card URL host must match the GoDaddy ANS host
- **Source-of-truth sync** — Lambda only updates records when GoDaddy ANS status or cert fingerprints actually change (avoids false DRAFT resets)
- **Multi-format support** — Sync works for A2A (extensions), MCP (x-ans-* fields), and CUSTOM/ACP records
- **Trust Vector** — 5 dimensions (integrity, identity, solvency, behavior, safety) computed from live GoDaddy ANS signals

## Prerequisites

- AWS account with Amazon Bedrock AgentCore access (Agent Registry)
- Python 3.10+
- boto3 >= 1.42.87
- Internet access (for DNS queries and HTTPS to GoDaddy ANS Transparency Log)

### Required IAM Permissions

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BedrockAgentCoreAccess",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:*",
                "bedrock-agentcore-control:*"
            ],
            "Resource": "*"
        },
        {
            "Sid": "LambdaManagement",
            "Effect": "Allow",
            "Action": [
                "lambda:CreateFunction",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:InvokeFunction",
                "lambda:AddPermission",
                "lambda:DeleteFunction"
            ],
            "Resource": "arn:aws:lambda:*:*:function:ans-registry-sync-poller"
        },
        {
            "Sid": "EventBridgeManagement",
            "Effect": "Allow",
            "Action": [
                "events:PutRule",
                "events:PutTargets",
                "events:DeleteRule",
                "events:RemoveTargets"
            ],
            "Resource": "arn:aws:events:*:*:rule/ans-registry-sync-*"
        },
        {
            "Sid": "IAMRoleManagement",
            "Effect": "Allow",
            "Action": [
                "iam:CreateRole",
                "iam:PutRolePolicy",
                "iam:PassRole"
            ],
            "Resource": "arn:aws:iam::*:role/ans-registry-sync-poller-role"
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogStreams",
                "logs:GetLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:log-group:/aws/lambda/ans-registry-sync-poller:*"
        },
        {
            "Sid": "STSAccess",
            "Effect": "Allow",
            "Action": "sts:GetCallerIdentity",
            "Resource": "*"
        }
    ]
}
```

## Getting Started

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 2: Configure AWS Credentials

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

### Step 3: Run the End-to-End Publishing Demo

```bash
python demo.py --region us-east-1
```

This will:
1. Create a registry (`ans-integration-demo`) with auto-approval
2. Fetch live ANS metadata for the GoDaddy demo agent
3. Build an A2A agent card with the ANS extension (including liveness + trust vector)
4. Create the record (validates: liveness, uniqueness, version alignment, host alignment)
5. Submit and approve the record
6. Search for it via semantic search
7. Print the full record with ANS extension

### Step 4: Run the Consumer Test (Discover → Verify → Call)

```bash
python discover_verify_call.py --registry-id <your-registry-id> --query "customer support"
```

This demonstrates the **runtime consumer flow**:
1. **DISCOVER** — Semantic search in AWS Agent Registry for an agent matching the query
2. **EXTRACT** — Pull the GoDaddy ANS name from the record's A2A extension (`capabilities.extensions`)
3. **VERIFY** — Full GoDaddy ANS verification: DNS resolution, Transparency Log badge, PKI fingerprint matching
4. **CALL** — Send an A2A JSON-RPC `message/send` to the verified agent and print the response

Example output:
```
██████████████████████████████████████████████████████████████████████
  AWS Agent Registry + ANS: Discover → Verify → Call
  ──────────────────────────────────────────────────────────────────
  Registry:  <your-registry-id>
  Query:     "customer support agent"
██████████████████████████████████████████████████████████████████████

  STEP 1: DISCOVER — Search AWS Agent Registry
  ✅ Found agent: godaddy-support-agent-ans

  STEP 2: EXTRACT — Pull ANS name from registry record
  ANS Name: ans://v1.0.0.support-a08c16a8-f972-472f-b95f-3debacfcb201.helpagent.club

  STEP 3: VERIFY — Validate agent via ANS
    ✅ ANS name format: version=1.0.0, host=support-...
    ✅ DNS _ans-badge TXT: Found
    ✅ Transparency Log badge: Status: ACTIVE
    ✅ TLS reachability: Connected to host:443
    ✅ MATCH — Agent is who they claim to be
    Trust Profile: UNTRUSTED (composite: 44.0)

  STEP 4: CALL — Send A2A message to verified agent
  🤖 Agent Reply: I'm here to assist you with a variety of customer service needs...

  END-TO-END SUMMARY
  1. DISCOVER:  Found 'godaddy-support-agent-ans' in AWS Registry
  2. EXTRACT:   ANS Name = ans://v1.0.0.support-...
  3. VERIFY:    Status=ACTIVE, FP=MATCH, Trust=UNTRUSTED
  4. CALL:      ✅ Success
```

### Step 5: Deploy the Sync Poller (Lambda + EventBridge)

```bash
python deploy_poller.py --registry-id <your-registry-id> --region us-east-1
```

This deploys:
- **Lambda function** (`ans-registry-sync-poller`) — checks all records with GoDaddy ANS data every 5 minutes
- **EventBridge rule** (`ans-registry-sync-every-5min`) — triggers the Lambda on schedule
- **IAM role** (`ans-registry-sync-poller-role`) — permissions for Registry + CloudWatch Logs

The poller only updates records when the GoDaddy ANS **source of truth** changes (status, cert fingerprints) — not when locally-computed trust scores differ. This prevents unnecessary DRAFT resets.

## How It Works

### GoDaddy ANS Extension in A2A Agent Card

GoDaddy ANS metadata is stored as a standard A2A protocol extension:

```json
{
  "protocolVersion": "0.3.0",
  "name": "My Agent",
  "url": "https://my-agent.example.com/a2a",
  "capabilities": {
    "extensions": [{
      "uri": "https://ans-protocol.org/ext/ans-identity/v1",
      "description": "ANS public identity, trust verification, and liveness",
      "required": false,
      "params": {
        "ansName": "ans://v1.0.0.my-agent.example.com",
        "host": "my-agent.example.com",
        "version": "1.0.0",
        "status": "ACTIVE",
        "domainValidation": "ACME-DNS-01",
        "identityCert": {"type": "X509-OV-CLIENT", "fingerprint": "SHA256:..."},
        "serverCert": {"type": "X509-DV-SERVER", "fingerprint": "SHA256:..."},
        "trustVector": {"integrity": 80, "identity": 50, "solvency": 0, "behavior": 50, "safety": 40},
        "trustComposite": 44.0,
        "trustProfile": "UNTRUSTED",
        "liveness": {
          "valid": true,
          "dnsResolvable": true,
          "tlBadgeReachable": true,
          "tlsReachable": true,
          "formatValid": true,
          "checkedAt": "2026-04-22T12:00:00Z"
        },
        "syncedAt": "2026-04-22T12:00:00Z"
      }
    }]
  }
}
```

### Validation Rules (enforced on create/update)

| Rule | What It Checks | Error |
|:---|:---|:---|
| **Liveness** | DNS _ans-badge exists, TL badge reachable + ACTIVE, TLS connectable | `AnsLivenessError` (422) |
| **Uniqueness** | No other record in the registry has the same ANS name | `AnsNameConflictError` (409) |
| **Version alignment** | ANS version matches record version | `AnsVersionMismatchError` (400) |
| **Host alignment** | Agent card URL host matches ANS host | `AnsHostMismatchError` (400) |

### Sync Poller Logic

The Lambda runs every 5 minutes and for each record with GoDaddy ANS data:

1. Extracts the GoDaddy ANS name (supports A2A extensions, MCP x-ans-* fields, CUSTOM ans.* objects)
2. Fetches the TL badge from GoDaddy's Transparency Log
3. Compares **only source-of-truth fields**: `status`, `serverCert.fingerprint`, `identityCert.fingerprint`
4. If nothing changed → skip (no DRAFT reset)
5. If something changed → update the record with fresh data

### MCP Records with GoDaddy ANS

MCP server schemas support custom fields via `x-` prefix:

```json
{
  "name": "io.example/my-mcp-server",
  "version": "1.0.0",
  "x-ans-name": "ans://v1.0.0.my-server.example.com",
  "x-ans-status": "ACTIVE",
  "x-ans-trust-profile": "TRANSACTIONAL",
  "x-ans-liveness-valid": true
}
```

### ACP (Agentic Commerce Protocol) via CUSTOM

For agents with commerce credentials (Visa TAP, Mastercard Agent Pay, Forter TACP):

```json
{
  "protocolType": "ACP",
  "protocolVersion": "0.1.0",
  "name": "Commerce Agent",
  "ans": { "ansName": "...", "status": "ACTIVE", "trustVector": {...} },
  "commerceCredentials": [
    {"network": "visa-tap", "status": "REGISTERED", "credentialId": "..."},
    {"network": "forter-tacp", "status": "ACTIVE", "credentialId": "..."}
  ]
}
```

## Cleanup

To remove all resources created by this tutorial:

```bash
# Delete the Lambda and EventBridge rule
aws lambda delete-function --function-name ans-registry-sync-poller --region us-east-1
aws events remove-targets --rule ans-registry-sync-every-5min --ids ans-sync-lambda --region us-east-1
aws events delete-rule --name ans-registry-sync-every-5min --region us-east-1
aws iam delete-role-policy --role-name ans-registry-sync-poller-role --policy-name ans-sync-permissions
aws iam delete-role --role-name ans-registry-sync-poller-role

# Delete registry records and registry via boto3
```

## Resources

- [AWS Agent Registry documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html)
- [GoDaddy ANS Registry (GitHub)](https://github.com/godaddy/ans-registry)
- [GoDaddy ANS Rust SDK](https://github.com/godaddy/ans-sdk-rust)
- [A2A Protocol Extensions](https://a2a-protocol.org/latest/topics/extensions/)
- [IETF ANS Draft](https://datatracker.ietf.org/doc/html/draft-narajala-ans-00)
- [Strands Agents SDK](https://github.com/strands-agents/sdk-python)
