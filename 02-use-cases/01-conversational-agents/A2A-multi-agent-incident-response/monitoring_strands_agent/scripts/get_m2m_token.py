"""Script to get M2M token for monitoring agent"""

from utils import get_m2m_token_for_agent


def main():
    access_token, agent_card_url = get_m2m_token_for_agent("/monitoragent")
    print(f"Bearer Token: {access_token}")
    print(f"Agent Card URL: {agent_card_url}")


if __name__ == "__main__":
    main()
