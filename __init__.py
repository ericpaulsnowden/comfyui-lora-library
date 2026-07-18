"""ComfyUI entry point for comfyui-lora-library.

This is the only file in the pack that touches ComfyUI's own modules
(``server``, ``folder_paths``). It builds the real
:class:`~lora_library.context.LibraryContext`, registers the HTTP routes and
the nodes, and exposes the standard ``NODE_CLASS_MAPPINGS`` /
``WEB_DIRECTORY`` attributes ComfyUI's loader looks for. Everything under
``lora_library/`` stays importable (and tested) without ComfyUI — see
``lora_library/context.py``.

Feature modules are imported DEFENSIVELY: a broken or not-yet-present
feature logs loudly and is skipped, and the rest of the pack still loads —
one bad module must never take the whole pack (or a user's queue) down.
"""

import importlib
import logging
from pathlib import Path

try:
    from .lora_library import routes as _routes
    from .lora_library.context import LibraryContext
    from .lora_library.version import __version__

    _PACKAGE_PREFIX = f"{__name__}.lora_library"
except ImportError:
    # Imported without package context (e.g. pytest rootdir setups, or tooling
    # that loads node-pack entry files flat). ComfyUI itself always loads this
    # file as a package, taking the relative-import branch above.
    from lora_library import routes as _routes
    from lora_library.context import LibraryContext
    from lora_library.version import __version__

    _PACKAGE_PREFIX = "lora_library"

logger = logging.getLogger("lora_library")


def _build_context() -> LibraryContext:
    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI

    try:
        user_root = Path(folder_paths.get_user_directory())
    except AttributeError:  # pre-user-directory ComfyUI builds
        user_root = Path(folder_paths.base_path) / "user"
    pack_user_dir = user_root / "lora_library"

    def _list_loras() -> list[str]:
        try:
            return list(folder_paths.get_filename_list("loras"))
        except Exception:  # noqa: BLE001 - a broken model dir must not kill the pack
            logger.exception("lora_library: could not list loras")
            return []

    def _resolve_lora_path(name: str) -> str | None:
        try:
            return folder_paths.get_full_path("loras", name)
        except Exception:  # noqa: BLE001 - resolution failure means "not found"
            return None

    return LibraryContext(
        user_dir=pack_user_dir,
        default_library_dir=pack_user_dir / "library",
        list_loras=_list_loras,
        resolve_lora_path=_resolve_lora_path,
    )


_context = _build_context()
_routes.register(_context)

# Class ids are FROZEN once shipped (FORMAT.md §8): saved workflows reference
# nodes by id, and renaming one silently breaks every workflow containing it.
# The "LoraLibrary" prefix exists to avoid colliding with other packs'
# generically named lora nodes.
_NODE_SPECS = [
    ("nodes_notebook", "LoraLibraryNotebook", "LoRA Notebook"),
    ("nodes_sets", "LoraLibraryApplySet", "Apply LoRA Set"),
]

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

for _module_name, _class_id, _display in _NODE_SPECS:
    try:
        _module = importlib.import_module(f"{_PACKAGE_PREFIX}.{_module_name}")
        _module.set_context(_context)
        NODE_CLASS_MAPPINGS[_class_id] = getattr(_module, _class_id)
        NODE_DISPLAY_NAME_MAPPINGS[_class_id] = _display
    except Exception:  # noqa: BLE001 - skip the feature, keep the pack alive
        logger.exception("lora_library: feature module %s failed to load", _module_name)

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY", "__version__"]

logger.info(
    "lora_library v%s loaded (%d nodes; library: %s)",
    __version__,
    len(NODE_CLASS_MAPPINGS),
    _context.library_dir(),
)
