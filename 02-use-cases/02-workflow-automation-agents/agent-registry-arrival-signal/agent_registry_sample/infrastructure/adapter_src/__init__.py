# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trigger-adapter Lambda source (packaged as a CodeZip asset).

Contains handler.py, the logic-free SQS-to-A2A adapter. The __init__ makes it
importable as agent_registry_sample.infrastructure.adapter_src.handler for unit
tests. In the deployed Lambda bundle, handler.py sits at the zip root and is
imported as the top-level ``handler`` module (handler.on_message).
"""
