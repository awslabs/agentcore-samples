"""
Lab 02: ZIP-based Lambda Deployment Packager

Replaces Docker-based deployment with pure Python ZIP packaging.
Works natively in SageMaker VPC mode (no Docker daemon needed).

Functions:
- create_deployment_package()      # Create ZIP with dependencies
- upload_package_to_s3()           # Upload to S3
- create_lambda_function_from_zip() # Deploy Lambda from ZIP
- get_package_info()                # Validate size and contents
- setup_s3_bucket()                 # Create/verify S3 bucket
"""

import os
import sys
import zipfile
import subprocess
import shutil
from typing import Dict, Optional, Tuple
import boto3

from lab_helpers.constants import PARAMETER_PATHS, LAMBDA_CONFIG
from lab_helpers.parameter_store import put_parameter, get_parameter
from lab_helpers.config import MODEL_ID, AWS_REGION


# ============================================================================
# UTILITIES
# ============================================================================


def get_dir_size(path: str) -> int:
    """Calculate total size of directory in bytes"""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if os.path.exists(filepath):
                total += os.path.getsize(filepath)
    return total


def get_zip_size(zip_path: str) -> int:
    """Get compressed ZIP file size in bytes"""
    return os.path.getsize(zip_path)


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def validate_requirements(build_dir: str) -> bool:
    """Verify requirements.txt exists in build directory"""
    req_file = os.path.join(build_dir, "requirements.txt")
    if not os.path.exists(req_file):
        print(f"❌ requirements.txt not found in {build_dir}")
        return False
    return True


# ============================================================================
# PACKAGE CREATION
# ============================================================================


def install_dependencies(build_dir: str, requirements_content: str) -> Tuple[bool, Dict]:
    """
    Install dependencies using pip into lib/ directory

    Args:
        build_dir: Directory where dependencies will be installed
        requirements_content: Content of requirements.txt

    Returns:
        (success: bool, stats: dict with installation info)
    """
    print("\n📦 Installing dependencies...")

    # Write requirements.txt
    req_file = os.path.join(build_dir, "requirements.txt")
    with open(req_file, "w") as f:
        f.write(requirements_content)
    print(f"✓ Wrote requirements.txt ({len(requirements_content)} bytes)")

    # Create lib directory
    lib_dir = os.path.join(build_dir, "lib")
    os.makedirs(lib_dir, exist_ok=True)

    # Install dependencies
    print("✓ Installing to lib/...")
    print("  Target: Python 3.11 Linux x86_64 (Lambda runtime)")
    try:
        result = subprocess.run(  # noqa: F841
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                req_file,
                "-t",
                lib_dir,
                "--upgrade",
                "--quiet",
                "--disable-pip-version-check",
                "--platform",
                "manylinux2014_x86_64",
                "--implementation",
                "cp",
                "--python-version",
                "3.11",
                "--only-binary",
                ":all:",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        # Count packages
        installed_packages = [d for d in os.listdir(lib_dir) if os.path.isdir(os.path.join(lib_dir, d))]

        lib_size = get_dir_size(lib_dir)

        print(f"✓ Installed {len(installed_packages)} packages")
        print(f"  lib/ size: {format_size(lib_size)}")

        return True, {
            "packages_count": len(installed_packages),
            "lib_size": lib_size,
            "packages": installed_packages,
        }

    except subprocess.TimeoutExpired:
        print("❌ Installation timeout after 5 minutes")
        return False, {}
    except subprocess.CalledProcessError as e:
        print(f"❌ Installation failed: {e.stderr}")
        return False, {}
    except Exception as e:
        print(f"❌ Unexpected error during installation: {e}")
        return False, {}


def create_lambda_zip(build_dir: str, handler_code: str, output_zip: str) -> Tuple[bool, Dict]:
    """
    Create Lambda deployment ZIP with proper structure

    Structure:
    ├── app.py (handler)
    ├── lab_helpers/ (utilities)
    ├── lib/ (dependencies)
    │   ├── boto3/
    │   ├── botocore/
    │   ├── strands/
    │   └── ...
    └── requirements.txt

    Args:
        build_dir: Source build directory
        handler_code: Python code for app.py
        output_zip: Output ZIP file path

    Returns:
        (success: bool, stats: dict)
    """
    print("\n📦 Creating Lambda ZIP package...")

    # Write app.py
    app_py = os.path.join(build_dir, "app.py")
    with open(app_py, "w") as f:
        f.write(handler_code)
    print(f"✓ Wrote app.py ({len(handler_code)} bytes)")

    # Create ZIP
    try:
        with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            total_files = 0

            # Add lib/ (dependencies)
            lib_dir = os.path.join(build_dir, "lib")
            if os.path.exists(lib_dir):
                for root, dirs, files in os.walk(lib_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, build_dir)
                        zf.write(file_path, arcname)
                        total_files += 1
                print(f"✓ Added {total_files} files from lib/")

            # Add lab_helpers/ (utilities)
            lab_helpers_dir = os.path.join(build_dir, "lab_helpers")
            if os.path.exists(lab_helpers_dir):
                helpers_start = total_files
                for root, dirs, files in os.walk(lab_helpers_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, build_dir)
                        zf.write(file_path, arcname)
                        total_files += 1
                print(f"✓ Added {total_files - helpers_start} files from lab_helpers/")

            # Add app.py and requirements.txt at root
            zf.write(app_py, "app.py")
            req_file = os.path.join(build_dir, "requirements.txt")
            if os.path.exists(req_file):
                zf.write(req_file, "requirements.txt")
            total_files += 2
            print("✓ Added app.py and requirements.txt at root")

        zip_size = get_zip_size(output_zip)

        print(f"✓ ZIP created: {output_zip}")
        print(f"  Compressed size: {format_size(zip_size)}")
        print(f"  Total files: {total_files}")

        return True, {
            "zip_path": output_zip,
            "zip_size": zip_size,
            "total_files": total_files,
        }

    except Exception as e:
        print(f"❌ ZIP creation failed: {e}")
        return False, {}


def create_deployment_package(
    handler_code: str,
    requirements_content: str,
    build_dir: str = "lambda_diagnostic_agent_zip",
) -> Dict:
    """
    Complete workflow: Create deployment package with dependencies

    Args:
        handler_code: Python code for Lambda handler (app.py)
        requirements_content: pip requirements text
        build_dir: Build directory name

    Returns:
        Dictionary with package info or error details
    """
    print("=" * 70)
    print("CREATING LAMBDA ZIP DEPLOYMENT PACKAGE")
    print("=" * 70)

    # Cleanup old build if exists
    if os.path.exists(build_dir):
        print("\n🧹 Cleaning up existing build directory...")
        shutil.rmtree(build_dir)

    os.makedirs(build_dir, exist_ok=True)
    print(f"✓ Created build directory: {build_dir}")

    # Copy lab_helpers from repository root to build directory
    lab_helpers_src = "lab_helpers"
    if os.path.exists(lab_helpers_src):
        lab_helpers_dest = os.path.join(build_dir, "lab_helpers")
        print("\n📂 Copying lab_helpers to build directory...")
        shutil.copytree(
            lab_helpers_src,
            lab_helpers_dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        print(f"✓ Copied lab_helpers/ to {build_dir}/lab_helpers/")
    else:
        print("⚠️  lab_helpers directory not found in repository root")

    # Step 1: Install dependencies
    success, install_stats = install_dependencies(build_dir, requirements_content)
    if not success:
        return {"status": "error", "error": "Failed to install dependencies"}

    # Step 2: Create ZIP
    output_zip = f"{build_dir}.zip"
    success, zip_stats = create_lambda_zip(build_dir, handler_code, output_zip)
    if not success:
        return {"status": "error", "error": "Failed to create ZIP"}

    # Step 3: Validate size
    print("\n✅ Validating package size...")
    zip_size = zip_stats["zip_size"]
    uncompressed_size = get_dir_size(build_dir)

    # Check against Lambda limits
    DIRECT_UPLOAD_LIMIT = 50 * 1024 * 1024  # 50 MB
    S3_UPLOAD_LIMIT = 250 * 1024 * 1024  # 250 MB
    UNCOMPRESSED_LIMIT = 250 * 1024 * 1024  # 250 MB

    size_status = "✅"
    upload_method = "direct"

    if zip_size > DIRECT_UPLOAD_LIMIT:
        upload_method = "S3"
        size_status = "⚠️"

    if zip_size > S3_UPLOAD_LIMIT or uncompressed_size > UNCOMPRESSED_LIMIT:
        return {
            "status": "error",
            "error": f"Package too large: {format_size(zip_size)} (limit: 250MB)",
            "size_compressed": zip_size,
            "size_uncompressed": uncompressed_size,
        }

    print(f"{size_status} Compressed: {format_size(zip_size)} (50 MB direct / 250 MB S3 limit)")
    print(f"✓ Uncompressed: {format_size(uncompressed_size)} (250 MB limit)")
    print(f"✓ Deployment method: {upload_method}")

    print("\n" + "=" * 70)
    print("✅ PACKAGE CREATION SUCCESSFUL")
    print("=" * 70)

    return {
        "status": "success",
        "zip_path": output_zip,
        "build_dir": build_dir,
        "size_compressed": zip_size,
        "size_uncompressed": uncompressed_size,
        "size_formatted": format_size(zip_size),
        "upload_method": upload_method,
        "install_stats": install_stats,
        "zip_stats": zip_stats,
    }


# ============================================================================
# S3 OPERATIONS
# ============================================================================


def setup_s3_bucket(bucket_name: str, region_name: Optional[str] = None) -> Dict:
    """
    Create or verify S3 bucket for Lambda deployment packages

    Args:
        bucket_name: S3 bucket name
        region_name: AWS region

    Returns:
        Dictionary with bucket info
    """
    if region_name is None:
        region_name = AWS_REGION

    print("\n📦 Setting up S3 bucket for deployment packages...")

    s3 = boto3.client("s3", region_name=region_name)

    try:
        # Check if bucket exists
        s3.head_bucket(Bucket=bucket_name)
        print(f"✓ S3 bucket already exists: {bucket_name}")
        bucket_arn = f"arn:aws:s3:::{bucket_name}"

    except Exception as e:
        # head_bucket raises ClientError with error code '404', not NoSuchBucket
        # Check if bucket doesn't exist (404/NotFound error)
        is_not_found = False

        if hasattr(e, "response"):
            # Extract error code from ClientError response
            error_code = e.response.get("Error", {}).get("Code", "")
            http_status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            is_not_found = error_code == "404" or http_status == 404

        if is_not_found:
            # Create bucket
            print(f"✓ Creating S3 bucket: {bucket_name}")

            try:
                if region_name == "us-east-1":
                    s3.create_bucket(Bucket=bucket_name)
                else:
                    s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={"LocationConstraint": region_name},
                    )

                bucket_arn = f"arn:aws:s3:::{bucket_name}"
                print(f"✓ Bucket created: {bucket_arn}")
            except Exception as create_error:
                # If bucket creation fails (e.g., already exists from concurrent request), continue
                create_error_str = str(create_error)
                if any(err in create_error_str for err in ["BucketAlreadyExists", "BucketAlreadyOwnedByYou"]):
                    print(f"✓ S3 bucket exists (concurrent creation): {bucket_name}")
                    bucket_arn = f"arn:aws:s3:::{bucket_name}"
                else:
                    print(f"❌ Error creating bucket: {create_error}")
                    raise
        else:
            # Other error - re-raise
            print(f"❌ Error checking/setting up bucket: {e}")
            raise

    return {"bucket_name": bucket_name, "bucket_arn": bucket_arn, "region": region_name}


def upload_package_to_s3(
    zip_path: str,
    s3_bucket: str,
    s3_key: str = "lambda-packages/diagnostic-agent.zip",
    region_name: Optional[str] = None,
) -> Dict:
    """
    Upload ZIP package to S3

    Args:
        zip_path: Local ZIP file path
        s3_bucket: S3 bucket name
        s3_key: S3 object key
        region_name: AWS region

    Returns:
        Dictionary with S3 URI and upload info
    """
    if region_name is None:
        region_name = AWS_REGION

    if not os.path.exists(zip_path):
        return {"status": "error", "error": f"ZIP file not found: {zip_path}"}

    zip_size = get_zip_size(zip_path)

    print("\n📤 Uploading package to S3...")
    print(f"   Local file: {zip_path} ({format_size(zip_size)})")
    print(f"   Destination: s3://{s3_bucket}/{s3_key}")

    s3 = boto3.client("s3", region_name=region_name)

    try:
        # Upload with metadata
        s3.upload_file(
            zip_path,
            s3_bucket,
            s3_key,
            ExtraArgs={"Metadata": {"creator": "aiml301-lambda-packager", "model-id": MODEL_ID}},
        )

        s3_uri = f"s3://{s3_bucket}/{s3_key}"
        s3_url = f"https://{s3_bucket}.s3.{region_name}.amazonaws.com/{s3_key}"

        print("✓ Upload complete")
        print(f"  S3 URI: {s3_uri}")
        print(f"  HTTPS URL: {s3_url}")

        return {
            "status": "success",
            "s3_uri": s3_uri,
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            "s3_url": s3_url,
            "size": zip_size,
        }

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return {"status": "error", "error": str(e)}


# ============================================================================
# LAMBDA DEPLOYMENT
# ============================================================================


def create_lambda_function_from_zip(
    function_name: str,
    zip_path: str,
    s3_uri: Optional[str],
    role_arn: str,
    region_name: Optional[str] = None,
) -> Dict:
    """
    Create or update Lambda function from ZIP package

    Args:
        function_name: Lambda function name
        zip_path: Local ZIP file path (for direct upload, <50MB)
        s3_uri: S3 URI (for S3 upload, >50MB). Format: s3://bucket/key
        role_arn: Lambda execution role ARN
        region_name: AWS region

    Returns:
        Dictionary with Lambda function info
    """
    if region_name is None:
        region_name = AWS_REGION

    print("\n⚡ Deploying Lambda function...")
    print(f"   Function: {function_name}")
    print(f"   Role: {role_arn}")

    lambda_client = boto3.client("lambda", region_name=region_name)

    # Prepare code argument
    code_arg = {}
    if s3_uri:
        # S3-based upload (for larger packages)
        # Format: s3://bucket/key
        parts = s3_uri.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        code_arg = {"S3Bucket": bucket, "S3Key": key}
        upload_method = "S3"
        print("   Upload method: S3")
    elif zip_path and os.path.exists(zip_path):
        # Direct ZIP upload (for smaller packages)
        with open(zip_path, "rb") as f:
            code_arg = {"ZipFile": f.read()}
        upload_method = "Direct"
        print("   Upload method: Direct ZIP")
    else:
        return {"status": "error", "error": "No valid zip_path or s3_uri provided"}

    try:
        # Check if function exists
        try:
            func = lambda_client.get_function(FunctionName=function_name)  # noqa: F841

            # Function exists, update it
            print("✓ Function exists, updating...")

            response = lambda_client.update_function_code(FunctionName=function_name, **code_arg)

            # Wait for update to complete
            print("  Waiting for update to complete...")
            waiter = lambda_client.get_waiter("function_updated")
            waiter.wait(FunctionName=function_name)

            # Update configuration
            config_response = lambda_client.update_function_configuration(  # noqa: F841
                FunctionName=function_name,
                Runtime="python3.11",
                Handler="app.lambda_handler",
                Timeout=LAMBDA_CONFIG["timeout"],
                MemorySize=LAMBDA_CONFIG["memory_size"],
                Environment={"Variables": {"MODEL_ID": MODEL_ID, "REGION": region_name}},
            )

            print("✓ Configuration updated")

            function_arn = response["FunctionArn"]

        except lambda_client.exceptions.ResourceNotFoundException:
            # Function doesn't exist, create it
            print("✓ Creating new function...")

            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime="python3.11",
                Role=role_arn,
                Handler="app.lambda_handler",
                Code=code_arg,
                Timeout=LAMBDA_CONFIG["timeout"],
                MemorySize=LAMBDA_CONFIG["memory_size"],
                Environment={"Variables": {"MODEL_ID": MODEL_ID, "REGION": region_name}},
                Description="AIML301 Workshop - Diagnostics Agent (ZIP-based)",
            )

            # Wait for creation to complete
            print("  Waiting for function to become active...")
            waiter = lambda_client.get_waiter("function_active")
            waiter.wait(FunctionName=function_name)

            print("✓ Function created and active")

            function_arn = response["FunctionArn"]  # noqa: F841

        # Get final function details
        final_func = lambda_client.get_function(FunctionName=function_name)
        config = final_func["Configuration"]

        print("\n" + "=" * 70)
        print("✅ LAMBDA DEPLOYMENT SUCCESSFUL")
        print("=" * 70)
        print(f"Function: {config['FunctionName']}")
        print(f"ARN: {config['FunctionArn']}")
        print(f"Runtime: {config['Runtime']}")
        print(f"Memory: {config['MemorySize']} MB")
        print(f"Timeout: {config['Timeout']} s")
        print(f"State: {config['State']}")
        print(f"Upload method: {upload_method}")

        return {
            "status": "success",
            "function_name": config["FunctionName"],
            "function_arn": config["FunctionArn"],
            "runtime": config["Runtime"],
            "memory": config["MemorySize"],
            "timeout": config["Timeout"],
            "state": config["State"],
            "upload_method": upload_method,
        }

    except Exception as e:
        print(f"❌ Lambda deployment failed: {e}")
        import traceback

        traceback.print_exc()
        return {"status": "error", "error": str(e)}


# ============================================================================
# COMPLETE WORKFLOW
# ============================================================================


def setup_lambda_zip_deployment(
    handler_code: str, requirements_content: str, region_name: Optional[str] = None
) -> Dict:
    """
    Complete workflow: Package creation → (optional) S3 upload → Lambda deployment

    Args:
        handler_code: Python code for Lambda handler
        requirements_content: pip requirements text
        region_name: AWS region

    Returns:
        Complete deployment results
    """
    if region_name is None:
        region_name = AWS_REGION

    # Step 1: Create package
    package_result = create_deployment_package(handler_code, requirements_content)

    if package_result.get("status") == "error":
        return package_result

    # Step 2: Skip S3 for direct uploads (packages < 50MB)
    zip_path = package_result["zip_path"]
    upload_method = package_result.get("upload_method", "direct")
    s3_result = {
        "status": "success",
        "upload_method": "direct",
    }  # Default for direct uploads

    if upload_method == "S3":
        # Only setup S3 bucket if needed for large packages
        bucket_result = setup_s3_bucket("aiml301-lambda-packages", region_name)

        # Step 3: Upload to S3
        s3_result = upload_package_to_s3(zip_path, bucket_result["bucket_name"], region_name=region_name)

        if s3_result.get("status") == "error":
            return s3_result

    # Step 4: Get Lambda role from Parameter Store
    try:
        role_arn = get_parameter(PARAMETER_PATHS["lab_02"]["lambda_role_arn"], region_name=region_name)
    except Exception as e:
        print("❌ Could not retrieve Lambda role ARN from Parameter Store")
        return {"status": "error", "error": f"Lambda role not found: {e}"}

    # Step 5: Deploy Lambda
    lambda_result = create_lambda_function_from_zip(
        function_name="aiml301-diagnostic-agent",
        zip_path=zip_path if package_result["upload_method"] == "direct" else None,
        s3_uri=s3_result.get("s3_uri") if package_result["upload_method"] == "S3" else None,
        role_arn=role_arn,
        region_name=region_name,
    )

    if lambda_result.get("status") == "error":
        return lambda_result

    # Step 6: Save Lambda ARN to Parameter Store
    print("\n📝 Saving Lambda ARN to Parameter Store...")
    put_parameter(
        PARAMETER_PATHS["lab_02"]["lambda_function_arn"],
        lambda_result["function_arn"],
        description="Lambda function ARN for Lab 02 diagnostic agent",
        region_name=region_name,
    )

    return {
        "status": "success",
        "package": package_result,
        "s3": s3_result,
        "lambda": lambda_result,
        "region": region_name,
    }


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def get_package_info(zip_path: str) -> Dict:
    """
    Get detailed information about a ZIP package

    Args:
        zip_path: Path to ZIP file

    Returns:
        Dictionary with package information
    """
    if not os.path.exists(zip_path):
        return {"status": "error", "error": f"ZIP not found: {zip_path}"}

    zip_size = get_zip_size(zip_path)

    # List contents
    files = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            files = zf.namelist()
    except Exception as e:
        return {"status": "error", "error": f"Invalid ZIP: {e}"}

    # Categorize files
    has_app = "app.py" in files
    has_handler = any(f.endswith(".py") for f in files)
    lib_files = [f for f in files if f.startswith("lib/")]
    helper_files = [f for f in files if f.startswith("lab_helpers/")]

    return {
        "status": "success",
        "zip_path": zip_path,
        "zip_size": zip_size,
        "zip_size_formatted": format_size(zip_size),
        "total_files": len(files),
        "has_app_py": has_app,
        "has_handlers": has_handler,
        "lib_files_count": len(lib_files),
        "helper_files_count": len(helper_files),
        "files": {
            "total": len(files),
            "lib": len(lib_files),
            "helpers": len(helper_files),
            "root": len([f for f in files if "/" not in f]),
        },
    }


if __name__ == "__main__":
    # Example usage
    print("Lambda Packager - Example Usage\n")
    print("from lab_helpers.lab_02.lambda_packager import:")
    print("  - create_deployment_package()")
    print("  - setup_s3_bucket()")
    print("  - upload_package_to_s3()")
    print("  - create_lambda_function_from_zip()")
    print("  - get_package_info()")
