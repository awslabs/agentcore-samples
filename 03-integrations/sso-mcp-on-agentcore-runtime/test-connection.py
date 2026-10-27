#!/usr/bin/env python3
"""
Test script for Enterprise CloudWatch MCP Server V2
"""

import subprocess
import json
import sys
import asyncio
from pathlib import Path

async def test_mcp_server():
    """Test the MCP server directly"""
    print("🧪 Testing Enterprise CloudWatch MCP Server V2")
    print("=" * 50)
    
    # Check if server file exists
    server_file = Path("enterprise-cloudwatch-mcp-server.py")
    if not server_file.exists():
        print("❌ enterprise-cloudwatch-mcp-server.py not found")
        return False
    
    # Check if config exists
    config_file = Path("config.json")
    template_file = Path("config-template.json")
    
    if not config_file.exists() and not template_file.exists():
        print("❌ No configuration file found")
        return False
    
    config_to_use = config_file if config_file.exists() else template_file
    print(f"📋 Using configuration: {config_to_use}")
    
    # Test basic import
    try:
        print("🔍 Testing server imports...")
        # Test just the imports without running main
        test_code = '''
import sys
sys.path.insert(0, ".")
try:
    import asyncio
    import json
    import boto3
    from datetime import datetime, timezone, timedelta
    from typing import Dict, List, Optional, Any
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    from botocore.exceptions import ClientError
    import logging
    print("✅ All imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
'''
        result = subprocess.run([
            sys.executable, "-c", test_code
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            print(f"❌ Import failed: {result.stderr}")
            print("💡 Run: pip install mcp")
            return False
        else:
            print(result.stdout.strip())
    
    except Exception as e:
        print(f"❌ Error testing imports: {e}")
        return False
    
    # Check dependencies
    try:
        print("📦 Checking dependencies...")
        import mcp
        import boto3
        print("✅ All dependencies available")
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("💡 Run: pip install -r requirements.txt")
        return False
    
    print("\n🎉 Basic tests completed successfully!")
    print("\n📋 Next steps:")
    print("1. Configure your Identity Center settings in config.json")
    print("2. Run: python setup-kiro.py")
    print("3. Restart Kiro IDE")
    print("4. Test commands in Kiro chat")
    
    return True

def main():
    """Main test function"""
    try:
        success = asyncio.run(test_mcp_server())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n❌ Test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Test error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()