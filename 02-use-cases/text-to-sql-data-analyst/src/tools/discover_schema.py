# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Schema Discovery Tool

Discovers tables and columns from AWS Glue Data Catalog
based on keyword search.
"""

from dataclasses import dataclass
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError


@dataclass
class ColumnInfo:
    name: str
    type: str
    description: str


@dataclass
class TableInfo:
    name: str
    description: str
    columns: List[ColumnInfo]
    relationships: Optional[List[str]] = None


@dataclass
class SchemaInfo:
    tables: List[TableInfo]
    total_tables_in_catalog: int = 0


class SchemaDiscoveryError(Exception):
    pass


def discover_schema(
    keywords: List[str],
    database_name: str,
    aws_region: str = "us-east-1",
    max_tables: int = 5,
) -> SchemaInfo:
    """
    Discover relevant tables in Glue Data Catalog based on keywords.

    Args:
        keywords: List of keywords (e.g., ["sales", "customer"])
        database_name: Glue database name
        aws_region: AWS region
        max_tables: Maximum tables to return
    """
    if not keywords:
        raise SchemaDiscoveryError("Keywords list cannot be empty")
    if not database_name:
        raise SchemaDiscoveryError("Database name cannot be empty")

    try:
        glue_client = boto3.client("glue", region_name=aws_region)
    except Exception as e:
        raise SchemaDiscoveryError(f"Failed to create Glue client: {e}")

    try:
        relevant_tables = _search_tables_by_keywords(
            glue_client, database_name, keywords
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "EntityNotFoundException":
            raise SchemaDiscoveryError(f"Database '{database_name}' not found")
        raise SchemaDiscoveryError(f"Failed to search tables: {e}")

    relevant_tables = relevant_tables[:max_tables]
    schema_info = SchemaInfo(tables=[])

    for table_name in relevant_tables:
        try:
            table_metadata = glue_client.get_table(
                DatabaseName=database_name, Name=table_name
            )
            table = table_metadata["Table"]
            columns = [
                ColumnInfo(
                    name=col["Name"],
                    type=col["Type"],
                    description=col.get("Comment", ""),
                )
                for col in table.get("StorageDescriptor", {}).get("Columns", [])
            ]
            relationships = _extract_relationships(table.get("Description", ""))
            schema_info.tables.append(
                TableInfo(
                    name=table_name,
                    description=table.get("Description", ""),
                    columns=columns,
                    relationships=relationships if relationships else None,
                )
            )
        except ClientError:
            continue

    try:
        all_tables = glue_client.get_tables(DatabaseName=database_name)
        schema_info.total_tables_in_catalog = len(all_tables.get("TableList", []))
    except ClientError:
        pass

    return schema_info


def _search_tables_by_keywords(
    glue_client, database_name: str, keywords: List[str]
) -> List[str]:
    all_tables = []
    next_token = None
    while True:
        kwargs = {"DatabaseName": database_name}
        if next_token:
            kwargs["NextToken"] = next_token
        response = glue_client.get_tables(**kwargs)
        all_tables.extend(response.get("TableList", []))
        next_token = response.get("NextToken")
        if not next_token:
            break

    keywords_lower = [kw.lower() for kw in keywords]
    scored_tables = []

    for table in all_tables:
        table_name = table["Name"]
        table_desc = table.get("Description", "")
        score = 0

        for kw in keywords_lower:
            if kw in table_name.lower():
                score += 10
            if kw in table_desc.lower():
                score += 5
            for col in table.get("StorageDescriptor", {}).get("Columns", []):
                if kw in col["Name"].lower():
                    score += 3
                if kw in col.get("Comment", "").lower():
                    score += 1

        if score > 0:
            scored_tables.append((table_name, score))

    scored_tables.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in scored_tables]


def _extract_relationships(description: str) -> List[str]:
    if not description:
        return []
    relationships = []
    indicators = ["join", "related", "relationship", "foreign key", "references", "links"]
    for indicator in indicators:
        if indicator in description.lower():
            for sentence in description.split("."):
                if indicator in sentence.lower():
                    relationships.append(sentence.strip())
    return relationships
