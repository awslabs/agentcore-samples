# Testing Guide

Two ways to test the deployed ticket agent:

- **Method 1 (AWS Console)**: Browser-based testing using the Bedrock AgentCore Sandbox through AWS Management Console. Paste JSON payloads and view results.

- **Method 2 (Interactive Script)**: Terminal-based conversational testing using Python script. Type natural language commands and switch between users. 

---

## Method 1: AWS Console

**Location Steps:**
1. Open AWS Console
2. Search for "Amazon Bedrock AgentCore" in the search bar
3. Click on "Amazon Bedrock AgentCore" service
4. In the left navigation panel, select **Sandbox**
5. In the "Sandbox Runtime agent" dropdown, select **AgentCoreGatewayWithAuthStack_TicketAuthAgent**
6. For "Endpoint", choose **Default**
7. In the **Input** section, paste one of the test cases below
8. Click **Run**
9. View results in the **Output** panel at the bottom

### Test Cases

**1. Create Ticket**
```json
{
  "input": "Create a ticket: My laptop screen is flickering",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**2. Create Ticket with Comment**
```json
{
  "input": "Create a ticket for printer not working. Add comment: Located in Building A, Floor 3",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**3. Create Ticket as Different User**
```json
{
  "input": "Create a ticket: Network connectivity issue in conference room",
  "user_id": "user456",
  "session_id": "session-user456-demo-testing-0001"
}
```

**4. List All Tickets** (should show 2 tickets for user123 only)
```json
{
  "input": "Show me all my tickets",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**5. Get Ticket** (use actual ticket ID from step 1)
```json
{
  "input": "Get details for ticket REQ-XXXXXXXX",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**6. Update Ticket** (use ticket ID from step 1)
```json
{
  "input": "Update ticket REQ-XXXXXXXX: approve it and mark as in progress",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**7. List Filtered Tickets** (should show only 1 PENDING ticket from step 2)
```json
{
  "input": "Show me all my pending tickets",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**8. Session Persistence** (agent remembers context)
```json
{
  "input": "What was the ticket ID I just created?",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

**9. User Isolation Test** (should show only 1 ticket for user456)
```json
{
  "input": "Show me all my tickets",
  "user_id": "user456",
  "session_id": "session-user456-demo-testing-0001"
}
```

**10. Cross-User Access Prevention** (use user123's ticket ID, agent should deny access)
```json
{
  "input": "Get details for ticket REQ-XXXXXXXX",
  "user_id": "user456",
  "session_id": "session-user456-demo-testing-0001"
}
```

**11. Ticket Not Found** (use non-existent ticket ID)
```json
{
  "input": "Get details for ticket REQ-NONEEXIST",
  "user_id": "user123",
  "session_id": "session-user123-demo-testing-0001"
}
```

---

## Method 2: Interactive Script

### Features

- **Interactive chat interface**: Type natural language commands conversationally
- **User/session switching**: Use `switch` command to test different users without restarting
- **AWS config integration**: Uses default region from cdk deployment

### Test Steps

**Step 1: Get Runtime ARN**

The runtime ARN is shown in the CDK deployment output:
```bash
cdk deploy
# Look for: AgentcoreGatewayWithAuthStack.AgentRuntimeArn = 
```

**Step 2: Run Test Script**

```bash
python testing/test_agent.py
```

**Step 3: Configure Runtime**

Paste the runtime ARN when prompted.

**Step 4: Configure Initial User**

Enter:
- user_id: `user123`
- session_id: `session-user123-demo-testing-0001`

**Expected Output:**

You should see:
```
AgentCore Gateway Ticket Agent - Interactive Tester
============================================================
Connected to us-east-1

Runtime Configuration
------------------------------------------------------------
Enter runtime ARN (from CDK output): [your ARN here]

============================================================
User & Session Configuration
------------------------------------------------------------
Enter user_id (e.g., user123): user123
Enter session_id (e.g., session-user123-demo-testing-0001): session-user123-demo-testing-0001

Active: user_id=user123, session_id=session-user123-demo-testing-0001
```

Now you're ready to test! Proceed to the test cases below.

### Test Cases

**1. Create Ticket (user123)**

Input: `Create a ticket: My laptop screen is flickering`

**2. Create Ticket with Comment (user123)**

Input: `Create a ticket for printer not working. Add comment: Located in Building A, Floor 3`

**3. Switch to Different User**

Type: `switch`
- New user_id: `user456`
- New session_id: `session-user456-demo-testing-0001`

**Expected Output:**

You should see:
```
You: switch

Switch User/Session
------------------------------------------------------------
Enter new user_id (current: user123): user456
Enter new session_id (current: session-user123-demo-testing-0001): session-user456-demo-testing-0001

Switched to: user_id=user456, session_id=session-user456-demo-testing-0001
```

**4. Create Ticket as Different User (user456)**

Input: `Create a ticket: Network connectivity issue in conference room`

**5. Switch Back to user123**

Type: `switch`
- New user_id: `user123`
- New session_id: `session-user123-demo-testing-0001`

**6. List All Tickets (user123)**

Input: `Show me all my tickets`

Expected: Shows 2 tickets for user123 only

**7. Get Ticket (user123)**

Input: `Get details for ticket REQ-XXXXXXXX` (use actual ticket ID from step 1)

**8. Update Ticket (user123)**

Input: `Update ticket REQ-XXXXXXXX: approve it and mark as in progress` (use ticket ID from step 1)

**9. List Filtered Tickets (user123)**

Input: `Show me all my pending tickets`

Expected: Shows only 1 PENDING ticket from step 2

**10. Session Persistence (user123)**

Input: `What was the ticket ID I just created?`

Expected: Agent remembers context

**11. Switch to user456**

Type: `switch`
- New user_id: `user456`
- New session_id: `session-user456-demo-testing-0001`

**12. User Isolation Test (user456)**

Input: `Show me all my tickets`

Expected: Shows only 1 ticket for user456

**13. Cross-User Access Prevention (user456)**

Input: `Get details for ticket REQ-XXXXXXXX` (use user123's ticket ID)

Expected: Agent denies access

**14. Switch to user123**

Type: `switch`
- New user_id: `user123`
- New session_id: `session-user123-demo-testing-0001`

**15. Ticket Not Found (user123)**

Input: `Get details for ticket REQ-NONEXIST`

Expected: Agent reports ticket not found

**16. Exit**

Type: `exit`

---

## Debugging

### Check DynamoDB
```bash
aws dynamodb scan --table-name tickets-auth-demo
```

### Check Lambda Logs

Lambda function names include random suffixes. Use CloudWatch Logs console or:
```bash
aws logs tail /aws/lambda/[FULL_LAMBDA_NAME_WITH_SUFFIX] --follow
```

### Check Agent Logs

Agent runtime log group names include random suffixes. Use CloudWatch Logs console or:
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/[FULL_RUNTIME_NAME_WITH_SUFFIX] --follow
```

---

## Troubleshooting

- "Runtime not found": Run `cdk deploy` first
- "Access denied": Check AWS IAM permissions for invoking Bedrock AgentCore
- "Invalid length for parameter runtimeSessionId": Session ID must be at least 33 characters
