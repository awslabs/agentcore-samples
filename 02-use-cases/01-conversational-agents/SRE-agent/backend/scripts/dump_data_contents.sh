#!/bin/bash
# dump_data_contents.sh - Recursively display all files in the data folder with paths and contents

# Get the script directory and navigate to backend
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$BACKEND_DIR/data"

# Check if data directory exists
if [ ! -d "$DATA_DIR" ]; then
    echo "Error: Data directory not found at $DATA_DIR"
    exit 1
fi

# Header
echo "=============================================="
echo "Data Directory Contents Dump"
echo "Generated on: $(date)"
echo "Data Directory: $DATA_DIR"
echo "=============================================="
echo

# Function to print file contents with formatting
print_file_contents() {
    local file="$1"
    local relative_path="${file#$DATA_DIR/}"
    
    echo "=================================================="
    echo "FILE: data/$relative_path"
    echo "=================================================="
    
    # Check if file is binary
    if file "$file" | grep -q "text"; then
        # Text file - display contents
        cat "$file"
    else
        # Binary file - just show file info
        echo "[Binary file - $(file "$file")]"
        echo "[Size: $(ls -lh "$file" | awk '{print $5}')]"
    fi
    
    echo
    echo
}

# Find and list all files that will be processed
echo "Files to be processed:"
echo "====================="
find "$DATA_DIR" -type f -name "*.json" -o -type f -name "*.txt" -o -type f -name "*.log" | grep -v "all_data_dump.txt" | sort | while read -r file; do
    relative_path="${file#$DATA_DIR/}"
    echo "  - data/$relative_path"
done
echo
echo

# Find and process all files recursively, excluding all_data_dump.txt
find "$DATA_DIR" -type f -name "*.json" -o -type f -name "*.txt" -o -type f -name "*.log" | grep -v "all_data_dump.txt" | sort | while read -r file; do
    relative_path="${file#$DATA_DIR/}"
    echo "Processing: data/$relative_path" >&2
    print_file_contents "$file"
done

# Summary at the end
echo "=============================================="
echo "Summary"
echo "=============================================="
echo "Total files (excluding all_data_dump.txt): $(find "$DATA_DIR" -type f | grep -v "all_data_dump.txt" | wc -l)"
echo "Total directories: $(find "$DATA_DIR" -type d | wc -l)"
echo "Total size: $(du -sh "$DATA_DIR" | cut -f1)"
echo

# Default output file
OUTPUT_FILE="$DATA_DIR/all_data_dump.txt"

# If running directly (not being sourced or piped)
if [ "$0" = "${BASH_SOURCE[0]}" ] && [ -t 1 ]; then
    echo "Generating data dump to: $OUTPUT_FILE"
    # Run the script again but redirect output to the file
    "$0" > "$OUTPUT_FILE" 2>&1
    echo "Data dump completed. Output saved to: $OUTPUT_FILE"
    echo "File size: $(ls -lh "$OUTPUT_FILE" | awk '{print $5}')"
fi