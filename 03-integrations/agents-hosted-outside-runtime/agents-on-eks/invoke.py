"""
Test the Travel Agent deployed on EKS via HTTP.

Start port-forward before running:
    kubectl port-forward service/strands-agents-travel 8080:80 &
    python invoke.py

Or specify a custom URL:
    python invoke.py --url http://<load-balancer-dns>/travel
"""

import argparse

import requests

DEFAULT_URL = "http://localhost:8080/travel"

TEST_PROMPTS = [
    "What are the best places to visit in Tokyo in March?",
    "What is the weather like in Bali in July?",
    "What is the budget for a 5-day trip to Paris, $150/day for 2 people?",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Agent endpoint URL")
    parser.add_argument("--prompt", help="Single prompt to send")
    args = parser.parse_args()

    prompts = [args.prompt] if args.prompt else TEST_PROMPTS

    print(f"Sending requests to: {args.url}")
    print()

    for i, prompt in enumerate(prompts, 1):
        print(f"{'=' * 60}")
        print(f"Prompt {i}: {prompt}")
        print("=" * 60)
        print("Waiting for response (this may take a minute)...")

        try:
            response = requests.post(
                args.url,
                json={"prompt": prompt},
                timeout=120,
            )
            print(f"Status: {response.status_code}")
            print(f"\nResponse:\n{response.text}")
        except requests.exceptions.ConnectionError as e:
            print(f"Connection failed: {e}")
            print("\nMake sure port-forward is running:")
            print("  kubectl port-forward service/strands-agents-travel 8080:80 &")
        except requests.exceptions.Timeout:
            print("Request timed out (agent may still be processing).")

        print()

    print("View traces: CloudWatch -> Gen AI Observability -> Agents")


if __name__ == "__main__":
    main()
