#!/usr/bin/env python3
"""
Quick verification script to check if the application is properly set up
"""

import sys
from pathlib import Path


def check_files():
    """Check if required files exist"""
    print("📁 Checking Required Files...")

    project_root = Path(__file__).parent.parent
    required_files = [
        "backend/main.py",
        "frontend/package.json",
        "frontend/src/App.jsx",
        ".env",
        "start.sh",
        "setup.sh",
    ]

    missing = []
    for file_path in required_files:
        if not (project_root / file_path).exists():
            missing.append(file_path)

    if missing:
        print(f"❌ Missing files: {', '.join(missing)}")
        return False
    else:
        print("✅ All required files present")
        return True


def check_environment():
    """Check Python environment"""
    print("\n🐍 Checking Python Environment...")

    project_root = Path(__file__).parent.parent
    venv_path = project_root / "venv"

    if not venv_path.exists():
        print("❌ Virtual environment not found")
        return False

    # Check if we're in the virtual environment
    if hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix):
        print("✅ Virtual environment activated")
    else:
        print("⚠️  Virtual environment not activated")

    return True


def check_dependencies():
    """Check Python dependencies"""
    print("\n📦 Checking Dependencies...")

    required_packages = ["fastapi", "uvicorn", "boto3", "strands", "bedrock-agentcore"]

    missing = []
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing.append(package)

    if missing:
        print(f"❌ Missing packages: {', '.join(missing)}")
        return False
    else:
        print("✅ All dependencies installed")
        return True


def check_aws_config():
    """Check AWS configuration"""
    print("\n☁️  Checking AWS Configuration...")

    # Check .env file
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"

    if not env_file.exists():
        print("❌ .env file not found")
        return False

    # Check for AWS configuration
    env_content = env_file.read_text()
    has_profile = "AWS_PROFILE" in env_content
    has_keys = "AWS_ACCESS_KEY_ID" in env_content and "AWS_SECRET_ACCESS_KEY" in env_content

    if has_profile:
        print("✅ AWS Profile configuration found")
        return True
    elif has_keys:
        print("✅ AWS Access Keys configuration found")
        return True
    else:
        print("❌ No AWS configuration found in .env")
        return False


def check_frontend():
    """Check frontend setup"""
    print("\n🌐 Checking Frontend Setup...")

    project_root = Path(__file__).parent.parent
    frontend_path = project_root / "frontend"

    if not (frontend_path / "node_modules").exists():
        print("❌ Frontend dependencies not installed")
        return False

    print("✅ Frontend dependencies installed")
    return True


def main():
    """Run all verification checks"""
    print("🔍 AgentCore Code Interpreter - Setup Verification")
    print("=" * 60)

    checks = [
        ("Files", check_files),
        ("Environment", check_environment),
        ("Dependencies", check_dependencies),
        ("AWS Config", check_aws_config),
        ("Frontend", check_frontend),
    ]

    passed = 0
    total = len(checks)

    for check_name, check_func in checks:
        try:
            if check_func():
                passed += 1
        except Exception as e:
            print(f"❌ {check_name} check failed: {e}")

    print("\n" + "=" * 60)
    print(f"🎯 VERIFICATION RESULTS: {passed}/{total} checks passed")

    if passed == total:
        print("🎉 Setup verification successful! Ready to run the application.")
        print("\n🚀 Next steps:")
        print("   1. Run: ./start.sh")
        print("   2. Open: http://localhost:3000")
        return 0
    else:
        print("❌ Setup verification failed. Please fix the issues above.")
        print("\n🔧 Common fixes:")
        print("   1. Run: ./setup.sh")
        print("   2. Configure AWS credentials in .env")
        print("   3. Install frontend: cd frontend && npm install")
        return 1


if __name__ == "__main__":
    sys.exit(main())
