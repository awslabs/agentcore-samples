from strands import tool
import requests
from bs4 import BeautifulSoup
import urllib.parse


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web for information using DuckDuckGo.

    This tool allows the agent to search the internet and retrieve
    relevant information to answer user questions. It uses DuckDuckGo's
    HTML interface for reliable results without external dependencies.

    Args:
        query: The search query string
        max_results: Maximum number of results to return (default: 5, max: 10)

    Returns:
        Formatted search results as a string with titles, snippets, and URLs

    Example:
        result = web_search("Python programming tutorials", max_results=3)
        result = web_search("latest AI news")
    """
    try:
        # Limit max_results to reasonable range
        max_results = min(max(1, max_results), 10)

        # Use DuckDuckGo HTML interface
        url = "https://html.duckduckgo.com/html/"

        # Prepare search parameters
        params = {
            "q": query,
            "kl": "us-en",  # Region/language
        }

        # Set headers to mimic a browser
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        # Make the request
        response = requests.post(url, data=params, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Find search results
        results = []
        result_divs = soup.find_all("div", class_="result")

        if not result_divs:
            # Try alternative selectors
            result_divs = soup.find_all("div", class_="results_links")

        for result_div in result_divs[:max_results]:
            try:
                # Extract title and link
                title_elem = result_div.find("a", class_="result__a")
                if not title_elem:
                    title_elem = result_div.find("a", class_="large")

                if title_elem:
                    title = title_elem.get_text(strip=True)
                    link = title_elem.get("href", "")

                    # Extract snippet/description
                    snippet_elem = result_div.find("a", class_="result__snippet")
                    if not snippet_elem:
                        snippet_elem = result_div.find("div", class_="result__snippet")
                    if not snippet_elem:
                        snippet_elem = result_div.find("td", class_="result-snippet")

                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else "No description available"

                    # Clean up the link (DuckDuckGo sometimes uses redirect URLs)
                    if link.startswith("/"):
                        # Try to extract the actual URL from redirect
                        if "uddg=" in link:
                            link = urllib.parse.unquote(link.split("uddg=")[1].split("&")[0])

                    results.append({"title": title, "snippet": snippet, "url": link})
            except Exception:
                # Skip this result if there's an error parsing it
                continue

        if not results:
            return f"No results found for query: '{query}'"

        # Format results
        formatted_results = []
        for idx, result in enumerate(results, 1):
            result_text = f"{idx}. **{result['title']}**\n"
            result_text += f"   {result['snippet']}\n"
            if result["url"]:
                result_text += f"   URL: {result['url']}\n"

            formatted_results.append(result_text)

        header = f"Found {len(results)} results for '{query}':\n\n"
        return header + "\n".join(formatted_results)

    except requests.exceptions.Timeout:
        return f"Search timed out for query: '{query}'. Please try again."
    except requests.exceptions.RequestException as e:
        return f"Network error during search: {str(e)}"
    except Exception as e:
        return f"Error performing web search: {str(e)}"
