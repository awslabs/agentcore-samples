from setuptools import setup, find_packages

setup(
    name="agentcore-runtime-gw-mcp-toolkit",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "agentcore-mcp-toolkit=agentcore_toolkit.main:main",
        ],
    },
)
