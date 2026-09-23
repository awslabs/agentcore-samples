#!/usr/bin/env python3

import asyncio
import sys


# Simple CLI wrapper for the multi-agent system
def main():
    """Main CLI entry point - runs the multi-agent system with debug support."""
    try:
        # Import and run the multi-agent system
        from .multi_agent_langgraph import main as multi_agent_main

        asyncio.run(multi_agent_main())
    except ImportError as e:
        print(f"Error importing multi-agent system: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error running multi-agent system: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
