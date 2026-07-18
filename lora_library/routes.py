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
import re
from pathlib import Path

from aiohttp import web

from .context import LibraryContext
from .version import __version__

logger = logging.getLogger("lora_library")

#: FORMAT.md §4 — set slugs; also the only characters accepted in URLs.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")


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


# ---------------------------------------------------------------- core routes

def register_core(context: LibraryContext, routes: web.RouteTableDef) -> None:
    """Attach the §5 core rows (version/config/loras) to *routes*."""

    @routes.get("/lora_library/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    @routes.get("/lora_library/config")
    async def get_config(_request: web.Request) -> web.Response:
        configured = bool(context.load_config().get("library_dir"))
        return web.json_response(
            {
                "library_dir": str(context.library_dir()),
                "default_library_dir": str(context.default_library_dir),
                "configured": configured,
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
