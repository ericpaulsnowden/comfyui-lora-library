"""Tests for eps_image.nodes_switcher (FORMAT.md §6.4, `EPSSwitcher`).

No ComfyUI/torch anywhere -- unlike lora_library's tests, this node needs no
context fixture (no `set_context`) and no faked `comfy.*` modules; "images"
are plain sentinel objects, since the node never inspects their contents.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types

import pytest

from eps_image import nodes_switcher
from eps_image.nodes_switcher import EPSSwitcher, _FlexibleOptionalImageInputs


def _toggles(**overrides: bool) -> str:
    """A `toggles` JSON string, e.g. `_toggles(image_2=False)`."""
    return json.dumps(overrides)


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake ``comfy_execution.graph`` module exposing
    ``ExecutionBlocker`` into ``sys.modules`` -- mirrors the ``fake_comfy``
    convention in ``tests/test_resolution.py``/``test_nodes_sets.py`` (this
    pack's tests never require a real ComfyUI install on the path).
    ``EPSSwitcher.execute``'s all-off path imports ``ExecutionBlocker``
    lazily from exactly this module path (FORMAT.md §6.4), so installing the
    fake here is the whole story -- nothing in ``nodes_switcher`` itself
    needs patching. Returns the fake class so tests can ``isinstance()``
    the returned blocker.
    """

    class FakeExecutionBlocker:
        """Mirrors the real ``comfy_execution.graph_utils.ExecutionBlocker``
        (``__init__(self, message)`` storing ``self.message``) exactly
        enough for these tests -- an identity/attribute check, since
        nodes_switcher.py never does anything else with the instance.
        """

        def __init__(self, message: object) -> None:
            self.message = message

    fake_graph = types.ModuleType("comfy_execution.graph")
    fake_graph.ExecutionBlocker = FakeExecutionBlocker
    fake_pkg = types.ModuleType("comfy_execution")
    fake_pkg.graph = fake_graph

    monkeypatch.setitem(sys.modules, "comfy_execution", fake_pkg)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", fake_graph)
    return FakeExecutionBlocker


# --------------------------------------------------------- flexible inputs


class TestFlexibleOptionalImageInputs:
    def test_declared_image_1_is_a_real_dict_entry(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert optional["image_1"] == ("IMAGE",)
        assert list(optional.keys()) == ["image_1"]

    def test_contains_accepts_any_image_n(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert "image_5" in optional
        assert "image_37" in optional
        # Only image_1 was actually inserted -- __contains__ says yes to
        # image_5 without it ever appearing in .keys()/.items() (see the
        # dedicated .keys() assertion above).

    def test_getitem_synthesizes_the_image_type_for_ungrown_slots(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert optional["image_5"] == ("IMAGE",)

    def test_contains_rejects_non_matching_keys(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        assert "video_1" not in optional
        assert "image_" not in optional
        assert "image_1x" not in optional

    def test_getitem_raises_keyerror_for_non_matching_keys(self) -> None:
        optional = _FlexibleOptionalImageInputs({"image_1": ("IMAGE",)})
        with pytest.raises(KeyError):
            optional["not_an_image_input"]

    def test_input_types_optional_accepts_image_5(self) -> None:
        input_types = EPSSwitcher.INPUT_TYPES()
        assert "image_5" in input_types["optional"]
        assert input_types["optional"]["image_5"] == ("IMAGE",)

    def test_input_types_optional_toggles_widget_default(self) -> None:
        # `toggles` rides in `optional` (NOT `required`): a hand-built /prompt
        # that omits it must still run -- a missing REQUIRED input is rejected
        # by ComfyUI before execute() ever sees it, which would break the
        # documented no-frontend API path. required is empty.
        input_types = EPSSwitcher.INPUT_TYPES()
        assert input_types["required"] == {}
        assert "toggles" not in input_types["required"]
        widget_type, spec = input_types["optional"]["toggles"]
        assert widget_type == "STRING"
        assert spec["default"] == "{}"


# ------------------------------------------------------------------ execute


class TestExecuteCollectsEnabledInAscendingOrder:
    def test_single_connected_and_enabled_image_passes_through(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles(), image_1="img1")
        assert result == (["img1"],)

    def test_default_toggles_enables_every_connected_slot(self) -> None:
        # No `toggles` value at all (a plain API caller who never loaded
        # switcher.js) -- module docstring's default-enabled rationale.
        node = EPSSwitcher()
        result = node.execute(image_1="img1", image_2="img2")
        assert result == (["img1", "img2"],)

    def test_collects_in_ascending_n_regardless_of_kwarg_order(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(),
            image_3="img3",
            image_1="img1",
            image_2="img2",
        )
        assert result == (["img1", "img2", "img3"],)

    def test_disabled_slot_is_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_2=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
        )
        assert result == (["img1", "img3"],)

    @pytest.mark.parametrize("falsy", [None, 0, "", [], {}])
    def test_non_bool_falsy_toggle_value_keeps_slot_enabled(self, falsy: object) -> None:
        # Regression (R9 review): only the LITERAL boolean False disables a
        # slot. A non-bool falsy value (null/0/""/[]/{}) from a hand-edited
        # workflow or a non-frontend API caller renders as ON in switcher.js
        # (`!== false`), so the backend must keep it too -- plain truthiness
        # would silently drop it and make the fan-out count disagree with the
        # UI. image_2's `null` here must NOT drop it.
        node = EPSSwitcher()
        toggles = json.dumps({"image_2": falsy})
        result = node.execute(toggles=toggles, image_1="img1", image_2="img2", image_3="img3")
        assert result == (["img1", "img2", "img3"],)

    def test_explicit_boolean_false_is_the_only_disabler(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=json.dumps({"image_2": False}), image_1="img1", image_2="img2"
        )
        assert result == (["img1"],)

    def test_disconnected_slot_none_is_skipped_even_if_marked_enabled(self) -> None:
        # A gap slot (per FORMAT.md §6.4 growth invariant, a disconnected
        # middle slot can still exist as a key) is None from ComfyUI's own
        # call path -- toggle state is moot for it.
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_2=True),
            image_1="img1",
            image_2=None,
            image_3="img3",
        )
        assert result == (["img1", "img3"],)

    def test_multiple_disabled_slots_all_omitted(self) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_1=False, image_3=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
            image_4="img4",
        )
        assert result == (["img2", "img4"],)

    def test_malformed_toggles_json_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="not json{{", image_1="img1", image_2="img2")
        assert result == (["img1", "img2"],)

    def test_toggles_that_is_not_a_json_object_falls_back_to_all_enabled(self) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles="[1, 2, 3]", image_1="img1")
        assert result == (["img1"],)


class TestAllOffOrNoneConnectedReturnsAnExecutionBlocker:
    """FORMAT.md §6.4 "All-off / none-connected is a VALID state" (owner
    decision 2026-07-20, superseding the v0.14.0 behavior these tests used
    to cover -- a queue-time ``ValueError``): queueing with nothing
    connected, or everything toggled off, must SUCCEED. ``execute`` returns
    a one-element list holding a silent (``message=None``)
    ``ExecutionBlocker`` instead of raising.
    """

    def test_nothing_connected_returns_a_one_element_blocker_list(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles())
        assert isinstance(result, tuple)
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert len(result[0]) == 1
        blocker = result[0][0]
        assert isinstance(blocker, fake_execution_blocker)
        # `message=None` matters: execution.py's own `execution_block_cb`
        # only broadcasts an `execution_error` websocket event when
        # `.message is not None` -- a message here would turn our graceful
        # skip back into a reported error.
        assert blocker.message is None

    def test_nothing_connected_logs_at_info_not_warning_or_error(
        self, fake_execution_blocker: type, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(toggles=_toggles())
        assert any(
            "no image inputs are connected" in record.message for record in caplog.records
        )
        assert all(record.levelno <= logging.INFO for record in caplog.records)

    def test_all_connected_but_toggled_off_returns_a_one_element_blocker_list(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(
            toggles=_toggles(image_1=False, image_2=False, image_3=False),
            image_1="img1",
            image_2="img2",
            image_3="img3",
        )
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)
        assert result[0][0].message is None

    def test_all_toggled_off_log_names_the_count_and_toggled_off(
        self, fake_execution_blocker: type, caplog: pytest.LogCaptureFixture
    ) -> None:
        node = EPSSwitcher()
        with caplog.at_level(logging.INFO, logger="eps_image"):
            node.execute(
                toggles=_toggles(image_1=False, image_2=False, image_3=False),
                image_1="img1",
                image_2="img2",
                image_3="img3",
            )
        messages = [record.message for record in caplog.records]
        assert any("3 image input" in message for message in messages)
        assert any("toggled off" in message for message in messages)

    def test_only_none_valued_slots_returns_the_nothing_connected_blocker(
        self, fake_execution_blocker: type
    ) -> None:
        node = EPSSwitcher()
        result = node.execute(toggles=_toggles(), image_1=None, image_2=None)
        assert len(result[0]) == 1
        assert isinstance(result[0][0], fake_execution_blocker)

    def test_all_off_does_not_raise(self, fake_execution_blocker: type) -> None:
        # The headline behavior change, asserted directly rather than only
        # via return-value shape: this must not raise ANYTHING.
        node = EPSSwitcher()
        node.execute(toggles=_toggles(image_1=False), image_1="img1")

    def test_missing_fake_execution_blocker_module_surfaces_as_import_error(self) -> None:
        # Sanity check on the test fixture's own premise (no
        # `fake_execution_blocker` requested here): without a real or faked
        # `comfy_execution.graph` on the path, the lazy import inside the
        # all-off branch fails loudly rather than silently -- confirms the
        # other tests above are genuinely exercising that import, not
        # accidentally passing because ExecutionBlocker was already
        # importable some other way.
        import sys as _sys

        if "comfy_execution" in _sys.modules or "comfy_execution.graph" in _sys.modules:
            pytest.skip("comfy_execution is already importable in this environment")
        node = EPSSwitcher()
        with pytest.raises(ModuleNotFoundError):
            node.execute(toggles=_toggles())


# --------------------------------------------------------- class shape / spec


class TestClassShapeMatchesFormatMdSection6_4:
    def test_category(self) -> None:
        assert EPSSwitcher.CATEGORY == "EPSNodes"

    def test_return_types_is_a_single_image_output(self) -> None:
        assert EPSSwitcher.RETURN_TYPES == ("IMAGE",)
        assert EPSSwitcher.RETURN_NAMES == ("images",)

    def test_output_is_list_flagged_true(self) -> None:
        assert EPSSwitcher.OUTPUT_IS_LIST == (True,)

    def test_function_name_matches_the_declared_entry_point(self) -> None:
        assert EPSSwitcher.FUNCTION == "execute"
        assert callable(getattr(EPSSwitcher(), EPSSwitcher.FUNCTION))

    def test_execute_return_shape_is_a_one_tuple_of_a_list(self) -> None:
        result = EPSSwitcher().execute(toggles=_toggles(), image_1="img1")
        assert isinstance(result, tuple)
        assert len(result) == 1
        assert isinstance(result[0], list)


# --------------------------------------------------------------- no ComfyUI import


def test_module_never_imports_comfy_or_torch() -> None:
    assert "comfy" not in nodes_switcher.__dict__
    assert "torch" not in nodes_switcher.__dict__
    source = inspect.getsource(sys.modules[nodes_switcher.__name__])
    assert "import comfy" not in source
    assert "import torch" not in source
