"""On-disk storage for LoRA sets (FORMAT.md §4).

One JSON file per set under ``context.sets_dir()``. This module owns the
whole §4 file lifecycle (slug derivation, validation/defaults, atomic
save/load/list/delete) and the §4 lora-resolution rule; ``routes_sets.py``
and ``nodes_sets.py`` both build on it and never touch the filesystem
directly. No ComfyUI imports here — same importable-without-ComfyUI seam as
``context.py`` (see its module docstring).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .context import LibraryContext, _atomic_write_text

logger = logging.getLogger("lora_library")

#: FORMAT.md §4 — the only ``format`` value this reader understands.
CURRENT_FORMAT = 1

#: Characters kept by :func:`slugify`; everything else is dropped outright
#: (v1 deliberately does not transliterate unicode/emoji — FORMAT.md §4).
_SLUG_DISALLOWED_RE = re.compile(r"[^a-z0-9\-_]")
_WHITESPACE_RE = re.compile(r"\s+")

#: What counts as a valid on-disk slug (FORMAT.md §4). Mirrors
#: ``routes.SLUG_RE`` and MUST stay in lockstep with it — duplicated here
#: (rather than imported) because the layering runs the other way: the HTTP
#: layer builds on this store, and the store must stay importable without it.
_VALID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")


class SetValidationError(ValueError):
    """Raised when a set payload/file doesn't match FORMAT.md §4.

    Always carries a human-readable message safe to surface verbatim in an
    HTTP ``{"error": ...}`` body (``routes_sets.py`` does exactly that).
    """


# ------------------------------------------------------------------- slugify

def slugify(name: str) -> str:
    """Filename stem for a set's JSON file, derived from its display *name*.

    Per FORMAT.md §4: lowercase; whitespace runs collapse to a single ``-``;
    anything outside ``[a-z0-9-_]`` is stripped. A leading run of ``-``/``_``
    is additionally trimmed so the result always satisfies
    ``routes.SLUG_RE`` (which requires an alphanumeric first character) —
    without this, an all-emoji or ``"_foo"`` name could otherwise slugify to
    something the routes would then refuse to serve. A trailing run is
    trimmed too, purely for cosmetics (SLUG_RE doesn't constrain the last
    character): a name bracketed by stripped characters on both ends, e.g.
    ``"\U0001f3a8 Style \U0001f3a8"``, would otherwise leave a dangling
    ``"style-"``. Collision numbering (``-2``, ``-3``, …) is NOT this
    function's job: it alone can't know what else is already on disk, so
    callers needing a unique slug (only :func:`save_set`, for a brand-new
    set) handle that separately.
    """
    slug = (name or "").strip().lower()
    slug = _WHITESPACE_RE.sub("-", slug)
    slug = _SLUG_DISALLOWED_RE.sub("", slug)
    slug = slug.strip("-_")
    return slug or "set"


def set_path(context: LibraryContext, slug: str) -> Path:
    """``<sets_dir>/<slug>.json`` — the single source of truth for the name."""
    return context.sets_dir() / f"{slug}.json"


def _unique_slug(context: LibraryContext, base: str) -> str:
    """*base*, or ``<base>-2``, ``<base>-3``, … — whichever isn't on disk yet."""
    candidate = base
    suffix = 2
    while set_path(context, candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


# --------------------------------------------------------------- validation

def _coerce_float(value: object, field_name: str) -> float:
    # bool is a subclass of int in Python; a stray `"on": true` must not be
    # silently accepted as a strength of 1.0 if it ends up in the wrong key.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SetValidationError(f"{field_name} must be a number — FORMAT.md §4")
    return float(value)


def _normalize_row(index: int, row: object) -> dict:
    if not isinstance(row, dict):
        raise SetValidationError(f"loras[{index}] must be an object — FORMAT.md §4")
    file = row.get("file")
    if not isinstance(file, str) or not file:
        raise SetValidationError(f"loras[{index}] is missing a 'file' — FORMAT.md §4")
    strength_clip_raw = row.get("strength_clip")
    strength_clip = (
        None
        if strength_clip_raw is None
        else _coerce_float(strength_clip_raw, f"loras[{index}].strength_clip")
    )
    return {
        "file": file,
        "on": bool(row.get("on", True)),
        "strength": _coerce_float(row.get("strength", 1.0), f"loras[{index}].strength"),
        "strength_clip": strength_clip,
    }


def normalize_set(raw: object) -> dict:
    """Validate *raw* (parsed JSON or a request body's ``set``) into a
    canonical FORMAT.md §4 dict, applying every documented default.

    Raises :class:`SetValidationError` — never a bare ``KeyError``/``TypeError``
    — so callers (routes, the loader below) can surface one clear message.
    """
    if not isinstance(raw, dict):
        raise SetValidationError("a set must be a JSON object — FORMAT.md §4")

    fmt = raw.get("format", CURRENT_FORMAT)
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise SetValidationError("set 'format' must be an integer — FORMAT.md §4")
    if fmt > CURRENT_FORMAT:
        raise SetValidationError(
            f"this set was saved by a newer version of the pack (format {fmt}); "
            "update the pack — FORMAT.md §4"
        )

    name = raw.get("name", "")
    if not isinstance(name, str):
        raise SetValidationError("set 'name' must be a string — FORMAT.md §4")

    loras_raw = raw.get("loras", [])
    if not isinstance(loras_raw, list):
        raise SetValidationError("set 'loras' must be a list — FORMAT.md §4")
    loras = [_normalize_row(i, row) for i, row in enumerate(loras_raw)]

    trigger_words = raw.get("trigger_words", "")
    if not isinstance(trigger_words, str):
        raise SetValidationError("set 'trigger_words' must be a string — FORMAT.md §4")

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise SetValidationError("set 'notes' must be a string — FORMAT.md §4")

    return {
        "format": CURRENT_FORMAT,
        "name": name,
        "loras": loras,
        "trigger_words": trigger_words,
        "notes": notes,
    }


# -------------------------------------------------------------- persistence

def load_set(context: LibraryContext, slug: str) -> dict | None:
    """The normalized set at *slug*, or ``None`` if no such file exists.

    A file that exists but fails to parse/validate raises
    :class:`SetValidationError` rather than being treated as missing — a
    corrupt/too-new file is a different situation from "not created yet"
    and callers (routes, nodes) are expected to tell them apart.
    """
    path = set_path(context, slug)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SetValidationError(f"could not read set {slug!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SetValidationError(f"set {slug!r} is not valid JSON: {exc}") from exc
    return normalize_set(raw)


def save_set(context: LibraryContext, set_data: dict, slug: str | None = None) -> tuple[str, dict]:
    """Normalize and atomically persist *set_data*.

    When *slug* is omitted (a brand-new set), it is derived from
    ``set_data["name"]`` via :func:`slugify` and de-duplicated against
    what's already on disk (FORMAT.md §4: collision → ``-2``, ``-3``, …).
    A caller-supplied *slug* (updating a known set) is used as-is — renaming
    a set's display name must not move its file out from under saved
    workflows/routes that reference it by slug. Returns ``(slug, normalized)``.
    """
    normalized = normalize_set(set_data)
    if slug is None:
        slug = _unique_slug(context, slugify(normalized["name"]))
    text = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(set_path(context, slug), text)
    return slug, normalized


def delete_set(context: LibraryContext, slug: str) -> bool:
    """Delete the set at *slug*. ``True`` if a file was removed, else ``False``."""
    try:
        set_path(context, slug).unlink()
        return True
    except FileNotFoundError:
        return False


def list_sets(context: LibraryContext) -> list[dict]:
    """``[{"slug", "name", "count"}, …]`` for every set, sorted by name.

    A single unreadable/invalid file is logged and skipped rather than
    failing the whole listing — the same "one bad thing must not take down
    the rest" posture ``routes.build_routes`` and the pack's ``__init__.py``
    already use for feature modules/nodes. That includes a hand-created file
    whose stem isn't a valid slug (e.g. ``My Set.json``): listing it would
    advertise a slug every other route then 400s on, so it is skipped with
    a rename hint instead.
    """
    summaries = []
    for path in context.sets_dir().glob("*.json"):
        slug = path.stem
        if not _VALID_SLUG_RE.match(slug):
            logger.warning(
                "lora_library: ignoring %s — %r is not a valid set slug (FORMAT.md §4); "
                "rename the file to a valid slug (lowercase letters/digits/-/_, "
                "starting with a letter or digit) to make it usable",
                path.name,
                slug,
            )
            continue
        try:
            data = load_set(context, slug)
        except SetValidationError as exc:
            logger.warning("lora_library: skipping unreadable set %r: %s", slug, exc)
            continue
        if data is None:  # shouldn't happen (we just globbed the file), but be defensive
            continue
        summaries.append({"slug": slug, "name": data["name"], "count": len(data["loras"])})
    summaries.sort(key=lambda entry: (entry["name"].casefold(), entry["slug"]))
    return summaries


# ------------------------------------------------------------- lora lookup

def _normalize_separators(value: str) -> str:
    """*value* with every ``\\`` flipped to ``/`` (FORMAT.md §4).

    ComfyUI's ``folder_paths.get_filename_list`` uses the OS's NATIVE
    separator, so the same subfoldered lora lists as
    ``styles\\film_grain.safetensors`` on the owner's Windows PC and
    ``styles/film_grain.safetensors`` on the Mac — and set files are shared
    between exactly those two machines. All comparisons in this section
    happen in this normalized form; the values *returned* to callers are
    always the installed originals.
    """
    return value.replace("\\", "/")


def _basename(value: str) -> str:
    """Last path segment of *value*, splitting across EITHER separator."""
    return _normalize_separators(value).rsplit("/", 1)[-1]


def resolve_lora(context: LibraryContext, file: str) -> str | None:
    """Resolve *file* against the installed lora list (FORMAT.md §4).

    SEPARATOR-INSENSITIVE, returning the INSTALLED spelling for this
    machine (never the set file's stored spelling — a set written on
    Windows carries ``\\`` and must still resolve here, and vice versa).
    Exact match after normalizing both sides' separators first — the common
    case, since ``file`` is normally written by this very resolution at
    save time. Otherwise, a *unique* basename match tolerates cross-machine
    subfolder differences (rgthree-style leniency). An AMBIGUOUS basename
    (two+ installed loras share it) is deliberately treated the same as
    "not found" rather than picking one arbitrarily — but it logs its own
    warning naming the candidates, so a user staring at a skipped lora can
    tell "ambiguous" apart from "truly missing" (the latter is silent here;
    the generic "could not resolve" warning belongs to the caller, e.g.
    ``nodes_sets.py``, per FORMAT.md §4's skip-with-logged-warning rule).
    """
    installed = context.list_loras()
    normalized_file = _normalize_separators(file)
    for candidate in installed:
        if _normalize_separators(candidate) == normalized_file:
            return candidate
    basename = _basename(file)
    matches = [candidate for candidate in installed if _basename(candidate) == basename]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        logger.warning(
            "lora_library: %r matches multiple installed loras by basename (%s); "
            "skipping rather than guessing — FORMAT.md §4",
            file,
            ", ".join(matches),
        )
    return None
