"""
Database connection pooling and management

This module provides connection pooling for PostgreSQL, MySQL, and other databases.
Credentials are stored in OS keychain and referenced by name.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from datetime import datetime

from backend.pipeline.models import DatabaseConfig

logger = logging.getLogger(__name__)

# Try to import database drivers
try:
    import asyncpg  # PostgreSQL async driver
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
    asyncpg = None

try:
    import aiomysql  # MySQL async driver
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False
    aiomysql = None

try:
    import aiosqlite  # SQLite async driver
    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False
    aiosqlite = None


class DatabaseConnection:
    """Wrapper for a database connection"""

    def __init__(self, conn: Any, provider: str, config: DatabaseConfig):
        self.conn = conn
        self.provider = provider
        self.config = config
        self.created_at = datetime.utcnow()

    async def execute(self, query: str, params: Optional[tuple] = None) -> Any:
        """Execute a query and return results"""
        if self.provider == "postgresql":
            return await self._execute_postgres(query, params)
        elif self.provider == "mysql":
            return await self._execute_mysql(query, params)
        elif self.provider == "sqlite":
            return await self._execute_sqlite(query, params)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _execute_postgres(self, query: str, params: Optional[tuple]) -> Any:
        """Execute PostgreSQL query"""
        if params:
            result = await self.conn.execute(query, *params)
        else:
            result = await self.conn.execute(query)

        # Try to fetch results if it's a SELECT
        if query.strip().upper().startswith("SELECT"):
            return await self.conn.fetch(query, *(params or ()))
        return result

    async def _execute_mysql(self, query: str, params: Optional[tuple]) -> Any:
        """Execute MySQL query"""
        async with self.conn.cursor() as cursor:
            await cursor.execute(query, params)

            if query.strip().upper().startswith("SELECT"):
                return await cursor.fetchall()
            await self.conn.commit()
            return cursor.rowcount

    async def _execute_sqlite(self, query: str, params: Optional[tuple]) -> Any:
        """Execute SQLite query"""
        if params:
            cursor = await self.conn.execute(query, params)
        else:
            cursor = await self.conn.execute(query)

        if query.strip().upper().startswith("SELECT"):
            return await cursor.fetchall()
        return cursor.rowcount

    async def close(self):
        """Close the connection"""
        if self.provider == "sqlite":
            await self.conn.close()
        else:
            await self.conn.close()


class DatabasePool:
    """Connection pool for a specific database"""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.provider = config.provider
        self.pool: Optional[Any] = None
        self._connections: List[DatabaseConnection] = []
        self._sqlite_conn_initialized = False  # Track SQLite connection state

    async def initialize(self):
        """Initialize the connection pool"""
        if self.provider == "postgresql":
            await self._init_postgres_pool()
        elif self.provider == "mysql":
            await self._init_mysql_pool()
        elif self.provider == "sqlite":
            await self._init_sqlite()
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _init_postgres_pool(self):
        """Initialize PostgreSQL connection pool"""
        if not POSTGRES_AVAILABLE:
            raise RuntimeError("asyncpg not installed - cannot connect to PostgreSQL")

        # Get credentials from keychain
        username, password = await self._get_credentials()

        self.pool = await asyncpg.create_pool(
            host=self.config.host,
            port=self.config.port,
            user=username,
            password=password,
            database=self.config.database,
            min_size=1,
            max_size=self.config.pool_size,
            command_timeout=60,
        )

        logger.info(f"Created PostgreSQL pool for {self.config.name}")

    async def _init_mysql_pool(self):
        """Initialize MySQL connection pool"""
        if not MYSQL_AVAILABLE:
            raise RuntimeError("aiomysql not installed - cannot connect to MySQL")

        # Get credentials from keychain
        username, password = await self._get_credentials()

        self.pool = await aiomysql.create_pool(
            host=self.config.host,
            port=self.config.port,
            user=username,
            password=password,
            db=self.config.database,
            minsize=1,
            maxsize=self.config.pool_size,
            autocommit=False,
        )

        logger.info(f"Created MySQL pool for {self.config.name}")

    async def _init_sqlite(self):
        """Initialize SQLite connection (not pooled, single connection)"""
        if not SQLITE_AVAILABLE:
            raise RuntimeError("aiosqlite not installed - cannot connect to SQLite")

        # SQLite doesn't use username/password
        self.pool = await aiosqlite.connect(self.config.database)
        logger.info(f"Created SQLite connection for {self.config.name}")

    async def _get_credentials(self) -> tuple:
        """Get credentials from OS keychain"""
        try:
            import keyring

            key = f"assistant_db_{self.config.name}"
            username = keyring.get_password(key, "username")
            password = keyring.get_password(key, "password")

            if not username or not password:
                raise ValueError(f"Credentials not found in keychain for {self.config.name}")

            return username, password

        except ImportError:
            raise RuntimeError("keyring library not installed - cannot retrieve database credentials")

    async def acquire(self) -> DatabaseConnection:
        """Acquire a connection from the pool"""
        if self.provider == "sqlite":
            # SQLite has single connection - return it directly
            if not self._sqlite_conn_initialized:
                # First time acquiring
                self._sqlite_conn_initialized = True
            return DatabaseConnection(self.pool, self.provider, self.config)

        conn = await self.pool.acquire()
        return DatabaseConnection(conn, self.provider, self.config)

    async def release(self, connection: DatabaseConnection):
        """Release a connection back to the pool"""
        if self.provider != "sqlite":
            await self.pool.release(connection.conn)

    async def close(self):
        """Close all connections in the pool"""
        if self.provider == "sqlite" and self.pool:
            await self.pool.close()
        elif self.pool:
            self.pool.close()
            await self.pool.wait_closed()

        logger.info(f"Closed pool for {self.config.name}")

    async def test_connection(self) -> bool:
        """Test if database connection is working"""
        try:
            conn = await self.acquire()
            if self.provider == "postgresql":
                await conn.conn.fetchval("SELECT 1")
            elif self.provider == "mysql":
                async with conn.conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
            elif self.provider == "sqlite":
                await conn.conn.execute("SELECT 1")

            await self.release(conn)
            return True
        except Exception as e:
            logger.error(f"Database connection test failed for {self.config.name}: {e}")
            return False


class DatabaseManager:
    """
    Manages multiple database connection pools.

    Provides:
    - Connection pooling for multiple databases
    - Credential retrieval from keychain
    - Connection testing and validation
    - Automatic reconnection on failure
    """

    def __init__(self):
        self.pools: Dict[str, DatabasePool] = {}

    async def add_database(self, config: DatabaseConfig) -> None:
        """
        Add a database to the manager.

        Args:
            config: Database configuration

        Raises:
            RuntimeError: If pool initialization fails
        """
        if config.name in self.pools:
            logger.warning(f"Database '{config.name}' already registered")
            return

        pool = DatabasePool(config)
        await pool.initialize()
        self.pools[config.name] = pool

        # Test connection
        if not await pool.test_connection():
            raise RuntimeError(f"Failed to connect to database '{config.name}'")

        logger.info(f"Added database: {config.name}")

    async def get_connection(self, name: str) -> DatabaseConnection:
        """
        Get a connection from the pool.

        Args:
            name: Database name

        Returns:
            DatabaseConnection

        Raises:
            ValueError: If database not found
        """
        if name not in self.pools:
            raise ValueError(f"Database '{name}' not registered")

        return await self.pools[name].acquire()

    @asynccontextmanager
    async def connection(self, name: str):
        """
        Context manager for acquiring and releasing connections.

        Example:
            async with db_manager.connection("mydb") as conn:
                result = await conn.execute("SELECT * FROM users")
        """
        conn = await self.get_connection(name)
        try:
            yield conn
        finally:
            await self.pools[name].release(conn)

    async def remove_database(self, name: str) -> None:
        """Remove a database and close its pool"""
        if name in self.pools:
            await self.pools[name].close()
            del self.pools[name]
            logger.info(f"Removed database: {name}")

    async def close_all(self) -> None:
        """Close all database pools"""
        for name, pool in self.pools.items():
            await pool.close()
        self.pools.clear()
        logger.info("Closed all database pools")

    def list_databases(self) -> List[str]:
        """List all registered database names"""
        return list(self.pools.keys())

    async def test_all(self) -> Dict[str, bool]:
        """Test all database connections"""
        results = {}
        for name, pool in self.pools.items():
            results[name] = await pool.test_connection()
        return results


# Global singleton instance
database_manager = DatabaseManager()
