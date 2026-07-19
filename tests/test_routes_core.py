"""Core route rows (FORMAT.md §5): config is_local + the fs/list picker feed.

Version/loras rows are exercised implicitly all over the suite; these tests
pin the two host-machine-awareness behaviors the §7.2 file panel depends on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lora_library import routes as lora_routes
from lora_library.context import LibraryContext
from lora_library.routes import build_routes

REMOTE_HEADERS = {"X-Forwarded-For": "192.168.1.50"}


@pytest.fixture
async def client(context: LibraryContext, aiohttp_client):
    app = web.Application()
    app.add_routes(build_routes(context))
    return await aiohttp_client(app)


async def test_config_reports_is_local_true_for_loopback(client) -> None:
    data = await (await client.get("/lora_library/config")).json()
    assert data["is_local"] is True


async def test_config_reports_is_local_false_for_forwarded_caller(client) -> None:
    data = await (await client.get("/lora_library/config", headers=REMOTE_HEADERS)).json()
    assert data["is_local"] is False


async def test_fs_list_defaults_to_library_dir_and_filters_md(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "prompts.md").write_text("## A\n", encoding="utf-8")
    (library / "notes.txt").write_text("x", encoding="utf-8")
    (library / "sub").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert data["dir"] == str(library)
    assert "sub" in data["dirs"]
    assert data["files"] == ["prompts.md"]
    assert data["parent"] == str(library.parent)


async def test_fs_list_navigates_an_explicit_absolute_dir(client, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "lib.md").write_text("## B\n", encoding="utf-8")
    response = await client.get(
        "/lora_library/fs/list", params={"dir": str(tmp_path / "elsewhere")}
    )
    data = await response.json()
    assert data["files"] == ["lib.md"]


async def test_fs_list_is_loopback_only(client) -> None:
    response = await client.get("/lora_library/fs/list", headers=REMOTE_HEADERS)
    assert response.status == 403


async def test_fs_list_rejects_relative_dir(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "not/absolute"})
    assert response.status == 400


async def test_fs_list_unreadable_dir_is_400(client, tmp_path: Path) -> None:
    response = await client.get(
        "/lora_library/fs/list", params={"dir": str(tmp_path / "nope" / "missing")}
    )
    assert response.status == 400


# ------------------------------------------------ fs/list: ROOTS & drive roots
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further.
# Drive enumeration and `os.name` aren't real on this (macOS) test machine,
# so every Windows-flavored case goes through the `_is_windows` /
# `_list_windows_drives` monkeypatch seams (routes.py §"fs/list: ROOTS &
# drives") rather than touching real drives.


async def test_fs_list_roots_sentinel_lists_windows_drives_when_monkeypatched(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    monkeypatch.setattr(lora_routes, "_list_windows_drives", lambda: ["C:\\", "D:\\", "U:\\"])
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data == {
        "dir": "ROOTS",
        "parent": None,
        "dirs": ["C:\\", "D:\\", "U:\\"],
        "files": [],
    }


async def test_fs_list_roots_sentinel_is_still_loopback_only(client, monkeypatch) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    response = await client.get(
        "/lora_library/fs/list", params={"dir": "ROOTS"}, headers=REMOTE_HEADERS
    )
    assert response.status == 403


async def test_fs_list_roots_sentinel_resolves_to_slash_on_posix(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] is None
    assert isinstance(data["dirs"], list)
    assert isinstance(data["files"], list)


async def test_fs_list_drive_root_parent_climbs_to_roots_under_monkeypatched_windows(
    client, monkeypatch
) -> None:
    # "/" doubles as our only real, listable filesystem root on this test
    # machine — `_is_windows` alone decides which label a root's parent
    # gets, so patching just that seam is enough to exercise the branch.
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    response = await client.get("/lora_library/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] == "ROOTS"


async def test_fs_list_posix_root_parent_is_null_without_monkeypatching(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] is None


def test_is_unc_share_root_detects_a_share_root() -> None:
    assert lora_routes._is_unc_share_root(Path(r"\\server\share")) is True


def test_is_unc_share_root_rejects_a_drive_root() -> None:
    assert lora_routes._is_unc_share_root(Path("C:\\")) is False


def test_fs_root_parent_is_roots_for_a_drive_root_on_windows() -> None:
    assert lora_routes._fs_root_parent(Path("C:\\"), windows=True) == "ROOTS"


def test_fs_root_parent_is_null_for_a_unc_share_root_even_on_windows() -> None:
    # No portable way to enumerate a server's other shares (FORMAT.md §5) —
    # a share root has nothing to climb to, unlike a drive root.
    assert lora_routes._fs_root_parent(Path(r"\\server\share"), windows=True) is None


def test_fs_root_parent_is_null_on_posix() -> None:
    assert lora_routes._fs_root_parent(Path("/"), windows=False) is None
