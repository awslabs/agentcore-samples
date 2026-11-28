"""A Gateway Lambda target for bounded full-text retrieval from one Elasticsearch index."""

import json
import os
import re
from http.client import HTTPSConnection
from urllib.parse import urlsplit

import boto3


def lookup(query, origin, index, get_key, connect=HTTPSConnection):
    dest = urlsplit(origin)
    if (
        dest.scheme != "https"
        or not dest.hostname
        or dest.username
        or dest.password
        or dest.port not in {None, 443}
        or dest.path not in ("", "/")
        or dest.query
        or dest.fragment
    ):
        raise ValueError("Configure an approved Elasticsearch HTTPS origin on port 443")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", index):
        raise ValueError("Configure one index name, not an expression")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("Expected a nonempty search string up to 500 characters")
    request = {
        "size": 3,
        "_source": ["filename", "attachment.content"],
        "query": {"multi_match": {"query": query, "fields": ["attachment.content"]}},
    }
    key = get_key()
    if not isinstance(key, str) or not key or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", key):
        raise ValueError("Secret must contain only the encoded read-only Elasticsearch API key")
    conn = connect(dest.hostname, port=443, timeout=8)
    try:
        # HTTPSConnection verifies TLS by default and does not follow redirects.
        conn.request(
            "POST",
            f"/{index}/_search",
            body=json.dumps(request).encode(),
            headers={"Authorization": f"ApiKey {key}", "Content-Type": "application/json"},
        )
        result = conn.getresponse()
        if result.status != 200:
            raise RuntimeError(f"Search endpoint returned HTTP {result.status}")
        raw = result.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("Search response exceeds the sample limit")
        hits = json.loads(raw)["hits"]["hits"]
        return {
            "matches": [
                {"filename": hit["_source"]["filename"][:200], "excerpt": hit["_source"]["attachment"]["content"][:600]}
                for hit in hits[:3]
            ]
        }
    finally:
        conn.close()


def lambda_handler(event, context):
    if context.client_context.custom.get("bedrockAgentCoreToolName") != "policies___search" or set(event) != {"query"}:
        raise ValueError("Expected a policies search request")

    def get_key():
        return boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["KEY_SECRET_ARN"])["SecretString"]

    return lookup(event["query"], os.environ["ELASTIC_ORIGIN"], os.environ["ELASTIC_INDEX"], get_key)
