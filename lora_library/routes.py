"""HTTP route registrar + shared route helpers (FORMAT.md §2/§5).

Layout keeps parallel workstreams out of each other's files: this module
owns the CORE routes (version/config/loras) and the shared security
helpers; ``routes_notebook.py`` and ``routes_sets.py`` each expose
``register(context, routes)`` and are wired in here. Handlers close over
the injected :class:`~lora_library.context.LibraryContext`, so tests build
an ``aiohttp.web.Application`` from these registrars directly — no ComfyUI.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import string
from pathlib import Path, PureWindowsPath

from aiohttp import web

from .context import LibraryContext
from .version import __version__

logger = logging.getLogger("lora_library")

#: FORMAT.md §4 — set slugs; also the only characters accepted in URLs.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

#: ../../STANDARD-fs-browse.md's `dir` sentinel meaning "the virtual top
#: level": this pack's own default library directory (labeled), "Home", and
#: every drive on Windows / every `/Volumes` mount on macOS.
ROOTS = "ROOTS"

#: STANDARD-fs-browse.md's locality policy for THIS pack, as an explicit,
#: documented, build-time flag (not a request-time param -- flipping this via
#: a query string would let any caller downgrade their own security posture).
#: `True` here is epsnodes' pre-existing posture (FORMAT.md §5: file browsing
#: is host-machine-only) -- porting to the shared contract must never
#: silently flip a pack's posture. Contrast cpsb's own (deliberately `False`)
#: flag for the same route shape.
FS_LIST_LOCAL_ONLY: bool = True

#: STANDARD-fs-browse.md's `ext` query param default/allowlist for this
#: pack's picker -- Prompt Notebook files are always `.md` (FORMAT.md §2).
DEFAULT_EXTENSIONS = (".md",)

#: STANDARD-fs-browse.md ROOTS listing label for this pack's own default
#: fs/list directory (`context.library_dir()` -- this pack's OWN folder, user
#: -configurable, unlike cprb's ComfyUI-owned `output_dir`; FORMAT.md §5).
_FS_LIST_DEFAULT_DIR_LABEL = "Library Folder"

#: Same for the user's home directory (always present, regardless of platform).
_FS_LIST_HOME_LABEL = "Home"

#: Cap on combined `dirs` + `files` entries returned by one
#: `/lora_library/fs/list` listing (root or directory) -- so a directory with
#: an enormous number of children can't turn one request into a
#: multi-megabyte response. Counts only entries actually emitted -- a huge
#: pile of hidden dotfiles or extension-filtered-out files never consumes a
#: slot.
_FS_LIST_MAX_ENTRIES = 500

#: FORMAT.md §5 ``library_dir_note`` -- the OS-shape mismatch message
#: (owner report 2026-07-19: a library folder set from the wrong machine's
#: perspective, e.g. a macOS `/Volumes/...` path pasted into a Windows
#: ComfyUI). ``{other}``/``{this}`` are filled with the two OS labels.
_OS_MISMATCH_NOTE = (
    "This looks like a {other} path, but ComfyUI is running on {this} — "
    "set the folder from the machine ComfyUI runs on."
)

#: Same section -- the generic "can't reach it" message when the configured
#: path is shaped fine for this OS but just isn't there right now (the
#: owner's actual 2026-07-19 case: a NAS mount that isn't mounted on the
#: server machine).
_UNREACHABLE_NOTE = (
    "The machine ComfyUI runs on can't reach this folder right now "
    "(is the NAS mounted there?)."
)


# --------------------------------------------------------------------- guards

def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (FORMAT.md §2).

    A forwarded request (``X-Forwarded-For``) is never loopback — the proxy
    hop hides the real origin, so it gets the restricted tier. ``remote``
    being absent (unix sockets, aiohttp test clients) counts as loopback:
    both mean "not a foreign machine".
    """
    if "X-Forwarded-For" in request.headers:
        return False
    remote = request.remote
    if remote is None:
        return True
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def notebook_path_error(
    context: LibraryContext, path: Path, *, loopback: bool, writing: bool
) -> str | None:
    """FORMAT.md §2 violation message for a notebook *path*, else None."""
    if writing and path.suffix.lower() != ".md":
        return f"notebook files must end in .md (got {path.name!r}) — FORMAT.md §2"
    if not loopback:
        try:
            inside = path.resolve().is_relative_to(context.library_dir().resolve())
        except OSError:
            inside = False
        if not inside:
            return (
                "remote (non-loopback) requests may only touch paths inside the "
                "library folder — FORMAT.md §2"
            )
    return None


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# --------------------------------------------------- fs/list: ROOTS & drives
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further
# — drive roots reported `parent: null`, which reads as "nothing above
# here" and traps the user instead of offering a way to another drive or a
# NAS/UNC path. The pieces below are their own functions (rather than
# inlined in the handler) specifically so tests can monkeypatch the
# Windows-only bits from macOS/Linux CI — real drive enumeration and real
# `os.name` are both unavailable there.


def _is_windows() -> bool:
    """True on a real Windows host (FORMAT.md §5's drive-letter world).

    A seam, not a bare ``os.name`` check inlined in the handler, so tests
    can monkeypatch it to exercise the Windows branches (drive enumeration,
    drive-root → "ROOTS" parent) without depending on the dev/CI machine's
    own platform.
    """
    return os.name == "nt"


def _list_windows_drives() -> list[str]:
    """Every existing drive's root, e.g. ``["C:\\\\", "D:\\\\"]``.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on Windows
    (:func:`_platform_root_entries`). Probes A-Z with ``Path.exists()`` —
    factored out to its own function so tests replace it wholesale instead of
    needing real drives to exist.
    """
    return [
        f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()
    ]


def _list_macos_volumes() -> list[str]:
    """Every mounted volume under ``/Volumes`` on a macOS (or other POSIX) host.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on POSIX
    (:func:`_platform_root_entries`) -- ``/Volumes`` always contains at least
    a symlink back to the boot volume (e.g. ``Macintosh HD``) plus one entry
    per externally-mounted disk/network share, exactly the set a user reaches
    for when they mean "browse by volume" the way Finder's own sidebar does.
    Hidden entries are skipped (same convention the directory-listing branch
    of ``get_fs_list`` uses) and a stat failure on any one entry is skipped
    rather than aborting the whole root listing. Factored out to its own
    function, like :func:`_list_windows_drives`, so tests replace it wholesale.
    """
    volumes_dir = Path("/Volumes")
    if not volumes_dir.is_dir():
        return []
    try:
        entries = sorted(volumes_dir.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    volumes = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            volumes.append(str(entry))
    return volumes


def _fs_entry(name: str, path: Path) -> dict[str, str]:
    """A labeled, directly-navigable ROOTS entry: ``{"name", "path"}``.

    STANDARD-fs-browse.md's general contract is names-only (the client joins
    ``dir``+``sep``+``name`` for a REAL directory listing), but a ROOTS entry
    (this pack's default library dir, "Home", a `/Volumes` mount, a Windows
    drive) has no single parent directory to join against -- each one is
    independently rooted, so the server hands back its actual absolute path
    directly. A deliberate, documented, additive extension of the base
    schema: any consumer that only reads ``name`` still gets a sensible label.
    """
    return {"name": name, "path": str(path)}


def _platform_root_entries(windows: bool) -> list[dict[str, str]]:
    """STANDARD-fs-browse.md ROOTS listing's platform-specific tail.

    Every existing drive letter on Windows (:func:`_list_windows_drives`,
    labeled by its short drive-letter form, e.g. ``"C:"``), or every mounted
    ``/Volumes`` entry on macOS/other POSIX (:func:`_list_macos_volumes`,
    labeled by its bare volume name, e.g. ``"Macintosh HD"``).
    """
    if windows:
        return [_fs_entry(raw.rstrip("\\"), Path(raw)) for raw in _list_windows_drives()]
    return [_fs_entry(Path(raw).name, Path(raw)) for raw in _list_macos_volumes()]


def _fs_list_roots(context: LibraryContext, *, windows: bool) -> list[dict[str, str]]:
    """The top-level entries for ``dir="ROOTS"`` (STANDARD-fs-browse.md).

    Always: this pack's own default fs/list directory (labeled
    :data:`_FS_LIST_DEFAULT_DIR_LABEL`) and the user's home directory, then
    :func:`_platform_root_entries`'s platform-specific tail -- the standard's
    exact ROOTS ordering ("the pack's default dir first (labeled) ... 'Home',
    then platform roots"). 2026-07-19: previously POSIX's ``ROOTS`` resolved
    straight to a real listing of ``/``; this labeled-roots shape (already
    used on Windows) now applies uniformly on every platform.
    """
    roots = [
        _fs_entry(_FS_LIST_DEFAULT_DIR_LABEL, context.library_dir().resolve()),
        _fs_entry(_FS_LIST_HOME_LABEL, Path.home().resolve()),
    ]
    roots.extend(_platform_root_entries(windows))
    return roots


def _parse_extensions(raw: str) -> tuple[str, ...]:
    """STANDARD-fs-browse.md's `ext` query param: a comma-separated,
    case-insensitive extension filter.

    Entries may be given with or without the leading dot. An empty/blank
    value means "the default allowlist" (:data:`DEFAULT_EXTENSIONS`, just
    ``.md`` -- this is a Prompt Notebook file picker, not a general file
    browser) -- mirrors cprb's identical ``_parse_extensions`` helper (same
    contract, independently implemented per pack per STANDARD-fs-browse.md).
    """
    parts = [part.strip().lower() for part in (raw or "").split(",")]
    cleaned = tuple(f".{p.lstrip('.')}" for p in parts if p.strip(". "))
    return cleaned or DEFAULT_EXTENSIONS


def _is_unc_share_root(directory: Path) -> bool:
    """True when *directory* is a UNC share root (``\\\\server\\share``).

    Re-parsed with ``PureWindowsPath`` rather than trusting the ambient
    ``Path`` flavor, so this is exercisable on any platform: a share root's
    "drive" is the ``\\\\server\\share`` string itself (no drive LETTER),
    which is exactly what distinguishes it from a real drive root like
    ``C:\\`` — the two need different ``parent`` answers (see below).
    """
    drive = PureWindowsPath(str(directory)).drive
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":")


def _fs_root_parent(directory: Path, *, windows: bool) -> str | None:
    """FORMAT.md §5 ``parent`` for a *directory* that IS a filesystem root.

    - Windows drive root (``C:\\``): climbs to the drive list (``"ROOTS"``)
      — the fix. Today this reported ``null`` and trapped the user.
    - UNC share root (``\\\\server\\share``): reports ``null`` even on
      Windows — there is no portable way to enumerate a server's other
      shares, so there is nothing to climb to.
    - POSIX root (``/``): reports ``null`` — it has no sibling to climb to.
    """
    if windows and not _is_unc_share_root(directory):
        return ROOTS
    return None


# --------------------------------------------------- library_dir diagnosis
#
# 2026-07-19 fix (owner report: a NAS-backed library_dir the server machine
# can't resolve was invisible until a node errored). `_diagnose_library_dir`
# is its own function -- not inlined in `get_config` -- specifically so tests
# can exercise both OS-mismatch directions from a single (macOS) dev/CI
# machine via the `is_windows` parameter, the same seam `_is_windows()`
# already provides for the fs/list ROOTS branches above.


def _looks_like_windows_path(raw: str) -> bool:
    """True for a drive-letter (``C:\\``) or UNC (``\\\\server\\share``)
    shape -- the signal that *raw* was set from a Windows machine's
    perspective, regardless of what OS is asking."""
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")


def _looks_like_posix_path(raw: str) -> bool:
    """True for a macOS/Linux mount-style shape (``/Volumes/...``,
    ``/mnt/...``) -- the signal that *raw* was set from a POSIX machine's
    perspective, regardless of what OS is asking."""
    normalized = raw.replace("\\", "/").lower()
    return normalized.startswith("/volumes/") or normalized.startswith("/mnt/")


def _diagnose_library_dir(raw: str, *, is_windows: bool) -> str:
    """FORMAT.md §5's ``library_dir_note``: a one-line human diagnosis for a
    configured ``library_dir`` value *raw* that the caller has already
    confirmed doesn't resolve on this server. *is_windows* is the server's
    own platform (:func:`_is_windows`, passed in rather than read here so
    tests can exercise both directions from any host OS).

    An OS-shape mismatch is checked first: it names the likely mistake
    directly ("set from the wrong machine") and is a stronger, more
    actionable signal than the generic unreachable-path message, so it wins
    when *raw* happens to look foreign-shaped. Otherwise the path is shaped
    fine for this OS but just isn't reachable right now (the owner's actual
    2026-07-19 case: an unmounted NAS share).
    """
    if is_windows and _looks_like_posix_path(raw):
        return _OS_MISMATCH_NOTE.format(other="macOS/Linux", this="Windows")
    if not is_windows and _looks_like_windows_path(raw):
        return _OS_MISMATCH_NOTE.format(other="Windows", this="macOS/Linux")
    return _UNREACHABLE_NOTE


def _check_library_dir(context: LibraryContext) -> tuple[bool, str]:
    """FORMAT.md §5 ``(library_dir_exists, library_dir_note)`` for THIS
    request, computed WITHOUT the side effect of creating anything.

    Reads the raw configured value straight from :meth:`LibraryContext.
    load_config` rather than calling :meth:`LibraryContext.library_dir`,
    which unconditionally ``mkdir``s -- exactly the wrong thing to do here:
    that call is *why* a bad NAS path used to surface only as a crash deep
    in some other route/node instead of a clean diagnosis (owner report
    2026-07-19). An unconfigured library_dir (the pack's own default,
    under ComfyUI's own user dir) is always trivially fine -- there is
    nothing to diagnose, so this only inspects a value the user actually
    set.
    """
    configured = context.load_config().get("library_dir")
    if not configured:
        return True, ""
    try:
        exists = Path(configured).is_dir()
    except OSError:
        exists = False
    if exists:
        return True, ""
    return False, _diagnose_library_dir(configured, is_windows=_is_windows())


# ---------------------------------------------------------------- core routes

def register_core(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 core rows (version/config/loras) to *routes*."""

    @routes.get("/lora_library/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    @routes.get("/lora_library/config")
    async def get_config(request: web.Request) -> web.Response:
        configured_raw = context.load_config().get("library_dir")
        configured = bool(configured_raw)
        library_dir_exists, library_dir_note = _check_library_dir(context)
        # 2026-07-19: when unreachable (unmounted NAS, wrong-OS shape),
        # report the raw configured value as-is rather than calling
        # `context.library_dir()` — its `mkdir` would take this whole route
        # down; `library_dir_exists`/`library_dir_note` explain why it's
        # stale (FORMAT.md §5/§7.3). The common (working) case still
        # resolves through the real method, matching prior behavior.
        library_dir = str(context.library_dir()) if library_dir_exists else configured_raw
        return web.json_response(
            {
                "library_dir": library_dir,
                "default_library_dir": str(context.default_library_dir),
                "configured": configured,
                # §2 verdict for THIS caller — lets the frontend gate the
                # host-machine-only affordances (§7.2 file panel buttons).
                "is_local": request_is_loopback(request),
                "library_dir_exists": library_dir_exists,
                "library_dir_note": library_dir_note,
            }
        )

    @routes.get("/lora_library/fs/list")
    async def get_fs_list(request: web.Request) -> web.Response:
        """STANDARD-fs-browse.md's shared cross-plugin contract. Gated by
        :data:`FS_LIST_LOCAL_ONLY` (``True`` for epsnodes -- FORMAT.md §5: a
        remote browser defers to the host for file selection, so it never
        needs to walk the server's filesystem). The loopback check runs
        before ROOTS/absolute-path handling below: ROOTS is an exception to
        the absolute-path requirement, never to this one.

        Query params:
            dir: Optional. Empty/omitted resolves to this pack's own default
                directory (``context.library_dir()``); the literal ``"ROOTS"``
                (:data:`ROOTS`) returns the virtual top-level listing
                (:func:`_fs_list_roots`). Any other value MUST be an absolute
                path naming an existing, listable directory.
            ext: Optional, comma-separated, case-insensitive
                (:func:`_parse_extensions`) -- defaults to
                :data:`DEFAULT_EXTENSIONS` (``.md``).

        Returns 200 with ``{"dir", "parent", "sep", "dirs", "files",
        "truncated"}`` (STANDARD-fs-browse.md) -- names-only ``dirs``/
        ``files`` entries for a real directory listing (the client joins
        with ``dir``+``sep``); ROOTS entries additionally carry ``path``
        (:func:`_fs_entry`). 403 when :data:`FS_LIST_LOCAL_ONLY` and the
        caller isn't loopback; 400 for a relative/non-existent/non-directory
        ``dir``.
        """
        if FS_LIST_LOCAL_ONLY and not request_is_loopback(request):
            return error_response(403, "file browsing is host-machine-only — FORMAT.md §5")
        raw = (request.query.get("dir") or "").strip()
        windows = _is_windows()
        if raw == ROOTS:
            return web.json_response(
                {
                    "dir": ROOTS,
                    "parent": None,
                    "sep": os.sep,
                    "dirs": _fs_list_roots(context, windows=windows),
                    "files": [],
                    "truncated": False,
                }
            )
        directory = Path(raw) if raw else context.library_dir()
        if not directory.is_absolute():
            return error_response(400, f"dir must be an absolute path (got {raw!r})")
        extensions = _parse_extensions(request.query.get("ext", ""))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            return error_response(400, f"could not list {directory}: {exc}")

        dirs: list[dict[str, str]] = []
        files: list[dict[str, object]] = []
        count = 0
        truncated = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if count >= _FS_LIST_MAX_ENTRIES:
                    truncated = True
                    break
                dirs.append({"name": entry.name})
                count += 1
                continue
            if entry.suffix.lower() not in extensions:
                continue
            try:
                stat_result = entry.stat()
            except OSError:
                continue
            if count >= _FS_LIST_MAX_ENTRIES:
                truncated = True
                break
            files.append(
                {"name": entry.name, "size": stat_result.st_size, "mtime": stat_result.st_mtime}
            )
            count += 1

        at_root = directory.parent == directory
        parent = _fs_root_parent(directory, windows=windows) if at_root else str(directory.parent)
        return web.json_response(
            {
                "dir": str(directory),
                "parent": parent,
                "sep": os.sep,
                "dirs": dirs,
                "files": files,
                "truncated": truncated,
            }
        )

    @routes.post("/lora_library/config")
    async def post_config(request: web.Request) -> web.Response:
        # Changing library_dir moves the very boundary §2 enforces for
        # remote callers, so only the local machine may change it.
        if not request_is_loopback(request):
            return error_response(403, "library folder can only be changed locally — FORMAT.md §2")
        try:
            body = await request.json()
        except Exception:
            return error_response(400, "body must be JSON")
        raw = str(body.get("library_dir") or "").strip()
        config = context.load_config()
        if not raw:
            config.pop("library_dir", None)
            context.save_config(config)
            return web.json_response({"ok": True, "library_dir": str(context.library_dir())})
        path = Path(raw)
        if not path.is_absolute():
            return error_response(400, f"library folder must be an absolute path (got {raw!r})")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".lora_library_write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return error_response(400, f"library folder is not writable: {exc}")
        config["library_dir"] = str(path)
        context.save_config(config)
        return web.json_response({"ok": True, "library_dir": str(path)})

    @routes.get("/lora_library/loras")
    async def get_loras(_request: web.Request) -> web.Response:
        return web.json_response({"loras": context.list_loras()})


def _register_all(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Core + every feature route module onto *routes*. Feature modules are
    optional (same defensive posture as ``__init__.py``): a missing/broken
    one logs and is skipped."""
    register_core(context, routes)
    for module_name in ("routes_notebook", "routes_sets"):
        try:
            module = __import__(f"{__package__}.{module_name}", fromlist=["register"])
            module.register(context, routes)
        except Exception:
            logger.exception("lora_library: route module %s failed to load", module_name)


def build_routes(context: LibraryContext) -> web.RouteTableDef:
    """Every lora_library route on a fresh table — the tests' entry point."""
    routes = web.RouteTableDef()
    _register_all(context, routes)
    return routes


def register(context: LibraryContext) -> None:
    """Attach all routes to the running ComfyUI server (called from
    ``__init__.py``; only function in the pack that touches PromptServer).

    Registers onto ``PromptServer.instance.routes`` — NOT directly onto the
    aiohttp app — because ComfyUI mirrors exactly that table under the
    ``/api`` prefix at startup (server.py: "Prefix every route with /api"),
    and the frontend's ``api.fetchApi`` calls ``/api/lora_library/...``.
    Direct ``app.add_routes`` registration works for curl but is invisible
    to the frontend (learned the hard way: HTTP 405s from settings.js).
    """
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    _register_all(context, PromptServer.instance.routes)
    logger.info("lora_library: routes registered")
