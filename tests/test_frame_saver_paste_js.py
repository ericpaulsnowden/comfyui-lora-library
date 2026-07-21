"""Pure-logic tests for the EPS Frame Saver paste-a-path helpers (FORMAT.md
§6.7 — the 2026-07-21 "paste a video path onto the node" feature).

``web/eps_image/frame_saver.js`` factors the paste pipeline's decision logic
into PURE exported functions (``stripWrappingQuotes``/``fileUrlToPath`` and
their composite ``cleanPastedVideoPath``, plus ``pathExtension``/
``looksAbsolutePath``/``isTextEntryElement`` and the whole-decision
``evaluatePastedText`` verdict) so the clipboard-text contract is testable
without a browser. The module's imports are ComfyUI's
``../../../scripts/api.js`` and ``../../../scripts/app.js``, resolved against
the served layout (``<web root>/extensions/<pack>/eps_image/frame_saver.js``
-> ``<web root>/scripts/*.js``), so the fixture mirrors that exact directory
depth in a tmp dir with stub modules, byte-copies the real module in
unchanged, and evaluates one probe script under Node (same runtime family as
the browser) — the exact pattern ``test_resolution_grid_js.py`` established.
This doubles as a regression test that the relative import depth itself is
correct. Skips cleanly when Node isn't installed; the LIVE event mechanics
(sole-selection gating, focused-field bail-out, capture-phase consumption,
listener removal on node delete) are verified on the rig, not here.
"""

# ruff: noqa: E501 — case tables and descriptive assert messages read better
# on one line here than wrapped; this exemption is scoped to this test file.

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAME_SAVER_JS = REPO_ROOT / "web" / "eps_image" / "frame_saver.js"
ROUTES_FRAME_SAVER_PY = REPO_ROOT / "eps_image" / "routes_frame_saver.py"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node (JS runtime) not installed")

# --------------------------------------------------------------------- cases
# Each list pairs (clipboard/pasted input, expected helper output). The
# inputs are shipped into the probe below; the expectations stay here so a
# failure names the exact case in pytest's diff.

#: stripWrappingQuotes — exactly ONE pair of matching wrapping quotes comes
#: off (Explorer's "Copy as path" double-quotes; shells single-quote), with
#: trimming; everything else survives untouched.
QUOTE_CASES = [
    ('"C:\\clips\\take 1.mp4"', "C:\\clips\\take 1.mp4"),
    ("'/Users/eric/clip.mp4'", "/Users/eric/clip.mp4"),
    ('  "D:\\v\\a.mp4"  ', "D:\\v\\a.mp4"),
    ('"unbalanced', '"unbalanced'),  # no matching pair -> unchanged
    ("it's fine.mp4", "it's fine.mp4"),  # inner apostrophe survives
    ('""', ""),
    ("", ""),
]

#: fileUrlToPath — file:// URLs decode to plain paths (percent-escapes,
#: the /C:/ drive form, localhost, and a real host -> UNC); non-URLs pass
#: through; a malformed %-escape keeps the raw pathname (fails soft).
FILE_URL_CASES = [
    ("file:///Users/eric/My%20Videos/clip.mp4", "/Users/eric/My Videos/clip.mp4"),
    ("file:///C:/clips/video.mp4", "C:/clips/video.mp4"),
    ("file://localhost/Users/eric/clip.mp4", "/Users/eric/clip.mp4"),
    ("file://nas/share/clip.mp4", "\\\\nas\\share\\clip.mp4"),
    ("FILE:///tmp/clip.mp4", "/tmp/clip.mp4"),  # scheme is case-insensitive
    ("/already/plain.mp4", "/already/plain.mp4"),
    ("file:///Users/eric/100%.mp4", "/Users/eric/100%.mp4"),
]

#: cleanPastedVideoPath — the composite: first non-empty line (Finder
#: multi-select copies one path per line), quotes, file://, trim.
CLEAN_CASES = [
    ('"D:\\video\\clip.mp4"\r\n', "D:\\video\\clip.mp4"),
    ("\n\n/Users/eric/clip.mp4\n/Users/eric/other.mp4\n", "/Users/eric/clip.mp4"),
    ("   /Users/eric/a.mp4   ", "/Users/eric/a.mp4"),
    ("'file:///tmp/a%20b.mp4'", "/tmp/a b.mp4"),  # quotes first, THEN file://
    ("", ""),
    ("   \n  \n", ""),
]

#: pathExtension — mirrors Python ``Path.suffix`` semantics, lowercased
#: (last dot of the LAST component; leading dot (`.hidden`) / trailing dot
#: (`name.`) is NO extension). Expectations are hardcoded rather than
#: computed from the local Python on purpose: any drift on the exotic
#: shapes is harmless (the client ACCEPTS no-extension paths and lets the
#: probe route be the authority), while the common shapes below are the
#: real contract.
EXTENSION_CASES = [
    ("/a/b/clip.mp4", ".mp4"),
    ("C:\\clips\\CLIP.MOV", ".mov"),
    ("/a/.hidden", ""),
    ("/a/name.", ""),
    ("/a/noext", ""),
    ("/a/archive.tar.gz", ".gz"),
    ("\\\\nas\\share\\clip.webm", ".webm"),
]

#: looksAbsolutePath — POSIX, Windows drive (either slash), and UNC are
#: path-shaped; relative/homedir/empty are not.
ABSOLUTE_CASES = [
    ("/Users/eric/clip.mp4", True),
    ("C:\\clips\\a.mp4", True),
    ("D:/clips/a.mp4", True),
    ("\\\\nas\\share\\a.mp4", True),
    ("clips/a.mp4", False),
    ("~/clips/a.mp4", False),
    ("", False),
    ("file.mp4", False),
]

#: evaluatePastedText — (input, expected action, expected path). 'ignore'
#: means the paste event is NOT consumed (core's pipeline still sees it);
#: 'reject' consumes but leaves the current video alone; 'accept' routes
#: to chooseVideoPath (the Browse code path).
VERDICT_CASES = [
    ("", "ignore", ""),
    ("hello world", "ignore", ""),
    ('{"nodes": [], "links": []}', "ignore", ""),  # workflow JSON stays core's
    ("relative/clip.mp4", "ignore", ""),
    ('"C:\\clips\\shot 01.mp4"', "accept", "C:\\clips\\shot 01.mp4"),
    ("file:///Users/eric/My%20Videos/clip.webm", "accept", "/Users/eric/My Videos/clip.webm"),
    ("/Users/eric/pic.png", "reject", "/Users/eric/pic.png"),
    ("/Users/eric/no_extension", "accept", "/Users/eric/no_extension"),  # probe decides
    ("/Users/eric/clip.MOV", "accept", "/Users/eric/clip.MOV"),  # case survives; POSIX is case-sensitive
    ("/" + "a" * 5000 + ".mp4", "ignore", ""),  # over-long: a document, not a path
]

#: isTextEntryElement — duck-typed DOM stand-ins (what the real handler
#: checks event.target/document.activeElement against).
TEXT_ENTRY_EXPECTED = [False, True, True, True, False, True, False]

PROBE_JS = """
import * as fs from './extensions/comfyui-epsnodes/eps_image/frame_saver.js'

const CASES = %(cases)s

const out = {
  exports: {
    hasInit: typeof fs.init === 'function',
    hasAttach: typeof fs.attach === 'function',
    hasLoadedGraphNode: typeof fs.loadedGraphNode === 'function'
  },
  videoExtList: fs.VIDEO_EXT_LIST,
  quotes: CASES.quotes.map((text) => fs.stripWrappingQuotes(text)),
  fileUrls: CASES.fileUrls.map((text) => fs.fileUrlToPath(text)),
  cleaned: CASES.cleaned.map((text) => fs.cleanPastedVideoPath(text)),
  extensions: CASES.extensions.map((path) => fs.pathExtension(path)),
  absolutes: CASES.absolutes.map((path) => fs.looksAbsolutePath(path)),
  verdicts: CASES.verdicts.map((text) => fs.evaluatePastedText(text)),
  textEntry: [
    fs.isTextEntryElement(null),
    fs.isTextEntryElement({ tagName: 'INPUT' }),
    fs.isTextEntryElement({ tagName: 'textarea' }),
    fs.isTextEntryElement({ tagName: 'SELECT' }),
    fs.isTextEntryElement({ tagName: 'CANVAS' }),
    fs.isTextEntryElement({ tagName: 'DIV', isContentEditable: true }),
    fs.isTextEntryElement({ tagName: 'DIV' })
  ]
}

process.stdout.write(JSON.stringify(out))
"""


@pytest.fixture(scope="module")
def paste_api(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Runs the probe against the REAL frame_saver.js in a served-layout tmp
    dir (see module docstring) and returns its JSON output."""
    layout = tmp_path_factory.mktemp("web_root")

    scripts = layout / "scripts"
    scripts.mkdir()
    # The module touches `api`/`app` only lazily (fetches, canvas selection,
    # toast plumbing); bare objects exercise the same optional-chaining the
    # browser path relies on.
    (scripts / "api.js").write_text("export const api = {}\n", encoding="utf-8")
    (scripts / "app.js").write_text("export const app = {}\n", encoding="utf-8")

    module_dir = layout / "extensions" / "comfyui-epsnodes" / "eps_image"
    module_dir.mkdir(parents=True)
    shutil.copyfile(FRAME_SAVER_JS, module_dir / "frame_saver.js")

    cases = {
        "quotes": [text for text, _ in QUOTE_CASES],
        "fileUrls": [text for text, _ in FILE_URL_CASES],
        "cleaned": [text for text, _ in CLEAN_CASES],
        "extensions": [path for path, _ in EXTENSION_CASES],
        "absolutes": [path for path, _ in ABSOLUTE_CASES],
        "verdicts": [text for text, _, _ in VERDICT_CASES],
    }
    probe = layout / "probe.mjs"
    probe.write_text(PROBE_JS % {"cases": json.dumps(cases)}, encoding="utf-8")

    result = subprocess.run(
        [NODE, str(probe)], capture_output=True, text=True, timeout=60, cwd=layout
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_frame_saver_js_parses() -> None:
    """`node --check` — the file must at minimum be valid ES module syntax."""
    result = subprocess.run(
        [NODE, "--check", str(FRAME_SAVER_JS)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_module_still_exports_the_extension_entry_points(paste_api: dict) -> None:
    """web/eps_image.js consumes init()/attach()/loadedGraphNode(); the test
    exports must never displace them."""
    assert paste_api["exports"] == {
        "hasInit": True,
        "hasAttach": True,
        "hasLoadedGraphNode": True,
    }


# ------------------------------------------------------------- path cleanup


def test_strip_wrapping_quotes(paste_api: dict) -> None:
    """Explorer's "Copy as path" double-quotes and shell single-quotes come
    off (one pair, trimmed); unbalanced or inner quotes survive."""
    for (given, expected), got in zip(QUOTE_CASES, paste_api["quotes"], strict=True):
        assert got == expected, f"stripWrappingQuotes({given!r}) -> {got!r}, wanted {expected!r}"


def test_file_url_decoding(paste_api: dict) -> None:
    """file:// URLs decode to plain server-openable paths: percent-escapes,
    the /C:/ Windows-drive form, localhost, host -> UNC; non-URLs pass
    through; malformed escapes fail soft to the raw pathname."""
    for (given, expected), got in zip(FILE_URL_CASES, paste_api["fileUrls"], strict=True):
        assert got == expected, f"fileUrlToPath({given!r}) -> {got!r}, wanted {expected!r}"


def test_clean_pasted_video_path_composite(paste_api: dict) -> None:
    """The full cleanup pipeline: first non-empty line, quotes, file://,
    trim — the exact transform the paste handler applies to clipboard
    text before judging it."""
    for (given, expected), got in zip(CLEAN_CASES, paste_api["cleaned"], strict=True):
        assert got == expected, f"cleanPastedVideoPath({given!r}) -> {got!r}, wanted {expected!r}"


# --------------------------------------------------------- path classifying


def test_path_extension_mirrors_python_suffix_semantics(paste_api: dict) -> None:
    """Lowercased last-dot-of-last-component, with `.hidden`/`name.` as NO
    extension — the same verdict routes_frame_saver.py's Path.suffix check
    reaches, so client and server never disagree on the common shapes."""
    for (given, expected), got in zip(EXTENSION_CASES, paste_api["extensions"], strict=True):
        assert got == expected, f"pathExtension({given!r}) -> {got!r}, wanted {expected!r}"


def test_looks_absolute_path(paste_api: dict) -> None:
    """POSIX roots, Windows drives (either slash), and UNC shares are
    path-shaped; relative/homedir/empty text is not (and so is never
    consumed by the paste handler)."""
    for (given, expected), got in zip(ABSOLUTE_CASES, paste_api["absolutes"], strict=True):
        assert got is expected, f"looksAbsolutePath({given!r}) -> {got!r}, wanted {expected!r}"


# ------------------------------------------------------------ whole verdict


def test_evaluate_pasted_text_verdicts(paste_api: dict) -> None:
    """The composite decision: non-paths are ignored (event NOT consumed —
    workflow JSON etc. stays core's), wrong-extension paths reject without
    clobbering the current video, video/no-extension absolute paths accept
    into the Browse code path with their original casing intact."""
    for (given, action, path), got in zip(VERDICT_CASES, paste_api["verdicts"], strict=True):
        assert got["action"] == action, f"evaluatePastedText({given!r}) -> {got}, wanted {action}"
        assert got["path"] == path, f"evaluatePastedText({given!r}) path {got['path']!r} != {path!r}"


def test_reject_reason_names_the_extension_and_the_allowlist(paste_api: dict) -> None:
    """The one reject case's user-facing reason must say WHAT was wrong
    (.png) and what would be accepted (the allowlist), matching the
    server's own rejection vocabulary."""
    rejects = [v for v in paste_api["verdicts"] if v["action"] == "reject"]
    assert len(rejects) == 1
    reason = rejects[0]["reason"]
    assert ".png" in reason
    assert ".mp4" in reason and ".mov" in reason  # allowlist is spelled out


def test_video_ext_list_matches_backend_allowlist(paste_api: dict) -> None:
    """frame_saver.js documents VIDEO_EXT_LIST as hand-lockstepped with
    routes_frame_saver.py's VIDEO_EXTENSIONS — machine-check it, so the
    paste early-reject can never disagree with what probe/stream accept."""
    source = ROUTES_FRAME_SAVER_PY.read_text(encoding="utf-8")
    match = re.search(r"VIDEO_EXTENSIONS\s*=\s*(\([^)]*\))", source)
    assert match, "VIDEO_EXTENSIONS tuple not found in routes_frame_saver.py"
    backend = list(ast.literal_eval(match.group(1)))
    assert paste_api["videoExtList"] == backend


# ------------------------------------------------------------- typing guard


def test_is_text_entry_element_duck_typing(paste_api: dict) -> None:
    """input/textarea/select (any casing) and contenteditable hosts are
    typing surfaces the paste handler must never hijack; canvas/div/null
    are fair game."""
    assert paste_api["textEntry"] == TEXT_ENTRY_EXPECTED
