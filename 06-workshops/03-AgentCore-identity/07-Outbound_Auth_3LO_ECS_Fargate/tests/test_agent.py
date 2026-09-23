# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Tests for agent runtime API."""

from unittest.mock import patch

import boto3

from tests.conftest import (
    TEST_AWS_REGION,
    TEST_BUCKET_NAME,
    TEST_USER_EMAIL,
    make_oidc_jwt,
    mock_bedrock_api_call,
)


class TestHealthEndpoints:
    def test_ping(self, agent_client):
        response = agent_client.get("/ping")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestInvocationsEndpoint:
    def test_invoke_agent_success(self, agent_client, s3_bucket):
        with (
            patch("backend.shared.alb_auth.verify_alb_jwt") as mock_verify,
            patch("botocore.client.BaseClient._make_api_call", new=mock_bedrock_api_call),
        ):
            mock_verify.return_value = {"sub": "test-user-id", "email": TEST_USER_EMAIL}
            response = agent_client.post(
                "/invocations",
                json={"session_id": "test-session", "user_message": "Hello agent"},
                headers={"x-amzn-oidc-data": make_oidc_jwt()},
            )

        assert response.status_code == 200
        assert "test response" in response.text

        # Verify session was saved to S3
        s3 = boto3.client("s3", region_name=TEST_AWS_REGION)
        objects = s3.list_objects_v2(Bucket=TEST_BUCKET_NAME)
        assert "Contents" in objects

    def test_invoke_agent_missing_oidc_header(self, agent_client):
        response = agent_client.post(
            "/invocations",
            json={"session_id": "test-session", "user_message": "Hello"},
        )
        assert response.status_code == 422  # FastAPI returns 422 for missing required header

    def test_invoke_agent_missing_fields(self, agent_client):
        with patch("backend.shared.alb_auth.verify_alb_jwt") as mock_verify:
            mock_verify.return_value = {"sub": "test-user-id", "email": TEST_USER_EMAIL}
            response = agent_client.post(
                "/invocations",
                json={},
                headers={"x-amzn-oidc-data": make_oidc_jwt()},
            )
        assert response.status_code == 422

    def test_invoke_agent_invalid_json(self, agent_client):
        with patch("backend.shared.alb_auth.verify_alb_jwt") as mock_verify:
            mock_verify.return_value = {"sub": "test-user-id", "email": TEST_USER_EMAIL}
            response = agent_client.post(
                "/invocations",
                content="invalid json",
                headers={
                    "Content-Type": "application/json",
                    "x-amzn-oidc-data": make_oidc_jwt(),
                },
            )
        assert response.status_code == 422
