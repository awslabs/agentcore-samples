"""
OpenSearch Tools

Per-document row-level security via the `owner_user_sub` keyword field.
Identity-claim source: the user's Okta `sub`, extracted upstream by server.py
from the validated Authorization header (the authorizer already validated
signature/issuer/audience/expiry; the tool layer extracts claims only). v1 RLS
scope: per-user filter only, policyholder archetype;
policyholder archetype; adjuster/admin scope expansion is a v2 deferral
(design §11 (e)).

Security Flow:
1. OBO_Gateway customJWTAuthorizer validates inbound JWT (Okta discovery URL,
   allowedAudience api://lakehouse-api).
2. OBO_Gateway TOKEN_EXCHANGE deposits the user's exchanged token into the
   AgentCore Identity vault (per-user, sub-keyed) and forwards a vault-issued
   Authorization header to this server.
3. Runtime authorizer revalidates the inbound JWT.
4. requestHeaderAllowlist makes the Authorization header readable in tool
   code via FastMCP ctx.request_context.request.headers.
5. server.py extracts `sub` from the Bearer token (PyJWT unverified decode).
6. opensearch_tools.search_claim_notes() filters AOSS query with
   term: { owner_user_sub: <sub> }.
"""

from typing import Any, Optional

import boto3
from aws_requests_auth.aws_auth import AWSRequestsAuth
from opensearchpy import OpenSearch, RequestsHttpConnection

# Index name for claim notes (matches load_sample_opensearch_data.py)
CLAIM_NOTES_INDEX = "claim-notes"


class OpenSearchClaimNotesTools:
    """
    Tools for querying claim-note documents in Amazon OpenSearch Serverless
    with per-user row-level security via the owner_user_sub field.
    """

    def __init__(self, region: str, collection_endpoint: str):
        """
        Initialize OpenSearch tools.

        Args:
            region: AWS region
            collection_endpoint: AOSS collection endpoint URL
                                 (e.g., https://<id>.us-east-1.aoss.amazonaws.com)
        """
        self.region = region
        self.collection_endpoint = collection_endpoint
        self.sts_client = boto3.client("sts", region_name=region)

        # Get account ID for diagnostic output
        self.account_id = self.sts_client.get_caller_identity()["Account"]

        print(f"🗄️  Using OpenSearch Serverless collection: {collection_endpoint}")

        # Build SigV4-signed OpenSearch client
        # The runtime's IAM role authenticates to the AOSS data plane via SigV4;
        # AOSS data-access policy (provisioned in 5b-obo-gateway-setup) grants
        # this role aoss:APIAccessAll on the collection.
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials available for OpenSearch SigV4 signing")

        # Strip scheme from endpoint for AWSRequestsAuth host parameter
        endpoint_host = collection_endpoint.replace("https://", "").replace("http://", "")

        auth = AWSRequestsAuth(
            aws_access_key=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_token=credentials.token,
            aws_host=endpoint_host,
            aws_region=region,
            aws_service="aoss",
        )

        self.client = OpenSearch(
            hosts=[{"host": endpoint_host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

        print("✅ OpenSearch client initialized")

    def search_claim_notes(self, user_sub: str, query: str, limit: int = 10) -> dict[str, Any]:
        """
        Search claim notes (free-text), scoped to the caller's identity.

        IMPORTANT: The query body filters on owner_user_sub via a `term` filter.
        The IAM-level data-access policy on the AOSS collection grants the
        runtime role broad access; row-level security is enforced by the
        query-shape contract here. Callers do not get to set owner_user_sub —
        it is always taken from the authenticated `sub`.

        Args:
            user_sub: `sub` of the authenticated caller, resolved by server.py
                      for the active IdP — `[OKTA]` from the OBO-exchanged
                      bearer in the Authorization header, `[COGNITO]` from the
                      interceptor-injected body context. Never caller-supplied.
            query: Natural-language / free-text search query
            limit: Maximum number of hits to return (1-100)

        Returns:
            Dict with success, hits (list of source docs), count, and
            security annotation
        """
        try:
            # Validate limit
            if limit < 1 or limit > 100:
                return {
                    "success": False,
                    "error": "Limit must be between 1 and 100",
                    "message": "Invalid limit parameter",
                }

            # Build query body: term filter on owner_user_sub is the ONLY hard
            # constraint (per-user RLS; design §7e — per-user
            # filter only, no group-archetype broadening in v1).
            #
            # The note_text match must NOT be a hard gate. Previously it sat in
            # `must`, so a blank/generic search phrase (e.g. "show me my notes") that
            # had no lexical overlap with the note bodies returned zero hits even
            # though the owner filter matched — an owner could fail to see their own
            # notes. Now: if the query is blank/whitespace, match_all; otherwise the
            # note_text match is a `should` (relevance boost only). Either way the
            # owner_user_sub filter alone determines which notes are returned, so an
            # owner always gets their own notes regardless of phrasing.
            if query and query.strip():
                bool_query = {
                    "should": [{"match": {"note_text": query}}],
                    "filter": [{"term": {"owner_user_sub": user_sub}}],
                }
            else:
                bool_query = {"must": [{"match_all": {}}], "filter": [{"term": {"owner_user_sub": user_sub}}]}

            search_body = {"size": limit, "query": {"bool": bool_query}}

            print("🔍 OpenSearch query body:")
            print(f"   index: {CLAIM_NOTES_INDEX}")
            print(f"   filter: term owner_user_sub={user_sub}")
            print(f"   match: note_text='{query}'")
            print(f"   size: {limit}")

            response = self.client.search(index=CLAIM_NOTES_INDEX, body=search_body)

            hits = response.get("hits", {}).get("hits", [])
            total = response.get("hits", {}).get("total", {})
            if isinstance(total, dict):
                total_value = total.get("value", 0)
            else:
                total_value = total

            # Format hits for output (return _source only; strip OpenSearch
            # internals like _index, _id, _score for tutorial readability)
            formatted_hits = []
            for hit in hits:
                source = hit.get("_source", {})
                formatted_hits.append(
                    {
                        "claim_id": source.get("claim_id", ""),
                        "owner_user_sub": source.get("owner_user_sub", ""),
                        "note_text": source.get("note_text", ""),
                        "note_type": source.get("note_type", ""),
                        "created_at": source.get("created_at", ""),
                        "_score": hit.get("_score", 0.0),
                    }
                )

            return {
                "success": True,
                "user_sub": user_sub,
                "query": query,
                "hits": formatted_hits,
                "count": len(formatted_hits),
                "total_matched": total_value,
                "message": f"Found {len(formatted_hits)} claim notes matching '{query}'",
                "security": "Row-level filtering via owner_user_sub term filter (per-user, sub-keyed)",
            }

        except Exception as e:
            return {"success": False, "error": str(e), "message": f"Error searching claim notes: {e!s}"}
