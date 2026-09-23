import os
import logging
import boto3
from typing import Any, Dict
from serpapi import GoogleSearch

logger = logging.getLogger(__name__)


def get_ssm_parameter(parameter_name: str, region: str) -> str:
    """
    Fetch parameter from SSM Parameter Store.

    Args:
        parameter_name: SSM parameter name
        region: AWS region

    Returns:
        Parameter value
    """
    ssm = boto3.client("ssm", region_name=region)
    try:
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        raise ValueError(f"SSM parameter not found: {parameter_name}")
    except Exception as e:
        raise ValueError(f"Failed to retrieve SSM parameter {parameter_name}: {e}")


def get_serpapi_key() -> str:
    """
    Get SerpAPI key from AWS SSM Parameter Store.

    Returns:
        SerpAPI key
    """
    region = os.getenv("AWS_REGION", "us-east-1")
    return get_ssm_parameter("/concierge-agent/shopping/serp-api-key", region)


def search_google_shopping_products(query: str, max_results: int = 10) -> Dict[str, Any]:
    """
    Search for products on Google Shopping using SerpAPI.

    Args:
        query: Search query for products
        max_results: Maximum number of results to return

    Returns:
        Dict containing search results with product information
    """
    try:
        api_key = get_serpapi_key()

        # Search Google Shopping using SerpAPI
        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": api_key,
        }

        search = GoogleSearch(params)
        results = search.get_dict()

        # Extract product information
        products = []
        shopping_results = results.get("shopping_results", [])[:max_results]

        for product in shopping_results:
            # Extract price value
            price_value = "N/A"
            if "price" in product:
                price_value = product["price"]
            elif "extracted_price" in product:
                price_value = product["extracted_price"]

            product_info = {
                "asin": product.get("product_id", ""),  # Store product_id in asin field for compatibility
                "title": product.get("title", ""),
                "link": product.get("product_link", ""),  # Google Shopping product comparison page
                "price": price_value,
                "rating": product.get("rating", 0),
                "reviews": product.get("reviews", 0),
                "thumbnail": product.get("thumbnail", ""),
                "source": product.get("source", ""),
            }
            products.append(product_info)

        return {"success": True, "products": products, "total_results": len(products)}

    except Exception as e:
        logger.error(f"Error searching Google Shopping products: {e}")
        return {"success": False, "error": str(e), "products": [], "total_results": 0}


def search_products(user_id: str, question: str) -> Dict[str, Any]:
    """
    Process a product search request from user by searching products on Google Shopping via SerpAPI.

    Args:
        user_id: The unique identifier of the user for whom products are being searched.
        question: User's query text requesting product information

    Returns:
        Dict: A dictionary called 'product_list' with search results
            - 'answer': Description of found products or error message
            - 'asins': List of product IDs found (stored in 'asins' field for compatibility)
            - 'products': List of product details
    """
    try:
        logger.info(f"Processing product search for user {user_id}: {question}")

        # Search for products
        search_results = search_google_shopping_products(question)

        if not search_results["success"]:
            return {
                "answer": f"Product search failed: {search_results.get('error', 'Unknown error')}",
                "asins": [],
                "products": [],
            }

        products = search_results["products"]
        asins = [p["asin"] for p in products if p.get("asin")]

        if not products:
            return {
                "answer": "No products found matching your search criteria.",
                "asins": [],
                "products": [],
            }

        # Build response
        answer = f"Found {len(products)} products matching '{question}':\n\n"
        for i, product in enumerate(products, 1):
            price_str = f"${product['price']}" if isinstance(product["price"], (int, float)) else product["price"]
            answer += f"{i}. {product['title']}\n"
            answer += f"   Price: {price_str}\n"
            if product.get("rating"):
                answer += f"   Rating: {product['rating']}/5 ({product.get('reviews', 0)} reviews)\n"
            answer += f"   Product ID: {product['asin']}\n"
            if product.get("source"):
                answer += f"   Source: {product['source']}\n"
            answer += f"   Link: {product['link']}\n\n"

        return {"answer": answer.strip(), "asins": asins, "products": products}

    except Exception as e:
        logger.error(f"Error in single_productsearch: {e}")
        return {
            "answer": f"An error occurred while searching for products: {str(e)}",
            "asins": [],
            "products": [],
        }
