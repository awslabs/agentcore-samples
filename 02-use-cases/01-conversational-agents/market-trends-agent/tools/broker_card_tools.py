import os

from langchain_core.tools import tool
from langchain_aws import ChatBedrock
from typing import Dict


def parse_broker_card_format(card_content: str) -> Dict[str, str]:
    """Parse broker card format from user input into structured data"""
    broker_data = {
        "name": "",
        "company": "",
        "role": "",
        "preferred_news_feed": "",
        "industry_interests": "",
        "investment_strategy": "",
        "risk_tolerance": "",
        "client_demographics": "",
        "geographic_focus": "",
        "recent_interests": "",
        "additional_notes": "",
    }

    # Parse broker card format from user message
    lines = card_content.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("Name:"):
            broker_data["name"] = line.replace("Name:", "").strip()
        elif line.startswith("Company:"):
            broker_data["company"] = line.replace("Company:", "").strip()
        elif line.startswith("Role:"):
            broker_data["role"] = line.replace("Role:", "").strip()
        elif line.startswith("Preferred News Feed:"):
            broker_data["preferred_news_feed"] = line.replace("Preferred News Feed:", "").strip()
        elif line.startswith("Industry Interests:"):
            broker_data["industry_interests"] = line.replace("Industry Interests:", "").strip()
        elif line.startswith("Investment Strategy:"):
            broker_data["investment_strategy"] = line.replace("Investment Strategy:", "").strip()
        elif line.startswith("Risk Tolerance:"):
            broker_data["risk_tolerance"] = line.replace("Risk Tolerance:", "").strip()
        elif line.startswith("Client Demographics:"):
            broker_data["client_demographics"] = line.replace("Client Demographics:", "").strip()
        elif line.startswith("Geographic Focus:"):
            broker_data["geographic_focus"] = line.replace("Geographic Focus:", "").strip()
        elif line.startswith("Recent Interests:"):
            broker_data["recent_interests"] = line.replace("Recent Interests:", "").strip()
        elif line.startswith("Additional Notes:"):
            broker_data["additional_notes"] = line.replace("Additional Notes:", "").strip()

    return broker_data


@tool
def parse_broker_profile_from_message(user_message: str) -> str:
    """
    Parse broker profile information from user message in broker card format.

    Args:
        user_message (str): User message containing broker profile in structured format

    Returns:
        str: Parsed and formatted broker profile information
    """
    try:
        # Check if message contains broker card format
        if any(field in user_message for field in ["Name:", "Company:", "Role:", "Industry Interests:"]):
            broker_data = parse_broker_card_format(user_message)

            # Format the parsed data
            profile_parts = []
            if broker_data["name"]:
                profile_parts.append(f"Name: {broker_data['name']}")
            if broker_data["company"]:
                profile_parts.append(f"Company: {broker_data['company']}")
            if broker_data["role"]:
                profile_parts.append(f"Role: {broker_data['role']}")
            if broker_data["preferred_news_feed"]:
                profile_parts.append(f"Preferred News Feed: {broker_data['preferred_news_feed']}")
            if broker_data["industry_interests"]:
                profile_parts.append(f"Industry Interests: {broker_data['industry_interests']}")
            if broker_data["investment_strategy"]:
                profile_parts.append(f"Investment Strategy: {broker_data['investment_strategy']}")
            if broker_data["risk_tolerance"]:
                profile_parts.append(f"Risk Tolerance: {broker_data['risk_tolerance']}")
            if broker_data["client_demographics"]:
                profile_parts.append(f"Client Demographics: {broker_data['client_demographics']}")
            if broker_data["geographic_focus"]:
                profile_parts.append(f"Geographic Focus: {broker_data['geographic_focus']}")
            if broker_data["recent_interests"]:
                profile_parts.append(f"Recent Interests: {broker_data['recent_interests']}")

            if profile_parts:
                return "Broker Profile Detected:\n" + "\n".join(profile_parts)
            else:
                return "No structured broker profile found in message"
        else:
            return "Message does not contain broker card format"

    except Exception as e:
        return f"Error parsing broker profile: {str(e)}"


@tool
def generate_market_summary_for_broker(broker_profile: str, market_data: str = "") -> str:
    """
    Generate a tailored market trends summary based on broker profile.

    Args:
        broker_profile (str): The broker's profile information
        market_data (str): Optional additional market data from news searches

    Returns:
        str: Tailored market summary focusing on broker's interests
    """
    try:
        # Create tailored prompt
        llm = ChatBedrock(
            model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )

        prompt = f"""
Generate a comprehensive market trends summary tailored for this broker profile:

{broker_profile}

Please provide a structured summary covering:

1. LATEST IMPORTANT NEWS
   - Focus on sectors mentioned in their industry interests
   - Include breaking news from their preferred news source perspective

2. MAJOR STOCK MOVEMENTS
   - Highlight stocks in their industries of interest
   - Include percentage changes and key drivers

3. INDEXES RECAP
   - Major index performance (S&P 500, NASDAQ, Dow Jones)
   - Sector-specific index performance relevant to their interests

4. MAJOR IPOs AND ACQUISITIONS
   - Recent or upcoming IPOs in relevant sectors
   - M&A activity in their areas of interest

5. LEGAL OR POLITICAL EVENTS
   - Regulatory changes affecting their focus industries
   - Political developments impacting markets

Additional market data to consider:
{market_data}

Format the response as a professional market briefing suitable for this broker's profile and client base.
"""

        result = llm.invoke(prompt).content
        return result

    except Exception as e:
        return f"Error generating market summary: {str(e)}"


@tool
def get_broker_card_template() -> str:
    """
    Provide a template for broker card format that users can fill out.

    Returns:
        str: Template showing the expected broker card format
    """
    template = """
BROKER CARD TEMPLATE:
Please provide your information in this format:

Name: [Your Full Name]
Company: [Your Company/Firm]
Role: [Your Role/Title]
Preferred News Feed: [Bloomberg, WSJ, Reuters, etc.]
Industry Interests: [technology, healthcare, energy, etc.]
Investment Strategy: [growth, value, dividend, etc.]
Risk Tolerance: [conservative, moderate, aggressive]
Client Demographics: [retail, institutional, high net worth, etc.]
Geographic Focus: [North America, Europe, Asia-Pacific, etc.]
Recent Interests: [specific sectors, trends, or companies]

Example:
Name: Sarah Chen
Company: Morgan Stanley
Role: Investment Advisor
Preferred News Feed: Bloomberg
Industry Interests: technology, healthcare, financial services
Investment Strategy: growth investing
Risk Tolerance: moderate to high
Client Demographics: younger professionals, tech workers
Geographic Focus: North America, Asia-Pacific
Recent Interests: artificial intelligence, renewable energy, fintech
"""
    return template


@tool
def collect_broker_preferences_interactively(preference_type: str) -> str:
    """
    Guide the collection of specific broker preferences through conversation.

    Args:
        preference_type (str): Type of preference to collect (industries, risk, strategy, etc.)

    Returns:
        str: Questions to ask the broker about their preferences
    """
    questions = {
        "industries": "What industries or sectors are you most interested in? (e.g., technology, healthcare, energy, financial services)",
        "risk": "What's your typical risk tolerance? (conservative, moderate, or aggressive)",
        "strategy": "What investment strategy do you typically follow? (growth, value, dividend, momentum, etc.)",
        "news": "What's your preferred news source for market information? (Bloomberg, WSJ, Reuters, Financial Times, etc.)",
        "clients": "What type of clients do you primarily serve? (retail investors, institutional, high net worth, etc.)",
        "geography": "What geographic regions do you focus on? (North America, Europe, Asia-Pacific, emerging markets, etc.)",
        "recent": "Are there any specific companies, trends, or sectors you're particularly interested in right now?",
    }

    return questions.get(
        preference_type.lower(),
        "Please tell me more about your investment preferences and areas of focus.",
    )
