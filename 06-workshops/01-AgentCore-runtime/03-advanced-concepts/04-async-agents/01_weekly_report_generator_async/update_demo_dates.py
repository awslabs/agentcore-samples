#!/usr/bin/env python3
"""
Update demo data dates to match the current week and upload to S3.

This script dynamically updates all dates in the demo_data directory to reflect
the current week, making the demo data always appear fresh and relevant, then
uploads the updated data to an S3 bucket.
"""

import json
import re
import argparse
import platform
from datetime import datetime, timedelta
from pathlib import Path
import boto3
from botocore.exceptions import ClientError


# Detect if running on Windows to disable emojis
IS_WINDOWS = platform.system() == "Windows"


def get_symbol(emoji, fallback):
    """Return emoji on non-Windows systems, fallback text on Windows."""
    return fallback if IS_WINDOWS else emoji


def get_current_week_info():
    """Get current week number and date range."""
    today = datetime.now()
    week_num = today.isocalendar()[1]

    # Get Monday of current week
    monday = today - timedelta(days=today.weekday())

    # Generate dates for the week (Mon-Fri)
    week_dates = {
        "monday": monday,
        "tuesday": monday + timedelta(days=1),
        "wednesday": monday + timedelta(days=2),
        "thursday": monday + timedelta(days=3),
        "friday": monday + timedelta(days=4),
        "saturday": monday + timedelta(days=5),
        "sunday": monday + timedelta(days=6),
    }

    return week_num, week_dates


def update_json_dates(file_path, week_dates):
    """Update dates in JSON files."""
    with open(file_path, "r") as f:
        data = json.load(f)

    # Recursively update all date fields
    def update_dates_recursive(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
                    # Map old dates to new dates based on day of week
                    old_date = datetime.strptime(value, "%Y-%m-%d")
                    day_name = old_date.strftime("%A").lower()
                    if day_name in week_dates:
                        obj[key] = week_dates[day_name].strftime("%Y-%m-%d")
                elif isinstance(value, (dict, list)):
                    update_dates_recursive(value)
        elif isinstance(obj, list):
            for item in obj:
                update_dates_recursive(item)

    update_dates_recursive(data)

    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"{get_symbol('✓', '+')} Updated {file_path.name}")


def update_markdown_dates(file_path, week_dates):
    """Update dates in Markdown files."""
    with open(file_path, "r") as f:
        content = f.read()

    # Update YYYY-MM-DD format dates
    def replace_date(match):
        old_date = datetime.strptime(match.group(0), "%Y-%m-%d")
        day_name = old_date.strftime("%A").lower()
        if day_name in week_dates:
            return week_dates[day_name].strftime("%Y-%m-%d")
        return match.group(0)

    content = re.sub(r"\d{4}-\d{2}-\d{2}", replace_date, content)

    # Update "Week of Month Day" format
    monday = week_dates["monday"]
    week_of_pattern = r"Week of \w+ \d{1,2}"
    content = re.sub(week_of_pattern, f"Week of {monday.strftime('%B %d')}", content)

    with open(file_path, "w") as f:
        f.write(content)

    print(f"{get_symbol('✓', '+')} Updated {file_path.name}")


def update_csv_dates(file_path, week_dates):
    """Update dates in CSV files."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    updated_lines = []
    for line in lines:
        # Update YYYY-MM-DD format dates
        def replace_date(match):
            old_date = datetime.strptime(match.group(0), "%Y-%m-%d")
            day_name = old_date.strftime("%A").lower()
            if day_name in week_dates:
                return week_dates[day_name].strftime("%Y-%m-%d")
            return match.group(0)

        updated_line = re.sub(r"\d{4}-\d{2}-\d{2}", replace_date, line)
        updated_lines.append(updated_line)

    with open(file_path, "w") as f:
        f.writelines(updated_lines)

    print(f"{get_symbol('✓', '+')} Updated {file_path.name}")


def rename_files_with_week(demo_data_path, week_num, week_dates):
    """Rename files to match current week number and dates."""
    monday = week_dates["monday"]

    # Patterns to update
    patterns = [
        (r"_week_\d{2}\.", f"_week_{week_num:02d}."),
        (
            r"_(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)_\d{2}\.",
            f"_{monday.strftime('%b').lower()}_{monday.day:02d}.",
        ),
    ]

    for file_path in demo_data_path.rglob("*"):
        if file_path.is_file():
            new_name = file_path.name

            for pattern, replacement in patterns:
                new_name = re.sub(pattern, replacement, new_name, flags=re.IGNORECASE)

            if new_name != file_path.name:
                new_path = file_path.parent / new_name
                file_path.rename(new_path)
                print(f"{get_symbol('✓', '+')} Renamed {file_path.name} {get_symbol('→', '->')} {new_name}")


def upload_to_s3(demo_data_path, bucket_name, prefix="demo_data"):
    """Upload all demo data files to S3 bucket."""
    s3_client = boto3.client("s3")

    print(f"\nUploading to S3 bucket: {bucket_name}")
    print(f"   Prefix: {prefix}/\n")

    uploaded_files = []

    for file_path in demo_data_path.rglob("*"):
        if file_path.is_file():
            # Calculate relative path from demo_data directory itself (not parent)
            relative_path = file_path.relative_to(demo_data_path)
            s3_key = f"{prefix}/{relative_path.as_posix()}" if prefix else relative_path.as_posix()

            try:
                # Determine content type
                content_type = "text/plain"
                if file_path.suffix == ".json":
                    content_type = "application/json"
                elif file_path.suffix == ".csv":
                    content_type = "text/csv"
                elif file_path.suffix == ".md":
                    content_type = "text/markdown"

                s3_client.upload_file(
                    str(file_path),
                    bucket_name,
                    s3_key,
                    ExtraArgs={"ContentType": content_type},
                )
                uploaded_files.append(s3_key)
                print(f"{get_symbol('✓', '+')} Uploaded {s3_key}")
            except ClientError as e:
                print(f"{get_symbol('✗', 'X')} Failed to upload {s3_key}: {e}")

    print(f"\n{get_symbol('✅', 'SUCCESS:')} Uploaded {len(uploaded_files)} files to s3://{bucket_name}/{prefix}/")
    return uploaded_files


def update_tools_config(bucket_name):
    """Update S3_BUCKET configuration in tools.py file."""
    script_dir = Path(__file__).parent
    tools_file = script_dir / "agent" / "tools.py"

    if not tools_file.exists():
        print(f"{get_symbol('⚠️', 'WARNING:')} tools.py not found at {tools_file}")
        return False

    try:
        # Read the file
        with open(tools_file, "r") as f:
            content = f.read()

        # Update S3_BUCKET line - match simple assignment
        bucket_pattern = r"S3_BUCKET = ['\"][^'\"]*['\"]"
        bucket_replacement = f"S3_BUCKET = '{bucket_name}'"

        if re.search(bucket_pattern, content):
            content = re.sub(bucket_pattern, bucket_replacement, content)
            print(f"{get_symbol('✓', '+')} Updated S3_BUCKET in tools.py to: {bucket_name}")

            # Write back
            with open(tools_file, "w") as f:
                f.write(content)

            return True
        else:
            print(f"{get_symbol('⚠️', 'WARNING:')} Could not find S3_BUCKET configuration in tools.py")
            return False

    except Exception as e:
        print(f"{get_symbol('❌', 'ERROR:')} Error updating tools.py: {e}")
        return False


def main():
    """Main function to update all demo data and upload to S3."""
    parser = argparse.ArgumentParser(
        description="Update demo data dates and upload to S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update dates and upload to S3 bucket
  python update_demo_dates.py --bucket my-weekly-reports-bucket
  
  # Update dates and upload with custom prefix
  python update_demo_dates.py --bucket my-bucket --prefix data/weekly-reports
  
  # Update dates only (no upload)
  python update_demo_dates.py
        """,
    )
    parser.add_argument("--bucket", type=str, help="S3 bucket name to upload demo data to")
    parser.add_argument(
        "--prefix",
        type=str,
        default="demo_data",
        help="S3 key prefix for uploaded files (default: demo_data)",
    )

    args = parser.parse_args()

    script_dir = Path(__file__).parent
    demo_data_path = script_dir / "demo_data"

    if not demo_data_path.exists():
        print(f"Error: demo_data directory not found at {demo_data_path}")
        return

    week_num, week_dates = get_current_week_info()
    monday = week_dates["monday"]

    print(f"\n{get_symbol('📅', 'CALENDAR:')} Updating demo data to current week:")
    print(f"   Week {week_num} of {monday.year}")
    print(f"   Week of {monday.strftime('%B %d, %Y')}\n")

    # First, rename files to match current week
    print("Renaming files...")
    rename_files_with_week(demo_data_path, week_num, week_dates)

    print("\nUpdating file contents...")

    # Update all files in demo_data
    for file_path in demo_data_path.rglob("*"):
        if file_path.is_file() and file_path.name != "README.md":
            if file_path.suffix == ".json":
                update_json_dates(file_path, week_dates)
            elif file_path.suffix == ".md":
                update_markdown_dates(file_path, week_dates)
            elif file_path.suffix == ".csv":
                update_csv_dates(file_path, week_dates)

    print(f"\n{get_symbol('✅', 'SUCCESS:')} Demo data updated successfully!")
    print(f"   All dates now reflect week {week_num} ({monday.strftime('%B %d - %B %d, %Y')})")

    # Upload to S3 if bucket specified
    if args.bucket:
        # Update tools.py configuration with the bucket name
        print(f"\n{get_symbol('📝', 'NOTE:')} Updating tools.py configuration...")
        update_tools_config(args.bucket)

        # Upload demo data
        upload_to_s3(demo_data_path, args.bucket, args.prefix)
    else:
        print(f"\n{get_symbol('💡', 'TIP:')} Use --bucket to upload data to S3 for AgentCore deployment")


if __name__ == "__main__":
    main()
