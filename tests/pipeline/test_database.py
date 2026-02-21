"""
Tests for Database Manager
"""

import pytest
from datetime import datetime

from backend.storage.database import DatabaseManager, DatabasePool
from backend.pipeline.models import DatabaseConfig


class TestDatabaseConfig:
    """Test database configuration"""

    def test_create_config(self):
        """Test creating a database config"""
        config = DatabaseConfig(
            name="test_db",
            host="localhost",
            port=5432,
            database="testdb",
            provider="postgresql",
        )

        assert config.name == "test_db"
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == "testdb"
        assert config.provider == "postgresql"


class TestDatabasePool:
    """Test database connection pooling"""

    @pytest.mark.asyncio
    async def test_sqlite_pool_initialization(self):
        """Test SQLite pool initialization (no credentials required)"""
        config = DatabaseConfig(
            name="test_sqlite",
            host="localhost",
            port=0,
            database=":memory:",  # In-memory SQLite
            provider="sqlite",
        )

        pool = DatabasePool(config)

        try:
            await pool.initialize()
            assert pool.pool is not None
        except ImportError:
            pytest.skip("aiosqlite not installed")
        finally:
            if pool.pool:
                await pool.close()

    @pytest.mark.asyncio
    async def test_sqlite_query_execution(self):
        """Test executing SQLite queries"""
        config = DatabaseConfig(
            name="test_sqlite",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )

        pool = DatabasePool(config)

        try:
            await pool.initialize()

            # Create a test table
            conn = await pool.acquire()
            await conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
            await conn.execute("INSERT INTO test (name) VALUES (?)", ("Alice",))
            await conn.execute("INSERT INTO test (name) VALUES (?)", ("Bob",))

            # Query back
            result = await conn.execute("SELECT * FROM test")
            await pool.release(conn)

            # Should have 2 rows
            assert len(result) == 2

        except ImportError:
            pytest.skip("aiosqlite not installed")
        finally:
            if pool.pool:
                await pool.close()


class TestDatabaseManager:
    """Test database manager"""

    def setup_method(self):
        """Create fresh manager for each test"""
        self.manager = DatabaseManager()

    @pytest.mark.asyncio
    async def test_add_and_list_database(self):
        """Test adding a database and listing it"""
        config = DatabaseConfig(
            name="test_sqlite",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )

        try:
            await self.manager.add_database(config)

            databases = self.manager.list_databases()
            assert "test_sqlite" in databases

        except ImportError:
            pytest.skip("aiosqlite not installed")
        finally:
            await self.manager.close_all()

    @pytest.mark.asyncio
    async def test_connection_context_manager(self):
        """Test using connection context manager"""
        config = DatabaseConfig(
            name="test_sqlite",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )

        try:
            await self.manager.add_database(config)

            async with self.manager.connection("test_sqlite") as conn:
                result = await conn.execute("SELECT 1 as value")
                assert result[0][0] == 1

            # Connection should be released after context

        except ImportError:
            pytest.skip("aiosqlite not installed")
        finally:
            await self.manager.close_all()

    @pytest.mark.asyncio
    async def test_remove_database(self):
        """Test removing a database"""
        config = DatabaseConfig(
            name="test_sqlite",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )

        try:
            await self.manager.add_database(config)
            assert "test_sqlite" in self.manager.list_databases()

            await self.manager.remove_database("test_sqlite")
            assert "test_sqlite" not in self.manager.list_databases()

        except ImportError:
            pytest.skip("aiosqlite not installed")
        finally:
            await self.manager.close_all()

    @pytest.mark.asyncio
    async def test_close_all(self):
        """Test closing all database pools"""
        config1 = DatabaseConfig(
            name="test1",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )
        config2 = DatabaseConfig(
            name="test2",
            host="localhost",
            port=0,
            database=":memory:",
            provider="sqlite",
        )

        try:
            await self.manager.add_database(config1)
            await self.manager.add_database(config2)

            assert len(self.manager.list_databases()) == 2

            await self.manager.close_all()

            assert len(self.manager.list_databases()) == 0

        except ImportError:
            pytest.skip("aiosqlite not installed")

    @pytest.mark.asyncio
    async def test_get_connection_fails_for_unknown_db(self):
        """Test that getting connection for unknown database fails"""
        with pytest.raises(ValueError, match="not registered"):
            await self.manager.get_connection("nonexistent")
