"""KVS signaling channel management.

Handles creating/finding a KVS signaling channel and fetching
TURN/ICE server credentials for WebRTC connections.
"""

import boto3
from loguru import logger

# Module state — initialized once via init()
channel_arn = None
_https_endpoint = None


def init(channel_name, region):
    """Initialize KVS: find or create signaling channel, resolve HTTPS endpoint."""
    global channel_arn, _https_endpoint
    client = boto3.client("kinesisvideo", region_name=region)

    # Find existing channel or create a new one
    try:
        resp = client.describe_signaling_channel(ChannelName=channel_name)
        channel_arn = resp["ChannelInfo"]["ChannelARN"]
    except client.exceptions.ResourceNotFoundException:
        resp = client.create_signaling_channel(ChannelName=channel_name, ChannelType="SINGLE_MASTER")
        channel_arn = resp["ChannelARN"]
    logger.info(f"Signaling channel: {channel_arn}")

    # Resolve the HTTPS endpoint for ICE server requests
    resp = client.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={
            "Protocols": ["HTTPS"],
            "Role": "MASTER",
        },
    )
    _https_endpoint = resp["ResourceEndpointList"][0]["ResourceEndpoint"]


def get_ice_servers(region, client_id=None):
    """Fetch raw ICE server config from KVS."""
    client = boto3.client("kinesis-video-signaling", region_name=region, endpoint_url=_https_endpoint)
    params = {"ChannelARN": channel_arn, "Service": "TURN"}
    if client_id:
        params["ClientId"] = client_id
    return client.get_ice_server_config(**params)["IceServerList"]


def get_rtc_ice_servers(region, client_id=None, turn_only=False):
    """Fetch ICE servers from KVS and return as RTCIceServer objects."""
    from aiortc import RTCIceServer

    servers = []
    for s in get_ice_servers(region, client_id):
        urls = [u for u in s["Uris"] if u.startswith("turn:")] if turn_only else s["Uris"]
        if urls:
            servers.append(RTCIceServer(urls=urls, username=s.get("Username"), credential=s.get("Password")))
    return servers
