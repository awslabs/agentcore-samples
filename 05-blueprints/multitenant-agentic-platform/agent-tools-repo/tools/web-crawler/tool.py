from strands import tool
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig


@tool
def web_crawler(
    url: str,
    extract_links: bool = True,
    extract_images: bool = False,
    word_count_threshold: int = 10,
) -> str:
    """
    Crawl a website and extract its content using Crawl4AI.

    This tool allows the agent to crawl specific web pages and extract
    their content in a clean, readable format. It handles JavaScript-rendered
    pages and provides structured content extraction.

    Args:
        url: The URL of the website to crawl
        extract_links: Whether to include extracted links in the output (default: True)
        extract_images: Whether to include image URLs in the output (default: False)
        word_count_threshold: Minimum words per content block to include (default: 10)

    Returns:
        Extracted content from the website including text, and optionally links and images

    Example:
        result = web_crawler("https://example.com")
        result = web_crawler("https://docs.python.org", extract_links=True)
    """
    try:
        # Run the async crawler
        result = asyncio.run(_crawl_website(url, extract_links, extract_images, word_count_threshold))
        return result
    except Exception as e:
        return f"Error crawling website: {str(e)}"


async def _crawl_website(url: str, extract_links: bool, extract_images: bool, word_count_threshold: int) -> str:
    """Async helper function to perform the actual crawling."""

    # Configure browser settings
    browser_config = BrowserConfig(headless=True, verbose=False)

    # Configure crawler run settings
    crawler_config = CrawlerRunConfig(
        word_count_threshold=word_count_threshold,
        exclude_external_links=False,
        remove_overlay_elements=True,
        process_iframes=False,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=crawler_config)

        if not result.success:
            return f"Failed to crawl {url}: {result.error_message or 'Unknown error'}"

        # Build the output
        output_parts = []

        # Add page title
        if result.metadata and result.metadata.get("title"):
            output_parts.append(f"# {result.metadata['title']}\n")

        output_parts.append(f"**URL:** {url}\n")

        # Add main content (markdown format)
        if result.markdown:
            output_parts.append("## Content\n")
            # Truncate if too long
            content = result.markdown
            if len(content) > 10000:
                content = content[:10000] + "\n\n... [Content truncated]"
            output_parts.append(content)

        # Add extracted links if requested
        if extract_links and result.links:
            internal_links = result.links.get("internal", [])
            external_links = result.links.get("external", [])

            if internal_links or external_links:
                output_parts.append("\n## Extracted Links\n")

                if internal_links:
                    output_parts.append("### Internal Links")
                    for link in internal_links[:20]:  # Limit to 20 links
                        href = link.get("href", "")
                        text = link.get("text", "No text")
                        output_parts.append(f"- [{text[:50]}]({href})")

                if external_links:
                    output_parts.append("\n### External Links")
                    for link in external_links[:20]:  # Limit to 20 links
                        href = link.get("href", "")
                        text = link.get("text", "No text")
                        output_parts.append(f"- [{text[:50]}]({href})")

        # Add extracted images if requested
        if extract_images and result.media:
            images = result.media.get("images", [])
            if images:
                output_parts.append("\n## Images\n")
                for img in images[:10]:  # Limit to 10 images
                    src = img.get("src", "")
                    alt = img.get("alt", "No description")
                    output_parts.append(f"- {alt}: {src}")

        return "\n".join(output_parts)
