"""
Tests for LocalFileSystem backend (P4-02)
"""

import pytest
from pathlib import Path

from backend.storage.local import LocalFileSystem, _is_blocked, _resolve
from backend.storage.abstract import FileInfo, filesystem_router


@pytest.fixture
def fs():
    return LocalFileSystem()


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello, World!", encoding="utf-8")
    return f


@pytest.fixture
def tmp_dir_tree(tmp_path):
    """
    tmp/
      a.txt
      b.py
      subdir/
        c.json
        d.md
    """
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.py").write_text("print('hi')")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "c.json").write_text("{}")
    (sub / "d.md").write_text("# Docs")
    return tmp_path


# ---------------------------------------------------------------------------
# _is_blocked
# ---------------------------------------------------------------------------


class TestIsBlocked:
    def test_env_file_blocked(self, tmp_path):
        f = tmp_path / ".env"
        assert _is_blocked(f)

    def test_env_local_blocked(self, tmp_path):
        f = tmp_path / ".env.local"
        assert _is_blocked(f)

    def test_pem_blocked(self, tmp_path):
        f = tmp_path / "cert.pem"
        assert _is_blocked(f)

    def test_key_blocked(self, tmp_path):
        f = tmp_path / "server.key"
        assert _is_blocked(f)

    def test_id_rsa_blocked(self, tmp_path):
        f = tmp_path / "id_rsa"
        assert _is_blocked(f)

    def test_regular_file_not_blocked(self, tmp_path):
        f = tmp_path / "report.txt"
        assert not _is_blocked(f)

    def test_py_file_not_blocked(self, tmp_path):
        f = tmp_path / "main.py"
        assert not _is_blocked(f)


# ---------------------------------------------------------------------------
# read / read_text
# ---------------------------------------------------------------------------


class TestRead:
    async def test_read_text_returns_content(self, fs, tmp_file):
        content = await fs.read_text(str(tmp_file))
        assert content == "Hello, World!"

    async def test_read_chunks_stream(self, fs, tmp_file):
        chunks = []
        async for chunk in fs.read(str(tmp_file)):
            chunks.append(chunk)
        assert b"Hello, World!" in b"".join(chunks)

    async def test_read_file_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.read_text(str(tmp_path / "missing.txt"))

    async def test_read_blocked_env(self, fs, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SECRET=abc")
        with pytest.raises(PermissionError):
            await fs.read_text(str(env))

    async def test_read_directory_raises(self, fs, tmp_path):
        with pytest.raises(IsADirectoryError):
            await fs.read_text(str(tmp_path))

    async def test_read_large_file_streams_in_chunks(self, fs, tmp_path):
        large = tmp_path / "big.bin"
        large.write_bytes(b"x" * 200_000)
        chunk_count = 0
        total = 0
        async for chunk in fs.read(str(large)):
            chunk_count += 1
            total += len(chunk)
        assert total == 200_000
        assert chunk_count >= 3  # ≥ 3 chunks for 200 KB at 64 KB each


# ---------------------------------------------------------------------------
# write / write_text
# ---------------------------------------------------------------------------


class TestWrite:
    async def test_write_text_creates_file(self, fs, tmp_path):
        dest = tmp_path / "out.txt"
        await fs.write_text(str(dest), "written!")
        assert dest.read_text() == "written!"

    async def test_write_text_overwrites(self, fs, tmp_file):
        await fs.write_text(str(tmp_file), "new content")
        assert tmp_file.read_text() == "new content"

    async def test_write_creates_parent_dirs(self, fs, tmp_path):
        dest = tmp_path / "deep" / "nested" / "file.txt"
        await fs.write_text(str(dest), "deep!")
        assert dest.read_text() == "deep!"

    async def test_write_blocked_path(self, fs, tmp_path):
        blocked = tmp_path / "secrets.key"
        with pytest.raises(PermissionError):
            await fs.write_text(str(blocked), "secret")


# ---------------------------------------------------------------------------
# append_text
# ---------------------------------------------------------------------------


class TestAppend:
    async def test_append_to_existing(self, fs, tmp_file):
        await fs.append_text(str(tmp_file), "\nAppended!")
        assert "Hello, World!" in tmp_file.read_text()
        assert "Appended!" in tmp_file.read_text()

    async def test_append_creates_file(self, fs, tmp_path):
        new_file = tmp_path / "new.txt"
        await fs.append_text(str(new_file), "first line")
        assert new_file.read_text() == "first line"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    async def test_delete_file(self, fs, tmp_file):
        await fs.delete(str(tmp_file))
        assert not tmp_file.exists()

    async def test_delete_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.delete(str(tmp_path / "ghost.txt"))

    async def test_delete_blocked(self, fs, tmp_path):
        blocked = tmp_path / ".env"
        blocked.write_text("SECRET=xyz")
        with pytest.raises(PermissionError):
            await fs.delete(str(blocked))

    async def test_delete_empty_dir(self, fs, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        await fs.delete(str(empty))
        assert not empty.exists()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    async def test_list_returns_entries(self, fs, tmp_dir_tree):
        entries = await fs.list(str(tmp_dir_tree))
        names = [e.name for e in entries]
        assert "a.txt" in names
        assert "b.py" in names
        assert "subdir" in names

    async def test_list_dirs_come_first(self, fs, tmp_dir_tree):
        entries = await fs.list(str(tmp_dir_tree))
        dir_entries = [e for e in entries if e.is_dir]
        file_entries = [e for e in entries if not e.is_dir]
        # All dirs appear before all files in the list
        if dir_entries and file_entries:
            last_dir_idx = max(entries.index(e) for e in dir_entries)
            first_file_idx = min(entries.index(e) for e in file_entries)
            assert last_dir_idx < first_file_idx

    async def test_list_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.list(str(tmp_path / "missing"))

    async def test_list_file_raises(self, fs, tmp_file):
        with pytest.raises(NotADirectoryError):
            await fs.list(str(tmp_file))

    async def test_list_file_info_fields(self, fs, tmp_dir_tree):
        entries = await fs.list(str(tmp_dir_tree))
        txt = next(e for e in entries if e.name == "a.txt")
        assert isinstance(txt, FileInfo)
        assert txt.size > 0
        assert txt.path.startswith("local://")
        assert txt.modified_at is not None

    async def test_empty_dir(self, fs, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        entries = await fs.list(str(empty))
        assert entries == []


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


class TestMove:
    async def test_move_renames_file(self, fs, tmp_file):
        dst = tmp_file.parent / "renamed.txt"
        await fs.move(str(tmp_file), str(dst))
        assert dst.exists()
        assert not tmp_file.exists()

    async def test_move_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.move(str(tmp_path / "ghost.txt"), str(tmp_path / "dst.txt"))


# ---------------------------------------------------------------------------
# exists / stat
# ---------------------------------------------------------------------------


class TestExistsStat:
    async def test_exists_true(self, fs, tmp_file):
        assert await fs.exists(str(tmp_file))

    async def test_exists_false(self, fs, tmp_path):
        assert not await fs.exists(str(tmp_path / "no.txt"))

    async def test_stat_file(self, fs, tmp_file):
        info = await fs.stat(str(tmp_file))
        assert info.name == "hello.txt"
        assert info.size == len("Hello, World!")
        assert not info.is_dir
        assert info.path.startswith("local://")

    async def test_stat_dir(self, fs, tmp_path):
        info = await fs.stat(str(tmp_path))
        assert info.is_dir

    async def test_stat_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.stat(str(tmp_path / "missing.txt"))


# ---------------------------------------------------------------------------
# find_files
# ---------------------------------------------------------------------------


class TestFindFiles:
    async def test_find_all(self, fs, tmp_dir_tree):
        results = await fs.find_files(str(tmp_dir_tree), "*")
        names = [r.name for r in results]
        assert "a.txt" in names
        assert "b.py" in names
        assert "c.json" in names

    async def test_find_by_extension(self, fs, tmp_dir_tree):
        results = await fs.find_files(str(tmp_dir_tree), "*.py")
        assert all(r.name.endswith(".py") for r in results)
        assert len(results) == 1

    async def test_find_max_results(self, fs, tmp_dir_tree):
        results = await fs.find_files(str(tmp_dir_tree), "*", max_results=2)
        assert len(results) <= 2

    async def test_find_max_depth_zero(self, fs, tmp_dir_tree):
        results = await fs.find_files(str(tmp_dir_tree), "*", max_depth=0)
        # depth=0 means no recursion, only top-level files
        names = [r.name for r in results]
        assert "c.json" not in names  # c.json is in a subdir

    async def test_find_not_found(self, fs, tmp_path):
        with pytest.raises(FileNotFoundError):
            await fs.find_files(str(tmp_path / "missing"), "*")


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------


class TestRouterRegistration:
    def test_local_scheme_registered(self):
        assert "local" in filesystem_router.registered_schemes()

    async def test_router_resolves_local_uri(self, tmp_file):
        backend, path = filesystem_router.resolve(f"local://{tmp_file}")
        assert backend.scheme == "local"
        assert str(tmp_file) in path

    async def test_router_bare_path_defaults_to_local(self, tmp_file):
        backend, path = filesystem_router.resolve(str(tmp_file))
        assert backend.scheme == "local"

    def test_router_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="No backend"):
            filesystem_router.resolve("neverregistered://some/path")
