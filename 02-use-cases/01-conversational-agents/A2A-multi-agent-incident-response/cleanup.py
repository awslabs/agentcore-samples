#!/usr/bin/env python3
"""
Cleanup script for A2A Multi-Agent Incident Response System.
This script removes all deployed resources in the correct order.
"""

import sys
import yaml
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock


class Colors:
    """ANSI color codes for terminal output"""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    END = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


# Thread-safe print lock for parallel deletions
print_lock = Lock()


def print_header(text: str, thread_safe: bool = False):
    """Print a formatted header"""
    output = f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}\n"
    output += f"{Colors.HEADER}{Colors.BOLD}{text.center(70)}{Colors.END}\n"
    output += f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.END}\n"

    if thread_safe:
        with print_lock:
            print(output, end="")
    else:
        print(output, end="")


def print_info(text: str, thread_safe: bool = False):
    """Print info message"""
    output = f"{Colors.CYAN}ℹ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_success(text: str, thread_safe: bool = False):
    """Print success message"""
    output = f"{Colors.GREEN}✓ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_warning(text: str, thread_safe: bool = False):
    """Print warning message"""
    output = f"{Colors.YELLOW}⚠ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def print_error(text: str, thread_safe: bool = False):
    """Print error message"""
    output = f"{Colors.RED}✗ {text}{Colors.END}"
    if thread_safe:
        with print_lock:
            print(output)
    else:
        print(output)


def get_input(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    """Get user input with optional default value"""
    if default:
        display_prompt = f"{Colors.BLUE}{prompt} [{Colors.GREEN}{default}{Colors.BLUE}]: {Colors.END}"
    else:
        display_prompt = f"{Colors.BLUE}{prompt}: {Colors.END}"

    while True:
        value = input(display_prompt).strip()

        if value:
            return value
        elif default:
            return default
        elif not required:
            return ""
        else:
            print_error("This field is required. Please provide a value.")


def run_command(cmd: list, capture_output: bool = True, timeout: int = 30) -> Tuple[bool, str]:
    """Run a shell command and return (success, output)"""
    try:
        result = subprocess.run(cmd, capture_output=capture_output, text=True, timeout=timeout, check=False)
        return (result.returncode == 0, result.stdout.strip() if capture_output else "")
    except subprocess.TimeoutExpired:
        return (False, f"Command timed out after {timeout} seconds")
    except FileNotFoundError:
        return (False, f"Command not found: {cmd[0]}")
    except Exception as e:
        return (False, str(e))


def load_config(config_path: Path) -> Optional[Dict[str, Any]]:
    """Load configuration from .a2a.config file"""
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return None


def wait_for_stack_deletion(stack_name: str, region: str, thread_safe: bool = False) -> bool:
    """Wait for CloudFormation stack to be deleted"""
    print_info(f"Waiting for stack '{stack_name}' to be deleted...", thread_safe=thread_safe)

    max_wait_time = 1800  # 30 minutes
    wait_interval = 15  # 15 seconds
    elapsed_time = 0

    while elapsed_time < max_wait_time:
        success, output = run_command(
            [
                "aws",
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                stack_name,
                "--region",
                region,
                "--query",
                "Stacks[0].StackStatus",
                "--output",
                "text",
            ]
        )

        # Stack no longer exists - this is the success case
        if not success:
            # Check for common stack deletion indicators in the output
            output_lower = output.lower()
            if (
                "does not exist" in output_lower
                or "validationerror" in output_lower
                or "stack with id" in output_lower
                or not output.strip()
            ):  # Empty output also means stack is gone
                print_success(
                    f"Stack '{stack_name}' deleted successfully!",
                    thread_safe=thread_safe,
                )
                return True
            else:
                # Some other unexpected error occurred, but continue checking
                # Don't show warnings for empty output
                if output.strip():
                    print_warning(
                        f"Error checking stack status: {output}",
                        thread_safe=thread_safe,
                    )
                # Continue to next iteration - stack might be gone

        # Stack still exists, check its status
        if success and output:
            status = output.strip()

            # DELETE_COMPLETE means stack is fully deleted and will disappear soon
            if status == "DELETE_COMPLETE":
                print_success(
                    f"Stack '{stack_name}' deleted successfully!",
                    thread_safe=thread_safe,
                )
                return True
            elif status == "DELETE_FAILED":
                print_error(f"Stack '{stack_name}' deletion failed!", thread_safe=thread_safe)
                print_error(
                    "Check the CloudFormation console for details",
                    thread_safe=thread_safe,
                )
                return False
            else:
                print_info(
                    f"[{stack_name}] Status: {status} (waiting...)",
                    thread_safe=thread_safe,
                )

        time.sleep(wait_interval)
        elapsed_time += wait_interval

    print_error(
        f"Timeout waiting for stack '{stack_name}' deletion (waited {max_wait_time}s)",
        thread_safe=thread_safe,
    )
    return False


def delete_stack(stack_name: str, region: str, step_name: str, thread_safe: bool = False) -> bool:
    """Delete a CloudFormation stack"""
    if not thread_safe:
        print_header(f"Deleting {step_name}")
    else:
        print_header(f"Deleting {step_name}", thread_safe=True)

    # Check if stack exists
    success, output = run_command(
        [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            stack_name,
            "--region",
            region,
        ]
    )

    if not success:
        # Check if stack doesn't exist (not an error)
        output_lower = output.lower()
        if (
            "does not exist" in output_lower
            or "stack with id" in output_lower
            or "validationerror" in output_lower
            or not output.strip()
        ):
            print_info(
                f"Stack '{stack_name}' does not exist, skipping",
                thread_safe=thread_safe,
            )
            return True
        # Some other error
        print_error(f"Error checking stack: {output}", thread_safe=thread_safe)
        return False

    # Delete the stack
    print_info(f"Deleting CloudFormation stack: {stack_name}", thread_safe=thread_safe)
    success, output = run_command(
        [
            "aws",
            "cloudformation",
            "delete-stack",
            "--stack-name",
            stack_name,
            "--region",
            region,
        ]
    )

    if success:
        print_success(f"Stack deletion initiated: {stack_name}", thread_safe=thread_safe)
        return wait_for_stack_deletion(stack_name, region, thread_safe=thread_safe)
    else:
        print_error(f"Failed to delete stack: {output}", thread_safe=thread_safe)
        return False


def empty_s3_bucket(bucket_name: str, region: str) -> bool:
    """Empty all objects from S3 bucket"""
    print_info(f"Checking if bucket '{bucket_name}' exists...")

    # Check if bucket exists
    success, output = run_command(["aws", "s3api", "head-bucket", "--bucket", bucket_name, "--region", region])

    if not success:
        if "404" in output or "Not Found" in output:
            print_warning(f"Bucket '{bucket_name}' does not exist, skipping")
            return True
        print_error(f"Error checking bucket: {output}")
        return False

    print_info(f"Emptying S3 bucket: {bucket_name}")
    success, output = run_command(["aws", "s3", "rm", f"s3://{bucket_name}", "--recursive", "--region", region])

    if success or "remove" in output:
        print_success(f"S3 bucket '{bucket_name}' emptied successfully")
        return True
    else:
        print_warning(f"No objects to delete or error: {output}")
        return True  # Continue even if empty fails


def delete_s3_bucket(bucket_name: str, region: str) -> bool:
    """Delete S3 bucket"""
    print_info(f"Deleting S3 bucket: {bucket_name}")

    success, output = run_command(["aws", "s3", "rb", f"s3://{bucket_name}", "--region", region])

    if success:
        print_success(f"S3 bucket '{bucket_name}' deleted successfully")
        return True
    else:
        if "NoSuchBucket" in output or "does not exist" in output:
            print_warning(f"Bucket '{bucket_name}' does not exist")
            return True
        print_error(f"Failed to delete bucket: {output}")
        return False


def cleanup_s3_bucket(bucket_name: str, region: str) -> bool:
    """Empty and delete S3 bucket"""
    print_header("Step 5: Delete S3 Bucket")

    if not empty_s3_bucket(bucket_name, region):
        print_warning("Failed to empty bucket, but continuing...")

    return delete_s3_bucket(bucket_name, region)


def delete_stack_parallel(stack_name: str, region: str, step_name: str) -> Tuple[str, bool]:
    """Delete a stack in parallel (thread-safe)"""
    try:
        success = delete_stack(stack_name, region, step_name, thread_safe=True)
        return (step_name, success)
    except Exception as e:
        print_error(f"Error deleting {step_name}: {str(e)}", thread_safe=True)
        return (step_name, False)


def delete_agent_stacks_parallel(config: Dict[str, Any], region: str) -> bool:
    """Delete all three agent stacks in parallel"""
    print_header("Steps 1-3: Delete Agent Stacks (Parallel)")
    print_info("Deleting Host, Web Search, and Monitoring agent stacks in parallel...")
    print_warning("This is faster but may produce interleaved output\n")

    # Prepare deletion tasks (in reverse dependency order)
    tasks = [
        (config["stacks"]["host_agent"], region, "Host Agent Stack"),
        (config["stacks"]["web_search_agent"], region, "Web Search Agent Stack"),
        (config["stacks"]["monitoring_agent"], region, "Monitoring Agent Stack"),
    ]

    # Delete stacks in parallel using ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all deletion tasks
        future_to_stack = {executor.submit(delete_stack_parallel, *task): task[2] for task in tasks}

        # Collect results as they complete
        for future in as_completed(future_to_stack):
            stack_label = future_to_stack[future]
            try:
                name, success = future.result()
                results[name] = success
                if success:
                    print_success(f"✓ {name} deletion completed", thread_safe=True)
                else:
                    print_error(f"✗ {name} deletion failed", thread_safe=True)
            except Exception as e:
                print_error(f"Exception deleting {stack_label}: {str(e)}", thread_safe=True)
                results[stack_label] = False

    # Check if all deletions succeeded
    all_success = all(results.values())

    print()
    if all_success:
        print_success("All agent stacks deleted successfully!")
    else:
        print_error("One or more agent stacks failed to delete")
        for stack, success in results.items():
            status = "✓" if success else "✗"
            print_info(f"  {status} {stack}: {'Success' if success else 'Failed'}")

    return all_success


def run_cleanup(config: Dict[str, Any], parallel: bool = True) -> bool:
    """Run all cleanup steps in reverse order"""
    print_header("Starting Cleanup")
    print_warning("This will DELETE all deployed resources")
    print_warning("This action cannot be undone!")

    if parallel:
        print_info("Using parallel deletion - approximately 7-10 minutes\n")
    else:
        print_info("Using sequential deletion - approximately 10-15 minutes\n")

    confirm = get_input(
        f"{Colors.RED}Are you absolutely sure you want to delete all resources? Type 'DELETE' to confirm{Colors.END}",
        default=None,
        required=True,
    )

    if confirm != "DELETE":
        print_warning("Cleanup cancelled. Resources were not deleted.")
        return False

    print()
    region = config["aws"]["region"]
    all_success = True

    # Steps 1-3: Delete agent stacks
    if parallel:
        # Delete all three agent stacks in parallel
        if not delete_agent_stacks_parallel(config, region):
            print_error("Failed to delete one or more agent stacks")
            all_success = False
    else:
        # Delete agent stacks sequentially (original behavior)
        # Step 1: Delete Host Agent (reverse order)
        if not delete_stack(config["stacks"]["host_agent"], region, "Host Agent Stack"):
            print_error("Failed to delete Host Agent stack")
            all_success = False

        print()

        # Step 2: Delete Web Search Agent
        if not delete_stack(config["stacks"]["web_search_agent"], region, "Web Search Agent Stack"):
            print_error("Failed to delete Web Search Agent stack")
            all_success = False

        print()

        # Step 3: Delete Monitoring Agent
        if not delete_stack(config["stacks"]["monitoring_agent"], region, "Monitoring Agent Stack"):
            print_error("Failed to delete Monitoring Agent stack")
            all_success = False

    print()

    # Step 4: Delete Cognito Stack
    if not delete_stack(config["stacks"]["cognito"], region, "Cognito Stack"):
        print_error("Failed to delete Cognito stack")
        all_success = False

    print()

    # Step 5: Delete S3 Bucket
    if not cleanup_s3_bucket(config["s3"]["smithy_models_bucket"], region):
        print_error("Failed to delete S3 bucket")
        all_success = False

    print()

    # Delete .a2a.config file
    config_path = Path(".a2a.config")
    if config_path.exists():
        try:
            config_path.unlink()
            print_success("Deleted .a2a.config file")
        except Exception as e:
            print_warning(f"Failed to delete .a2a.config: {e}")
            print_info("You can manually delete it if needed")

    print()

    if all_success:
        print_header("Cleanup Complete!")
        print_success("All resources have been deleted successfully!")
        print_info("\nTo deploy again, run: uv run deploy.py")
    else:
        print_header("Cleanup Completed with Errors")
        print_warning("Some resources may not have been deleted successfully")
        print_info("Check the errors above and manually delete remaining resources if needed")
        if config_path.exists():
            print_info("Note: .a2a.config was not deleted due to cleanup errors")

    return all_success


def list_resources(config: Dict[str, Any]):
    """List all resources that will be deleted"""
    print_header("Resources to be Deleted")

    print(f"{Colors.BOLD}CloudFormation Stacks:{Colors.END}")
    print(f"  1. {config['stacks']['host_agent']} (Host Agent)")
    print(f"  2. {config['stacks']['web_search_agent']} (Web Search Agent)")
    print(f"  3. {config['stacks']['monitoring_agent']} (Monitoring Agent)")
    print(f"  4. {config['stacks']['cognito']} (Cognito)")

    print(f"\n{Colors.BOLD}S3 Resources:{Colors.END}")
    print(f"  5. {config['s3']['smithy_models_bucket']} (S3 Bucket + Contents)")

    print(f"\n{Colors.BOLD}Region:{Colors.END} {config['aws']['region']}")
    print()


def main():
    """Main entry point"""
    try:
        print_header("A2A Multi-Agent System - Cleanup Script")

        # Load configuration
        config_path = Path(".a2a.config")
        config = load_config(config_path)

        if not config:
            print_error("Configuration file '.a2a.config' not found!")
            print_info("Make sure you're in the project directory where deployment was run.")
            print_info("If you deployed manually, you'll need to delete resources manually as well.")
            sys.exit(1)

        print_success("Configuration loaded from .a2a.config")

        # List resources
        list_resources(config)

        # Ask if user wants to proceed
        proceed = get_input("Do you want to proceed with cleanup? (yes/no)", default="no", required=True).lower() in [
            "yes",
            "y",
        ]

        if not proceed:
            print_warning("Cleanup cancelled by user.")
            sys.exit(0)

        print()

        # Ask about parallel deletion
        use_parallel = get_input(
            "Use parallel deletion for faster execution? (yes/no)",
            default="yes",
            required=True,
        ).lower() in ["yes", "y"]

        print()

        # Run cleanup
        if run_cleanup(config, parallel=use_parallel):
            sys.exit(0)
        else:
            sys.exit(1)

    except KeyboardInterrupt:
        print_error("\n\nCleanup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print_error(f"An error occurred: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
