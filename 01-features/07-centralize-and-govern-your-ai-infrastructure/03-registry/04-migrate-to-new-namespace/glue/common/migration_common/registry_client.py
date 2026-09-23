"""boto3 (SDK) client factory for the registry control planes.

The migration talks to the control plane through modeled ``boto3`` operations rather than
hand-rolled SigV4 REST. Both service models come from the installed ``boto3``/``botocore``, so
the SDK must be recent enough to carry ``bedrock-agentcore-control`` (the preview source) and
``agent-registry-control`` (the target). Creating a client raises
``UnknownServiceError`` when it is not -- see the minimum version in ``requirements-dev.txt``.

The factory reuses the assumed-role/direct session managed by :class:`AwsApiInvoker`, so
credential handling and cross-account AssumeRole are unchanged; only the transport (raw REST
-> modeled SDK calls) changes.
"""

from __future__ import annotations

import logging
from typing import Any

from botocore.config import Config

from .util import USER_AGENT_EXTRA

LOGGER = logging.getLogger("agent-registry-migration.registry-client")

_CLIENT_CONFIG = Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
    user_agent_extra=USER_AGENT_EXTRA,
    signature_version="v4",
)


def build_control_plane_client(
    *,
    session: Any,
    service_name: str,
    region: str,
    endpoint_url: str,
) -> Any:
    """Build a modeled boto3 client for ``service_name`` at ``endpoint_url``.

    ``session`` is a ``boto3.Session`` (from :class:`AwsApiInvoker`) carrying the resolved
    credentials. The service model comes from the installed SDK, so signing name and HTTP
    bindings come from the model metadata and an SDK that predates the service raises
    ``UnknownServiceError`` here.
    """
    return session.client(
        service_name,
        region_name=region,
        endpoint_url=endpoint_url,
        config=_CLIENT_CONFIG,
    )
