# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from typing import Any

from pydantic import BaseModel


class AccessToken(BaseModel):
    """Decoded JWT access token with extracted role and scope claims."""

    token: str
    roles: list[str]  # From custom:roles claim (Cognito) or roles claim (Entra ID)
    scopes: list[str]  # From scope/scp claim (e.g. "mcp/invoke agentcore/invoke")
    claims: dict[str, Any]  # Full decoded JWT payload
