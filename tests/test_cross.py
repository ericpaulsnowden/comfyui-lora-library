"""Tests for ``eps_image.nodes_cross`` (FORMAT.md §6.9, "EPS Cross Product").

Pure-Python contract tests -- elements are opaque sentinels, no torch/ComfyUI
required (the module promises no module-scope imports of either; verified
here the same way ``test_switcher.py`` does).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from eps_image.nodes_cross import EPSCrossProduct


@pytest.fixture
def fake_execution_blocker(monkeypatch: pytest.MonkeyPatch):
    """Fake ``comfy_execution.graph.ExecutionBlocker`` in ``sys.modules`` --
    the same convention as ``test_switcher.py``'s fixture of the same name
    (the empty-side path imports it lazily from exactly that module path).
    """

    class FakeExecutionBlocker:
        def __init__(self, message):
            self.message = message

    import types

    graph_mod = types.ModuleType("comfy_execution.graph")
    graph_mod.ExecutionBlocker = FakeExecutionBlocker
    pkg_mod = types.ModuleType("comfy_execution")
    pkg_mod.graph = graph_mod
    monkeypatch.setitem(sys.modules, "comfy_execution", pkg_mod)
    monkeypatch.setitem(sys.modules, "comfy_execution.graph", graph_mod)
    return FakeExecutionBlocker


class TestCrossProduct:
    def test_owner_case_2_images_x_4_texts_is_8_pairs_image_major(self) -> None:
        """The report that motivated this node: grid(2) x notebook(4) must be
        8 pairs -- not core's zip of 4 with the last image repeated."""
        images, texts, names = EPSCrossProduct().run(
            images=["i1", "i2"], texts=["p1", "p2", "p3", "p4"]
        )
        assert images == ["i1", "i1", "i1", "i1", "i2", "i2", "i2", "i2"]
        assert texts == ["p1", "p2", "p3", "p4", "p1", "p2", "p3", "p4"]
        assert names == [""] * 8  # unwired names -> aligned empties, never a shape change

    def test_names_ride_the_same_cross_aligned_with_texts(self) -> None:
        """2026-07-23b: the Notebook's name list (parallel to its text list)
        crosses identically, so every output pair keeps its identity."""
        images, texts, names = EPSCrossProduct().run(
            images=["i1", "i2"], texts=["p1", "p2"], names=["NameA", "NameB"]
        )
        assert list(zip(texts, names, strict=True)) == [
            ("p1", "NameA"), ("p2", "NameB"), ("p1", "NameA"), ("p2", "NameB"),
        ]
        assert images == ["i1", "i1", "i2", "i2"]

    def test_short_names_list_pads_with_empty_strings(self) -> None:
        _images, texts, names = EPSCrossProduct().run(
            images=["i"], texts=["p1", "p2", "p3"], names=["OnlyOne"]
        )
        assert texts == ["p1", "p2", "p3"]
        assert names == ["OnlyOne", "", ""]

    def test_single_by_single_is_one_pair(self) -> None:
        images, texts, names = EPSCrossProduct().run(images=["i"], texts=["t"])
        assert (images, texts, names) == (["i"], ["t"], [""])

    def test_pairs_stay_index_aligned(self) -> None:
        images, texts, _names = EPSCrossProduct().run(
            images=["a", "b", "c"], texts=["x", "y"]
        )
        assert len(images) == len(texts) == 6
        assert list(zip(images, texts, strict=True)) == [
            ("a", "x"), ("a", "y"), ("b", "x"), ("b", "y"), ("c", "x"), ("c", "y"),
        ]

    def test_batch_elements_stay_single_elements(self) -> None:
        """A [B,H,W,C] batch element is ONE element (switcher-consistent):
        the node never unpacks what upstream produced."""
        batch = object()
        images, _texts, _names = EPSCrossProduct().run(images=[batch], texts=["t1", "t2"])
        assert images == [batch, batch]

    def test_none_elements_are_dropped(self) -> None:
        images, texts, _names = EPSCrossProduct().run(
            images=["i1", None, "i2"], texts=[None, "t"]
        )
        assert images == ["i1", "i2"]
        assert texts == ["t", "t"]

    def test_bare_non_list_inputs_are_tolerated(self) -> None:
        images, texts, names = EPSCrossProduct().run(
            images="solo", texts="text", names="lone"
        )
        assert (images, texts, names) == (["solo"], ["text"], ["lone"])

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"images": [], "texts": ["t"]},
            {"images": ["i"], "texts": []},
            {"images": [], "texts": []},
            {"images": [None], "texts": ["t"]},
            {"images": None, "texts": None},
        ],
    )
    def test_empty_side_returns_blocker_triple(self, kwargs, fake_execution_blocker) -> None:
        outputs = EPSCrossProduct().run(**kwargs)
        assert len(outputs) == 3
        for lst in outputs:
            assert len(lst) == 1 and isinstance(lst[0], fake_execution_blocker)


class TestClassShape:
    def test_category(self) -> None:
        assert EPSCrossProduct.CATEGORY == "EPSNodes"

    def test_input_is_list_flagged_true(self) -> None:
        assert EPSCrossProduct.INPUT_IS_LIST is True

    def test_outputs_are_three_paired_lists(self) -> None:
        assert EPSCrossProduct.RETURN_TYPES == ("IMAGE", "STRING", "STRING")
        assert EPSCrossProduct.RETURN_NAMES == ("image", "text", "name")
        assert EPSCrossProduct.OUTPUT_IS_LIST == (True, True, True)

    def test_inputs_required_plus_optional_names(self) -> None:
        spec = EPSCrossProduct.INPUT_TYPES()
        assert set(spec["required"]) == {"images", "texts"}
        assert spec["required"]["texts"][1]["forceInput"] is True
        assert set(spec["optional"]) == {"names"}
        assert spec["optional"]["names"][1]["forceInput"] is True

    def test_function_entry_point(self) -> None:
        assert callable(getattr(EPSCrossProduct, EPSCrossProduct.FUNCTION))


def test_module_never_imports_comfy_or_torch() -> None:
    """Same guarantee (and same proof) as the sibling modules: importing the
    module in a bare interpreter must not drag in torch/ComfyUI."""
    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, r'" + str(repo) + "'); "
        "import eps_image.nodes_cross; "
        "bad = [m for m in sys.modules if m == 'torch' or m.startswith('torch.') "
        "or m == 'comfy' or m.startswith('comfy.') or m.startswith('comfy_execution')]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
