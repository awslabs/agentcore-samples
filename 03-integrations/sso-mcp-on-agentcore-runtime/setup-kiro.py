#!/usr/bin/env python3
"""
Kiro MCP Configuration Setup Script
Configures Kiro IDE to use the Enterprise CloudWatch MCP Server
"""

import json
import os
import sys
from pathlib import Path

def find_kiro_config_path():
    """Find the Kiro MCP configuration file path"""
    # Try workspace-specific config first
    workspace_config = Path('.kiro/settings/mcp.json')
    if workspace_config.parent.exists():
        return workspace_config
    
    # Try user-level config
    user_config = Path.home() / '.kiro' / 'settings' / 'mcp.json'
    return user_config

def load_existing_config(config_path):
    """Load existing Kiro MCP configuration if it exists"""
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    
    return {"mcpServers": {}}

def update_kiro_config():
    """Update Kiro MCP configuration with the Enterprise CloudWatch server"""
    print("⚙️ Configuring Kiro MCP integration...")
    
    # Load the Kiro configuration
    try:
        with open('kiro-mcp-config.json', 'r') as f:
            new_config = json.load(f)
    except FileNotFoundError:
        print("❌ kiro-mcp-config.json not found.")
        return False
    
    # Find Kiro config path
    config_path = find_kiro_config_path()
    print(f"📁 Kiro config path: {config_path}")
    
    # Create directory if it doesn't exist
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing configuration
    existing_config = load_existing_config(config_path)
    
    # Merge configurations
    existing_config["mcpServers"].update(new_config["mcpServers"])
    
    # Save updated configuration
    try:
        with open(config_path, 'w') as f:
            json.dump(existing_config, f, indent=2)
        
        print(f"✅ Kiro MCP configuration updated: {config_path}")
        return True
        
    except Exception as e:
        print(f"❌ Error updating Kiro configuration: {e}")
        return False

def show_usage_instructions():
    """Show instructions for using the MCP server in Kiro"""
    print("\n🎭 Usage Instructions")
    print("=" * 30)
    print("After restarting Kiro, you can use these natural language commands:")
    print()
    print("Health & Status:")
    print('  - "Check server health"')
    print('  - "What\'s my user info?"')
    print()
    print("CloudWatch Logs:")
    print('  - "List my log groups"')
    print('  - "Search logs in [log-group-name] for errors"')
    print('  - "Show recent logs from [log-group-name]"')
    print()
    print("Cross-Account Access:")
    print('  - "List log groups in account [account-id]"')
    print('  - "Search logs in account [account-id] for [pattern]"')
    print()
    print("CloudWatch Metrics & Alarms:")
    print('  - "List CloudWatch metrics"')
    print('  - "Show alarms in ALARM state"')
    print('  - "List metrics for AWS/Lambda"')

def main():
    print("🚀 Enterprise CloudWatch MCP Server - Kiro Setup")
    print("=" * 50)
    
    # Update Kiro configuration
    if not update_kiro_config():
        sys.exit(1)
    
    print("\n🎉 Kiro configuration completed successfully!")
    print("\n📋 Next Steps:")
    print("1. Install dependencies: pip install -r requirements.txt")
    print("2. Configure your Identity Center settings in config-template.json")
    print("3. Restart Kiro IDE to load the new MCP server")
    print("4. Run: python test-connection.py (to verify setup)")
    print("5. Start using natural language commands in Kiro!")
    
    # Show usage instructions
    show_usage_instructions()
    
    print("\n💡 Tip: Check the MCP Server view in Kiro's feature panel to see connection status")

if __name__ == "__main__":
    main()