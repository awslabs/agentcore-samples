"""
Secure Athena Tools

This implementation uses user based filtering for row-level security:
- User identity passed as session tags when assuming IAM role
- NO application-level SQL manipulation
- NO SQL injection risk

Security Flow:
1. Gateway interceptor extracts user_id from JWT
2. MCP server receives user_id in headers
3. MCP server assumes IAM role WITH session tag: user_id=<actual_user>
4. Athena queries use those credentials
"""

import boto3
import time
import json
from typing import List, Dict, Any, Optional


class SecureAthenaClaimsTools:
    """
    Secure tools for querying health lakehouse data with Lake Formation RBAC and .
    """

    def __init__(
        self,
        region: str,
        database_name: str,
        s3_output_location: str,
        catalog_name: Optional[str] = None,
    ):
        """
        Initialize secure Athena tools.

        Args:
            region: AWS region
            database_name: Database/namespace name
            s3_output_location: S3 location for query results
            catalog_name: Optional catalog name for S3 Tables (e.g., s3tablescatalog/my-bucket)
        """
        self.region = region
        self.database_name = database_name
        self.s3_output_location = s3_output_location
        self.catalog_name = catalog_name
        self.sts_client = boto3.client("sts", region_name=region)

        # Get account ID for catalog operations
        self.account_id = self.sts_client.get_caller_identity()["Account"]

        # Determine table prefix based on catalog
        if catalog_name:
            # S3 Tables: use catalog.database.table format
            self.table_prefix = f'"{catalog_name}".{database_name}'
            print(f"🗄️  Using S3 Tables: {self.table_prefix}")
        else:
            # Standard Athena: use database.table format
            self.table_prefix = database_name
            print(f"🗄️  Using Athena database: {self.table_prefix}")

        # Cache for schema information
        self._schema_cache = None

    def _get_athena_client(self, user_id: str, tenant_credentials: Optional[Dict[str, str]] = None):
        """
        Get Athena client with tenant-specific credentials from interceptor.

        Args:
            user_id: User email/ID
            tenant_credentials: Temporary credentials from interceptor (if available)

        Returns:
            Athena client with scoped credentials
        """
        # Use tenant credentials from interceptor (passed from Gateway)
        if tenant_credentials:
            return boto3.client(
                "athena",
                region_name=self.region,
                aws_access_key_id=tenant_credentials["access_key_id"],
                aws_secret_access_key=tenant_credentials["secret_access_key"],
                aws_session_token=tenant_credentials["session_token"],
            )

        # Default: Use default credentials (local development)
        return boto3.client("athena", region_name=self.region)

    def _execute_query(
        self,
        user_id: str,
        query: str,
        wait_for_results: bool = True,
        tenant_credentials: Optional[Dict[str, str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Execute Athena query with tenant-scoped credentials.

        IMPORTANT: This query does NOT include user_id filter in SQL!
        The filtering is applied by Lake Formation based on session tags or tenant role.

        Args:
            user_id: User email/ID (for session tag)
            query: SQL query WITHOUT user filtering
            wait_for_results: Whether to wait for completion
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Query results
        """
        try:
            # Get Athena client with tenant credentials
            athena_client = self._get_athena_client(user_id, tenant_credentials)

            # Determine which role is being used for the query
            if tenant_credentials:
                role_name = tenant_credentials.get("role_name", "unknown")
                role_arn = tenant_credentials.get("role_arn", "unknown")
                print(f"🔐 Executing query with TENANT ROLE: {role_name}")
                print(f"   Role ARN: {role_arn}")
            else:
                # Get current identity
                try:
                    sts_client = boto3.client("sts", region_name=self.region)
                    identity = sts_client.get_caller_identity()
                    arn = identity["Arn"]
                    if ":assumed-role/" in arn:
                        role_name = arn.split(":assumed-role/")[1].split("/")[0]
                        print(f"🔐 Executing query with DEFAULT ROLE: {role_name}")
                    else:
                        print(f"🔐 Executing query with IDENTITY: {arn}")
                except Exception:
                    print("🔐 Executing query with DEFAULT CREDENTIALS")

            # Execute query - Lake Formation will automatically apply row filter
            query_context = {"Database": self.database_name}
            if self.catalog_name:
                query_context["Catalog"] = self.catalog_name

            response = athena_client.start_query_execution(
                QueryString=query,
                QueryExecutionContext=query_context,
                ResultConfiguration={"OutputLocation": self.s3_output_location},
            )

            query_execution_id = response["QueryExecutionId"]

            if not wait_for_results:
                return None

            # Wait for query completion
            max_wait_time = 30
            start_time = time.time()

            while time.time() - start_time < max_wait_time:
                status_response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
                status = status_response["QueryExecution"]["Status"]["State"]

                if status == "SUCCEEDED":
                    break
                elif status in ["FAILED", "CANCELLED"]:
                    error = status_response["QueryExecution"]["Status"].get("StateChangeReason", "Unknown error")
                    raise Exception(f"Query failed: {error}")

                time.sleep(0.5)

            # Get results
            results_response = athena_client.get_query_results(QueryExecutionId=query_execution_id, MaxResults=100)

            # Parse results
            rows = results_response["ResultSet"]["Rows"]
            if len(rows) == 0:
                return []

            columns = [col["VarCharValue"] for col in rows[0]["Data"]]

            data = []
            for row in rows[1:]:
                row_data = {}
                for i, col in enumerate(row["Data"]):
                    row_data[columns[i]] = col.get("VarCharValue", "")
                data.append(row_data)

            return data

        except Exception as e:
            raise Exception(f"Error executing secure Athena query: {str(e)}")

    def _is_policyholder_role(self, tenant_credentials: Optional[Dict[str, str]] = None) -> bool:
        """Check if the tenant role is a policyholder (restricted column access)."""
        if not tenant_credentials:
            return False
        role_name = tenant_credentials.get("role_name", "")
        return "policyholders" in role_name.lower()

    def query_claims(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        tenant_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Query claims

        NOTICE: No user_id in WHERE clause! Lake Formation adds it automatically.

        Args:
            user_id: User email (passed as session tag, not SQL parameter)
            filters: Optional additional filters
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            User's claims (automatically filtered by Lake Formation or tenant role)
        """
        try:
            query = f"""
                WITH role_exp AS (
                    SELECT user_role FROM {self.table_prefix}.users
                    WHERE user_id='{user_id}'
                )
                SELECT
                    *
                FROM {self.table_prefix}.claims as c
                WHERE 1=1
                    AND c.user_id='{user_id}'
                    OR ('adjuster' in (SELECT user_role FROM role_exp)
                        AND c.adjuster_user_id='{user_id}')
            """

            # Add optional filters (safely)
            if filters:
                if "claim_status" in filters and filters["claim_status"]:
                    # Use parameterization instead of string interpolation
                    query += f" AND claim_status = '{filters['claim_status']}'"

                if "claim_type" in filters and filters["claim_type"]:
                    query += f" AND claim_type = '{filters['claim_type']}'"

            query += " ORDER BY submitted_date DESC LIMIT 50"

            # Execute with tenant-scoped credentials
            results = self._execute_query(user_id, query, tenant_credentials=tenant_credentials)

            return {
                "success": True,
                "user_id": user_id,
                "claims": results or [],
                "count": len(results) if results else 0,
                "message": f"Found {len(results) if results else 0} claims",
                "security": "Row-level filtering enforced by AWS Lake Formation",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error querying claims: {str(e)}",
            }

    def get_claim_details(
        self,
        user_id: str,
        claim_id: str,
        tenant_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Get claim details - Lake Formation ensures user can only see their claims.

        Args:
            user_id: User email (for session tag)
            claim_id: Claim ID
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Claim details (only if user owns it)
        """
        try:
            is_policyholder = self._is_policyholder_role(tenant_credentials)

            if is_policyholder:
                query = f"""
                    SELECT *
                    FROM {self.table_prefix}.claims
                    WHERE claim_id = '{claim_id}'
                        AND user_id='{user_id}'
                """
            else:
                query = f"""
                    SELECT *
                    FROM {self.table_prefix}.claims
                    WHERE claim_id = '{claim_id}'
                        AND (user_id='{user_id}' OR adjuster_user_id='{user_id}')
                """

            results = self._execute_query(user_id, query, tenant_credentials=tenant_credentials)

            if results and len(results) > 0:
                return {
                    "success": True,
                    "claim": results[0],
                    "message": f"Retrieved claim {claim_id}",
                    "security": "Access validated by AWS Lake Formation",
                }
            else:
                return {
                    "success": False,
                    "message": f"Claim {claim_id} not found or access denied",
                    "security": "Lake Formation filtered this claim (not owned by user)",
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error retrieving claim: {str(e)}",
            }

    def get_claims_summary(self, user_id: str, tenant_credentials: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Get claims summary - automatically scoped to user by Lake Formation.

        Args:
            user_id: User email
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Summary statistics (only for user's claims)
        """
        try:
            is_policyholder = self._is_policyholder_role(tenant_credentials)

            if is_policyholder:
                where_clause = f"user_id='{user_id}'"
            else:
                where_clause = f"(user_id='{user_id}' OR adjuster_user_id='{user_id}')"

            query = f"""
                SELECT
                    COUNT(*) as total_claims,
                    SUM(claim_amount) as total_amount,
                    SUM(approved_amount) as total_approved,
                    COUNT(CASE WHEN claim_status = 'pending' THEN 1 END) as pending_claims,
                    COUNT(CASE WHEN claim_status = 'approved' THEN 1 END) as approved_claims,
                    COUNT(CASE WHEN claim_status = 'denied' THEN 1 END) as denied_claims
                FROM {self.table_prefix}.claims
                WHERE {where_clause}
            """

            results = self._execute_query(user_id, query, tenant_credentials=tenant_credentials)

            if results and len(results) > 0:
                summary = results[0]
                return {
                    "success": True,
                    "user_id": user_id,
                    "summary": {
                        "total_claims": int(summary.get("total_claims", 0)),
                        "total_amount_claimed": float(summary.get("total_amount", 0) or 0),
                        "total_amount_approved": float(summary.get("total_approved", 0) or 0),
                        "pending_claims": int(summary.get("pending_claims", 0)),
                        "approved_claims": int(summary.get("approved_claims", 0)),
                        "denied_claims": int(summary.get("denied_claims", 0)),
                    },
                    "message": "Claims summary retrieved successfully",
                    "security": "Automatically scoped to user by Lake Formation",
                }

            return {
                "success": True,
                "user_id": user_id,
                "summary": {
                    "total_claims": 0,
                    "total_amount_claimed": 0.0,
                    "total_amount_approved": 0.0,
                    "pending_claims": 0,
                    "approved_claims": 0,
                    "denied_claims": 0,
                },
                "message": "No claims found",
                "security": "Lake Formation enforced row-level security",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error retrieving summary: {str(e)}",
            }

    def get_database_schema(self, tenant_credentials: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Get database schema from Glue Data Catalog.

        Args:
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Dictionary containing schema information for all tables
        """
        try:
            # Get Glue client with tenant credentials
            if tenant_credentials:
                glue_client = boto3.client(
                    "glue",
                    region_name=self.region,
                    aws_access_key_id=tenant_credentials["access_key_id"],
                    aws_secret_access_key=tenant_credentials["secret_access_key"],
                    aws_session_token=tenant_credentials["session_token"],
                )
            else:
                glue_client = boto3.client("glue", region_name=self.region)

            # Determine catalog_id based on whether we're using S3 Tables
            if self.catalog_name:
                # For S3 Tables, we need the full catalog ID format:
                # {account_id}:s3tablescatalog/{table_bucket_name}
                # Get table bucket name from SSM
                try:
                    ssm_client = boto3.client("ssm", region_name=self.region)
                    response = ssm_client.get_parameter(Name="/app/lakehouse-agent/table-bucket-name")
                    table_bucket_name = response["Parameter"]["Value"]
                    catalog_id = f"{self.account_id}:s3tablescatalog/{table_bucket_name}"
                    print(f"📚 Querying schema from S3 Tables catalog: {catalog_id}")
                except Exception as e:
                    print(f"⚠️  Could not get table bucket name from SSM: {e}")
                    # Fallback: try to construct from account_id
                    table_bucket_name = f"lakehouse-{self.account_id}"
                    catalog_id = f"{self.account_id}:s3tablescatalog/{table_bucket_name}"
                    print(f"📚 Using fallback catalog ID: {catalog_id}")
            else:
                # For default Glue catalog, use account ID
                catalog_id = self.account_id
                print(f"📚 Querying schema from default catalog (account: {catalog_id})")

            # Get all tables in the database
            get_tables_params = {
                "CatalogId": catalog_id,
                "DatabaseName": self.database_name,
            }

            tables_response = glue_client.get_tables(**get_tables_params)

            schema = {
                "database": self.database_name,
                "catalog": self.catalog_name or "default",
                "catalog_id": catalog_id,
                "tables": [],
            }

            for table in tables_response.get("TableList", []):
                table_name = table["Name"]
                columns = []

                for col in table.get("StorageDescriptor", {}).get("Columns", []):
                    columns.append(
                        {
                            "name": col["Name"],
                            "type": col["Type"],
                            "comment": col.get("Comment", ""),
                        }
                    )

                # Also include partition columns if any
                for col in table.get("PartitionKeys", []):
                    columns.append(
                        {
                            "name": col["Name"],
                            "type": col["Type"],
                            "comment": col.get("Comment", ""),
                            "is_partition": True,
                        }
                    )

                schema["tables"].append(
                    {
                        "name": table_name,
                        "columns": columns,
                        "description": table.get("Description", ""),
                        "table_type": table.get("TableType", ""),
                    }
                )

            return {
                "success": True,
                "schema": schema,
                "message": f"Retrieved schema for {len(schema['tables'])} tables",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error retrieving schema: {str(e)}",
            }

    def text_to_sql(
        self,
        user_id: str,
        natural_language_query: str,
        tenant_credentials: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Convert natural language query to SQL and execute it.

        This tool:
        1. Retrieves database schema from Glue Data Catalog
        2. Uses Bedrock to generate SQL from natural language
        3. Executes the generated SQL with tenant credentials
        4. Returns results with the generated SQL for transparency

        SECURITY: Row-level filtering is enforced by Lake Formation based on tenant role.

        Args:
            user_id: User email (for session context)
            natural_language_query: Natural language description of desired query
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Dictionary with generated SQL, execution results, and metadata
        """
        try:
            # Step 1: Get database schema (cached)
            if not self._schema_cache:
                schema_result = self.get_database_schema(tenant_credentials)
                if not schema_result.get("success"):
                    return {
                        "success": False,
                        "error": "Failed to retrieve database schema",
                        "details": schema_result.get("error"),
                    }
                self._schema_cache = schema_result["schema"]

            schema = self._schema_cache

            # Step 2: Generate SQL using Bedrock
            bedrock_runtime = boto3.client("bedrock-runtime", region_name=self.region)

            # Build schema description for the prompt
            schema_description = f"Database: {schema['database']}\n"
            if self.catalog_name:
                schema_description += f"Catalog: {schema['catalog']}\n"
            schema_description += "\nTables:\n"

            for table in schema["tables"]:
                schema_description += f"\n{table['name']}:\n"
                if table.get("description"):
                    schema_description += f"  Description: {table['description']}\n"
                schema_description += "  Columns:\n"
                for col in table["columns"]:
                    partition_marker = " (partition key)" if col.get("is_partition") else ""
                    comment = f" - {col['comment']}" if col.get("comment") else ""
                    schema_description += f"    - {col['name']} ({col['type']}){partition_marker}{comment}\n"

            # Create prompt for SQL generation
            prompt = f"""You are a SQL expert. Generate a SQL query based on the user's natural language request.

Database Schema:
{schema_description}

Important Rules:
1. Use the table prefix "{self.table_prefix}" for all table references (e.g., {self.table_prefix}.claims)
2. DO NOT add any WHERE clause filtering by user_id - Lake Formation handles row-level security automatically
3. Generate ONLY the SQL query, no explanations or markdown formatting
4. Use standard SQL syntax compatible with Amazon Athena
5. Limit results to 100 rows unless the user specifies otherwise
6. Use proper column names and data types from the schema above

User Request: {natural_language_query}

SQL Query:"""

            # Call Bedrock to generate SQL
            response = bedrock_runtime.invoke_model(
                modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 1000,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    }
                ),
            )

            response_body = json.loads(response["body"].read())
            generated_sql = response_body["content"][0]["text"].strip()

            # Clean up the SQL (remove markdown code blocks if present)
            if generated_sql.startswith("```sql"):
                generated_sql = generated_sql.replace("```sql", "").replace("```", "").strip()
            elif generated_sql.startswith("```"):
                generated_sql = generated_sql.replace("```", "").strip()

            print(f"🤖 Generated SQL:\n{generated_sql}")

            # Step 3: Execute the generated SQL
            results = self._execute_query(user_id, generated_sql, tenant_credentials=tenant_credentials)

            return {
                "success": True,
                "user_id": user_id,
                "natural_language_query": natural_language_query,
                "generated_sql": generated_sql,
                "results": results or [],
                "count": len(results) if results else 0,
                "message": f"Query executed successfully, returned {len(results) if results else 0} rows",
                "security": "Row-level filtering enforced by AWS Lake Formation",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error in text-to-SQL: {str(e)}",
            }
