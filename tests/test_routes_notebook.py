"""Tests for the FORMAT.md §5 notebook routes, through
``lora_library.routes.build_routes`` (no ComfyUI) and aiohttp's own test
client (``aiohttp_client``, from ``pytest-aiohttp``).

``build_routes`` also tries to import ``routes_sets`` (a parallel
workstream's file) and, per its own defensive try/except, logs and skips it
if broken — expected noise here, not a failure of anything owned by this
file.

Remote (non-loopback) callers are simulated with the ``X-Forwarded-For``
header (FORMAT.md §2 / ``routes.request_is_loopback``'s own contract: any
forwarded request is treated as non-loopback regardless of its value).
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from lora_library.context import LibraryContext
from lora_library.routes import build_routes

REMOTE = {"X-Forwarded-For": "203.0.113.5"}


def make_app(context: LibraryContext) -> web.Application:
    app = web.Application()
    app.add_routes(build_routes(context))
    return app


# --------------------------------------------------------- GET /notebook


async def test_get_notebook_missing_file_is_not_an_error(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    assert resp.status == 200
    body = await resp.json()
    assert body["exists"] is False
    assert body["mtime"] is None
    assert body["entries"] == []
    assert body["problems"] == []


async def test_get_notebook_defaults_to_loras_md(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook")
    body = await resp.json()
    assert body["file"] == str(library_dir / "loras.md")


async def test_get_notebook_lists_entries_and_reports_problems(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "body", "category": "Cat A"},
    )
    resp = await client.get("/lora_library/notebook", params={"file": "loras.md"})
    body = await resp.json()
    assert body["exists"] is True
    assert isinstance(body["mtime"], float)
    assert body["entries"] == [{"name": "Foo", "category": "Cat A"}]


async def test_get_notebook_remote_caller_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Secret\nnope\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook", params={"file": str(outside)}, headers=REMOTE
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_get_notebook_loopback_caller_may_read_outside_library_dir(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Secret\nshh\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook", params={"file": str(outside)})
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [{"name": "Secret", "category": ""}]


async def test_get_notebook_non_string_file_query_is_still_a_string_from_query_params(
    context: LibraryContext, aiohttp_client
) -> None:
    # Query params are always strings; this just documents that an absent
    # `file` behaves like an empty string (falls back to the default).
    client = await aiohttp_client(make_app(context))
    resp = await client.get("/lora_library/notebook")
    assert resp.status == 200


# ----------------------------------------------------- GET /notebook/entry


async def test_get_notebook_entry_success(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "hello", "category": "Cat A"},
    )
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["name"] == "Foo"
    assert body["category"] == "Cat A"
    assert body["text"] == "hello"
    assert isinstance(body["mtime"], float)


async def test_get_notebook_entry_missing_entry_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "does-not-exist"}
    )
    assert resp.status == 404
    assert "error" in await resp.json()


async def test_get_notebook_entry_missing_file_is_404(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 404


async def test_get_notebook_entry_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Foo\nbody\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.get(
        "/lora_library/notebook/entry",
        params={"file": str(outside), "name": "Foo"},
        headers=REMOTE,
    )
    assert resp.status == 403


# ---------------------------------------------------- POST /notebook/entry


async def test_post_entry_creates_new_file_and_entry(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "hello"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == [{"name": "Foo", "category": ""}]
    assert (library_dir / "loras.md").exists()


async def test_post_entry_create_appends_new_category(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "E1", "text": "a"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "E2", "text": "b", "category": "Cat A"},
    )
    body = await resp.json()
    assert body["entries"] == [
        {"name": "E1", "category": ""},
        {"name": "E2", "category": "Cat A"},
    ]


async def test_post_entry_update_replaces_text_in_place(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "old"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "new"}
    )
    assert resp.status == 200
    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert (await fetched.json())["text"] == "new"


async def test_post_entry_rename(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Old", "text": "body"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Old", "text": "body", "rename_to": "New"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["entries"] == [{"name": "New", "category": ""}]
    missing = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Old"}
    )
    assert missing.status == 404


async def test_post_entry_rename_collision_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "A", "text": "a"}
    )
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "B", "text": "b"}
    )
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "A", "text": "a", "rename_to": "B"},
    )
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_post_entry_blank_name_on_create_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "   ", "text": "x"}
    )
    assert resp.status == 400


async def test_post_entry_text_with_heading_line_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "line\n# looks like a heading"},
    )
    assert resp.status == 400
    body = await resp.json()
    assert "error" in body


async def test_post_entry_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "text": "x"}
    )
    assert resp.status == 400


async def test_post_entry_non_string_text_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": 5}
    )
    assert resp.status == 400


async def test_post_entry_malformed_json_body_is_400(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_entry_non_object_body_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/entry", json=["nope"])
    assert resp.status == 400


async def test_post_entry_requires_md_suffix_even_for_loopback(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "notes.txt", "name": "Foo", "text": "x"}
    )
    assert resp.status == 403
    assert "error" in await resp.json()


async def test_post_entry_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": str(outside), "name": "Foo", "text": "x"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert not outside.exists()


async def test_post_entry_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    real_mtime = (await created.json())["mtime"]

    resp = await client.post(
        "/lora_library/notebook/entry",
        json={
            "file": "loras.md",
            "name": "Foo",
            "text": "hijacked",
            "base_mtime": real_mtime - 100.0,
        },
    )
    assert resp.status == 409
    body = await resp.json()
    assert "error" in body
    assert body["mtime"] == real_mtime

    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert (await fetched.json())["text"] == "orig"


async def test_post_entry_matching_base_mtime_succeeds(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Foo", "text": "updated", "base_mtime": real_mtime},
    )
    assert resp.status == 200


async def test_post_entry_omitted_base_mtime_skips_conflict_check(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "orig"}
    )
    # No base_mtime at all, even though the file already exists — must not 409.
    resp = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "updated"}
    )
    assert resp.status == 200


# --------------------------------------------------- POST /notebook/delete


async def test_post_delete_removes_entry(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["entries"] == []

    missing = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert missing.status == 404


async def test_post_delete_keeps_emptied_category_heading(
    context: LibraryContext, library_dir: Path, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Only", "text": "body", "category": "Cat A"},
    )
    await client.post("/lora_library/notebook/delete", json={"file": "loras.md", "name": "Only"})
    raw = (library_dir / "loras.md").read_text(encoding="utf-8")
    assert "# Cat A" in raw
    assert "Only" not in raw


async def test_post_delete_missing_entry_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "does-not-exist"}
    )
    assert resp.status == 404


async def test_post_delete_missing_file_is_404(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete", json={"file": "loras.md", "name": "Foo"}
    )
    assert resp.status == 404


async def test_post_delete_missing_name_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post("/lora_library/notebook/delete", json={"file": "loras.md"})
    assert resp.status == 400


async def test_post_delete_malformed_json_is_400(context: LibraryContext, aiohttp_client) -> None:
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete",
        data="not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_delete_stale_base_mtime_is_409_and_file_untouched(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))
    created = await client.post(
        "/lora_library/notebook/entry", json={"file": "loras.md", "name": "Foo", "text": "x"}
    )
    real_mtime = (await created.json())["mtime"]
    resp = await client.post(
        "/lora_library/notebook/delete",
        json={"file": "loras.md", "name": "Foo", "base_mtime": real_mtime - 100.0},
    )
    assert resp.status == 409
    body = await resp.json()
    assert body["mtime"] == real_mtime

    fetched = await client.get(
        "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Foo"}
    )
    assert fetched.status == 200  # entry survived the refused delete


async def test_post_delete_remote_outside_library_dir_is_403(
    context: LibraryContext, tmp_path: Path, aiohttp_client
) -> None:
    outside = tmp_path / "elsewhere.md"
    outside.write_text("## Foo\nx\n", encoding="utf-8")
    client = await aiohttp_client(make_app(context))
    resp = await client.post(
        "/lora_library/notebook/delete",
        json={"file": str(outside), "name": "Foo"},
        headers=REMOTE,
    )
    assert resp.status == 403
    assert "Foo" in outside.read_text(encoding="utf-8")


# ------------------------------------------------------------- integration


async def test_full_lifecycle_create_read_update_rename_delete(
    context: LibraryContext, aiohttp_client
) -> None:
    client = await aiohttp_client(make_app(context))

    await client.post(
        "/lora_library/notebook/entry",
        json={"file": "loras.md", "name": "Portrait", "text": "prompt text", "category": "Style"},
    )
    listing = await (await client.get("/lora_library/notebook", params={"file": "loras.md"})).json()
    assert listing["entries"] == [{"name": "Portrait", "category": "Style"}]

    entry = await (
        await client.get(
            "/lora_library/notebook/entry", params={"file": "loras.md", "name": "Portrait"}
        )
    ).json()
    assert entry["text"] == "prompt text"

    renamed = await (
        await client.post(
            "/lora_library/notebook/entry",
            json={
                "file": "loras.md",
                "name": "Portrait",
                "text": "prompt text v2",
                "rename_to": "Portrait v2",
                "base_mtime": entry["mtime"],
            },
        )
    ).json()
    assert renamed["entries"] == [{"name": "Portrait v2", "category": "Style"}]

    deleted = await (
        await client.post(
            "/lora_library/notebook/delete",
            json={"file": "loras.md", "name": "Portrait v2", "base_mtime": renamed["mtime"]},
        )
    ).json()
    assert deleted["entries"] == []
