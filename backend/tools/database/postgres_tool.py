"""
PostgreSQL tool - Query PostgreSQL databases

This is an example tool demonstrating database integration.
Credentials are stored in OS keychain and referenced by database name.
"""

import logging
import re
from typing import Dict, Any, List
from datetime import datetime

from backend.tools.base import ToolBase
from backend.pipeline.models import (
    CapabilityMetadata,
    CapabilityType,
    ActionTier,
    OutputBlock,
)
from backend.storage.database import database_manager

logger = logging.getLogger(__name__)


class PostgresTool(ToolBase):
    """
    Query PostgreSQL databases.

    This tool requires database credentials to be stored in the OS keychain.
    Queries are classified by type (SELECT, INSERT, UPDATE, DROP, etc.) to
    determine the appropriate action tier.
    """

    @property
    def metadata(self) -> CapabilityMetadata:
        return CapabilityMetadata(
            name="postgres_query",
            type=CapabilityType.TOOL,
            tier=ActionTier.TIER_2,  # Default tier - may be overridden per query
            description="Query PostgreSQL databases",
            requires_network=True,
            allowed_databases=["localhost:*"],  # Configure per deployment
            max_runtime_seconds=60,
        )

    async def execute(self, params: Dict[str, Any]) -> OutputBlock:
        """
        Execute PostgreSQL query.

        Args:
            params: {
                "database": "database_name",
                "query": "SQL query",
                "params": [query parameters for prepared statements],
            }
        """
        database_name = params.get("database")
        query = params.get("query")
        query_params = params.get("params", [])

        if not database_name:
            raise ValueError("database is required")
        if not query:
            raise ValueError("query is required")

        # Classify query type for tier validation
        query_type = self._classify_query(query)

        # Tier validation: destructive queries require Tier 3
        if query_type == "destructive" and self.metadata.tier == ActionTier.TIER_2:
            raise PermissionError(
                f"Destructive queries (DROP, TRUNCATE, DELETE without WHERE) "
                f"require Tier 3 approval. Query: {query[:50]}..."
            )

        try:
            # Get connection from pool
            async with database_manager.connection(database_name) as conn:
                # Execute query
                if query_params:
                    result = await conn.execute(query, tuple(query_params))
                else:
                    result = await conn.execute(query)

                # Format output based on query type
                if query_type == "select":
                    return self._format_select_result(result, query)
                else:
                    return self._format_write_result(result, query, query_type)

        except ValueError as e:
            # Database not registered or credentials not found
            return OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": "Database Error",
                    "message": str(e),
                },
                metadata={"error": str(e)}
            )
        except Exception as e:
            logger.error(f"PostgreSQL query failed: {e}")
            return OutputBlock(
                type="notification",
                content={
                    "level": "error",
                    "title": "Query Failed",
                    "message": str(e),
                },
                metadata={"error": str(e), "database": database_name}
            )

    def _classify_query(self, query: str) -> str:
        """
        Classify query type for tier determination.

        Returns:
            "select", "insert", "update", "delete", "destructive", "other"
        """
        query_upper = query.strip().upper()

        # Check for destructive operations first
        destructive_patterns = [
            r"DROP\s+(TABLE|DATABASE|SCHEMA|INDEX)",
            r"TRUNCATE\s+TABLE",
            r"DELETE\s+FROM\s+\w+\s*$",  # DELETE without WHERE
            r"ALTER\s+TABLE.*DROP",
        ]

        for pattern in destructive_patterns:
            if re.search(pattern, query_upper):
                return "destructive"

        # Classify by operation type
        if query_upper.startswith("SELECT"):
            return "select"
        elif query_upper.startswith("INSERT"):
            return "insert"
        elif query_upper.startswith("UPDATE"):
            return "update"
        elif query_upper.startswith("DELETE"):
            return "delete"
        else:
            return "other"

    def _format_select_result(self, result: Any, query: str) -> OutputBlock:
        """Format SELECT query results as table"""
        if not result or len(result) == 0:
            return OutputBlock(
                type="text",
                content="Query returned no results.",
                metadata={"query": query, "row_count": 0}
            )

        # Convert to table format
        # Assuming result is list of tuples or similar
        if isinstance(result, list):
            if len(result) > 0:
                # Get column names from first row keys if dicts
                if isinstance(result[0], dict):
                    columns = list(result[0].keys())
                    rows = [[str(row.get(col, "")) for col in columns] for row in result]
                else:
                    # Use generic column names
                    columns = [f"col_{i}" for i in range(len(result[0]))]
                    rows = [[str(val) for val in row] for row in result]

                return OutputBlock(
                    type="table",
                    content={
                        "columns": columns,
                        "rows": rows[:100],  # Limit to 100 rows
                        "metadata": {
                            "query": query,
                            "row_count": len(result),
                            "truncated": len(result) > 100
                        }
                    }
                )

        # Fallback for unexpected formats
        return OutputBlock(
            type="text",
            content=f"Query returned {len(result) if hasattr(result, '__len__') else '?'} results",
            metadata={"query": query}
        )

    def _format_write_result(self, result: Any, query: str, query_type: str) -> OutputBlock:
        """Format INSERT/UPDATE/DELETE result"""
        row_count = result if isinstance(result, int) else 0

        return OutputBlock(
            type="notification",
            content={
                "level": "success",
                "title": f"{query_type.upper()} Successful",
                "message": f"Affected {row_count} row(s)",
            },
            metadata={
                "query": query,
                "query_type": query_type,
                "row_count": row_count
            }
        )

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Return JSON schema for parameters"""
        return {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "description": "Database name (must be registered)"
                },
                "query": {
                    "type": "string",
                    "description": "SQL query to execute"
                },
                "params": {
                    "type": "array",
                    "description": "Query parameters for prepared statements",
                    "items": {"type": "string"}
                }
            },
            "required": ["database", "query"]
        }
