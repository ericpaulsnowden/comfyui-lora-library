"""HTTP routes for ``EPSFrameSaver`` (FORMAT.md §6.7): a metadata probe and a
byte-range-capable file stream, both feeding ``web/eps_image/frame_saver.js``'s
player.

Both routes are **loopback-only**, mirroring ``lora_library/routes.py``'s
``request_is_loopback`` guard (FORMAT.md §2's rule) — reimplemented locally
rather than imported, since ``eps_image/`` is a sibling FEATURE FAMILY
(FORMAT.md's naming note: "future non-lora features arrive as sibling
modules ... without repo churn") that must not reach into ``lora_library/``'s
internals; ``eps_image/routes_image_grid.py`` makes the identical choice to
stay self-contained. Unlike ``/lora_library/fs/list``'s "still fine inside
the library folder" carve-out for remote callers, there is no such
carve-out here: a video's path is arbitrary filesystem, never confined to
one shared folder, so FORMAT.md §6.7 is unconditionally loopback-only for
BOTH routes -- NOT VHS's permissive default.

Registered directly onto ``PromptServer.instance.routes`` -- never raw
``app.add_routes`` (invisible to the frontend; the same finding
``lora_library/routes.py`` and ``eps_image/routes_image_grid.py`` document in
their own module docstrings). Split the same way those two split
``register``/``build_routes``: :func:`register_routes` attaches to any
``web.RouteTableDef`` (used by :func:`register` for the live server, and
directly by tests against a throwaway ``aiohttp.web.Application`` -- no
ComfyUI needed either way).
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path

from aiohttp import web

from . import frame_saver_video as video

logger = logging.getLogger("eps_image")

#: FORMAT.md §6.7's video ext allowlist. `web/eps_image/frame_saver.js`'s
#: Browse picker passes this SAME list (as a literal -- JS has no way to
#: import a Python module) to `/lora_library/fs/list`'s `ext` query param, so
#: a file the picker lets you choose is always one these routes will accept.
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".ogv")


def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (FORMAT.md §2's rule,
    reimplemented here -- see module docstring for why not imported from
    ``lora_library.routes``).

    A forwarded request (``X-Forwarded-For``) is never loopback -- the proxy
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


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _validate_video_path(raw: str) -> tuple[Path | None, str | None]:
    """FORMAT.md §6.7's shared path validation for both routes below.

    Returns ``(resolved_path, None)`` on success, or ``(None, message)`` on
    failure -- NEVER raises, so a handler can turn *message* straight into a
    400 without a try/except of its own (FORMAT.md §6.7 "never 500 on bad
    input").

    Requires an ABSOLUTE path: a relative one is ambiguous (it would resolve
    against the SERVER PROCESS's own cwd, not anything the caller can see),
    so it's rejected outright rather than guessed at. ``Path.resolve()``
    normalizes any ``.``/``..`` segments BEFORE the extension check and
    before anything ever touches the filesystem, so what gets validated is
    exactly what gets opened -- no traversal-by-encoding trick can present a
    resolved path other than its own true target. The extension allowlist
    (:data:`VIDEO_EXTENSIONS`) is the second half of "path-validated" per
    FORMAT.md §6.7.

    ``Path.resolve()``/``Path.is_absolute()`` can themselves raise for a
    sufficiently hostile *raw* (confirmed live: an embedded NUL byte makes
    ``resolve()`` raise a bare ``ValueError`` from the underlying ``lstat``
    call, an absolute-looking string a caller could still send even though
    it can never name a real file) -- caught here and turned into the same
    400-worthy message shape as every other rejection, rather than
    propagating into a 500.
    """
    trimmed = (raw or "").strip()
    if not trimmed:
        return None, "missing 'path' query parameter"
    try:
        path = Path(trimmed)
        if not path.is_absolute():
            return None, f"path must be an absolute path (got {trimmed!r})"
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        return None, f"invalid path ({exc})"
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        allowed = ", ".join(VIDEO_EXTENSIONS)
        return None, f"unsupported video extension {resolved.suffix!r} (allowed: {allowed})"
    return resolved, None


def register_routes(routes: web.RouteTableDef) -> None:
    """Attach the probe + stream routes to *routes* (FORMAT.md §6.7)."""

    @routes.get("/eps_frame_saver/probe")
    async def get_probe(request: web.Request) -> web.Response:
        if not request_is_loopback(request):
            return error_response(403, "video probing is host-machine-only -- FORMAT.md §6.7")
        resolved, error = _validate_video_path(request.query.get("path", ""))
        if error is not None:
            return error_response(400, error)
        if not resolved.is_file():
            return error_response(400, f"not a file: {resolved}")
        try:
            info = video.probe(str(resolved))
        except ValueError as exc:
            # video.probe() wraps every av/ffmpeg failure into a ValueError
            # naming the path (module docstring) -- a bad/unreadable/
            # codec-less file is always a clean 400 here, never a 500.
            return error_response(400, str(exc))
        return web.json_response(info)

    @routes.get("/eps_frame_saver/stream")
    async def get_stream(request: web.Request) -> web.Response:
        if not request_is_loopback(request):
            return error_response(403, "video streaming is host-machine-only -- FORMAT.md §6.7")
        resolved, error = _validate_video_path(request.query.get("path", ""))
        if error is not None:
            return error_response(400, error)
        if not resolved.is_file():
            return error_response(400, f"not a file: {resolved}")
        # aiohttp's FileResponse handles Range/If-Modified-Since/ETag itself
        # -- this is what gives the frontend's <video> element real 206 seek
        # support for free (FORMAT.md §6.7), no custom byte-range code here.
        return web.FileResponse(resolved)


def build_routes() -> web.RouteTableDef:
    """A standalone table with just this module's routes -- used by tests
    (wrapped in a plain ``aiohttp.web.Application``, no ComfyUI needed) and,
    indirectly, by :func:`register`."""
    routes = web.RouteTableDef()
    register_routes(routes)
    return routes


def register() -> None:
    """Attach this module's routes to ComfyUI's live server.

    Only function in this module that touches ``PromptServer`` -- called
    once from the pack's ``__init__.py`` (mirrors
    ``eps_image.routes_image_grid.register``).
    """
    from server import PromptServer  # ComfyUI's own module; import only inside ComfyUI

    register_routes(PromptServer.instance.routes)
