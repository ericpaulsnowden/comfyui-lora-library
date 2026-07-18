"""Tests for lora_library.sets_store (FORMAT.md §4).

Uses the shared ``context``/``library_dir`` fixtures from conftest.py
(``FAKE_LORAS``: ``detailer.safetensors``, ``styles/film_grain.safetensors``,
``styles/cinematic.safetensors``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lora_library import sets_store
from lora_library.context import LibraryContext
from lora_library.routes import SLUG_RE

# ------------------------------------------------------------------- slugify


class TestSlugify:
    def test_lowercases_and_turns_spaces_into_hyphens(self) -> None:
        assert sets_store.slugify("Cinematic Portrait") == "cinematic-portrait"

    def test_collapses_whitespace_runs_to_one_hyphen(self) -> None:
        assert sets_store.slugify("Multi   Word   Name") == "multi-word-name"

    def test_strips_characters_outside_allowed_set(self) -> None:
        assert sets_store.slugify("Foo! Bar?") == "foo-bar"

    def test_unicode_letters_are_stripped_not_transliterated(self) -> None:
        # v1 deliberately drops non-ASCII rather than transliterating.
        assert sets_store.slugify("Café Style") == "caf-style"

    def test_emoji_only_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("\U0001f3a8\U0001f3a8\U0001f3a8") == "set"

    def test_mixed_emoji_and_words_keeps_the_words(self) -> None:
        assert sets_store.slugify("\U0001f3a8 Style \U0001f3a8") == "style"

    def test_empty_or_blank_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("") == "set"
        assert sets_store.slugify("   ") == "set"

    def test_all_punctuation_name_falls_back_to_set(self) -> None:
        assert sets_store.slugify("---") == "set"
        assert sets_store.slugify("!!!") == "set"

    def test_leading_underscore_is_trimmed_so_result_satisfies_slug_re(self) -> None:
        slug = sets_store.slugify("_leading")
        assert slug == "leading"
        assert SLUG_RE.match(slug)

    def test_result_always_satisfies_slug_re_for_a_sample_of_tricky_names(self) -> None:
        for name in ["", "   ", "---", "_x", "\U0001f3a8", "Café", "!!!leading punctuation"]:
            slug = sets_store.slugify(name)
            assert SLUG_RE.match(slug), f"{name!r} -> {slug!r} does not satisfy SLUG_RE"


class TestSlugCollision:
    def test_repeated_name_gets_dash_two_dash_three(self, context: LibraryContext) -> None:
        slug1, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        slug2, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        slug3, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        assert (slug1, slug2, slug3) == ("foo", "foo-2", "foo-3")

    def test_collision_numbering_fills_from_what_is_actually_on_disk(
        self, context: LibraryContext
    ) -> None:
        sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo")
        sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo-2")
        # Neither prior save went through derivation, but derivation must
        # still see both files and land on foo-3.
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        assert slug == "foo-3"

    def test_explicit_slug_bypasses_collision_numbering_and_overwrites(
        self, context: LibraryContext
    ) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []}, slug="foo")
        assert slug == "foo"
        slug_again, data = sets_store.save_set(
            context, {"name": "Foo Updated", "loras": []}, slug="foo"
        )
        assert slug_again == "foo"
        assert data["name"] == "Foo Updated"
        assert sets_store.list_sets(context) == [{"slug": "foo", "name": "Foo Updated", "count": 0}]


# -------------------------------------------------------------- format field


class TestFormatValidation:
    def test_format_1_is_accepted(self) -> None:
        result = sets_store.normalize_set({"format": 1, "name": "x", "loras": []})
        assert result["format"] == 1

    def test_missing_format_defaults_to_current(self) -> None:
        result = sets_store.normalize_set({"name": "x", "loras": []})
        assert result["format"] == sets_store.CURRENT_FORMAT

    def test_format_greater_than_current_is_rejected_with_update_the_pack_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="update the pack"):
            sets_store.normalize_set({"format": 2, "name": "x", "loras": []})

    def test_non_int_format_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="format"):
            sets_store.normalize_set({"format": "1", "name": "x", "loras": []})

    def test_bool_format_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="format"):
            sets_store.normalize_set({"format": True, "name": "x", "loras": []})

    def test_load_set_with_format_2_on_disk_raises_on_load(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        path = library_dir / "sets" / "future.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"format": 2, "name": "x", "loras": []}), encoding="utf-8")
        with pytest.raises(sets_store.SetValidationError, match="update the pack"):
            sets_store.load_set(context, "future")


# --------------------------------------------------------- shape validation


class TestShapeValidation:
    def test_non_dict_payload_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError):
            sets_store.normalize_set(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_non_list_loras_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="loras"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": "nope"})

    def test_non_dict_lora_row_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match=r"loras\[0\]"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": ["nope"]})

    def test_row_missing_file_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="file"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": [{"on": True}]})

    def test_row_with_non_string_file_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="file"):
            sets_store.normalize_set({"format": 1, "name": "x", "loras": [{"file": 5}]})

    def test_string_strength_is_rejected_with_clear_message(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="strength"):
            sets_store.normalize_set(
                {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "strength": "0.8"}]}
            )

    def test_string_strength_clip_is_rejected(self) -> None:
        with pytest.raises(sets_store.SetValidationError, match="strength_clip"):
            sets_store.normalize_set(
                {
                    "format": 1,
                    "name": "x",
                    "loras": [{"file": "a.safetensors", "strength_clip": "0.8"}],
                }
            )


class TestRowDefaults:
    def test_missing_optional_row_fields_get_documented_defaults(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors"}]}
        )
        assert result["loras"][0] == {
            "file": "a.safetensors",
            "on": True,
            "strength": 1.0,
            "strength_clip": None,
        }

    def test_missing_trigger_words_and_notes_default_to_empty_string(self) -> None:
        result = sets_store.normalize_set({"format": 1, "name": "x", "loras": []})
        assert result["trigger_words"] == ""
        assert result["notes"] == ""

    def test_missing_name_defaults_to_empty_string(self) -> None:
        result = sets_store.normalize_set({"format": 1, "loras": []})
        assert result["name"] == ""

    def test_int_strength_is_coerced_to_float(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "strength": 1}]}
        )
        strength = result["loras"][0]["strength"]
        assert strength == 1.0
        assert isinstance(strength, float)

    def test_explicit_strength_clip_null_is_preserved_as_none(self) -> None:
        result = sets_store.normalize_set(
            {
                "format": 1,
                "name": "x",
                "loras": [{"file": "a.safetensors", "strength_clip": None}],
            }
        )
        assert result["loras"][0]["strength_clip"] is None

    def test_off_rows_are_kept_not_dropped(self) -> None:
        result = sets_store.normalize_set(
            {"format": 1, "name": "x", "loras": [{"file": "a.safetensors", "on": False}]}
        )
        assert result["loras"][0]["on"] is False


# -------------------------------------------------------------- persistence


class TestRoundTrip:
    def test_save_then_load_preserves_row_order_and_fields(self, context: LibraryContext) -> None:
        payload = {
            "format": 1,
            "name": "Cinematic portrait",
            "loras": [
                {
                    "file": "subdir/detailer.safetensors",
                    "on": True,
                    "strength": 0.8,
                    "strength_clip": None,
                },
                {
                    "file": "film_grain.safetensors",
                    "on": False,
                    "strength": 1.0,
                    "strength_clip": 0.5,
                },
            ],
            "trigger_words": "cinematic, film grain",
            "notes": "",
        }
        slug, saved = sets_store.save_set(context, payload)
        assert slug == "cinematic-portrait"
        loaded = sets_store.load_set(context, slug)
        assert loaded == saved
        assert [row["file"] for row in loaded["loras"]] == [
            "subdir/detailer.safetensors",
            "film_grain.safetensors",
        ]

    def test_saved_file_is_utf8_json_under_sets_dir(
        self, context: LibraryContext, library_dir: Path
    ) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Foo", "loras": []})
        path = library_dir / "sets" / f"{slug}.json"
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["format"] == 1

    def test_load_missing_slug_returns_none(self, context: LibraryContext) -> None:
        assert sets_store.load_set(context, "does-not-exist") is None

    def test_delete_set_true_then_false(self, context: LibraryContext) -> None:
        slug, _ = sets_store.save_set(context, {"name": "Temp", "loras": []})
        assert sets_store.delete_set(context, slug) is True
        assert sets_store.load_set(context, slug) is None
        assert sets_store.delete_set(context, slug) is False


class TestListSets:
    def test_sorted_by_name_with_row_counts(self, context: LibraryContext) -> None:
        sets_store.save_set(context, {"name": "Zebra", "loras": [{"file": "a.safetensors"}]})
        sets_store.save_set(context, {"name": "Apple", "loras": []})
        listed = sets_store.list_sets(context)
        assert [entry["name"] for entry in listed] == ["Apple", "Zebra"]
        zebra = next(entry for entry in listed if entry["name"] == "Zebra")
        assert zebra["count"] == 1

    def test_empty_library_has_no_sets(self, context: LibraryContext) -> None:
        assert sets_store.list_sets(context) == []

    def test_skips_unreadable_file_and_logs_a_warning(
        self,
        context: LibraryContext,
        library_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bad = library_dir / "sets" / "broken.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{not json", encoding="utf-8")
        sets_store.save_set(context, {"name": "Good", "loras": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = sets_store.list_sets(context)

        assert [entry["name"] for entry in listed] == ["Good"]
        assert any("broken" in record.message for record in caplog.records)

    def test_skips_file_whose_stem_is_not_a_valid_slug(
        self,
        context: LibraryContext,
        library_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A hand-created file: valid §4 JSON, but the stem would be a slug
        # every set route 400s on — listing it would advertise a dead entry.
        bad = library_dir / "sets" / "My Set.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(
            json.dumps({"format": 1, "name": "My Set", "loras": []}), encoding="utf-8"
        )
        sets_store.save_set(context, {"name": "Good", "loras": []})

        with caplog.at_level(logging.WARNING, logger="lora_library"):
            listed = sets_store.list_sets(context)

        assert [entry["slug"] for entry in listed] == ["good"]
        assert any(
            "My Set" in record.message and "rename" in record.message
            for record in caplog.records
        )


# -------------------------------------------------------------- lora lookup


class TestResolveLora:
    def test_exact_top_level_match(self, context: LibraryContext) -> None:
        assert sets_store.resolve_lora(context, "detailer.safetensors") == "detailer.safetensors"

    def test_exact_match_with_subdir(self, context: LibraryContext) -> None:
        assert (
            sets_store.resolve_lora(context, "styles/cinematic.safetensors")
            == "styles/cinematic.safetensors"
        )

    def test_unique_basename_match_resolves_across_subdir(self, context: LibraryContext) -> None:
        # FAKE_LORAS only has this file as styles/film_grain.safetensors; a
        # bare-basename request should still resolve (FORMAT.md §4:
        # "exact match first, then unique basename match").
        assert (
            sets_store.resolve_lora(context, "film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_no_match_at_all_returns_none(self, context: LibraryContext) -> None:
        assert sets_store.resolve_lora(context, "nope.safetensors") is None

    def test_no_match_at_all_does_not_log_an_ambiguous_warning(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "nope.safetensors")
        assert result is None
        assert not any("ambiguous" in r.message.lower() for r in caplog.records)
        assert not any("multiple" in r.message.lower() for r in caplog.records)

    def test_ambiguous_basename_is_skipped_rather_than_guessed(
        self, context: LibraryContext
    ) -> None:
        context.list_loras = lambda: ["a/dup.safetensors", "b/dup.safetensors"]
        assert sets_store.resolve_lora(context, "dup.safetensors") is None

    def test_ambiguous_basename_logs_a_reason_distinct_from_not_found(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        context.list_loras = lambda: ["a/dup.safetensors", "b/dup.safetensors"]
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "dup.safetensors")
        assert result is None
        messages = [r.message for r in caplog.records]
        assert any("multiple" in m.lower() for m in messages)
        assert any("a/dup.safetensors" in m and "b/dup.safetensors" in m for m in messages)


class TestResolveLoraCrossOS:
    """FORMAT.md §4: resolution is SEPARATOR-INSENSITIVE and returns the
    INSTALLED spelling — the pack's headline scenario is one library shared
    between a Windows PC (``folder_paths`` lists ``styles\\x``) and a Mac
    (``styles/x``)."""

    def test_windows_stored_value_exact_matches_posix_installed(
        self, context: LibraryContext
    ) -> None:
        # Set saved on the Windows PC, applied on the Mac (FAKE_LORAS are
        # posix-style). Same folder, same file — only the separator differs.
        assert (
            sets_store.resolve_lora(context, "styles\\film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_posix_stored_value_exact_matches_windows_installed(
        self, context: LibraryContext
    ) -> None:
        # The reverse trip: set saved on the Mac, applied on the Windows PC.
        # The returned value is the INSTALLED spelling, not the stored one.
        context.list_loras = lambda: [
            "detailer.safetensors",
            "styles\\film_grain.safetensors",
            "styles\\cinematic.safetensors",
        ]
        assert (
            sets_store.resolve_lora(context, "styles/film_grain.safetensors")
            == "styles\\film_grain.safetensors"
        )

    def test_windows_stored_subfolder_falls_back_to_basename_across_separators(
        self, context: LibraryContext
    ) -> None:
        # Different subfolder on each machine AND windows separators in the
        # stored value: exact-after-normalize fails, basename must still hit
        # (a `/`-only split would keep `old\location\` glued to the name).
        assert (
            sets_store.resolve_lora(context, "old\\location\\film_grain.safetensors")
            == "styles/film_grain.safetensors"
        )

    def test_posix_stored_subfolder_matches_windows_installed_by_basename(
        self, context: LibraryContext
    ) -> None:
        context.list_loras = lambda: ["styles\\film_grain.safetensors"]
        assert (
            sets_store.resolve_lora(context, "elsewhere/film_grain.safetensors")
            == "styles\\film_grain.safetensors"
        )

    def test_ambiguity_that_only_appears_after_separator_normalization(
        self, context: LibraryContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A `/`-only basename split would read `a\dup.safetensors` as one
        # flat name (no basename collision → false unique match on b/);
        # separator-aware splitting sees the true two-way collision.
        context.list_loras = lambda: ["a\\dup.safetensors", "b/dup.safetensors"]
        with caplog.at_level(logging.WARNING, logger="lora_library"):
            result = sets_store.resolve_lora(context, "dup.safetensors")
        assert result is None
        assert any("multiple" in r.message.lower() for r in caplog.records)
