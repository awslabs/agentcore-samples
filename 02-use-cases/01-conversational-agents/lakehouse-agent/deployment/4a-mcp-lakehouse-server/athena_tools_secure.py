"""
Secure Athena Tools

Per-user ROW scoping is enforced by a SQL predicate binding the interceptor-propagated
caller identity (``WHERE user_id = ?``, passed as a bound Athena execution parameter in
the scoped claims tools) — NOT by Lake Formation row filters. Lake Formation governs
COLUMN-level filtering (per-role) and the tenant-role TABLE grants. LF data-cell filters
are not used here because a filter's row expression takes constants only and so cannot
express per-caller scope — full reasoning and AWS references in
``deployment/3-s3tables-setup/setup_lakeformation_permissions.py``.

Why these tools select ``*`` rather than naming columns
-------------------------------------------------------
The column projection is left IMPLICIT on purpose. Lake Formation filters the table
DEFINITION per role — a ``glue:GetTable`` under a tenant role returns only that role's
granted columns — so ``SELECT *`` already expands to exactly the caller's permitted set:
15 columns for policyholders, 20 for adjusters (all but ``policyholder_dob``), 21 for
administrators. The tool inherits the grant instead of restating it.

Naming the columns per role was considered and rejected. It would copy the grant list
out of ``deployment/3-s3tables-setup/setup_lakeformation_permissions.py`` into a second
file with nothing keeping the two in step, and the failure mode when they drift is total
rather than partial: a projected column the role is no longer granted makes EVERY query
for that persona fail with ``COLUMN_NOT_FOUND``. It would also need a parity test purely
to guard a duplication the tool introduced. Lake Formation already manages this.

Boundary of the protection, stated plainly: column filtering is not enforced inside the
storage layer. Per AWS's Lake Formation permissions reference, "it is the responsibility
of the integrated analytics service to apply the column filtering when processing a
query" — so the guarantee here is delegated to Athena as the integrated service. That is
the honest limit of what these grants buy: they are authoritative for queries that go
through Athena, not a property of the underlying S3 Tables data itself.

Tenant roles are per-GROUP (policyholders / adjusters / administrators), not per-user, and
share a single Athena/S3 access policy; there are no per-user IAM session tags. Per-user
isolation therefore rides on the bound identity predicate above, not on IAM or LF row rules.

Security flow:
1. The request interceptor validates the user JWT, maps the ``groups`` claim to a tenant
   IAM role, and propagates the caller's identity (email) to the MCP server.
2. The MCP server runs Athena queries under that tenant role's temporary credentials;
   Lake Formation applies the role's column/table grants.
3. The scoped claims tools add a bound ``WHERE`` predicate on the caller's identity, so
   each caller only ever queries their own rows.

Note: values bound into the SQL are server-derived identity/role values (from the validated
JWT) plus agent-supplied filters, all passed as Athena execution parameters (not string-
interpolated); the only tool that runs model-authored SQL is the admin-only ``text_to_sql``.
"""

import json
import os
import time
from typing import Any

import boto3


def _as_sql_literal(value: Any) -> str:
    """
    Render a caller value as an Athena execution parameter (a single-quoted SQL
    string literal with internal quotes doubled).

    Athena's ExecutionParameters are NOT opaque bind variables: each parameter is
    parsed as a SQL *literal*. Verified empirically against Athena
    (2026-08-07, us-east-1):

        param "'abc'"     -> abc          (surrounding quotes consumed)
        param "O''Brien"  -> O'Brien      (doubled quote collapsed to one)

    Two consequences follow, and they are the whole reason this helper exists.

    1. A value carrying an unescaped single quote breaks the literal it is placed
       in. `' OR '1'='1` was previously wrapped as `'' OR '1'='1'`, which Athena
       parsed as an empty string followed by trailing tokens and rejected with
       `TYPE_MISMATCH: Logical expression term must evaluate to a boolean
       (actual: varchar(0))`. Doubling the internal quotes keeps it a single
       well-formed literal, so the comparison simply does not match and the
       caller gets a clean empty result instead of an Athena error.

    2. Passing the raw value with no quoting does NOT fix that -- verified: the
       raw payload fails with the identical TYPE_MISMATCH. Bare values happen to
       work for simple tokens (`alice` binds fine), which is why the AWS CLI
       examples read as though parameters were opaque, but that is not a safe
       general rule for caller-supplied input.

    Injection was never possible in any of these forms: the `?` placeholder keeps
    the query structure fixed, and every probed payload either matched nothing or
    failed closed. Escaping upgrades "fails with a confusing Athena error" to
    "returns no rows", which is what a caller can actually act on. It never
    widens a result set.
    """
    return "'" + str(value).replace("'", "''") + "'"


class SecureAthenaClaimsTools:
    """
    Secure tools for querying health lakehouse data: per-role Lake Formation
    column filtering + a bound identity SQL predicate for per-user row scoping.
    """

    def __init__(
        self,
        region: str,
        database_name: str,
        s3_output_location: str,
        catalog_name: str | None = None,
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

    def _get_athena_client(self, user_id: str, tenant_credentials: dict[str, str] | None = None):
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

        # Fail CLOSED: no tenant credentials means the request bypassed the
        # interceptor's role exchange. Refuse rather than silently using the
        # runtime's own role (which would defeat tenant-scoped access — LF column
        # grants + the bound row predicate). The ONLY escape hatch is
        # LOCAL_DEVELOPMENT for offline dev against default creds.
        #
        # ⚠️  FOOTGUN: LOCAL_DEVELOPMENT is an unguarded manual override — anyone who
        # sets this env var (e.g. on the deployed runtime) disables tenant isolation
        # and the query runs under the runtime's default role. Deploy never sets it
        # (see deploy_runtime.py), but nothing here detects a "real" environment. A
        # stronger option would be to REFUSE when live IdP/SSM config is present
        # (e.g. an idp-provider param exists) so the hatch only works truly offline;
        # left as an explicit tutorial-simplicity tradeoff (documented, not enforced).
        if os.environ.get("LOCAL_DEVELOPMENT", "false").lower() == "true":
            print("=" * 72)
            print("⚠️  LOCAL_DEVELOPMENT=true — TENANT ISOLATION DISABLED")
            print("⚠️  Building an Athena client with the runtime's DEFAULT credentials")
            print("⚠️  (no tenant role; Lake Formation row/column scoping is bypassed).")
            print("⚠️  NEVER set LOCAL_DEVELOPMENT in a deployed environment.")
            print("=" * 72)
            return boto3.client("athena", region_name=self.region)

        raise PermissionError(
            "No tenant credentials provided — refusing to build an Athena client with "
            "runtime default credentials (tenant-scoped access — LF column grants + the "
            "bound row predicate — would be bypassed). This request must carry "
            "interceptor-exchanged tenant credentials."
        )

    def _execute_query(
        self,
        user_id: str,
        query: str,
        wait_for_results: bool = True,
        tenant_credentials: dict[str, str] | None = None,
        execution_parameters: list[str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """
        Execute Athena query with tenant-scoped credentials.

        Row scoping is the CALLER's responsibility: the scoped claims tools already
        embed a bound ``WHERE`` predicate on the interceptor-propagated caller identity
        (passed here as execution_parameters). This method just runs the given SQL;
        Lake Formation then applies the tenant role's column/table grants (per-role
        filtering + admin full-table). LF data-cell filters are not used (see the module
        docstring),
        so this method adds no row filter itself.

        Args:
            user_id: Caller email/ID (used by callers to build the bound WHERE predicate)
            query: SQL to run (the scoped tools already embed the caller's WHERE predicate)
            wait_for_results: Whether to wait for completion
            tenant_credentials: Temporary credentials from interceptor
            execution_parameters: Optional positional values for Athena "?" query
                parameters, applied in order. String values must be single-quoted
                per Athena convention (e.g. "'alice@example.com'"). When None/empty,
                the query is executed with no bound parameters (unchanged behavior).

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

            # Run under tenant-role creds: Lake Formation applies the role's column/
            # table grants; row scoping is the caller's bound WHERE predicate (not an LF
            # row filter — LF data-cell filters are not used; see the module docstring).
            query_context = {"Database": self.database_name}
            if self.catalog_name:
                query_context["Catalog"] = self.catalog_name

            start_params: dict[str, Any] = {
                "QueryString": query,
                "QueryExecutionContext": query_context,
                "ResultConfiguration": {"OutputLocation": self.s3_output_location},
            }
            # Only attach ExecutionParameters when values are present, so callers
            # that pass no params (e.g. text_to_sql) execute exactly as before.
            if execution_parameters:
                start_params["ExecutionParameters"] = execution_parameters

            response = athena_client.start_query_execution(**start_params)

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
            raise Exception(f"Error executing secure Athena query: {e!s}")

    def _is_policyholder_role(self, tenant_credentials: dict[str, str] | None = None) -> bool:
        """Check if the tenant role is a policyholder (restricted column access)."""
        if not tenant_credentials:
            return False
        role_name = tenant_credentials.get("role_name", "")
        return "policyholders" in role_name.lower()

    def query_claims(
        self,
        user_id: str,
        filters: dict[str, Any] | None = None,
        tenant_credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Query claims

        Row scope: query_claims embeds ``WHERE c.user_id = ?`` — the row-scope identity
        SQL predicate, where the caller identity is supplied by the request interceptor
        and passed as a bound Athena execution parameter (not string-interpolated).

        Args:
            user_id: Caller identity (server-derived from the validated JWT by the request
                interceptor); bound into the SQL predicate as a parameter, not a session tag
            filters: Optional additional filters (also bound as parameters)
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            User's claims — row-scoped by the bound SQL predicate; Lake Formation provides
            column filtering (per-role), not row filtering
        """
        try:
            # Bind all caller-derived values as Athena "?" execution parameters
            # (injection-safe) rather than interpolating them into the SQL string.
            # The identity group is parenthesized so the optional filters below AND
            # against it (fixes the prior unparenthesized-OR precedence bug that
            # silently dropped a policyholder's status/type filter). Params are pushed
            # in the exact positional order the "?" placeholders appear in the query.
            #
            # Role-branch (mirrors get_claim_details._is_policyholder_role): the
            # policyholder LF grant does NOT include adjuster_user_id, so a query that
            # references that column fails with COLUMN_NOT_FOUND for policyholders.
            # Policyholders get a query that references ONLY caller-visible columns
            # (row scope = c.user_id = ?, no adjuster branch); adjusters/admins keep
            # the adjuster-assignment branch (they ARE LF-granted adjuster_user_id).
            is_policyholder = self._is_policyholder_role(tenant_credentials)

            if is_policyholder:
                params: list[str] = [
                    _as_sql_literal(user_id),  # c.user_id = ?
                ]
                query = f"""
                    SELECT
                        *
                    FROM {self.table_prefix}.claims as c
                    WHERE (
                        c.user_id = ?
                    )
                """
            else:
                params = [
                    _as_sql_literal(user_id),  # role_exp CTE: WHERE user_id = ?
                    _as_sql_literal(user_id),  # c.user_id = ?
                    _as_sql_literal(user_id),  # c.adjuster_user_id = ?
                ]
                query = f"""
                    WITH role_exp AS (
                        SELECT user_role FROM {self.table_prefix}.users
                        WHERE user_id = ?
                    )
                    SELECT
                        *
                    FROM {self.table_prefix}.claims as c
                    WHERE (
                        c.user_id = ?
                        OR ('adjuster' in (SELECT user_role FROM role_exp)
                            AND c.adjuster_user_id = ?)
                    )
                """

            # Add optional filters (bound as parameters, appended OUTSIDE the
            # parenthesized identity group so they apply to every returned row).
            if filters:
                if filters.get("claim_status"):
                    query += " AND claim_status = ?"
                    params.append(_as_sql_literal(filters["claim_status"]))

                if filters.get("claim_type"):
                    query += " AND claim_type = ?"
                    params.append(_as_sql_literal(filters["claim_type"]))

            query += " ORDER BY submitted_date DESC LIMIT 50"

            # Execute with tenant-scoped credentials
            results = self._execute_query(
                user_id, query, tenant_credentials=tenant_credentials, execution_parameters=params
            )

            return {
                "success": True,
                "user_id": user_id,
                "claims": results or [],
                "count": len(results) if results else 0,
                "message": f"Found {len(results) if results else 0} claims",
                "security": "Row scope via bound identity SQL predicate (WHERE user_id=<caller>); Lake Formation does column filtering, not row filtering",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error querying claims: {e!s}",
            }

    def get_claim_details(
        self,
        user_id: str,
        claim_id: str,
        tenant_credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Get claim details - row-scoped by the bound identity SQL predicate so a caller
        only sees their own claims (Lake Formation does column filtering, not row filtering).

        Args:
            user_id: Caller email (bound into the WHERE predicate as a parameter)
            claim_id: Claim ID
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Claim details (only if user owns it)
        """
        try:
            is_policyholder = self._is_policyholder_role(tenant_credentials)

            # Bind caller-derived values as Athena "?" params (injection-safe);
            # push params in positional "?" order.
            if is_policyholder:
                query = f"""
                    SELECT *
                    FROM {self.table_prefix}.claims
                    WHERE claim_id = ?
                        AND user_id = ?
                """
                params: list[str] = [_as_sql_literal(claim_id), _as_sql_literal(user_id)]
            else:
                query = f"""
                    SELECT *
                    FROM {self.table_prefix}.claims
                    WHERE claim_id = ?
                        AND (user_id = ? OR adjuster_user_id = ?)
                """
                params = [_as_sql_literal(claim_id), _as_sql_literal(user_id), _as_sql_literal(user_id)]

            results = self._execute_query(
                user_id, query, tenant_credentials=tenant_credentials, execution_parameters=params
            )

            if results and len(results) > 0:
                return {
                    "success": True,
                    "claim": results[0],
                    "message": f"Retrieved claim {claim_id}",
                    "security": "Row scope via bound identity SQL predicate (caller's own claim)",
                }
            else:
                return {
                    "success": False,
                    "message": f"Claim {claim_id} not found or access denied",
                    "security": "Not returned by the bound identity SQL predicate — this claim isn't owned by the caller",
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error retrieving claim: {e!s}",
            }

    def get_claims_summary(self, user_id: str, tenant_credentials: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Get claims summary - row-scoped to the caller by the bound identity SQL predicate
        (Lake Formation does column filtering, not row filtering).

        Args:
            user_id: Caller email (bound into the WHERE predicate as a parameter)
            tenant_credentials: Temporary credentials from interceptor

        Returns:
            Summary statistics (only for user's claims)
        """
        try:
            is_policyholder = self._is_policyholder_role(tenant_credentials)

            # Bind caller identity as Athena "?" params (injection-safe); the
            # CASE-WHEN status strings are fixed literals (not caller input), so
            # they stay inline. Push params in positional "?" order.
            if is_policyholder:
                where_clause = "user_id = ?"
                params: list[str] = [_as_sql_literal(user_id)]
            else:
                where_clause = "(user_id = ? OR adjuster_user_id = ?)"
                params = [_as_sql_literal(user_id), _as_sql_literal(user_id)]

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

            results = self._execute_query(
                user_id, query, tenant_credentials=tenant_credentials, execution_parameters=params
            )

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
                    "security": "Row scope via bound identity SQL predicate (caller's own claims)",
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
                "security": "Row scope via bound identity SQL predicate; column filtering via Lake Formation grants",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error retrieving summary: {e!s}",
            }

    def get_database_schema(self, tenant_credentials: dict[str, str] | None = None) -> dict[str, Any]:
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
                "message": f"Error retrieving schema: {e!s}",
            }

    def text_to_sql(
        self,
        user_id: str,
        natural_language_query: str,
        tenant_credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Convert natural language query to SQL and execute it.

        This tool:
        1. Retrieves database schema from Glue Data Catalog
        2. Uses Bedrock to generate SQL from natural language
        3. Executes the generated SQL with tenant credentials
        4. Returns results with the generated SQL for transparency

        SECURITY: admin-only tool. It is intentionally unscoped — the administrators
        role holds a full-table Lake Formation grant (admin sees all rows by design);
        there is no LF row filter and no per-user predicate is added here.

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
2. DO NOT add any WHERE clause filtering by user_id - admin/text_to_sql is intentionally unscoped because the administrators role holds a full-table Lake Formation grant (admin sees all rows by design); there is no LF row filter
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
                "security": "Admin-only, unscoped: full-table access via the administrators Lake Formation grant (no per-user row filter)",
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error in text-to-SQL: {e!s}",
            }
