/**
 * @file Prompt Notebook two-pane DOM widget (FORMAT.md §7.2) — attaches to
 * `LoraLibraryNotebook` nodes. Left pane: a scrollable, category-grouped,
 * multi-selectable, drag-to-reorder, double-click-to-rename entry list —
 * with CLICKABLE category headers (incl. empty ones — see "Categories"
 * below) and a ＋ New control that creates either an entry or (given a
 * `#`-prefixed name) a category, plus 🗑 Delete (entry-only). Right pane: a
 * `<textarea>` editor with a Save button and a status line (conflict
 * resolution per §3.5 lands there too) that's CONTEXTUAL — entry body or
 * category description, per whichever was last clicked, with a mode hint
 * saying which. Above both panes, a file panel shows the notebook's
 * RESOLVED absolute path plus Browse…/Open folder buttons — hidden (and the
 * `file` widget made effectively read-only) for a remote (`is_local: false`)
 * viewer. The node's own `file`/`entry` STRING widgets stay the serialized
 * truth (§6.1/§7.2) — this DOM widget only ever *reads* `file` and *writes*
 * `entry`/`file` through their normal widget setters; it never serializes
 * itself.
 *
 * Multi-select (FORMAT.md §6.1/§7.2, owner amendment 2026-07-18): ctrl/
 * cmd+click toggles one entry in/out of the selection, shift+click extends
 * the visible range from the ACTIVE entry (the most recently clicked one),
 * and a plain click collapses to a single selection. `entry` serializes the
 * whole selection as one name per line, in selection order (§6.1) — the
 * ACTIVE entry alone drives the editor pane, dirty tracking, Save, Delete,
 * and conflict handling, exactly like the single-select behavior this file
 * always had. See the "Selection model" section below the state helpers.
 *
 * Drag-to-reorder (FORMAT.md §3.4/§5/§7.2, owner amendment 2026-07-18):
 * pointer-based row dragging (deliberately not HTML5 drag-and-drop — see the
 * pointer-events bullet below) with an insertion-line marker, committed as
 * one `POST /lora_library/notebook/move`. See the "Drag-to-reorder" section
 * below the selection helpers.
 *
 * Frontend APIs relied on here (verified against a `Comfy-Org/ComfyUI_frontend`
 * checkout — see the notebook-frontend handoff notes for exact file:line
 * references):
 *  - `LGraphNode.prototype.addDOMWidget(name, type, element, options)` —
 *    present for both the legacy canvas renderer and the Vue-node renderer
 *    (`scripts/domWidget.ts`), which is why it is used instead of any
 *    renderer-specific API.
 *  - `options.getMinHeight` and the *absence* of `options.getMaxHeight` —
 *    litegraph's widget-arrange pass (`LGraphNode._arrangeWidgets`) gives
 *    DOM widgets whatever vertical space is left after fixed-height widgets
 *    (`file`, `entry`) via `distributeSpace()`; an unset max means "take all
 *    remaining space", which is exactly "the widget fills available height".
 *  - `node.comfyClass` — ComfyUI's node-registration step
 *    (`services/litegraphService.ts`) sets `comfyClass` on *both* the node
 *    class's `.prototype` and the class itself, specifically so extensions
 *    can feature-detect a node's Python class id from `nodeCreated`; this is
 *    the same mechanism core extensions (e.g. `extensions/core/load3d.ts`)
 *    use.
 *  - Excluding a DOM widget from serialization has two independent knobs:
 *    `widget.serialize = false` (workflow JSON — checked by
 *    `LGraphNode.serialize`/`.configure`) and `widget.options.serialize =
 *    false` / `widget.serializeValue = () => undefined` (the API prompt sent
 *    for execution — checked by `utils/executionUtil.ts`). All three are set
 *    below so the widget never serializes under either mechanism, matching
 *    core's own idiom (e.g. `extensions/core/webcamCapture.ts`:
 *    `btn.serializeValue = () => undefined`).
 *  - Pointer events over a DOM widget are NOT swallowed by the litegraph
 *    canvas underneath it, which is why drag-to-reorder below uses plain
 *    pointerdown/move/up instead of HTML5 DnD (owner's ask, since native DnD
 *    is known to fight canvas-level handlers):
 *    `src/components/graph/GraphCanvas.vue` mounts `<canvas
 *    id="graph-canvas">` (line 58) and `<DomWidgets>` (line 113) as DOM
 *    SIBLINGS in the same template, never nested. A pointerdown that targets
 *    our widget's elements therefore never passes through the `<canvas>`
 *    element at all, so litegraph's own capture-phase handler
 *    (`LGraphCanvas.ts:2026`: `canvas.addEventListener('pointerdown',
 *    this._mousedown_callback, true)`) structurally cannot see it — capture
 *    phase only intercepts events whose target is a descendant of the
 *    listener's element, and a DOM sibling is not a descendant. On top of
 *    that, `src/components/graph/widgets/DomWidget.vue` (lines 109-113)
 *    inline-styles `pointerEvents: 'auto'` on the widget wrapper whenever it
 *    is visible, not read-only, and not disabled (the normal editing state)
 *    — that's what makes the browser hit-test to our DOM content instead of
 *    falling through to the canvas visually underneath it. (The only other
 *    capture-phase `document`-level pointerdown listener found,
 *    `useNodeDragToCanvas.ts:125`, only activates mid "drag a new node from
 *    the library onto the canvas" and no-ops otherwise, so it doesn't
 *    interfere either.) This file's pre-existing pane-splitter drag
 *    (`wireSplitter`, unchanged below) already exercised this exact
 *    pointer-event path live before today's change — drag-to-reorder reuses
 *    the identical technique.
 *
 * Multi-delete (FORMAT.md §7.2 amendment, owner 2026-07-18c): Delete now
 * removes EVERY selected entry, not just the active one. The confirm label
 * shows the count when >1 ("Are you sure? (3)"); deletion is sequential
 * over the existing single-entry §5 delete route (one request per name, in
 * selection order), refreshing `base_mtime` from each response so later
 * requests in the same run check against the file's latest state. A
 * mid-run 409 stops the run and surfaces the same Reload/Overwrite
 * conflict UI Save/Move already use; Overwrite resumes the run from the
 * failed name with that one request forced (base_mtime omitted), then
 * continues normally. See performDeleteRun() below.
 *
 * Rename (FORMAT.md §7.2 amendment): double-clicking a row swaps its label
 * for an inline `<input>` (same `.llnb-input` styling family as the
 * New-entry row) with ✓/✕ buttons; Enter/✓ commits via the existing §5
 * entry route's `rename_to` + `base_mtime`, Esc/blur cancels. Click-vs-
 * drag-vs-double-click disambiguation: a browser's native `dblclick` only
 * ever fires *after* two complete click cycles on the same element, and
 * both of this file's own click handlers (onEntryPointerDown's
 * sub-threshold-movement path, dispatching to handleEntryClick) run
 * synchronously inside each of those two cycles — so by the time our
 * `dblclick` listener runs, both single-clicks have already resolved
 * (normally collapsing the selection to just this row, per selectSingle(),
 * which is also why the renamed row is always `state.activeName`). There
 * is therefore no "pending click" to race against the dblclick itself; the
 * real hazard is a LATER-resolving async side effect of one of those
 * clicks — specifically chooseSelection()'s load-failure rollback — calling
 * renderList() after rename mode has already opened. renderList() is what
 * renders `state.renamingName`'s row as the input in the first place
 * (rather than an imperative one-off DOM swap), so any such incidental
 * re-render regenerates the SAME rename input instead of destroying it.
 * Every other "something else wants the list/selection to change" entry
 * point (chooseSelection, openNewEntryRow, reloadNow) explicitly calls
 * closeRenameRow() first, exactly like they already do for
 * cancelDeleteConfirm()/closeNewEntryRow() — so clicking another row (or
 * anything else that mutates the list) deterministically cancels an
 * in-progress rename rather than leaving it to chance. The one case that
 * isn't one of those explicit call sites — focus simply leaving the input
 * (clicking Save, the splitter, outside the widget, tabbing away) — is
 * caught by the input's own `blur` handler, deferred by one tick so a
 * same-tick blur racing a successful commit's own renderList() (which
 * naturally blurs the about-to-be-removed input) sees `renamingName`
 * already cleared and no-ops. The confirm/cancel buttons call
 * `preventDefault()` on their OWN `mousedown` for the standard reason:
 * without it, clicking them would shift focus away from the input (firing
 * that same blur-cancel) before their `click` handler ever ran. See the
 * "Rename" section below (openRenameRow/closeRenameRow/buildRenameRow/
 * performRename) for the implementation.
 *
 * File panel + remote gating (FORMAT.md §7.2 amendment): a muted bar
 * between the node's own widgets and the two panes shows the notebook's
 * RESOLVED absolute path (the `file` field of every `GET /notebook`
 * response — NOT the `file` WIDGET's possibly-relative value), truncated
 * with a `direction: rtl` trick (keeps the tail — usually the filename —
 * visible instead of the head) and the full path in `title`. Its two
 * buttons: `Browse…` opens a small modal file picker (attached to
 * `document.body`, not nested inside this widget's own root — see
 * openBrowsePicker()'s doc comment for why) walking `GET /fs/list`;
 * `Open folder` fires `POST /notebook/open_folder` and reports failure on
 * the status line. `GET /config`'s `is_local` (fetched once per attach,
 * cached at MODULE scope with a short TTL so N attached nodes share one
 * fetch) hides both buttons and makes the `file` widget effectively
 * read-only for a remote (non-loopback) viewer — deliberately NOT via
 * `widget.disabled`, which on this litegraph fork blanks a disabled TEXT
 * widget's VALUE entirely rather than just graying it out (see
 * controller.js's header finding on its own `status` widget); instead the
 * `file` widget's callback (already wrapped by wireFileWidget below)
 * reverts any edit back to the last known-good value and posts a calm
 * status note. Every other feature in this file (browsing/editing/saving/
 * deleting/renaming/reordering entries) stays fully functional for a
 * remote viewer — only the FILE the node points at is host-controlled.
 *
 * Categories (FORMAT.md §7.2 amendment, owner ask 2026-07-19): typing a name
 * STARTING WITH `#` into the ＋ New row creates a CATEGORY instead of an
 * entry (POST `/notebook/category`; the `#`s + surrounding whitespace are
 * stripped from the stored name — see isCategoryNameInput()/
 * categoryNameFromInput()). Category headers, rendered from the §5
 * `categories` list rather than derived from `entries` (so an EMPTY
 * category still shows — see renderList()'s two-pointer merge of
 * `categories` and `entries`, both already in file order), are CLICKABLE:
 * selecting one (selectCategory()) enters "category mode" —
 * `state.activeCategory` holds its name, the header highlights, and the
 * SAME editor pane/textarea/Save button/dirty-tracking/base_mtime-conflict
 * machinery entry-editing already used now targets that category's §3.1
 * description (GET/POST `/notebook/category`) instead — see
 * performSaveCategory(), the category-mode sibling of performSave().
 * `state.modeHintEl` (a muted line directly above the textarea) always says
 * which of the two the editor currently targets. Category mode is
 * deliberately UI-only: it is never allowed to touch `state.selection`,
 * `state.activeName`, the `entry` widget, or multi-select — clicking an
 * entry always exits it (chooseSelection() clears `activeCategory`, and
 * reloads the entry pane even if the clicked entry was already the
 * "active" one underneath category mode) and clicking a header always
 * enters it, but neither path ever calls setSelection()/syncEntryWidget().
 * Delete is entry-only and disabled outright in category mode
 * (updateDeleteButtonEnabled()); double-click-to-rename stays wired to entry
 * rows only, so it never applies to a header. Drag-reorder/drop-on-header
 * targeting is unaffected — headers were already valid drop targets
 * (computeDropTarget()) before category mode existed, and that geometry
 * doesn't know or care whether a header happens to be the active one.
 *
 * Vanilla ES modules, no build step — DOM nodes are built with
 * `document.createElement` (see the local `el()` helper) rather than any
 * templating, matching this pack's other frontend modules.
 */

import * as api from './api.js'

/** FORMAT.md §6.1 — frozen once shipped. */
const NODE_CLASS = 'LoraLibraryNotebook'

const WIDGET_NAME = 'notebook'
const WIDGET_TYPE = 'lora_library_notebook'

/** FORMAT.md §7.2: "resizable via getMinHeight (~180)". */
const MIN_WIDGET_HEIGHT = 180

/** How long the Delete button stays in "Are you sure?" mode. */
const DELETE_CONFIRM_MS = 4000

/** Debounce for reloading after the `file` widget's value changes. */
const FILE_CHANGE_DEBOUNCE_MS = 250

/**
 * Pointer-movement distance (px) before a row pointerdown "becomes" a drag
 * instead of a click (owner ask: "~4px"). Below this, pointerup resolves as
 * a plain/ctrl/shift click; at or past it, the gesture commits to reordering
 * and the click never fires — see onEntryPointerDown().
 */
const DRAG_THRESHOLD_PX = 4

const STYLE_TAG_ID = 'lora-library-notebook-styles'

/** Nodes we've already attached to — guards against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Styles — one injected <style> tag, guarded so re-registration (hot reload,
// multiple nodes) never duplicates it. Uses ComfyUI's own theme variables
// (verified against Comfy-Org/ComfyUI_frontend's `assets/palettes/dark.json`
// / `light.json`) with literal fallbacks so the widget still looks
// intentional on a frontend old enough not to define them.
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.llnb-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.llnb-filepanel {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-bottom: 1px solid var(--border-color, #444);
  background: var(--comfy-menu-bg, #262626);
}
.llnb-filepanel-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  /* Front-truncation trick (owner ask, FORMAT.md §7.2): direction:rtl moves
     the ellipsis to the START of the string so the TAIL (usually the
     filename) stays visible when the path is too long for the bar; the
     text's own character order stays left-to-right via unicode-bidi. */
  direction: rtl;
  text-align: left;
  unicode-bidi: plaintext;
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.llnb-filepanel-note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
}
.llnb-filepanel-note:empty { display: none; }
.llnb-filepanel-actions { flex: 0 0 auto; display: flex; gap: 4px; }
.llnb-panes {
  display: flex;
  flex-direction: row;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.llnb-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.llnb-pane-left { flex: 0 0 40%; }
.llnb-pane-right { flex: 1 1 60%; }
.llnb-splitter {
  flex: 0 0 5px;
  cursor: col-resize;
  background: var(--border-color, #444);
  opacity: 0.6;
}
.llnb-splitter:hover { opacity: 1; }
.llnb-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 3px;
}
.llnb-category {
  padding: 4px 6px 2px;
  margin-top: 4px;
  font-size: 9.5px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--descrip-text, #999);
  user-select: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  /* Categories in the UI (FORMAT.md §7.2 amendment): headers are clickable
     (selectCategory()) to enter "category mode" — same affordance language
     as an entry row below. */
  cursor: pointer;
  border-radius: 3px;
  outline: none;
}
.llnb-category:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-category:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-category-active,
.llnb-category-active:hover {
  background: rgba(66, 133, 244, 0.22);
  color: var(--input-text, #ccc);
}
.llnb-entry {
  padding: 3px 7px;
  margin: 1px 0;
  border-radius: 3px;
  border-left: 3px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  outline: none;
  user-select: none;
  touch-action: none;
}
.llnb-entry:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-entry:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-entry-selected,
.llnb-entry-selected:hover {
  background: rgba(66, 133, 244, 0.22);
  border-left-color: rgba(66, 133, 244, 0.9);
}
/* Active = most recently clicked among the selected rows (§7.2); it alone
   drives the editor, so it gets a visibly stronger treatment than a plain
   multi-selected row. Declared after .llnb-entry-selected so it wins on the
   properties they share (equal specificity, later rule wins). */
.llnb-entry-active,
.llnb-entry-active:hover {
  background: rgba(66, 133, 244, 0.38);
  border-left-color: rgba(66, 133, 244, 1);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(66, 133, 244, 0.55);
}
.llnb-entry-dragging { opacity: 0.4; }
.llnb-entry-renaming {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  margin: 1px 0;
}
.llnb-input-rename {
  flex: 1 1 auto;
  min-width: 0;
  padding: 2px 4px;
}
.llnb-drag-marker {
  height: 2px;
  margin: 3px 4px;
  border-radius: 1px;
  background: rgba(66, 133, 244, 0.9);
  pointer-events: none;
}
.llnb-empty {
  padding: 6px 7px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.llnb-footer {
  flex: 0 0 auto;
  display: flex;
  gap: 4px;
  padding: 4px;
  border-top: 1px solid var(--border-color, #444);
}
.llnb-btn {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 4px;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.llnb-btn:disabled { opacity: 0.45; cursor: default; }
.llnb-btn-danger { border-color: var(--error-text, #ff4444); color: var(--error-text, #ff4444); }
.llnb-btn-small { flex: 0 0 auto; padding: 2px 8px; }
.llnb-btn-save { flex: 0 0 auto; }
.llnb-input {
  flex: 1 1 auto;
  min-width: 0;
  box-sizing: border-box;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 5px;
  font-size: 11px;
}
.llnb-mode-hint {
  /* Categories in the UI (FORMAT.md §7.2 amendment): "entry selected ⇒
     entry body; category selected ⇒ category description; a visible mode
     hint says which" — updateModeHint(). Sits directly above the textarea
     so it reads as "what Save is about to write", not a status message. */
  flex: 0 0 auto;
  padding: 3px 6px;
  font-size: 10px;
  font-style: italic;
  color: var(--descrip-text, #999);
  border-bottom: 1px solid var(--border-color, #444);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-mode-hint:empty { display: none; }
.llnb-textarea {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  box-sizing: border-box;
  resize: none;
  background: var(--comfy-input-bg, #1e1e1e);
  color: var(--input-text, #ccc);
  border: none;
  border-bottom: 1px solid var(--border-color, #444);
  padding: 6px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  line-height: 1.4;
}
.llnb-textarea:disabled { opacity: 0.5; }
.llnb-textarea::placeholder { color: var(--descrip-text, #999); }
.llnb-bottom-row {
  /* One row: Save left, status right-justified (owner ask 2026-07-18 —
     the stacked layout wasted vertical space). Wraps only when cramped. */
  flex: 0 0 auto;
  display: flex;
  flex-direction: row;
  align-items: center;
  flex-wrap: wrap;
  gap: 3px 8px;
  padding: 4px 6px;
}
.llnb-status {
  flex: 1 1 auto;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}
.llnb-status-text {
  color: var(--descrip-text, #999);
  font-size: 10px;
  text-align: right;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-status-text:empty { display: none; }
.llnb-status-actions { display: flex; flex: 0 0 auto; gap: 4px; }
.llnb-status-actions:empty { display: none; }
.llnb-status-hint {
  color: var(--descrip-text, #999);
  font-size: 10px;
  font-style: italic;
  text-align: right;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-status-hint:empty { display: none; }
.llnb-picker-backdrop {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
}
.llnb-picker {
  display: flex;
  flex-direction: column;
  width: min(480px, 90vw);
  max-height: min(520px, 80vh);
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.llnb-picker-header {
  flex: 0 0 auto;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  direction: rtl;
  text-align: left;
  unicode-bidi: plaintext;
  color: var(--descrip-text, #999);
}
.llnb-picker-list {
  flex: 1 1 auto;
  min-height: 120px;
  overflow-y: auto;
  padding: 4px;
}
.llnb-picker-row {
  padding: 5px 8px;
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.llnb-picker-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-picker-status,
.llnb-picker-empty {
  padding: 10px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.llnb-picker-error { color: var(--error-text, #ff4444); font-style: normal; }
.llnb-picker-footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-color, #444);
}
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

// ---------------------------------------------------------------------------
// Tiny DOM builder — this pack is vanilla JS with no templating engine.
// ---------------------------------------------------------------------------

/**
 * @param {string} tag
 * @param {{className?: string, text?: string, attrs?: Record<string,string>}} [options]
 * @param {(Node|string)[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

// ---------------------------------------------------------------------------
// Node / widget lookups
// ---------------------------------------------------------------------------

/**
 * @param {object} node
 * @returns {boolean}
 */
function isNotebookNode(node) {
  if (!node) return false
  if (node.comfyClass === NODE_CLASS) return true
  if (node.constructor && node.constructor.comfyClass === NODE_CLASS) return true
  return false
}

/**
 * @param {object} node
 * @param {string} name
 */
function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Attach the two-pane editor to *node* when it is a LoraLibraryNotebook;
 * no-op for every other node type. Never throws — every failure is logged
 * via `api.warn` and leaves the node's plain `file`/`entry` widgets fully
 * functional on their own (FORMAT.md §7.2).
 * @param {object} node - LiteGraph node instance.
 */
export function attachNotebookWidget(node) {
  try {
    if (!isNotebookNode(node)) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      api.warn('this ComfyUI frontend has no addDOMWidget; notebook editor not attached')
      return
    }

    const fileWidget = findWidget(node, 'file')
    const entryWidget = findWidget(node, 'entry')
    if (!fileWidget || !entryWidget) {
      api.warn('LoraLibraryNotebook node is missing its file/entry widgets; notebook editor not attached')
      return
    }

    attachedNodes.add(node)

    const state = createState(node, fileWidget, entryWidget)
    buildUi(state)
    wireFileWidget(state)
    wireNodeCleanup(state)

    // FORMAT.md §7.2 amendment: one `/config` check per attach (cached at
    // module scope — see "Remote gating" below) to gate the file panel's
    // buttons and the `file` widget's edit-guard.
    refreshRemoteGating(state).catch((error) => api.warn('initial config load failed', error))
    reloadNow(state).catch((error) => api.warn('initial notebook load failed', error))
  } catch (error) {
    api.warn('attachNotebookWidget failed', error)
  }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

function createState(node, fileWidget, entryWidget) {
  return {
    node,
    fileWidget,
    entryWidget,
    file: null,
    exists: true,
    entries: [],
    // Categories in the UI (FORMAT.md §7.2 amendment) — the §5 `categories`
    // list (file order, may include empty/repeated names) and the name of
    // the category currently shown in the editor ("category mode"), or
    // null. Deliberately independent of `selection`/`activeName` below —
    // see the file header's "Categories" paragraph for why entering/exiting
    // category mode must never touch either.
    categories: [],
    activeCategory: null,
    // Selection model (§6.1/§7.2): `selection` is the ordered list of
    // selected entry names — exactly what gets newline-joined into the
    // `entry` widget. `activeName` is the most-recently-clicked selected
    // entry; it alone drives the editor/dirty/Save/Delete/conflict flow.
    // See the "Selection model" functions below.
    selection: [],
    activeName: null,
    baseMtime: null,
    lastSavedText: '',
    dirty: false,
    busy: false,
    loadToken: 0,
    selectToken: 0,
    creatingNew: false,
    deleteConfirmActive: false,
    deleteConfirmTimer: null,
    fileChangeDebounceTimer: null,
    // Flat, top-to-bottom list of {el, kind: 'header'|'entry', name?,
    // category} rebuilt every renderList() call — the drag hit-testing
    // geometry in "Drag-to-reorder" below walks this instead of re-querying
    // the DOM.
    dragRows: [],
    // In-flight pointer gesture (pointerdown → move → up), or null between
    // gestures. See onEntryPointerDown().
    drag: null,
    // FORMAT.md §7.2 amendment — name of the row currently showing the
    // inline rename input (buildRenameRow), or null. See the file header's
    // "Rename" paragraph.
    renamingName: null,
    // FORMAT.md §7.2 amendment — the file panel's resolved absolute path
    // (the `file` field of the last `GET /notebook` response) and whether
    // THIS browser is local (`GET /config`'s `is_local`; null = not yet
    // known, treated as local — see refreshRemoteGating()).
    resolvedFile: null,
    isLocal: null,
    // The file WIDGET's last known-good value — wireFileWidget() reverts to
    // this when a remote viewer edits a read-only `file` widget.
    lastKnownFileValue: null,
    // The Browse… picker's window-level Escape-key listener while open (the
    // picker lives on document.body, not inside this widget's own DOM — see
    // openBrowsePicker()).
    pickerKeydownHandler: null,
    // DOM refs, filled in by buildUi() — only elements later functions need
    // to reach back into are tracked here (e.g. `newBtn` isn't, since
    // nothing but renderFooter() itself ever touches it).
    root: null,
    leftPane: null,
    listEl: null,
    footerEl: null,
    modeHintEl: null,
    textarea: null,
    saveBtn: null,
    statusTextEl: null,
    statusActionsEl: null,
    statusHintEl: null,
    deleteBtn: null,
    filePanelPathEl: null,
    filePanelNoteEl: null,
    browseBtn: null,
    openFolderBtn: null
  }
}

// ---------------------------------------------------------------------------
// UI construction
// ---------------------------------------------------------------------------

function buildUi(state) {
  injectStyles()

  state.listEl = el('div', { className: 'llnb-list' })
  state.footerEl = el('div', { className: 'llnb-footer' })
  state.leftPane = el('div', { className: 'llnb-pane llnb-pane-left' }, [state.listEl, state.footerEl])

  const splitter = el('div', { className: 'llnb-splitter', attrs: { title: 'Drag to resize' } })

  // Categories in the UI (FORMAT.md §7.2 amendment): a muted line saying
  // which of the two contexts (entry body vs. category description) the
  // textarea/Save below currently target — see updateModeHint().
  state.modeHintEl = el('div', { className: 'llnb-mode-hint' })
  state.textarea = el('textarea', {
    className: 'llnb-textarea',
    attrs: {
      placeholder: 'Select an entry or category on the left, or click ＋ New to create one.',
      spellcheck: 'false'
    }
  })
  state.saveBtn = el('button', { className: 'llnb-btn llnb-btn-save', text: 'Save' })
  state.statusTextEl = el('div', { className: 'llnb-status-text' })
  state.statusActionsEl = el('div', { className: 'llnb-status-actions' })
  state.statusHintEl = el('div', { className: 'llnb-status-hint' })
  const statusRow = el('div', { className: 'llnb-status' }, [
    state.statusTextEl,
    state.statusActionsEl,
    state.statusHintEl
  ])
  const bottomRow = el('div', { className: 'llnb-bottom-row' }, [state.saveBtn, statusRow])
  const rightPane = el('div', { className: 'llnb-pane llnb-pane-right' }, [
    state.modeHintEl,
    state.textarea,
    bottomRow
  ])

  const panesRow = el('div', { className: 'llnb-panes' }, [state.leftPane, splitter, rightPane])
  const filePanel = buildFilePanel(state)
  state.root = el('div', { className: 'llnb-root' }, [filePanel, panesRow])

  state.textarea.addEventListener('input', () => {
    setDirty(state, state.textarea.value !== state.lastSavedText)
  })
  state.textarea.addEventListener('keydown', (event) => event.stopPropagation())
  state.saveBtn.addEventListener('click', () => {
    performSave(state).catch((error) => api.warn('save failed', error))
  })

  wireSplitter(state, splitter)

  renderFooter(state)
  clearEditor(state)

  attachDomWidget(state.node, state.root)
}

/**
 * Wraps `node.addDOMWidget` — kept as its own function so the three
 * non-serialization flags (see file header) sit next to the call that needs
 * them, instead of being scattered across `buildUi`.
 */
function attachDomWidget(node, rootEl) {
  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_TYPE, rootEl, {
    // Same default litegraph itself applies (`scripts/domWidget.ts`); kept
    // explicit for readability.
    hideOnZoom: true,
    // Excludes the widget from the API prompt (utils/executionUtil.ts).
    serialize: false,
    getMinHeight: () => MIN_WIDGET_HEIGHT
  })
  // Excludes the widget from the workflow JSON (LGraphNode.serialize /
  // .configure check `widget.serialize`, a *different* flag from
  // `options.serialize` above — see file header).
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

function wireSplitter(state, splitter) {
  let dragging = false
  let startX = 0
  let startWidth = 0

  const onPointerMove = (event) => {
    if (!dragging) return
    const rootRect = state.root.getBoundingClientRect()
    const minLeft = 80
    const maxLeft = Math.max(minLeft, rootRect.width - 160)
    const next = Math.min(maxLeft, Math.max(minLeft, startWidth + (event.clientX - startX)))
    state.leftPane.style.flex = `0 0 ${next}px`
  }
  const stopDragging = (event) => {
    if (!dragging) return
    dragging = false
    try {
      splitter.releasePointerCapture(event.pointerId)
    } catch {
      // Not captured, or already released — nothing to do.
    }
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', stopDragging)
  }
  splitter.addEventListener('pointerdown', (event) => {
    dragging = true
    startX = event.clientX
    startWidth = state.leftPane.getBoundingClientRect().width
    try {
      splitter.setPointerCapture(event.pointerId)
    } catch {
      // Best-effort; the window-level listeners below still cover dragging.
    }
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', stopDragging)
    event.preventDefault()
  })
}

// ---------------------------------------------------------------------------
// file widget chaining + node cleanup
// ---------------------------------------------------------------------------

/**
 * Wraps the `file` widget's callback for two independent reasons that share
 * one seam: (1) the pre-existing debounced-reload-on-change
 * (onFileWidgetChanged), and (2) FORMAT.md §7.2's remote read-only guard —
 * a remote (`is_local: false`) viewer's edit is reverted here instead of
 * via `widget.disabled` (see the file header's "File panel + remote
 * gating" paragraph for why that flag is unusable for this).
 */
function wireFileWidget(state) {
  const widget = state.fileWidget
  const original = widget.callback
  state.lastKnownFileValue = widget.value
  widget.callback = function (value, ...rest) {
    if (state.isLocal === false && value !== state.lastKnownFileValue) {
      widget.value = state.lastKnownFileValue
      setStatus(state, 'The host machine controls which file this node reads.')
      state.node.graph?.setDirtyCanvas(true, true)
      return undefined
    }
    state.lastKnownFileValue = value

    let result
    if (typeof original === 'function') {
      try {
        result = original.apply(this, [value, ...rest])
      } catch (error) {
        api.warn('original file widget callback threw', error)
      }
    }
    try {
      onFileWidgetChanged(state)
    } catch (error) {
      api.warn('notebook file-change handler threw', error)
    }
    return result
  }
}

function onFileWidgetChanged(state) {
  if (state.fileChangeDebounceTimer) clearTimeout(state.fileChangeDebounceTimer)
  state.fileChangeDebounceTimer = setTimeout(() => {
    state.fileChangeDebounceTimer = null
    if (state.fileWidget.value === state.file) return
    reloadNow(state).catch((error) => api.warn('notebook reload after file change failed', error))
  }, FILE_CHANGE_DEBOUNCE_MS)
}

function wireNodeCleanup(state) {
  const node = state.node
  const originalOnRemoved = node.onRemoved
  node.onRemoved = function (...args) {
    let result
    if (typeof originalOnRemoved === 'function') {
      try {
        result = originalOnRemoved.apply(this, args)
      } catch (error) {
        api.warn('original node onRemoved threw', error)
      }
    }
    try {
      teardown(state)
    } catch (error) {
      api.warn('notebook teardown failed', error)
    }
    return result
  }
}

function teardown(state) {
  if (state.deleteConfirmTimer) clearTimeout(state.deleteConfirmTimer)
  if (state.fileChangeDebounceTimer) clearTimeout(state.fileChangeDebounceTimer)
  // A node removal mid-drag (e.g. undo, right-click delete) would otherwise
  // leak the drag's window-level pointermove/pointerup/pointercancel
  // listeners forever — see onEntryPointerDown().
  state.drag?.cleanup?.()
  // The Browse… picker lives on document.body, not inside this node's own
  // DOM — it must be torn down explicitly, or a node removed mid-picker
  // would leak it (and its window-level keydown listener) forever.
  closeBrowsePicker(state)
  // Invalidate any in-flight fetches so their `.then` handlers no-op.
  state.loadToken += 1
  state.selectToken += 1
}

// ---------------------------------------------------------------------------
// Remote gating (FORMAT.md §7.2 amendment) — see the file header's "File
// panel + remote gating" paragraph. `GET /lora_library/config` is cached at
// MODULE scope (every attached LoraLibraryNotebook node shares one fetch)
// with a short TTL, and concurrent callers de-dupe onto one in-flight
// promise.
// ---------------------------------------------------------------------------

const CONFIG_CACHE_TTL_MS = 60000

let cachedConfig = null
let cachedConfigAt = 0
let cachedConfigPromise = null

function fetchConfig() {
  if (cachedConfigPromise) return cachedConfigPromise
  cachedConfigPromise = api
    .getJson('/lora_library/config')
    .then((data) => {
      cachedConfig = data
      cachedConfigAt = Date.now()
      return data
    })
    .finally(() => {
      cachedConfigPromise = null
    })
  return cachedConfigPromise
}

function getConfig() {
  if (cachedConfig && Date.now() - cachedConfigAt < CONFIG_CACHE_TTL_MS) {
    return Promise.resolve(cachedConfig)
  }
  return fetchConfig()
}

/**
 * Refreshes `state.isLocal` from (cached) `/config` and applies it to the
 * file-panel buttons + the `file` widget's edit-guard (wireFileWidget).
 * Never throws: a failed fetch is logged and leaves `state.isLocal`
 * whatever it already was (`null`/unknown reads as "local" everywhere this
 * is checked with `=== false`) — this fails OPEN rather than disabling
 * functionality over a network hiccup, this file's usual posture.
 */
async function refreshRemoteGating(state) {
  let config
  try {
    config = await getConfig()
  } catch (error) {
    api.warn('could not load /lora_library/config; treating this node as local', error)
    return
  }
  state.isLocal = config?.is_local !== false
  updateRemoteGatingUi(state)
}

function updateRemoteGatingUi(state) {
  const remote = state.isLocal === false
  if (state.browseBtn) state.browseBtn.style.display = remote ? 'none' : ''
  if (state.openFolderBtn) state.openFolderBtn.style.display = remote ? 'none' : ''
  if (state.filePanelNoteEl) {
    state.filePanelNoteEl.textContent = remote ? 'Host machine controls this file' : ''
    state.filePanelNoteEl.title = remote
      ? 'The host machine controls which file this node reads.'
      : ''
  }
}

// ---------------------------------------------------------------------------
// File panel: resolved path + Browse…/Open folder (FORMAT.md §7.2 amendment)
// ---------------------------------------------------------------------------

function buildFilePanel(state) {
  state.filePanelPathEl = el('div', { className: 'llnb-filepanel-path' })
  state.filePanelNoteEl = el('div', { className: 'llnb-filepanel-note' })
  state.browseBtn = el('button', {
    className: 'llnb-btn llnb-btn-small',
    text: 'Browse…',
    attrs: { title: 'Pick a notebook .md file on the server' }
  })
  state.openFolderBtn = el('button', {
    className: 'llnb-btn llnb-btn-small',
    text: 'Open folder',
    attrs: { title: "Reveal this file's folder on the server machine" }
  })

  state.browseBtn.addEventListener('click', () => {
    if (state.busy) return
    openBrowsePicker(state)
  })
  state.openFolderBtn.addEventListener('click', () => {
    onOpenFolderClick(state).catch((error) => api.warn('open folder failed', error))
  })

  const actions = el('div', { className: 'llnb-filepanel-actions' }, [state.browseBtn, state.openFolderBtn])
  return el('div', { className: 'llnb-filepanel' }, [state.filePanelPathEl, state.filePanelNoteEl, actions])
}

function updateFilePanelPath(state) {
  if (!state.filePanelPathEl) return
  const path = state.resolvedFile || ''
  state.filePanelPathEl.textContent = path
  state.filePanelPathEl.title = path
}

async function onOpenFolderClick(state) {
  if (!state.resolvedFile) return
  try {
    await api.postJson('/lora_library/notebook/open_folder', { file: state.resolvedFile })
  } catch (error) {
    api.warn('open folder failed', error)
    setStatus(state, `Could not open folder: ${error.message}`)
  }
}

/** Best-effort join of a `GET /fs/list` `dir` + child name using the SAME
 * separator style the server's `dir` string already uses — the server may
 * run on Windows (backslash paths, incl. UNC `\\server\share`) or POSIX
 * (forward slash), and the picker has no other way to know which. */
function joinServerPath(dir, name) {
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/'
  return dir.endsWith(sep) ? `${dir}${name}` : `${dir}${sep}${name}`
}

/** Best-effort parent-folder guess for seeding the picker at the resolved
 * file's own folder; null (→ server default, the library folder) if it
 * can't tell. */
function dirnameOfServerPath(path) {
  if (!path) return null
  const sep = path.includes('\\') && !path.includes('/') ? '\\' : '/'
  const idx = path.lastIndexOf(sep)
  if (idx <= 0) return null
  return path.slice(0, idx)
}

const PICKER_OVERLAY_ID = 'llnb-picker-overlay'

function closeBrowsePicker(state) {
  document.getElementById(PICKER_OVERLAY_ID)?.remove()
  if (state.pickerKeydownHandler) {
    window.removeEventListener('keydown', state.pickerKeydownHandler)
    state.pickerKeydownHandler = null
  }
}

/**
 * FORMAT.md §7.2's Browse… dialog. Deliberately attached to `document.body`
 * rather than nested inside this widget's own root: the DOM widget's box is
 * only ever as tall as the node currently is (as small as
 * MIN_WIDGET_HEIGHT), and litegraph can reposition/clip it during pan/zoom
 * (see `hideOnZoom` on attachDomWidget() above) — a file browser confined to
 * that box would be cramped on a small node and would fight the same
 * clipping. A fixed, centered overlay on `document.body` stays a
 * comfortable, constant size regardless of the node's size/position, at the
 * cost of managing its own teardown by hand (closeBrowsePicker(), called
 * from here, Escape, a backdrop click, and this node's own teardown()).
 */
function openBrowsePicker(state) {
  closeBrowsePicker(state) // only one picker at a time, ever

  const backdrop = el('div', { className: 'llnb-picker-backdrop', attrs: { id: PICKER_OVERLAY_ID } })
  const dialog = el('div', { className: 'llnb-picker' })
  backdrop.append(dialog)
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) closeBrowsePicker(state)
  })
  dialog.addEventListener('mousedown', (event) => event.stopPropagation())
  document.body.append(backdrop)

  state.pickerKeydownHandler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeBrowsePicker(state)
    }
  }
  window.addEventListener('keydown', state.pickerKeydownHandler)

  loadPickerDir(state, dialog, dirnameOfServerPath(state.resolvedFile))
}

async function loadPickerDir(state, dialog, dir) {
  dialog.replaceChildren(el('div', { className: 'llnb-picker-status', text: 'Loading…' }))
  let data
  try {
    data = await api.getJson('/lora_library/fs/list', dir ? { dir } : undefined)
  } catch (error) {
    api.warn('fs/list failed', error)
    dialog.replaceChildren(
      el('div', { className: 'llnb-picker-header', text: 'Browse' }),
      el('div', {
        className: 'llnb-picker-status llnb-picker-error',
        text: `Could not list folder: ${error.message}`
      }),
      buildPickerFooter(state)
    )
    return
  }
  renderPickerDialog(state, dialog, data)
}

function renderPickerDialog(state, dialog, data) {
  const header = el('div', { className: 'llnb-picker-header', text: data.dir, attrs: { title: data.dir } })
  const list = el('div', { className: 'llnb-picker-list' })

  if (data.parent) {
    const upRow = el('div', { className: 'llnb-picker-row', text: '.. (parent folder)' })
    upRow.addEventListener('click', () => loadPickerDir(state, dialog, data.parent))
    list.append(upRow)
  }
  for (const name of data.dirs || []) {
    const row = el('div', { className: 'llnb-picker-row', text: `📁 ${name}` })
    row.addEventListener('click', () => loadPickerDir(state, dialog, joinServerPath(data.dir, name)))
    list.append(row)
  }
  for (const name of data.files || []) {
    const row = el('div', { className: 'llnb-picker-row', text: `📄 ${name}` })
    row.addEventListener('click', () => {
      closeBrowsePicker(state)
      setFileWidgetValue(state, joinServerPath(data.dir, name))
    })
    list.append(row)
  }
  if (!data.parent && !(data.dirs || []).length && !(data.files || []).length) {
    list.append(el('div', { className: 'llnb-picker-empty', text: 'No subfolders or .md files here.' }))
  }

  dialog.replaceChildren(header, list, buildPickerFooter(state))
}

function buildPickerFooter(state) {
  const cancelBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Cancel' })
  cancelBtn.addEventListener('click', () => closeBrowsePicker(state))
  return el('div', { className: 'llnb-picker-footer' }, [cancelBtn])
}

/** Writes `value` through the `file` widget's real setter+callback — the
 * exact same pattern syncEntryWidget() uses for `entry` — so picking a file
 * here behaves exactly like typing it in, including the debounced reload
 * (onFileWidgetChanged, via wireFileWidget) and that same wrapper's §7.2
 * read-only guard (moot in practice, since Browse… is itself hidden for a
 * remote caller — belt-and-suspenders all the same). */
function setFileWidgetValue(state, value) {
  const widget = state.fileWidget
  if (widget.value === value) return
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    api.warn('file widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Loading the notebook list + auto-select
// ---------------------------------------------------------------------------

async function reloadNow(state) {
  const token = ++state.loadToken
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  closeRenameRow(state)
  clearConflict(state)

  // FORMAT.md §7.2 remote gating: opportunistic re-check on every reload, on
  // top of the initial one at attach — cheap, since getConfig() is
  // module-level cached/de-duped (see "Remote gating" above).
  refreshRemoteGating(state).catch((error) => api.warn('config refresh failed', error))

  const file = state.fileWidget.value ?? ''
  let data
  try {
    data = await api.getJson('/lora_library/notebook', { file })
  } catch (error) {
    if (token !== state.loadToken) return
    api.warn('failed to load notebook list', error)
    setStatus(state, `Could not load notebook: ${error.message}`)
    return
  }
  if (token !== state.loadToken) return

  state.file = file
  state.entries = Array.isArray(data.entries) ? data.entries : []
  // FORMAT.md §5/§7.2: named categories in file order, incl. empty ones —
  // see renderList()'s merge of this against `entries`.
  state.categories = Array.isArray(data.categories) ? data.categories : []
  state.exists = data.exists !== false
  // FORMAT.md §7.2 file panel: the RESOLVED absolute path, distinct from
  // the (possibly relative) `file` WIDGET value above.
  state.resolvedFile = typeof data.file === 'string' ? data.file : null
  updateFilePanelPath(state)
  setStatus(state, baselineStatus(state, data.problems))

  // Restore the selection from the entry widget's (possibly multi-line)
  // value (§6.1: one name per line, selection order; §7.2: "missing names
  // silently drop out of the selection, first surviving = active"). This
  // only updates in-memory rendering state — it deliberately does NOT
  // rewrite entryWidget.value, mirroring this file's original single-select
  // behavior (the old clearEditor() never touched the widget on a reload
  // mismatch; only an explicit user action like delete did, via its own
  // widget write). A name merely absent from THIS load stays in the
  // serialized value untouched, so a transient race can't silently truncate
  // a workflow's stored selection — the next real selection change (which
  // only ever adds names backed by a rendered row) is what actually drops
  // it from serialization.
  const survivors = restoreSelectionFromWidget(state)
  state.selection = survivors
  state.activeName = survivors.length ? survivors[0] : null
  // Category mode survives a reload the same way entry selection does
  // (above): kept only if the category is still there, dropped silently
  // otherwise (FORMAT.md §7.2 amendment) — independent of the entry
  // selection restore, per the file header's "never touches `selection`"
  // rule for category mode.
  if (state.activeCategory != null && !state.categories.includes(state.activeCategory)) {
    state.activeCategory = null
  }
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)

  if (state.activeCategory != null) {
    const result = await loadCategoryDescription(state, state.activeCategory)
    if (result === 'failed') resetEditorDom(state)
  } else if (state.activeName) {
    const result = await loadEntryText(state, state.activeName)
    if (result === 'failed') resetEditorDom(state)
  } else {
    resetEditorDom(state)
  }
}

/**
 * Parses the entry widget's raw value into candidate selected names: split
 * on any line ending, trim, drop blanks. A single bare name (no newline) is
 * the pre-multiselect degenerate case (§6.1) and parses to a one-element
 * array, so old workflows restore unchanged.
 * @param {string} rawValue
 * @returns {string[]}
 */
function parseSelectionValue(rawValue) {
  if (!rawValue) return []
  return String(rawValue)
    .split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

/**
 * @returns {string[]} the entry widget's requested names, deduped, filtered
 * to those that exist in `state.entries` right now, in their original order.
 */
function restoreSelectionFromWidget(state) {
  const requested = parseSelectionValue(state.entryWidget.value)
  const seen = new Set()
  const survivors = []
  for (const name of requested) {
    if (seen.has(name)) continue
    if (!state.entries.some((entry) => entry.name === name)) continue
    seen.add(name)
    survivors.push(name)
  }
  return survivors
}

function baselineStatus(state, problems) {
  const parts = []
  if (!state.exists) parts.push('File does not exist yet — it will be created on first save.')
  const list = Array.isArray(problems) ? problems : []
  if (list.length) {
    parts.push(`${list.length} problem${list.length === 1 ? '' : 's'}: ${list.join(' · ')}`)
  }
  return parts.join(' ')
}

// ---------------------------------------------------------------------------
// Selection model (FORMAT.md §6.1/§7.2)
//
// `state.selection` is the ordered list of selected entry names — exactly
// what gets newline-joined into the `entry` widget. `state.activeName` is
// the most-recently-clicked selected entry; it alone drives the editor pane,
// dirty tracking, Save, Delete, and conflict handling — the single-select
// behavior this file always had, just decoupled from "what's highlighted."
//
// Two layers:
//  - setSelection() is a dumb setter: replace selection+active, sync the
//    widget, re-render. It never touches the editor pane.
//  - chooseSelection() is the interactive entry point (click/ctrl+click/
//    shift+click): applies immediately for a responsive list, then loads
//    the new active entry's text; a failed load rolls the WHOLE selection
//    back to what it was before the click — the click "didn't happen" —
//    matching this file's original single-select failure behavior.
// Callers that already know the target is valid and don't want rollback
// semantics (delete's post-delete reassignment, move, reload-restore) call
// setSelection() directly and load the active entry themselves.
// ---------------------------------------------------------------------------

function isSelected(state, name) {
  return state.selection.includes(name)
}

function lastOrNull(items) {
  return items.length ? items[items.length - 1] : null
}

/** Resets the editor DOM/dirty state only — no rendering, no widget sync.
 * Shared by every path that ends up with no (or no-longer-loadable) active
 * entry. */
function resetEditorDom(state) {
  state.textarea.value = ''
  state.lastSavedText = ''
  state.baseMtime = null
  state.textarea.disabled = true
  setDirty(state, false)
}

/** Initial, pre-load editor state (buildUi() only — nothing has been
 * fetched yet, so there is nothing to preserve in the entry widget). */
function clearEditor(state) {
  state.selection = []
  state.activeName = null
  state.activeCategory = null
  resetEditorDom(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
  updateModeHint(state)
}

/**
 * Writes the full multi-select `entry` STRING widget value (§6.1: one name
 * per line, in selection order) through the widget's real setter + callback
 * ("Selection writes the entry STRING widget so serialization needs no
 * custom code") and nudges the canvas to redraw so the change is visible
 * immediately. Mirrors the pattern ComfyUI's own `scripts/widgets.ts`
 * (`applyWidgetControl`) uses to drive one widget's value from other logic:
 * `targetWidget.value = next; targetWidget.callback?.(next)`.
 */
function syncEntryWidget(state) {
  const widget = state.entryWidget
  const next = state.selection.join('\n')
  if (widget.value === next) return
  widget.value = next
  try {
    widget.callback?.(next)
  } catch (error) {
    api.warn('entry widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

/** Dumb setter: replace the selection + active entry, sync the `entry`
 * widget, and re-render. Does not touch the editor pane — callers that
 * change the ACTIVE entry are responsible for loading (or clearing) it. */
function setSelection(state, names, active) {
  state.selection = names
  state.activeName = active
  syncEntryWidget(state)
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateSelectionHint(state)
}

function fetchEntry(state, name) {
  return api.getJson('/lora_library/notebook/entry', { file: state.file, name })
}

function fetchCategory(state, name) {
  return api.getJson('/lora_library/notebook/category', { file: state.file, name })
}

/** Shared by entry mode and category mode (FORMAT.md §7.2 amendment): both
 * are "one editable text blob + an mtime for the §3.5 conflict check", so
 * one function populates the shared textarea/dirty/baseMtime state for
 * either — callers just pass the right pair. */
function populateEditor(state, text, mtime) {
  state.textarea.value = text ?? ''
  state.lastSavedText = state.textarea.value
  state.baseMtime = typeof mtime === 'number' ? mtime : null
  state.textarea.disabled = false
  setDirty(state, false)
  updateDeleteButtonEnabled(state)
  clearConflict(state)
}

/**
 * Fetches `name`'s text and populates the editor. Token-guarded against
 * races with a later reload/select/teardown. Returns `'ok'`, `'failed'`
 * (fetch/parse error — already reported via setStatus), or `'stale'` (a
 * newer load/select superseded this one before it resolved — caller should
 * no-op either way, distinguished from `'failed'` only so a caller COULD
 * tell them apart if it ever needed to).
 * @returns {Promise<'ok'|'failed'|'stale'>}
 */
async function loadEntryText(state, name) {
  const loadToken = state.loadToken
  const selectToken = ++state.selectToken
  let data
  try {
    data = await fetchEntry(state, name)
  } catch (error) {
    if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
    api.warn('failed to load notebook entry', error)
    setStatus(state, `Could not load "${name}": ${error.message}`)
    return 'failed'
  }
  if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
  populateEditor(state, data.text, data.mtime)
  return 'ok'
}

/** Category-mode sibling of loadEntryText() above — same token-guard/return
 * contract, fetching the §5 category route instead (FORMAT.md §7.2
 * amendment). */
async function loadCategoryDescription(state, name) {
  const loadToken = state.loadToken
  const selectToken = ++state.selectToken
  let data
  try {
    data = await fetchCategory(state, name)
  } catch (error) {
    if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
    api.warn('failed to load category description', error)
    setStatus(state, `Could not load category "${name}": ${error.message}`)
    return 'failed'
  }
  if (loadToken !== state.loadToken || selectToken !== state.selectToken) return 'stale'
  populateEditor(state, data.description, data.mtime)
  return 'ok'
}

/**
 * The interactive entry point: apply a new selection immediately, then load
 * the new active entry (only when the active identity actually changed —
 * clicking around a multi-selection must never clobber unsaved edits in the
 * entry that's already open). A failed load rolls back to the selection
 * that was in effect before this call.
 *
 * Also the ONE place that exits category mode on behalf of an entry click
 * (FORMAT.md §7.2 amendment, file header's "Categories" paragraph): clearing
 * `activeCategory` here — never touching `selection`/`activeName` to do it —
 * is what makes "clicking any entry exits category mode" true regardless of
 * which of selectSingle/toggleEntry/selectRange dispatched here. Because
 * category mode is independent of `activeName`, exiting it can require a
 * reload even when `active === previousActive` (the entry that was already
 * "active" underneath category mode) — `wasInCategoryMode` covers exactly
 * that one case.
 * @param {string[]} names
 * @param {string|null} active
 */
async function chooseSelection(state, names, active) {
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  closeRenameRow(state)

  const wasInCategoryMode = state.activeCategory != null
  state.activeCategory = null

  const previousSelection = state.selection
  const previousActive = state.activeName
  setSelection(state, names, active)
  updateModeHint(state)
  if (active === previousActive && !wasInCategoryMode) return

  if (active == null) {
    resetEditorDom(state)
    updateDeleteButtonEnabled(state)
    return
  }

  const result = await loadEntryText(state, active)
  if (result === 'failed') {
    setSelection(state, previousSelection, previousActive)
  }
}

/** Plain click: collapse to a single selection. */
function selectSingle(state, name) {
  chooseSelection(state, [name], name).catch((error) => api.warn('select entry failed', error))
}

/** ctrl/cmd+click: toggle membership. Toggling the active entry off hands
 * "active" to the last-remaining selected entry (or clears it). Toggling
 * any entry on makes it active — it's the one just clicked. */
function toggleEntry(state, name) {
  if (isSelected(state, name)) {
    const nextSelection = state.selection.filter((n) => n !== name)
    const nextActive = state.activeName === name ? lastOrNull(nextSelection) : state.activeName
    chooseSelection(state, nextSelection, nextActive).catch((error) => api.warn('select entry failed', error))
  } else {
    chooseSelection(state, [...state.selection, name], name).catch((error) => api.warn('select entry failed', error))
  }
}

/** shift+click: replace the selection with the visible range between the
 * current active entry (the anchor) and the clicked one, inclusive, in
 * top-to-bottom list order — order = selection order (§6.1), so a shift-
 * range runs prompts top-to-bottom. No anchor yet (nothing active) falls
 * back to a plain single-select. */
function selectRange(state, name) {
  const anchorName = state.activeName
  if (!anchorName) {
    selectSingle(state, name)
    return
  }
  const anchorIndex = state.entries.findIndex((entry) => entry.name === anchorName)
  const clickIndex = state.entries.findIndex((entry) => entry.name === name)
  if (anchorIndex === -1 || clickIndex === -1) {
    selectSingle(state, name)
    return
  }
  const lo = Math.min(anchorIndex, clickIndex)
  const hi = Math.max(anchorIndex, clickIndex)
  const names = state.entries.slice(lo, hi + 1).map((entry) => entry.name)
  chooseSelection(state, names, name).catch((error) => api.warn('select entry failed', error))
}

/** Dispatches a resolved (non-drag) pointer gesture to the right selection
 * mode, mirroring standard list-box modifier conventions. */
function handleEntryClick(state, name, modifiers) {
  if (modifiers.shiftKey) selectRange(state, name)
  else if (modifiers.toggleKey) toggleEntry(state, name)
  else selectSingle(state, name)
}

/** Muted status-area hint (owner ask): visible only for 2+ selected, since
 * that's when OUTPUT_IS_LIST fan-out (§6.1) actually changes queue behavior.
 * Lives in its own element (not statusTextEl) so Saving…/Deleted…/conflict
 * messages never clobber it and vice versa. */
function updateSelectionHint(state) {
  if (!state.statusHintEl) return
  const count = state.selection.length
  state.statusHintEl.textContent =
    count >= 2 ? `${count} prompts selected — queue runs once per prompt.` : ''
}

// ---------------------------------------------------------------------------
// Categories in the UI (FORMAT.md §7.2 amendment) — see the file header's
// "Categories" paragraph for the overall design. This is the category-mode
// counterpart of the "Selection model" section above: selectCategory() is
// its chooseSelection() — the interactive "click a header" entry point
// (confirmNewCategory() also sets `state.activeCategory` directly, for the
// "just created it" case, same relationship confirmNewEntry() has to
// setSelection()). Neither ever calls setSelection()/syncEntryWidget(),
// which is what keeps category mode from touching the entry selection or
// the `entry` widget.
// ---------------------------------------------------------------------------

/**
 * Clicking a category header: enters "category mode" for *name*, loading its
 * §3.1 description into the shared editor pane. Deliberately mirrors
 * chooseSelection()'s shape (immediate UI update, then an async load, then a
 * rollback-on-failure so a failed click reads as "didn't happen") but never
 * touches `state.selection`/`state.activeName`/the `entry` widget — see the
 * file header. Re-clicking the already-active category is a no-op (nothing
 * changed, so nothing to reload).
 */
async function selectCategory(state, name) {
  if (state.busy) return
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  closeRenameRow(state)
  if (state.activeCategory === name) return

  const previousCategory = state.activeCategory
  state.activeCategory = name
  renderList(state)
  updateDeleteButtonEnabled(state)
  updateModeHint(state)

  const result = await loadCategoryDescription(state, name)
  if (result === 'failed') {
    // Roll back exactly like chooseSelection() does on a failed entry load —
    // the editor's own content was never touched by the failed fetch, so
    // restoring just the pointer is enough to undo the click.
    state.activeCategory = previousCategory
    renderList(state)
    updateDeleteButtonEnabled(state)
    updateModeHint(state)
  }
}

/** FORMAT.md §7.2 amendment: "the editor is contextual … a visible mode
 * hint says which" — updates `state.modeHintEl`, directly above the
 * textarea. Category mode wins when both are technically set (selection
 * survives entering category mode — see the file header), since it's what
 * the editor is actually showing. */
function updateModeHint(state) {
  if (!state.modeHintEl) return
  if (state.activeCategory != null) {
    state.modeHintEl.textContent = `Editing category description: ${state.activeCategory}`
  } else if (state.activeName) {
    state.modeHintEl.textContent = `Editing entry: ${state.activeName}`
  } else {
    state.modeHintEl.textContent = ''
  }
}

// ---------------------------------------------------------------------------
// Entry list rendering
// ---------------------------------------------------------------------------

/**
 * Renders headers from `state.categories` (FORMAT.md §5's file-order list,
 * NOT derived from `entries`) merged with `state.entries` by a single
 * forward walk over both — both arrays are already in file order from the
 * same parse, so this is a two-pointer merge, not a group-by-name: entries
 * are appended while they keep matching the CURRENT category, and a header
 * with nothing following it (an empty category) still renders. This is what
 * lets an empty category show at all, and keeps a hand-edited file's
 * repeated category name as two separate headers rather than one merged
 * group (see list_categories()'s doc comment on the Python side).
 */
function renderList(state) {
  state.listEl.replaceChildren()
  state.dragRows = []

  const categories = Array.isArray(state.categories) ? state.categories : []
  const entries = state.entries

  if (!entries.length && !categories.length) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: state.exists ? 'No entries yet.' : 'File not found yet.'
      })
    )
    return
  }

  let entryIndex = 0
  const appendEntry = (entry) => {
    const row =
      state.renamingName === entry.name ? buildRenameRow(state, entry) : buildEntryRow(state, entry)
    state.listEl.append(row)
    state.dragRows.push({ el: row, kind: 'entry', name: entry.name, category: entry.category || '' })
    entryIndex += 1
  }

  // The leading, un-headed "" region (FORMAT.md §3.1: entries before the
  // first H1) never gets a category row of its own.
  while (entryIndex < entries.length && (entries[entryIndex].category || '') === '') {
    appendEntry(entries[entryIndex])
  }

  for (const category of categories) {
    const headerEl = buildCategoryHeaderRow(state, category)
    state.listEl.append(headerEl)
    state.dragRows.push({ el: headerEl, kind: 'header', category })

    while (entryIndex < entries.length && (entries[entryIndex].category || '') === category) {
      appendEntry(entries[entryIndex])
    }
  }

  // Defensive fallback: an entry reporting a category `categories` didn't
  // list (shouldn't happen — both come from the same parse) still renders,
  // just without a header of its own, rather than silently vanishing.
  while (entryIndex < entries.length) {
    appendEntry(entries[entryIndex])
  }
}

/** The normal (non-renaming) row: click/ctrl/shift-click selection,
 * pointer-drag reorder (onEntryPointerDown below), double-click to rename
 * (onEntryDoubleClick, in the "Rename" section further down). */
function buildEntryRow(state, entry) {
  const selected = isSelected(state, entry.name)
  const active = entry.name === state.activeName
  const classes = ['llnb-entry']
  if (selected) classes.push('llnb-entry-selected')
  if (active) classes.push('llnb-entry-active')

  const row = el('div', {
    className: classes.join(' '),
    text: entry.name,
    attrs: { tabindex: '0', title: entry.name }
  })
  row.addEventListener('pointerdown', (event) => onEntryPointerDown(state, event, entry.name))
  row.addEventListener('dblclick', (event) => onEntryDoubleClick(state, event, entry.name))
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleEntryClick(state, entry.name, { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey })
  })
  return row
}

/**
 * A category header row (FORMAT.md §7.2 amendment): plain click/Enter/Space
 * enters category mode via selectCategory() — no drag-threshold
 * disambiguation needed here (unlike buildEntryRow() above), since headers
 * are never themselves a drag SOURCE, only a drop TARGET (computeDropTarget()
 * reads this row's geometry back out of `state.dragRows`, unaffected by
 * anything below).
 */
function buildCategoryHeaderRow(state, category) {
  const classes = ['llnb-category']
  if (state.activeCategory === category) classes.push('llnb-category-active')

  const headerEl = el('div', {
    className: classes.join(' '),
    text: category,
    attrs: { tabindex: '0', title: category }
  })
  headerEl.addEventListener('click', () => {
    selectCategory(state, category).catch((error) => api.warn('select category failed', error))
  })
  headerEl.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    selectCategory(state, category).catch((error) => api.warn('select category failed', error))
  })
  return headerEl
}

// ---------------------------------------------------------------------------
// Drag-to-reorder (FORMAT.md §3.4/§5/§7.2)
//
// Pointer-based (see the file header's pointer-events citation), mirroring
// this file's own pane-splitter drag: capture the pointer on the row that
// started the gesture, but listen on `window` so movement outside the row's
// (or even the list's) bounds still tracks. A single pointerdown starts a
// tentative gesture that resolves ONE of two ways on pointerup:
//  - moved < DRAG_THRESHOLD_PX the whole time → a click; dispatched to the
//    plain/ctrl/shift selection logic above.
//  - moved >= DRAG_THRESHOLD_PX at any point → a drag; commits to reorder
//    and the click never fires.
// ---------------------------------------------------------------------------

function onEntryPointerDown(state, event, name) {
  if (event.button !== 0) return // primary button/touch only
  cancelDeleteConfirm(state)

  const drag = {
    pointerId: event.pointerId,
    name,
    startX: event.clientX,
    startY: event.clientY,
    active: false,
    modifiers: { shiftKey: event.shiftKey, toggleKey: event.ctrlKey || event.metaKey },
    rowEl: event.currentTarget,
    marker: null,
    target: null
  }
  state.drag = drag

  const onMove = (moveEvent) => {
    if (moveEvent.pointerId !== drag.pointerId) return
    if (!drag.active) {
      if (state.busy) return // don't start reordering mid-save/delete/move
      const dx = moveEvent.clientX - drag.startX
      const dy = moveEvent.clientY - drag.startY
      if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return
      beginDrag(state, drag)
    }
    updateDrag(state, drag, moveEvent.clientY)
  }
  const onUp = (upEvent) => {
    if (upEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) {
      finishDrag(state, drag)
    } else {
      handleEntryClick(state, name, drag.modifiers)
    }
    state.drag = null
  }
  const onCancel = (cancelEvent) => {
    if (cancelEvent.pointerId !== drag.pointerId) return
    detach()
    if (drag.active) cancelDrag(state, drag)
    state.drag = null
  }
  function detach() {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onCancel)
  }
  // Escape hatch for teardown() — a node removal mid-drag has no pointerup
  // of its own, so it must be able to detach + restore visuals itself.
  drag.cleanup = () => {
    detach()
    if (drag.active) cancelDrag(state, drag)
  }

  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onCancel)
}

function beginDrag(state, drag) {
  drag.active = true
  try {
    drag.rowEl.setPointerCapture(drag.pointerId)
  } catch {
    // Best-effort, mirrors wireSplitter() — the window-level listeners still
    // cover the drag either way.
  }
  drag.rowEl.classList.add('llnb-entry-dragging')
  drag.marker = el('div', { className: 'llnb-drag-marker' })
}

function updateDrag(state, drag, clientY) {
  drag.target = computeDropTarget(state, clientY, drag.name)
  positionMarker(drag)
}

function positionMarker(drag) {
  drag.marker.remove()
  const target = drag.target
  if (!target) return
  if (target.kind === 'before') {
    target.markerBeforeEl.before(drag.marker)
  } else {
    target.markerAfterEl.after(drag.marker)
  }
}

function endDragVisuals(drag) {
  try {
    drag.rowEl.releasePointerCapture(drag.pointerId)
  } catch {
    // Already released, or never captured.
  }
  drag.rowEl.classList.remove('llnb-entry-dragging')
  drag.marker?.remove()
}

function finishDrag(state, drag) {
  endDragVisuals(drag)
  const target = drag.target
  if (!target) return
  if (isNoopMove(state, drag.name, target)) return
  performMove(state, drag.name, target).catch((error) => api.warn('move failed', error))
}

function cancelDrag(state, drag) {
  endDragVisuals(drag)
}

/**
 * Hit-tests `clientY` against the rendered rows (headers + entries, minus
 * the row being dragged) and returns the §5 `/notebook/move` target it
 * corresponds to, or null if there's nothing to hit (empty list).
 *
 * Model: find the row whose vertical midpoint is nearest `clientY`.
 *  - Nearest is an ENTRY and clientY is above its midpoint → before that
 *    entry.
 *  - Nearest is an ENTRY and clientY is at/below its midpoint → before the
 *    NEXT entry if the next row is an entry, else append to THIS entry's
 *    category (the next row is a different category's header, or there is
 *    no next row at all — either way this entry is the last of its run).
 *  - Nearest is a category HEADER (clientY landed anywhere near it, above
 *    or below) → append to that category, regardless of pointer side: §3.4
 *    has no "before a category heading" primitive (`before` always names a
 *    sibling ENTRY), so a header can only ever mean "append to this
 *    category's end" — the marker is placed at that category's actual last
 *    row so it never visually promises a landing spot other than where the
 *    entry will really go.
 * @returns {{kind:'before', before:string, markerBeforeEl:HTMLElement} |
 *           {kind:'category', category:string, markerAfterEl:HTMLElement} | null}
 */
function computeDropTarget(state, clientY, excludeName) {
  const rows = state.dragRows.filter((row) => row.kind !== 'entry' || row.name !== excludeName)
  if (!rows.length) return null

  let bestIndex = -1
  let bestMid = 0
  let bestDist = Infinity
  for (let i = 0; i < rows.length; i++) {
    const rect = rows[i].el.getBoundingClientRect()
    const mid = rect.top + rect.height / 2
    const dist = Math.abs(clientY - mid)
    if (dist < bestDist) {
      bestDist = dist
      bestIndex = i
      bestMid = mid
    }
  }
  const best = rows[bestIndex]

  if (best.kind === 'header') {
    return { kind: 'category', category: best.category, markerAfterEl: lastRowElOfCategory(rows, bestIndex) }
  }

  const above = clientY < bestMid
  if (above) {
    return { kind: 'before', before: best.name, markerBeforeEl: best.el }
  }
  const next = rows[bestIndex + 1]
  if (next && next.kind === 'entry') {
    return { kind: 'before', before: next.name, markerBeforeEl: next.el }
  }
  return { kind: 'category', category: best.category, markerAfterEl: best.el }
}

/** Walks forward from a header row to the last entry belonging to it
 * (falling back to the header itself for an empty category). `rows` is
 * already exclude-filtered, so this naturally treats "only the dragged
 * entry was in this category" as empty too. */
function lastRowElOfCategory(rows, headerIndex) {
  let lastEl = rows[headerIndex].el
  for (let i = headerIndex + 1; i < rows.length; i++) {
    if (rows[i].kind === 'header') break
    lastEl = rows[i].el
  }
  return lastEl
}

/** True when `target` describes the position `draggedName` is already in —
 * skips a pointless request + conflict round-trip for a drop back onto its
 * own slot. */
function isNoopMove(state, draggedName, target) {
  const entries = state.entries
  const index = entries.findIndex((entry) => entry.name === draggedName)
  if (index === -1) return false
  const currentCategory = entries[index].category || ''
  const next = entries[index + 1]

  if (target.kind === 'before') {
    return !!next && next.name === target.before
  }
  const isLastOfOwnCategory = !next || (next.category || '') !== currentCategory
  return currentCategory === target.category && isLastOfOwnCategory
}

/**
 * Commits one drag-drop as a single §5 `/notebook/move`. A 409 surfaces
 * through the same conflict UI Save/Delete use (Reload / Overwrite, where
 * Overwrite retries this exact move with the mtime check skipped); any
 * other error reports on the status line and falls back to a full reload
 * (§3.5 notwithstanding, a move failure means we no longer trust our
 * in-memory ordering).
 */
async function performMove(state, name, target, { force = false } = {}) {
  if (state.busy) return
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Moving…')
  try {
    const body = { file: state.file, name }
    if (target.kind === 'before') body.before = target.before
    else body.category = target.category
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/move', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    // The move just wrote the file, advancing its mtime — the active
    // entry's own content didn't change, but a stale baseMtime here would
    // make the NEXT save/delete/move spuriously 409 against this move's own
    // write (§3.5's conflict check is file-wide, not per-entry).
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    // A move only reorders/recategorizes — it never adds or removes
    // entries — so the current selection/active stay exactly as they were;
    // just re-render against the fresh order.
    setSelection(state, state.selection, state.activeName)
    updateSaveButtonEnabled(state)
    setStatus(state, 'Moved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performMove(state, name, target, { force: true })
      })
    } else {
      api.warn('failed to move notebook entry', error)
      try {
        await reloadNow(state)
      } catch (reloadError) {
        api.warn('notebook reload after move failure failed', reloadError)
      }
      setStatus(state, `Move failed: ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Rename (FORMAT.md §7.2 amendment) — double-click a row to edit its name
// inline. See the file header's "Rename" paragraph for the full writeup of
// how this avoids fighting the click/drag disambiguation above; this
// section is the implementation it describes.
// ---------------------------------------------------------------------------

function openRenameRow(state, name) {
  if (state.renamingName === name) return
  state.renamingName = name
  renderList(state)
}

function closeRenameRow(state) {
  if (state.renamingName == null) return
  state.renamingName = null
  renderList(state)
}

/** Native `dblclick` only ever follows two complete click cycles on the
 * same element — see the file header — so both single-clicks have already
 * run by the time this fires. */
function onEntryDoubleClick(state, event, name) {
  event.preventDefault()
  event.stopPropagation()
  if (state.busy) return
  // Belt-and-suspenders: a real drag can't produce a dblclick (a drag
  // commits via finishDrag()/pointerup, never a click), but a stray
  // in-flight drag object from some other pointer sequence should never
  // survive into rename mode.
  state.drag?.cleanup?.()
  state.drag = null
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  openRenameRow(state, name)
}

function buildRenameRow(state, entry) {
  const input = el('input', {
    className: 'llnb-input llnb-input-rename',
    attrs: { type: 'text', value: entry.name }
  })
  const confirmBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: '✓', attrs: { title: 'Rename' } })
  const cancelBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: '✕', attrs: { title: 'Cancel' } })

  const commit = () => {
    performRename(state, entry.name, input.value).catch((error) => api.warn('rename entry failed', error))
  }
  const cancel = () => closeRenameRow(state)

  // Without preventDefault on mousedown, clicking either button would shift
  // focus off `input` first, firing the blur handler below and tearing this
  // row down before the button's own `click` ever ran (see file header).
  confirmBtn.addEventListener('mousedown', (event) => event.preventDefault())
  cancelBtn.addEventListener('mousedown', (event) => event.preventDefault())
  confirmBtn.addEventListener('click', commit)
  cancelBtn.addEventListener('click', cancel)

  input.addEventListener('keydown', (event) => {
    event.stopPropagation()
    if (event.key === 'Enter') {
      event.preventDefault()
      commit()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      cancel()
    }
  })
  // Catch-all for every other way focus can leave the input (Save, the
  // splitter, outside the widget, tabbing away...). Deferred one tick so a
  // same-tick blur racing a commit's own renderList() — which naturally
  // blurs the about-to-be-removed input — sees `renamingName` already
  // cleared and no-ops instead of double-cancelling (see file header).
  input.addEventListener('blur', () => {
    setTimeout(() => {
      if (state.renamingName === entry.name) cancel()
    }, 0)
  })

  const row = el('div', { className: 'llnb-entry-renaming', attrs: { title: entry.name } }, [
    input,
    confirmBtn,
    cancelBtn
  ])

  requestAnimationFrame(() => {
    input.focus()
    input.select()
  })

  return row
}

/**
 * Commits a rename via the existing §5 entry route's `rename_to` (FORMAT.md
 * has no dedicated rename endpoint). Always refetches `name`'s CURRENT
 * server-side text first rather than trusting anything already in the
 * editor pane, so a rename can never silently rewrite the entry's body —
 * whether because the row being renamed isn't the active one (in practice
 * it always is — see the file header — but this doesn't rely on that) or
 * because the active entry has unsaved edits (those stay unsaved; renaming
 * is a heading-only operation, same as §3.4 promises for a plain update).
 */
async function performRename(state, name, rawNewName, { force = false } = {}) {
  if (state.renamingName !== name) return // superseded by another close/open already

  const newName = (rawNewName || '').trim()
  if (!newName) {
    setStatus(state, 'Enter a name for this entry.')
    return
  }
  if (newName === name) {
    closeRenameRow(state)
    return
  }
  if (state.entries.some((entry) => entry.name === newName)) {
    setStatus(state, `An entry named "${newName}" already exists.`)
    return
  }

  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, `Renaming to "${newName}"…`)
  try {
    const current = await fetchEntry(state, name)
    const body = { file: state.file, name, text: current.text ?? '', rename_to: newName }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.renamingName = null

    const nextSelection = state.selection.map((n) => (n === name ? newName : n))
    const nextActive = state.activeName === name ? newName : state.activeName
    setSelection(state, nextSelection, nextActive)
    updateSaveButtonEnabled(state)
    setStatus(state, `Renamed to "${newName}".`)
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    updateDeleteButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performRename(state, name, newName, { force: true })
      })
    } else {
      api.warn('failed to rename notebook entry', error)
      setStatus(state, `Could not rename "${name}" to "${newName}": ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Footer: New / Delete buttons <-> inline "new entry name" row
// ---------------------------------------------------------------------------

function renderFooter(state) {
  state.footerEl.replaceChildren()

  if (state.creatingNew) {
    const input = el('input', {
      className: 'llnb-input',
      attrs: { type: 'text', placeholder: 'Entry name… (or #Category name)' }
    })
    const confirmBtn = el('button', {
      className: 'llnb-btn llnb-btn-small',
      text: '✓',
      attrs: { title: 'Create' }
    })
    const cancelBtn = el('button', {
      className: 'llnb-btn llnb-btn-small',
      text: '✕',
      attrs: { title: 'Cancel' }
    })

    const submit = () => {
      confirmNewEntry(state, input.value).catch((error) => api.warn('create entry failed', error))
    }
    input.addEventListener('keydown', (event) => {
      event.stopPropagation()
      if (event.key === 'Enter') {
        event.preventDefault()
        submit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        closeNewEntryRow(state)
      }
    })
    confirmBtn.addEventListener('click', submit)
    cancelBtn.addEventListener('click', () => closeNewEntryRow(state))

    state.footerEl.append(input, confirmBtn, cancelBtn)
    state.deleteBtn = null
    requestAnimationFrame(() => input.focus())
  } else {
    const newBtn = el('button', { className: 'llnb-btn', text: '＋ New' })
    const deleteBtn = el('button', { className: 'llnb-btn', text: '🗑 Delete' })
    newBtn.addEventListener('click', () => openNewEntryRow(state))
    deleteBtn.addEventListener('click', () => onDeleteClick(state))

    state.footerEl.append(newBtn, deleteBtn)
    state.deleteBtn = deleteBtn
    updateDeleteButtonEnabled(state)
  }
}

function openNewEntryRow(state) {
  if (state.busy || state.creatingNew) return
  cancelDeleteConfirm(state)
  closeRenameRow(state)
  state.creatingNew = true
  renderFooter(state)
}

function closeNewEntryRow(state) {
  if (!state.creatingNew) return
  state.creatingNew = false
  renderFooter(state)
}

/** A ＋ New input starting with `#` (after trim) creates a CATEGORY instead
 * of an entry (FORMAT.md §7.2 amendment, owner ask 2026-07-19). */
function isCategoryNameInput(rawName) {
  return (rawName || '').trim().startsWith('#')
}

/** The stored category name: leading `#`s + whitespace stripped. */
function categoryNameFromInput(rawName) {
  return (rawName || '').trim().replace(/^#+\s*/, '').trim()
}

async function confirmNewEntry(state, rawName) {
  if (isCategoryNameInput(rawName)) {
    await confirmNewCategory(state, categoryNameFromInput(rawName))
    return
  }

  const name = (rawName || '').trim()
  if (!name) {
    setStatus(state, 'Enter a name for the new entry.')
    return
  }
  if (state.entries.some((entry) => entry.name === name)) {
    setStatus(state, `An entry named "${name}" already exists.`)
    return
  }

  state.busy = true
  setStatus(state, 'Creating…')
  try {
    const data = await api.postJson('/lora_library/notebook/entry', {
      file: state.file,
      name,
      text: ''
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.exists = true
    closeNewEntryRow(state)

    // A new entry is created empty and already known (no need to re-fetch
    // it) — becomes the sole active selection, replacing whatever
    // multi-selection existed before. Also exits category mode (FORMAT.md
    // §7.2 amendment): the newly created entry is what the editor shows now.
    state.activeCategory = null
    setSelection(state, [name], name)
    state.textarea.value = ''
    state.lastSavedText = ''
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
    state.textarea.disabled = false
    setDirty(state, false)
    updateModeHint(state)
    setStatus(state, `Created "${name}".`)
  } catch (error) {
    state.busy = false
    api.warn('failed to create notebook entry', error)
    setStatus(state, `Could not create "${name}": ${error.message}`)
  }
}

/**
 * ＋ New with a `#`-prefixed name (FORMAT.md §7.2 amendment): creates a
 * category via the §5 category route instead of an entry. Mirrors
 * confirmNewEntry() above closely, including skipping `base_mtime` (a
 * create is additive, never destructive, so — like confirmNewEntry() — it
 * doesn't defend against a concurrent edit elsewhere). On success the newly
 * created (empty-description) category becomes the active one, entering
 * category mode — the category-mode equivalent of confirmNewEntry()'s
 * "becomes the sole active selection".
 */
async function confirmNewCategory(state, name) {
  if (!name) {
    setStatus(state, 'Enter a name for the new category (after the "#").')
    return
  }
  if (state.categories.includes(name)) {
    setStatus(state, `A category named "${name}" already exists.`)
    return
  }

  state.busy = true
  setStatus(state, 'Creating category…')
  try {
    const data = await api.postJson('/lora_library/notebook/category', {
      file: state.file,
      name,
      description: ''
    })
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    state.exists = true
    closeNewEntryRow(state)

    // A new category is created with an empty description and already
    // known (no need to re-fetch it) — enters category mode for it,
    // untouched entry selection and all (see the file header).
    state.activeCategory = name
    renderList(state)
    updateDeleteButtonEnabled(state)
    populateEditor(state, '', data.mtime)
    updateModeHint(state)
    setStatus(state, `Created category "${name}".`)
  } catch (error) {
    state.busy = false
    api.warn('failed to create notebook category', error)
    setStatus(state, `Could not create category "${name}": ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// Delete (two-step inline confirm)
// ---------------------------------------------------------------------------

function onDeleteClick(state) {
  // Delete is entry-only — disabled outright in category mode
  // (updateDeleteButtonEnabled()); this is belt-and-suspenders against any
  // path that could invoke the handler despite that (FORMAT.md §7.2
  // amendment).
  if (!state.selection.length || state.busy || state.activeCategory != null) return
  closeRenameRow(state)

  if (!state.deleteConfirmActive) {
    state.deleteConfirmActive = true
    if (state.deleteBtn) {
      state.deleteBtn.textContent = deleteConfirmLabel(state.selection.length)
      state.deleteBtn.classList.add('llnb-btn-danger')
    }
    state.deleteConfirmTimer = setTimeout(() => cancelDeleteConfirm(state), DELETE_CONFIRM_MS)
    return
  }

  cancelDeleteConfirm(state)
  performDeleteRun(state, [...state.selection], 0).catch((error) => api.warn('delete failed', error))
}

/** "Are you sure?" (one entry) or "Are you sure? (3)" (owner amendment
 * 2026-07-18c) — the plain, not-yet-armed button label never changes. */
function deleteConfirmLabel(count) {
  return count > 1 ? `Are you sure? (${count})` : 'Are you sure?'
}

function cancelDeleteConfirm(state) {
  if (state.deleteConfirmTimer) {
    clearTimeout(state.deleteConfirmTimer)
    state.deleteConfirmTimer = null
  }
  state.deleteConfirmActive = false
  if (state.deleteBtn) {
    state.deleteBtn.textContent = '🗑 Delete'
    state.deleteBtn.classList.remove('llnb-btn-danger')
  }
}

/**
 * Deletes `names` sequentially over the single-entry §5 delete route,
 * starting at `startIndex` (>0 only on a post-conflict Overwrite resume —
 * see below). Each successful response's `mtime` becomes the NEXT
 * request's `base_mtime`, and each deleted name is dropped from the
 * selection right away using the exact rule this file always used for a
 * single delete ("Delete acts on the ACTIVE entry only": hand `active` to
 * the last other still-selected name, or clear it if none remain) —
 * applied once per name here, which naturally converges to "clear
 * selection" by the time the whole batch is gone, since nothing outside
 * this run ever ADDS to `state.selection` while it's in flight (see the
 * file header's "Multi-delete" paragraph).
 *
 * A 409 stops the run right where it is — everything deleted so far stays
 * deleted and is already reflected in `state.selection`/the `entry` widget
 * — and shows the same Reload/Overwrite conflict UI Save/Move already use;
 * Overwrite re-enters this same function at the failed index with
 * `force: true` (that ONE request skips `base_mtime`), then continues
 * normally through the rest of `names`.
 */
async function performDeleteRun(state, names, startIndex, { force = false } = {}) {
  state.busy = true
  updateSaveButtonEnabled(state)
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleting ${names.length} entries…` : 'Deleting…')

  for (let index = startIndex; index < names.length; index++) {
    const name = names[index]
    let data
    try {
      const body = { file: state.file, name }
      const skipCheck = force && index === startIndex
      if (!skipCheck && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime
      data = await api.postJson('/lora_library/notebook/delete', body)
    } catch (error) {
      state.busy = false
      updateSaveButtonEnabled(state)
      updateDeleteButtonEnabled(state)
      if (error?.status === 409) {
        showConflict(state, 'File changed on disk', {
          onReload: () => reloadNow(state),
          onOverwrite: () => performDeleteRun(state, names, index, { force: true })
        })
      } else {
        api.warn('failed to delete notebook entry', error)
        setStatus(state, `Delete failed: ${error.message}`)
      }
      return
    }

    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime

    const previousActive = state.activeName
    const nextSelection = state.selection.filter((n) => n !== name)
    const nextActive = previousActive === name ? lastOrNull(nextSelection) : previousActive
    setSelection(state, nextSelection, nextActive)

    if (nextActive !== previousActive) {
      if (nextActive == null) {
        resetEditorDom(state)
      } else {
        const result = await loadEntryText(state, nextActive)
        if (result === 'failed') resetEditorDom(state)
      }
    }
  }

  state.busy = false
  updateDeleteButtonEnabled(state)
  setStatus(state, names.length > 1 ? `Deleted ${names.length} entries.` : 'Deleted.')
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function performSave(state, { force = false } = {}) {
  // The editor is contextual (FORMAT.md §7.2 amendment): category mode owns
  // Save whenever it's active, entirely independent of `activeName` (which
  // may still name an entry underneath — see the file header). This is the
  // ONE branch point between the two; everything else about category-mode
  // saving lives in performSaveCategory() below.
  if (state.activeCategory != null) {
    await performSaveCategory(state, { force })
    return
  }
  if (!state.activeName || state.busy) return

  const name = state.activeName
  const text = state.textarea.value

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, text }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    if (state.activeName !== name) {
      // Selection moved on while the request was in flight; nothing left to
      // reconcile against the (now stale) textarea contents.
      updateSaveButtonEnabled(state)
      return
    }
    state.lastSavedText = text
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    setDirty(state, state.textarea.value !== state.lastSavedText)
    renderList(state)
    setStatus(state, 'Saved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performSave(state, { force: true })
      })
    } else {
      api.warn('failed to save notebook entry', error)
      setStatus(state, `Save failed: ${error.message}`)
    }
  }
}

/**
 * Category-mode sibling of performSave() above (FORMAT.md §7.2 amendment):
 * saves `state.activeCategory`'s description through the §5 category
 * route, sharing the same textarea/dirty/baseMtime/busy/conflict-UI
 * machinery entry-saving already used — only the endpoint and the field
 * name (`description` vs. `text`) differ. Always the "known name" branch of
 * that route: `state.activeCategory` only ever holds a category that's
 * either already in `state.categories` (clicked from the rendered list) or
 * was just created by confirmNewCategory(), so it never hits the create
 * branch here.
 */
async function performSaveCategory(state, { force = false } = {}) {
  const name = state.activeCategory
  if (!name || state.busy) return

  const description = state.textarea.value

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, description }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/category', body)
    state.busy = false
    if (state.activeCategory !== name) {
      updateSaveButtonEnabled(state)
      return
    }
    state.lastSavedText = description
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : state.baseMtime
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries
    state.categories = Array.isArray(data.categories) ? data.categories : state.categories
    setDirty(state, state.textarea.value !== state.lastSavedText)
    renderList(state)
    setStatus(state, 'Saved.')
  } catch (error) {
    state.busy = false
    updateSaveButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performSaveCategory(state, { force: true })
      })
    } else {
      api.warn('failed to save category description', error)
      setStatus(state, `Save failed: ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Dirty / button enablement
// ---------------------------------------------------------------------------

function setDirty(state, value) {
  state.dirty = value
  updateSaveButtonEnabled(state)
}

function updateSaveButtonEnabled(state) {
  if (!state.saveBtn) return
  // FORMAT.md §7.2 amendment: Save targets whichever of the two contextual
  // modes is active (category mode or entry mode — see performSave()).
  const hasTarget = state.activeCategory != null || Boolean(state.activeName)
  state.saveBtn.disabled = state.busy || !hasTarget || !state.dirty
}

function updateDeleteButtonEnabled(state) {
  if (!state.deleteBtn) return
  // Delete stays entry-only — disabled outright in category mode (FORMAT.md
  // §7.2 amendment).
  state.deleteBtn.disabled =
    state.busy || state.selection.length === 0 || state.activeCategory != null
}

// ---------------------------------------------------------------------------
// Status line + conflict UI (FORMAT.md §3.5)
// ---------------------------------------------------------------------------

function setStatus(state, text) {
  state.statusTextEl.textContent = text || ''
  state.statusActionsEl.replaceChildren()
}

function clearConflict(state) {
  state.statusActionsEl.replaceChildren()
}

/**
 * @param {{onReload: () => Promise<void>, onOverwrite: () => Promise<void>}} actions
 */
function showConflict(state, message, actions) {
  state.statusTextEl.textContent = message

  const reloadBtn = el('button', { className: 'llnb-btn llnb-btn-small', text: 'Reload' })
  const overwriteBtn = el('button', {
    className: 'llnb-btn llnb-btn-small llnb-btn-danger',
    text: 'Overwrite'
  })
  const disableBoth = () => {
    reloadBtn.disabled = true
    overwriteBtn.disabled = true
  }
  reloadBtn.addEventListener('click', () => {
    disableBoth()
    Promise.resolve(actions.onReload()).catch((error) => api.warn('reload (conflict) failed', error))
  })
  overwriteBtn.addEventListener('click', () => {
    disableBoth()
    Promise.resolve(actions.onOverwrite()).catch((error) => api.warn('overwrite (conflict) failed', error))
  })

  state.statusActionsEl.replaceChildren(reloadBtn, overwriteBtn)
}
