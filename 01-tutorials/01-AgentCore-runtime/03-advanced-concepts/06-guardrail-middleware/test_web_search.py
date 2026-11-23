#!/usr/bin/env python3
"""
Test script for web search functionality
"""

import os
import sys

# Set the Tavily API key
os.environ['TAVILY_API_KEY'] = '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'

try:
    from tavily import TavilyClient
    
    # Test Tavily API directly
    print("Testing Tavily API directly...")
    print("=" * 60)
    
    client = TavilyClient(api_key=os.environ['TAVILY_API_KEY'])
    
    # Search for boto3 Lambda create_function
    query = "boto3 create_function Lambda API documentation"
    print(f"Query: {query}")
    print("-" * 60)
    
    results = client.search(
        query=query,
        max_results=3,
        search_depth="advanced"
    )
    
    if results and 'results' in results:
        for i, result in enumerate(results['results'], 1):
            print(f"\nResult {i}:")
            print(f"Title: {result.get('title', 'N/A')}")
            print(f"URL: {result.get('url', 'N/A')}")
            content = result.get('content', '')
            if content:
                print(f"Content: {content[:500]}...")
            print("-" * 40)
    
    print("\n" + "=" * 60)
    print("✅ Tavily API is working correctly!")
    
    # Now test the agent
    print("\nTesting the agent with web search...")
    print("=" * 60)
    
    from simple_agent import agent
    
    response = agent("What is the boto3 API for creating a Lambda function?")
    result = response.message['content'][0]['text']
    
    print("Agent Response:")
    print(result)
    
    print("\n✅ Agent web search integration is working!")
    
except ImportError as e:
    print(f"❌ Missing required module: {e}")
    print("Please install: pip install tavily-python")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
