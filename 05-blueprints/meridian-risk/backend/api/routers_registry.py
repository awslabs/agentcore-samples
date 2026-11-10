# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`/api/registry/*` — browse, search, and govern the resource catalog."""

import logging
from typing import Any

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException

import aws
from models import SearchRequest, StatusRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/registry")


@router.get("/records")
def list_records() -> dict[str, Any]:
    """List every record in the registry, including non-approved ones.

    Unlike search, this shows DRAFT and PENDING_APPROVAL records too, so the
    console can drive the governance workflow.
    """
    registry_id = aws.require(aws.REGISTRY_ID, "aws.REGISTRY_ID")
    client = aws.client("bedrock-agentcore-control")

    try:
        records: list[dict] = []
        token = None
        while True:
            kwargs = {"registryId": registry_id, "maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            response = client.list_registry_records(**kwargs)
            records.extend(response.get("registryRecords", []))
            token = response.get("nextToken")
            if not token:
                break

        # ListRegistryRecords returns summaries without descriptor content, so
        # hydrate each record. The catalog is small enough (single digits) that
        # a Get per record is cheaper than making the UI fetch them lazily.
        for record in records:
            try:
                detail = client.get_registry_record(
                    registryId=registry_id, recordId=record["recordId"]
                )
                record["descriptors"] = detail.get("descriptors", {})
                record["statusReason"] = detail.get("statusReason")
            except ClientError as exc:
                logger.warning(
                    "Could not hydrate record %s: %s", record.get("name"), exc
                )
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"registry_id": registry_id, "count": len(records), "records": records}


@router.get("/records/{record_id}")
def get_record(record_id: str) -> dict[str, Any]:
    """Fetch one record with its full descriptor content."""
    registry_id = aws.require(aws.REGISTRY_ID, "aws.REGISTRY_ID")
    try:
        return aws.client("bedrock-agentcore-control").get_registry_record(
            registryId=registry_id, recordId=record_id
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(status_code=404, detail="Record not found") from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/search")
def search_registry(request: SearchRequest) -> dict[str, Any]:
    """Discover approved records by natural-language or keyword query.

    This is the Registry's consumer-facing API: it returns only APPROVED
    records, and newly approved records take roughly 30 seconds to index.
    """
    registry_id = aws.require(aws.REGISTRY_ID, "aws.REGISTRY_ID")
    try:
        response = aws.client("bedrock-agentcore").search_registry_records(
            searchQuery=request.query,
            registryIds=[registry_id],
            maxResults=request.max_results,
        )
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    records = response.get("registryRecords", [])
    return {"query": request.query, "count": len(records), "records": records}


@router.post("/records/{record_id}/status")
def update_record_status(record_id: str, request: StatusRequest) -> dict[str, Any]:
    """Advance a record through the governance workflow.

    PENDING_APPROVAL uses SubmitRegistryRecordForApproval; the terminal statuses
    use UpdateRegistryRecordStatus.
    """
    registry_id = aws.require(aws.REGISTRY_ID, "aws.REGISTRY_ID")
    client = aws.client("bedrock-agentcore-control")

    try:
        if request.status == "PENDING_APPROVAL":
            return client.submit_registry_record_for_approval(
                registryId=registry_id, recordId=record_id
            )
        return client.update_registry_record_status(
            registryId=registry_id,
            recordId=record_id,
            status=request.status,
            statusReason=request.reason,
        )
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
