#!/usr/bin/env python3
"""
Package Lambda function for deployment from mcp-tool-lambda directory
Matches the SAM template expectations
"""

import os
import zipfile
from pathlib import Path


def create_lambda_package():
    """Create ZIP package for Lambda deployment matching SAM template"""
    current_dir = Path.cwd()
    packaging_dir = current_dir / "packaging"
    lambda_dir = current_dir / "lambda"

    # Ensure packaging directory exists
    packaging_dir.mkdir(exist_ok=True)

    # SAM template expects this specific filename
    lambda_deployment_zip = packaging_dir / "mcp-tool-lambda.zip"

    print(f"Packaging Lambda function from: {lambda_dir}")
    print(f"Creating package: {lambda_deployment_zip}")

    # Check if lambda directory exists
    if not lambda_dir.exists():
        print(f"❌ Lambda directory not found: {lambda_dir}")
        return False

    # Check if dependencies are packaged in current directory
    deps_packaging_dir = current_dir / "packaging"
    if not deps_packaging_dir.exists():
        print(f"❌ Dependencies packaging directory not found: {deps_packaging_dir}")
        print("   Dependencies should be installed first!")
        return False

    # Create the Lambda deployment ZIP
    print("📦 Creating mcp-tool-lambda.zip...")
    with zipfile.ZipFile(lambda_deployment_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Add handler files from lambda directory
        handler_files = ["mcp-tool-handler.py", "optimized_mcp_system_prompt.py"]

        for file_name in handler_files:
            file_path = lambda_dir / file_name
            if file_path.exists():
                zipf.write(file_path, file_name)
                print(f"  ✅ Added: {file_name}")
            else:
                print(f"  ⚠️  Missing: {file_name}")

        # Add dependencies directly to the root of the ZIP (not in python/ subdirectory)
        deps_dir = deps_packaging_dir / "python"
        if deps_dir.exists():
            print("  📦 Adding dependencies to root level...")
            dep_count = 0
            for root, _, files in os.walk(deps_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Put dependencies at root level, not in python/ subdirectory
                    arcname = os.path.relpath(file_path, deps_dir)
                    zipf.write(file_path, arcname)
                    dep_count += 1
            print(f"  ✅ Added {dep_count} dependency files at root level")
        else:
            print(f"  ❌ Dependencies not found: {deps_dir}")
            return False

    # Show package size
    if lambda_deployment_zip.exists():
        size_mb = lambda_deployment_zip.stat().st_size / (1024 * 1024)
        print(f"✅ Package created: {size_mb:.2f} MB")
        print(f"📍 Location: {lambda_deployment_zip}")
        return True
    else:
        print("❌ Package creation failed")
        return False


if __name__ == "__main__":
    success = create_lambda_package()
    if not success:
        exit(1)
    print("🎉 Lambda packaging completed successfully!")
