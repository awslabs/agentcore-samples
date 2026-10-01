#!/usr/bin/env python3
"""
Interactive Test Script for AgentCore Gateway Ticket Agent
Invokes AgentCore Runtime directly via boto3
"""

import boto3
import json
import sys

# ANSI color codes
class Colors:
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def invoke_agent(client, runtime_arn, user_id, session_id, message):
    """Invoke agent with message"""
    payload = {
        "input": message,
        "user_id": user_id,
        "session_id": session_id
    }
    
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=session_id,
            payload=json.dumps(payload)
        )
        
        response_body = response['response'].read().decode('utf-8')
        result = json.loads(response_body)
        
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}

def print_response(result):
    """Print formatted response"""
    print(f"\n{Colors.BOLD}Agent:{Colors.RESET} ", end="")
    
    if result.get('status') == 'success':
        print(f"{Colors.GREEN}{result['response']}{Colors.RESET}")
    elif result.get('status') == 'error':
        print(f"{Colors.RED}Error: {result.get('error')}{Colors.RESET}")
    else:
        print(json.dumps(result, indent=2))

def run_chatbot():
    """Main chatbot loop"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}AgentCore Gateway Ticket Agent - Interactive Tester{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
    
    # Initialize client
    try:
        client = boto3.client('bedrock-agentcore')
        session = boto3.Session()
        region = session.region_name or 'us-east-1'
        print(f"{Colors.GREEN}Connected to {region}{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}Failed to connect: {e}{Colors.RESET}")
        sys.exit(1)
    
    # Get runtime ARN
    print(f"\n{Colors.BOLD}Runtime Configuration{Colors.RESET}")
    print(f"{Colors.BOLD}{'-'*60}{Colors.RESET}")
    runtime_arn = input("Enter runtime ARN (from CDK output): ").strip()
    
    if not runtime_arn:
        print(f"{Colors.RED}Runtime ARN is required{Colors.RESET}")
        sys.exit(1)
    
    print(f"{Colors.GREEN}Using runtime: {runtime_arn}{Colors.RESET}")
    
    # Get user_id and session_id
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}User & Session Configuration{Colors.RESET}")
    print(f"{Colors.BOLD}{'-'*60}{Colors.RESET}")
    
    user_id = input("Enter user_id (e.g., user123): ").strip()
    if not user_id:
        print(f"{Colors.RED}user_id is required{Colors.RESET}")
        sys.exit(1)
    
    session_id = input("Enter session_id (e.g., session-user123-demo-testing-0001): ").strip()
    if not session_id:
        print(f"{Colors.RED}session_id is required (min 33 characters){Colors.RESET}")
        sys.exit(1)
    
    if len(session_id) < 33:
        print(f"{Colors.YELLOW}Warning: session_id should be at least 33 characters{Colors.RESET}")
    
    print(f"\n{Colors.GREEN}Active: user_id={user_id}, session_id={session_id}{Colors.RESET}")
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}Chat Started{Colors.RESET} (type 'exit' to quit, 'switch' to change user/session)")
    print(f"{Colors.BOLD}{'-'*60}{Colors.RESET}\n")
    
    while True:
        # Get user input
        user_input = input(f"\n{Colors.BOLD}You:{Colors.RESET} ").strip()
        
        # Check for commands
        if user_input.lower() in ["exit", "quit", "bye"]:
            print(f"\n{Colors.YELLOW}Goodbye!{Colors.RESET}")
            break
        
        if user_input.lower() == "switch":
            print(f"\n{Colors.BOLD}Switch User/Session{Colors.RESET}")
            print(f"{'-'*60}")
            
            new_user = input(f"Enter new user_id (current: {user_id}): ").strip()
            if new_user:
                user_id = new_user
            
            new_session = input(f"Enter new session_id (current: {session_id}): ").strip()
            if new_session:
                session_id = new_session
                if len(session_id) < 33:
                    print(f"{Colors.YELLOW}Warning: session_id should be at least 33 characters{Colors.RESET}")
            
            print(f"\n{Colors.GREEN}Switched to: user_id={user_id}, session_id={session_id}{Colors.RESET}")
            continue
        
        if not user_input:
            continue
        
        # Invoke agent
        print(f"\n{Colors.BLUE}Sending request...{Colors.RESET}")
        result = invoke_agent(client, runtime_arn, user_id, session_id, user_input)
        
        print_response(result)

if __name__ == "__main__":
    try:
        run_chatbot()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Interrupted by user. Goodbye!{Colors.RESET}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.RED}Unexpected error: {e}{Colors.RESET}")
        sys.exit(1)
