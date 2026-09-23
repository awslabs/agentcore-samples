import json
import os
import urllib.request
import urllib.parse


def handler(event, context):
    print("=== LANGGRAPH TOOL CALL DEBUG ===")
    print(f"Event type: {type(event)}")
    print(f"Event keys: {list(event.keys()) if isinstance(event, dict) else 'Not a dict'}")
    print(f"Full event object: {json.dumps(event, indent=2)}")
    print(f"Context: {context}")
    print("=== END DEBUG ===")

    api_key = os.environ.get("TAVILY_API_KEY")
    print(f"API key present: {bool(api_key)}")

    if not api_key:
        print("ERROR: TAVILY_API_KEY not found in environment")
        return {"error": "TAVILY_API_KEY not configured"}

    # Handle MCP tool format - try multiple parameter names
    print(f"Processing event: {event}")
    if isinstance(event, dict):
        # Try common parameter names
        query = event.get("query") or event.get("value") or event.get("search_query")
        if query:
            print(f"Found query parameter: {query}")
        else:
            # Fallback to string representation
            query = str(event)
            print(f"Fallback to string: {query}")
    elif isinstance(event, str):
        query = event
        print(f"Event is string: {query}")
    else:
        query = str(event)
        print(f"Fallback extraction: {query}")

    print(f"Final extracted query: {query}")

    if not query:
        print("ERROR: No query provided")
        return {"error": "Query parameter is required"}

    url = "https://api.tavily.com/search"
    data = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
    }

    print(f"Making request to Tavily API with query: {query}")

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req) as response:
            print(f"Tavily API response status: {response.status}")
            result = json.loads(response.read().decode("utf-8"))
            print(f"Tavily API response: {json.dumps(result)[:500]}...")
            print(f"Full result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")

            # Format response for agent understanding
            if "results" in result:
                formatted_results = []
                for item in result["results"][:3]:  # Limit to top 3 results
                    formatted_results.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "content": item.get("content", "")[:500],  # Truncate content
                        }
                    )
                formatted_response = {
                    "search_results": formatted_results,
                    "answer": result.get("answer", ""),
                    "query": query,
                }
                print(f"Returning formatted response: {json.dumps(formatted_response)[:200]}...")
                print(f"Formatted response keys: {list(formatted_response.keys())}")
                return formatted_response

            print(f"Returning raw result: {json.dumps(result)[:200]}...")
            print(f"Raw result keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
            return result
    except Exception as e:
        print(f"ERROR in Lambda function: {str(e)}")
        import traceback

        print(f"Traceback: {traceback.format_exc()}")
        error_response = {"error": str(e)}
        print(f"Returning error response: {error_response}")
        return error_response
