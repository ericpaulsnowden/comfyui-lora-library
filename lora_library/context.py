"""Dependency-injection seam: everything ComfyUI-specific enters through here.

The rest of ``lora_library/`` (stores, nodes, routes) receives a
:class:`LibraryContext` and never imports ComfyUI modules itself, so the whole
package stays importable — and therefore testable — without ComfyUI. The real
context is built exactly once, in the pack's ``__init__.py``; tests build fake
ones over ``tmp_path`` (see ``tests/conftest.py``). Same pattern as
comfyui-photoshop-bridge's ``cpsb/context.py``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("lora_library")

CONFIG_FILENAME = "config.json"
DEFAULT_NOTEBOOK_FILENAME = "loras.md"
SETS_DIRNAME = "sets"


@dataclass
class LibraryContext:
    """Paths + host-app callables for one running lora_library instance.

    Args:
        user_dir: Directory for this pack's own persistent state (the
            ``config.json`` holding ``library_dir``). Under ComfyUI this is
            ``<user dir>/lora_library``; under tests, a tmp dir.
        default_library_dir: Where the library lives when the user has not
            configured one (FORMAT.md §1). Created lazily on first use.
        list_loras: Returns the installed lora filenames exactly as ComfyUI's
            own lora loaders present them (``folder_paths.get_filename_list``
            values, forward-slash relative paths). Injected so tests can fake
            the model folder.
        resolve_lora_path: Maps one of those filenames to an absolute path
            (``folder_paths.get_full_path``), or None when it doesn't exist.
    """

    user_dir: Path
    default_library_dir: Path
    list_loras: Callable[[], list[str]] = field(default=lambda: [])
    resolve_lora_path: Callable[[str], str | None] = field(default=lambda _name: None)

    # ------------------------------------------------------------------ config

    @property
    def _config_path(self) -> Path:
        return self.user_dir / CONFIG_FILENAME

    def load_config(self) -> dict:
        """The persisted pack config (currently only ``library_dir``).

        Missing or unreadable config is not an error — it simply means
        defaults (a fresh install, or a hand-deleted file).
        """
        try:
            with open(self._config_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "lora_library: unreadable %s (%s); using defaults", self._config_path, exc
            )
            return {}
        return data if isinstance(data, dict) else {}

    def save_config(self, config: dict) -> None:
        """Atomically persist *config* (FORMAT.md §1)."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self._config_path, json.dumps(config, indent=2) + "\n")

    # ------------------------------------------------------------- library dir

    def library_dir(self) -> Path:
        """The active library directory (configured, else default), created."""
        configured = self.load_config().get("library_dir")
        directory = Path(configured) if configured else self.default_library_dir
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def sets_dir(self) -> Path:
        """``<library_dir>/sets`` (FORMAT.md §4), created."""
        directory = self.library_dir() / SETS_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def resolve_notebook_file(self, file_value: str) -> Path:
        """Resolve a node/route ``file`` value to an absolute ``.md`` path.

        Relative values resolve against :meth:`library_dir`; absolute values
        (including Windows UNC ``\\\\server\\share`` paths) pass through
        untouched — pointing the notebook at a NAS is the design center, not
        an edge case (FORMAT.md §1/§2). No existence check here: readers
        surface "missing file" themselves so a brand-new path can be created
        by the first save.
        """
        value = (file_value or "").strip() or DEFAULT_NOTEBOOK_FILENAME
        path = Path(value)
        if not path.is_absolute():
            path = self.library_dir() / path
        return path


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* via a same-directory temp file + ``os.replace``.

    Same-directory matters: ``os.replace`` is only atomic within one
    filesystem, and the library may live on a NAS mount distinct from the
    system temp dir. Callers own error handling; a failed write must never
    leave a half-written target behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
