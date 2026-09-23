import os
import time
import logging
from playwright.sync_api import sync_playwright, Playwright, BrowserType
from bedrock_agentcore.tools.browser_client import browser_session
from langchain_core.tools import tool
from langchain_aws import ChatBedrock

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_stock_data_with_browser(playwright: Playwright, symbol: str) -> str:
    """Get stock data using browser"""
    with browser_session(AWS_REGION) as client:
        ws_url, headers = client.generate_ws_headers()
        chromium: BrowserType = playwright.chromium
        browser = chromium.connect_over_cdp(ws_url, headers=headers)

        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(f"https://finance.yahoo.com/quote/{symbol}")
            time.sleep(2)
            content = page.inner_text("body")

            # Use LLM to extract stock data
            llm = ChatBedrock(
                model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
                region_name=AWS_REGION,
            )
            prompt = "Extract stock price and key information for {} from this page content. Be concise:\n\n{}".format(
                symbol, content[:3000]
            )
            result = llm.invoke(prompt).content
            return result

        finally:
            if not page.is_closed():
                page.close()
            browser.close()


def search_news_with_browser(playwright: Playwright, query: str, news_source: str = "bloomberg") -> str:
    """Generic news search using browser and LLM analysis"""
    with browser_session(AWS_REGION) as client:
        ws_url, headers = client.generate_ws_headers()
        chromium: BrowserType = playwright.chromium
        browser = chromium.connect_over_cdp(ws_url, headers=headers)

        try:
            # Create context with user agent to avoid bot detection
            context = (
                browser.contexts[0]
                if browser.contexts
                else browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.pages[0] if context.pages else context.new_page()

            # Map news sources to URLs with proper encoding
            import urllib.parse

            encoded_query = urllib.parse.quote_plus(query)

            news_urls = {
                "bloomberg": f"https://www.bloomberg.com/search?query={encoded_query}",
                "reuters": f"https://www.reuters.com/site-search/?query={encoded_query}",
                "cnbc": f"https://www.cnbc.com/search/?query={encoded_query}",
                "wall street journal": f"https://www.wsj.com/search?query={encoded_query}",
                "wsj": f"https://www.wsj.com/search?query={encoded_query}",
                "financial times": f"https://www.ft.com/search?q={encoded_query}",
                "ft": f"https://www.ft.com/search?q={encoded_query}",
                "dow jones": f"https://www.dowjones.com/search/?q={encoded_query}",
                # Add more reliable sources
                "yahoo finance": "https://finance.yahoo.com/news/",
                "yahoo": "https://finance.yahoo.com/news/",
                "marketwatch": f"https://www.marketwatch.com/search?q={encoded_query}",
                "seeking alpha": f"https://seekingalpha.com/search?q={encoded_query}",
            }

            # Fallback URLs for direct section access (more reliable)
            fallback_urls = {
                "reuters": "https://www.reuters.com/markets/",
                "bloomberg": "https://www.bloomberg.com/markets",
                "cnbc": "https://www.cnbc.com/business/",  # More reliable than markets
                "wsj": "https://www.wsj.com/business",
                "financial times": "https://www.ft.com/markets",
                "yahoo finance": "https://finance.yahoo.com/news/",
                "yahoo": "https://finance.yahoo.com/news/",
                "marketwatch": "https://www.marketwatch.com/markets",
                "seeking alpha": "https://seekingalpha.com/market-news",
            }

            # Get URL for news source
            source_key = news_source.lower()
            url = news_urls.get(source_key, f"https://www.bloomberg.com/search?query={encoded_query}")

            # Try primary URL first with retry logic
            max_retries = 2
            content = None

            for attempt in range(max_retries):
                try:
                    page.goto(url, timeout=15000)
                    time.sleep(3 + attempt)  # Longer wait on retries
                    content = page.inner_text("body")

                    # Check for various error conditions
                    error_indicators = [
                        "can't find that page",
                        "page not found",
                        "404",
                        "503",
                        "backend fetch failed",
                        "service unavailable",
                        "access denied",
                        "blocked",
                        "rate limit",
                    ]

                    if any(indicator in content.lower() for indicator in error_indicators):
                        if attempt < max_retries - 1:
                            print(f"Error detected on attempt {attempt + 1}, retrying...")
                            time.sleep(2)
                            continue
                        else:
                            raise Exception("Got error page after retries, trying fallback")

                    # Success - break out of retry loop
                    break

                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"Attempt {attempt + 1} failed, retrying...")
                        time.sleep(2)
                        continue
                    else:
                        # Try fallback URL if available
                        fallback_url = fallback_urls.get(source_key)
                        if fallback_url:
                            print(f"All attempts failed for {news_source}, trying fallback: {fallback_url}")
                            page.goto(fallback_url, timeout=15000)
                            time.sleep(3)
                            content = page.inner_text("body")
                        else:
                            raise e

            # Use LLM to extract headlines and highlights
            llm = ChatBedrock(
                model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
                region_name=AWS_REGION,
            )

            # Enhanced prompt to handle both search results and general market pages
            if "search" in url or encoded_query in url:
                prompt = f"Extract the main news headlines and key highlights about '{query}' from this {news_source} search page. Focus on financial and market-relevant news:\n\n{content[:4000]}"
            else:
                prompt = f"Extract recent market news headlines and highlights from this {news_source} markets page, focusing on topics related to '{query}' if available:\n\n{content[:4000]}"

            result = llm.invoke(prompt).content
            return result

        finally:
            if not page.is_closed():
                page.close()
            browser.close()


@tool
def get_stock_data(symbol: str) -> str:
    """Get stock data for a given symbol"""
    try:
        with sync_playwright() as p:
            return get_stock_data_with_browser(p, symbol)
    except Exception as e:
        return f"Error getting stock data for {symbol}: {str(e)}"


@tool
def search_news(query: str, news_source: str = "bloomberg") -> str:
    """
    Search any news source for business news.

    Args:
        query (str): Search query
        news_source (str): News source options:
            - bloomberg (most reliable)
            - yahoo finance (very reliable)
            - marketwatch (reliable)
            - reuters (can have issues)
            - cnbc (can have server issues)
            - wsj (paywall)
            - financial times (paywall)
            - seeking alpha (reliable)

    Returns:
        str: News headlines and highlights
    """
    try:
        with sync_playwright() as p:
            result = search_news_with_browser(p, query, news_source)

            # Check if we got server errors or empty results
            error_indicators = [
                "503",
                "backend fetch failed",
                "service unavailable",
                "can't find that page",
                "page not found",
            ]
            if any(indicator in result.lower() for indicator in error_indicators):
                # Try reliable fallback sources in order
                reliable_sources = ["yahoo finance", "bloomberg", "marketwatch"]
                for fallback_source in reliable_sources:
                    if fallback_source != news_source.lower():
                        try:
                            print(f"Fallback: Trying {fallback_source} instead of {news_source}")
                            return search_news_with_browser(p, query, fallback_source)
                        except Exception as fallback_error:
                            logger.warning(f"Fallback source {fallback_source} failed: {fallback_error}")
                            continue

            return result

    except Exception as e:
        # Final fallback: try the most reliable sources
        reliable_sources = ["yahoo finance", "bloomberg", "marketwatch"]
        for fallback_source in reliable_sources:
            if fallback_source != news_source.lower():
                try:
                    print(f"Error with {news_source}, trying {fallback_source} as fallback")
                    with sync_playwright() as p:
                        return search_news_with_browser(p, query, fallback_source)
                except Exception as final_fallback_error:
                    logger.warning(f"Final fallback source {fallback_source} failed: {final_fallback_error}")
                    continue

        return f"Error searching {news_source} for '{query}': {str(e)}. Multiple fallback sources also failed. This may be due to temporary server issues or rate limiting. Try again in a few minutes or use a different query."
