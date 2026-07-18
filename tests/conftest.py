"""Shared fixtures: a fully fake LibraryContext wired to tmp_path directories.

No ComfyUI anywhere: the context-injection pattern (``lora_library/
context.py``) means every test gets real behavior against throwaway
directories and a fake installed-lora list. Same approach as
comfyui-photoshop-bridge's test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lora_library.context import LibraryContext

#: What the fake "models/loras" folder contains, in folder_paths format
#: (forward slashes, relative to the loras root).
FAKE_LORAS = [
    "detailer.safetensors",
    "styles/film_grain.safetensors",
    "styles/cinematic.safetensors",
]


@pytest.fixture
def context(tmp_path: Path) -> LibraryContext:
    """A LibraryContext over fresh tmp_path dirs with a fake lora list."""
    user_dir = tmp_path / "user" / "lora_library"
    return LibraryContext(
        user_dir=user_dir,
        default_library_dir=user_dir / "library",
        list_loras=lambda: list(FAKE_LORAS),
    )


@pytest.fixture
def library_dir(context: LibraryContext) -> Path:
    """The context's active library dir, created."""
    return context.library_dir()
