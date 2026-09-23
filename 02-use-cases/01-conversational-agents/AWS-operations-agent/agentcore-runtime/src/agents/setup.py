"""
Setup script for SDK Agent CLI deployment
Ensures proper packaging of shared dependencies
"""

from setuptools import setup, find_packages
import os

# Get the directory containing this setup.py
here = os.path.abspath(os.path.dirname(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))

setup(
    name="bac-sdk-agent",
    version="1.0.0",
    description="BAC SDK Agent for AgentCore Runtime",
    packages=find_packages(),
    py_modules=["sdk_agent"],
    install_requires=["strands-agents", "bedrock-agentcore", "pyyaml", "requests"],
    python_requires=">=3.11",
    package_data={"": ["*.yaml", "*.json"]},
    include_package_data=True,
)
