"""Core route rows (FORMAT.md §5): config is_local + the fs/list picker feed.

Version/loras rows are exercised implicitly all over the suite; these tests
pin the two host-machine-awareness behaviors the §7.2 file panel depends on.

fs/list reshaped 2026-07-19 to STANDARD-fs-browse.md, the shared cross-plugin
"server filesystem Browse" contract with cpsb/cprb: names-only `{"name": ...}`
(`{"name", "size", "mtime"}` for files) entries, a `sep` field, a `truncated`
cap, dotfile-skipping, and a `ROOTS` sentinel that now returns a labeled
roots list (default dir + Home + platform roots) on every platform, not just
Windows.
"""

from __future__ import annotations

import os
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
    assert data["sep"] == os.sep
    assert data["truncated"] is False
    assert {"name": "sub"} in data["dirs"]
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "prompts.md"
    assert data["files"][0]["size"] > 0
    assert isinstance(data["files"][0]["mtime"], float)
    assert data["parent"] == str(library.parent)


async def test_fs_list_skips_dotfiles(client, context: LibraryContext) -> None:
    library = context.library_dir()
    (library / "prompts.md").write_text("## A\n", encoding="utf-8")
    (library / ".hidden.md").write_text("## B\n", encoding="utf-8")
    (library / ".hidden_dir").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["prompts.md"]
    assert [entry["name"] for entry in data["dirs"]] == []


async def test_fs_list_ext_param_narrows_the_default_allowlist(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "a.md").write_text("## A\n", encoding="utf-8")
    response = await client.get("/lora_library/fs/list", params={"ext": ".md"})
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["a.md"]


async def test_fs_list_navigates_an_explicit_absolute_dir(client, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "lib.md").write_text("## B\n", encoding="utf-8")
    response = await client.get(
        "/lora_library/fs/list", params={"dir": str(tmp_path / "elsewhere")}
    )
    data = await response.json()
    assert [entry["name"] for entry in data["files"]] == ["lib.md"]


async def test_fs_list_sorted_case_insensitively_dirs_then_files(
    client, context: LibraryContext
) -> None:
    library = context.library_dir()
    (library / "zeta.md").write_text("## Z\n", encoding="utf-8")
    (library / "Alpha.md").write_text("## A\n", encoding="utf-8")
    (library / "Zeta").mkdir()
    (library / "alpha_dir").mkdir()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert [entry["name"] for entry in data["dirs"]] == ["alpha_dir", "Zeta"]
    assert [entry["name"] for entry in data["files"]] == ["Alpha.md", "zeta.md"]


async def test_fs_list_truncates_over_500_entries(client, context: LibraryContext) -> None:
    library = context.library_dir()
    for index in range(501):
        (library / f"{index:04d}.md").touch()
    response = await client.get("/lora_library/fs/list")
    data = await response.json()
    assert data["truncated"] is True
    assert len(data["files"]) == 500


async def test_fs_list_is_loopback_only(client) -> None:
    response = await client.get("/lora_library/fs/list", headers=REMOTE_HEADERS)
    assert response.status == 403


async def test_fs_list_rejects_relative_dir(client) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "not/absolute"})
    assert response.status == 400


async def test_fs_list_rejects_a_file_path(client, tmp_path: Path) -> None:
    a_file = tmp_path / "not_a_directory.md"
    a_file.write_text("## A\n", encoding="utf-8")
    response = await client.get("/lora_library/fs/list", params={"dir": str(a_file)})
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
    client, monkeypatch, context: LibraryContext
) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    monkeypatch.setattr(lora_routes, "_list_windows_drives", lambda: ["C:\\", "D:\\", "U:\\"])
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None
    assert data["sep"] == os.sep
    assert data["files"] == []
    assert data["truncated"] is False

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Library Folder"] == str(context.library_dir().resolve())
    assert by_name["Home"] == str(Path.home().resolve())
    assert by_name["C:"] == "C:\\"
    assert by_name["D:"] == "D:\\"
    assert by_name["U:"] == "U:\\"
    # Standard's declared ROOTS order: default dir, Home, then platform roots.
    assert [entry["name"] for entry in data["dirs"]] == [
        "Library Folder", "Home", "C:", "D:", "U:"
    ]


async def test_fs_list_roots_sentinel_is_still_loopback_only(client, monkeypatch) -> None:
    monkeypatch.setattr(lora_routes, "_is_windows", lambda: True)
    response = await client.get(
        "/lora_library/fs/list", params={"dir": "ROOTS"}, headers=REMOTE_HEADERS
    )
    assert response.status == 403


async def test_fs_list_roots_sentinel_includes_default_dir_and_home_on_posix(
    client, context: LibraryContext
) -> None:
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Library Folder"] == str(context.library_dir().resolve())
    assert by_name["Home"] == str(Path.home().resolve())


async def test_fs_list_roots_sentinel_lists_macos_volumes_when_monkeypatched(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        lora_routes, "_list_macos_volumes", lambda: ["/Volumes/Macintosh HD", "/Volumes/Backup"]
    )
    response = await client.get("/lora_library/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Macintosh HD"] == "/Volumes/Macintosh HD"
    assert by_name["Backup"] == "/Volumes/Backup"


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


# ------------------------------------------------- config: library_dir diagnosis
#
# 2026-07-19 fix: a configured library_dir the server machine can't resolve
# (an unmounted NAS share, or a path shaped for the wrong OS) used to be
# invisible until a node errored at run time. `_diagnose_library_dir` takes
# `is_windows` as a parameter (not a bare `os.name` read) specifically so
# both OS-mismatch directions are exercisable from this (macOS) test
# machine.


def test_diagnose_library_dir_flags_a_posix_path_on_a_windows_server() -> None:
    note = lora_routes._diagnose_library_dir("/Volumes/NAS/comfy-library", is_windows=True)
    assert "macOS/Linux" in note
    assert "Windows" in note
    assert "set the folder from the machine ComfyUI runs on" in note


def test_diagnose_library_dir_flags_a_windows_path_on_a_posix_server() -> None:
    note = lora_routes._diagnose_library_dir(r"C:\comfy-library", is_windows=False)
    assert "Windows" in note
    assert "macOS/Linux" in note
    assert "set the folder from the machine ComfyUI runs on" in note


def test_diagnose_library_dir_flags_a_unc_path_on_a_posix_server() -> None:
    note = lora_routes._diagnose_library_dir(r"\\nas\share\comfy-library", is_windows=False)
    assert "Windows" in note


def test_diagnose_library_dir_flags_a_mnt_path_on_a_windows_server() -> None:
    note = lora_routes._diagnose_library_dir("/mnt/nas/comfy-library", is_windows=True)
    assert "macOS/Linux" in note


def test_diagnose_library_dir_is_generic_when_the_shape_matches_but_its_unreachable() -> None:
    # A perfectly ordinary POSIX path, but on a POSIX server — no OS-shape
    # mismatch, so this falls through to the generic "can't reach it" note
    # (the owner's actual 2026-07-19 case: an unmounted NAS mount point).
    note = lora_routes._diagnose_library_dir("/Users/eric/nas-library", is_windows=False)
    assert "macOS/Linux" not in note
    assert "Windows" not in note
    assert "can't reach this folder" in note
    assert "NAS mounted" in note


def test_check_library_dir_is_fine_when_the_configured_dir_exists(
    context: LibraryContext, tmp_path: Path
) -> None:
    real_dir = tmp_path / "real-library"
    real_dir.mkdir()
    context.save_config({"library_dir": str(real_dir)})
    exists, note = lora_routes._check_library_dir(context)
    assert exists is True
    assert note == ""


def test_check_library_dir_is_fine_when_unconfigured(context: LibraryContext) -> None:
    # Nothing configured ⇒ the pack's own default dir — always trivially
    # fine, nothing to diagnose (FORMAT.md §5).
    exists, note = lora_routes._check_library_dir(context)
    assert exists is True
    assert note == ""


def test_check_library_dir_flags_a_bogus_unreachable_path(
    context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    exists, note = lora_routes._check_library_dir(context)
    assert exists is False
    assert note != ""
    assert not bogus.exists()


async def test_config_reports_library_dir_exists_true_for_a_real_directory(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    real_dir = tmp_path / "real-library"
    real_dir.mkdir()
    context.save_config({"library_dir": str(real_dir)})
    data = await (await client.get("/lora_library/config")).json()
    assert data["library_dir_exists"] is True
    assert data["library_dir_note"] == ""


async def test_config_reports_library_dir_exists_false_with_a_note_for_a_bogus_path(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    data = await (await client.get("/lora_library/config")).json()
    assert data["library_dir_exists"] is False
    assert data["library_dir_note"] != ""
    assert data["library_dir"] == str(bogus)


async def test_config_check_does_not_create_a_bogus_configured_directory(
    client, context: LibraryContext, tmp_path: Path
) -> None:
    bogus = tmp_path / "does" / "not" / "exist"
    context.save_config({"library_dir": str(bogus)})
    response = await client.get("/lora_library/config")
    assert response.status == 200
    assert not bogus.exists()
    assert not bogus.parent.exists()


async def test_config_unconfigured_library_dir_reports_exists_true(client) -> None:
    data = await (await client.get("/lora_library/config")).json()
    assert data["configured"] is False
    assert data["library_dir_exists"] is True
    assert data["library_dir_note"] == ""
