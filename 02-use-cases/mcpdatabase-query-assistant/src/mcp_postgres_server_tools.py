"""
Database tools for PostgreSQL MCP Server
"""

import logging
from typing import Dict, Any

# Try to import database drivers
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    import pg8000

    PG8000_AVAILABLE = True
except ImportError:
    PG8000_AVAILABLE = False

from config.mcp_server_config import DatabaseConfig

logger = logging.getLogger(__name__)


class DatabaseTools:
    """Database operations for MCP Server"""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection = None

    def get_connection(self):
        """Get database connection using available driver"""
        if not PSYCOPG2_AVAILABLE and not PG8000_AVAILABLE:
            logger.warning("No PostgreSQL driver available, using mock responses")
            return None

        try:
            # Check if we need a new connection
            need_new_connection = False
            if self._connection is None:
                need_new_connection = True
            elif (
                PSYCOPG2_AVAILABLE
                and hasattr(self._connection, "closed")
                and self._connection.closed
            ):
                need_new_connection = True
            elif not PSYCOPG2_AVAILABLE:
                # For pg8000, we'll try to use the existing connection and create new if it fails
                try:
                    cursor = self._connection.cursor()
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                except Exception:
                    need_new_connection = True

            if need_new_connection:
                if PSYCOPG2_AVAILABLE:
                    # Use psycopg2 if available
                    self._connection = psycopg2.connect(
                        host=self.config.host,
                        port=self.config.port,
                        database=self.config.database,
                        user=self.config.user,
                        password=self.config.password,
                    )
                    logger.info("Connected using psycopg2")
                elif PG8000_AVAILABLE:
                    # Use pg8000 as fallback
                    self._connection = pg8000.connect(
                        host=self.config.host,
                        port=self.config.port,
                        database=self.config.database,
                        user=self.config.user,
                        password=self.config.password,
                    )
                    logger.info("Connected using pg8000")
            return self._connection
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            return None

    def execute_query(self, query: str) -> Dict[str, Any]:
        """Execute a SQL query and return results"""
        logger.info(f"Executing query: {query[:100]}...")

        conn = self.get_connection()
        if not conn:
            return self._mock_query_response(query)

        try:
            if PSYCOPG2_AVAILABLE and hasattr(conn, "cursor"):
                # psycopg2 approach
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query)

                    if query.strip().upper().startswith(("SELECT", "WITH")):
                        results = cursor.fetchall()
                        return {
                            "success": True,
                            "data": [dict(row) for row in results],
                            "row_count": len(results),
                        }
                    else:
                        conn.commit()
                        return {
                            "success": True,
                            "message": f"Query executed successfully. Rows affected: {cursor.rowcount}",
                            "rows_affected": cursor.rowcount,
                        }
            else:
                # pg8000 approach
                cursor = conn.cursor()
                cursor.execute(query)

                if query.strip().upper().startswith(("SELECT", "WITH")):
                    results = cursor.fetchall()
                    columns = [desc[0] for desc in cursor.description]
                    data = [dict(zip(columns, row)) for row in results]
                    return {"success": True, "data": data, "row_count": len(results)}
                else:
                    conn.commit()
                    return {
                        "success": True,
                        "message": f"Query executed successfully. Rows affected: {cursor.rowcount}",
                        "rows_affected": cursor.rowcount,
                    }
        except Exception as e:
            logger.error(f"Database query error: {e}")
            return {"success": False, "error": str(e), "error_type": "database_error"}

    def list_tables(self) -> Dict[str, Any]:
        """List all tables in the database"""
        query = """
        SELECT table_name, table_schema, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name;
        """

        conn = self.get_connection()
        if not conn:
            return {
                "success": True,
                "tables": [
                    {
                        "table_name": "users",
                        "table_schema": "public",
                        "table_type": "BASE TABLE",
                    },
                    {
                        "table_name": "posts",
                        "table_schema": "public",
                        "table_type": "BASE TABLE",
                    },
                    {
                        "table_name": "comments",
                        "table_schema": "public",
                        "table_type": "BASE TABLE",
                    },
                ],
                "count": 3,
                "note": "Mock table list. Real tables will be shown when database is connected.",
            }

        try:
            if PSYCOPG2_AVAILABLE and hasattr(conn, "cursor"):
                # psycopg2 approach
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query)
                    results = cursor.fetchall()

                    return {
                        "success": True,
                        "tables": [dict(row) for row in results],
                        "count": len(results),
                    }
            else:
                # pg8000 approach
                cursor = conn.cursor()
                cursor.execute(query)
                results = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                data = [dict(zip(columns, row)) for row in results]

                return {"success": True, "tables": data, "count": len(results)}
        except Exception as e:
            logger.error(f"Error listing tables: {e}")
            return {"success": False, "error": str(e), "error_type": "database_error"}

    def describe_table(
        self, table_name: str, schema_name: str = "public"
    ) -> Dict[str, Any]:
        """Get detailed information about a table"""
        query = """
        SELECT 
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            tc.constraint_type
        FROM information_schema.columns c
        LEFT JOIN information_schema.key_column_usage kcu 
            ON c.table_name = kcu.table_name 
            AND c.column_name = kcu.column_name
            AND c.table_schema = kcu.table_schema
        LEFT JOIN information_schema.table_constraints tc 
            ON kcu.constraint_name = tc.constraint_name
            AND kcu.table_schema = tc.table_schema
        WHERE c.table_name = %s AND c.table_schema = %s
        ORDER BY c.ordinal_position;
        """

        conn = self.get_connection()
        if not conn:
            return {
                "success": True,
                "table_name": table_name,
                "schema_name": schema_name,
                "columns": [
                    {
                        "column_name": "id",
                        "data_type": "integer",
                        "is_nullable": "NO",
                        "column_default": "nextval('users_id_seq'::regclass)",
                        "constraint_type": "PRIMARY KEY",
                    },
                    {
                        "column_name": "name",
                        "data_type": "character varying",
                        "is_nullable": "NO",
                        "column_default": None,
                        "constraint_type": None,
                    },
                ],
                "note": "Mock table structure. Real structure will be shown when database is connected.",
            }

        try:
            if PSYCOPG2_AVAILABLE and hasattr(conn, "cursor"):
                # psycopg2 approach
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute(query, (table_name, schema_name))
                    results = cursor.fetchall()

                    if not results:
                        return {
                            "success": False,
                            "error": f"Table '{schema_name}.{table_name}' not found",
                            "error_type": "table_not_found",
                        }

                    return {
                        "success": True,
                        "table_name": table_name,
                        "schema_name": schema_name,
                        "columns": [dict(row) for row in results],
                    }
            else:
                # pg8000 approach
                cursor = conn.cursor()
                cursor.execute(query, (table_name, schema_name))
                results = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                data = [dict(zip(columns, row)) for row in results]

                if not results:
                    return {
                        "success": False,
                        "error": f"Table '{schema_name}.{table_name}' not found",
                        "error_type": "table_not_found",
                    }

                return {
                    "success": True,
                    "table_name": table_name,
                    "schema_name": schema_name,
                    "columns": data,
                }
        except Exception as e:
            logger.error(f"Error describing table: {e}")
            return {"success": False, "error": str(e), "error_type": "database_error"}

    def get_database_info(self) -> Dict[str, Any]:
        """Get general database information"""
        conn = self.get_connection()
        if not conn:
            return {
                "success": True,
                "database_name": self.config.database,
                "current_user": self.config.user,
                "host": self.config.host,
                "port": self.config.port,
                "version": "PostgreSQL 15.0 (Mock)",
                "database_size": "100 MB (Mock)",
                "note": "Mock database info. Real info will be shown when database is connected.",
            }

        try:
            if PSYCOPG2_AVAILABLE and hasattr(conn, "cursor"):
                # psycopg2 approach
                with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                    # Get database version
                    cursor.execute("SELECT version()")
                    version = cursor.fetchone()["version"]

                    # Get current database name
                    cursor.execute("SELECT current_database()")
                    current_db = cursor.fetchone()["current_database"]

                    # Get current user
                    cursor.execute("SELECT current_user")
                    current_user = cursor.fetchone()["current_user"]

                    # Get database size
                    cursor.execute(
                        "SELECT pg_size_pretty(pg_database_size(current_database()))"
                    )
                    db_size = cursor.fetchone()["pg_size_pretty"]

                    return {
                        "success": True,
                        "database_name": current_db,
                        "current_user": current_user,
                        "version": version,
                        "database_size": db_size,
                    }
            else:
                # pg8000 approach
                cursor = conn.cursor()

                # Get database version
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]

                # Get current database name
                cursor.execute("SELECT current_database()")
                current_db = cursor.fetchone()[0]

                # Get current user
                cursor.execute("SELECT current_user")
                current_user = cursor.fetchone()[0]

                # Get database size
                cursor.execute(
                    "SELECT pg_size_pretty(pg_database_size(current_database()))"
                )
                db_size = cursor.fetchone()[0]

                return {
                    "success": True,
                    "database_name": current_db,
                    "current_user": current_user,
                    "version": version,
                    "database_size": db_size,
                }
        except Exception as e:
            logger.error(f"Error getting database info: {e}")
            return {"success": False, "error": str(e), "error_type": "database_error"}

    def health_check(self) -> Dict[str, Any]:
        """Health check for the database connection"""
        conn = self.get_connection()
        if not conn:
            return {
                "success": True,
                "status": "healthy",
                "message": "Database MCP Server is running (mock mode)",
                "database_connected": False,
                "config": self.config.to_dict(),
            }

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()

            return {
                "success": True,
                "status": "healthy",
                "message": "Database MCP Server is running and connected",
                "database_connected": True,
                "config": self.config.to_dict(),
            }
        except Exception as e:
            return {
                "success": False,
                "status": "unhealthy",
                "message": f"Database connection failed: {e}",
                "database_connected": False,
                "config": self.config.to_dict(),
            }

    def _mock_query_response(self, query: str) -> Dict[str, Any]:
        """Generate mock response when database is not available"""
        return {
            "success": True,
            "message": "Database MCP Server is running successfully",
            "query": query,
            "mock_data": [
                {"id": 1, "name": "Sample Record", "created_at": "2024-01-01T00:00:00Z"}
            ],
            "row_count": 1,
            "note": "This is a mock response. Database connection will be established in the container.",
        }

    def close_connection(self):
        """Close database connection"""
        if self._connection:
            try:
                # Check if connection is still open (different methods for different drivers)
                if PSYCOPG2_AVAILABLE and hasattr(self._connection, "closed"):
                    # psycopg2 connection
                    if not self._connection.closed:
                        self._connection.close()
                else:
                    # pg8000 connection - just try to close it
                    self._connection.close()
                self._connection = None
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")
                self._connection = None
