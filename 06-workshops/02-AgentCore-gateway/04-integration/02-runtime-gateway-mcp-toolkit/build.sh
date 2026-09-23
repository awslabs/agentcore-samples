#!/bin/bash
# Build script for agentcore-runtime-gw-mcp-toolkit

echo "Building package..."
python -m build

echo "Package built successfully!"
echo "To install locally: pip install dist/agentcore_runtime_gw_mcp_toolkit-0.1.0-py3-none-any.whl"
echo "To upload to PyPI: twine upload dist/*"
