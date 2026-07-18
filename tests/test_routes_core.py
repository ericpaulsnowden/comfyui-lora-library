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
