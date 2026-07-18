/**
 * @file LoRA Notebook two-pane DOM widget (FORMAT.md §7.2) — attaches to
 * `LoraLibraryNotebook` nodes. Left pane: a scrollable, category-grouped
 * entry list with New/Delete controls. Right pane: a `<textarea>` editor
 * with a Save button and a status line (conflict resolution per §3.5 lands
 * there too). The node's own `file`/`entry` STRING widgets stay the
 * serialized truth (§6.1/§7.2) — this DOM widget only ever *reads* `file`
 * and *writes* `entry` through its normal widget setter; it never
 * serializes itself.
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

/** How long the Delete button stays in "Really delete?" mode. */
const DELETE_CONFIRM_MS = 4000

/** Debounce for reloading after the `file` widget's value changes. */
const FILE_CHANGE_DEBOUNCE_MS = 250

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
  flex-direction: row;
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
}
.llnb-entry:hover { background: var(--content-hover-bg, #2a2a2a); }
.llnb-entry:focus-visible { box-shadow: inset 0 0 0 1px var(--border-color, #444); }
.llnb-entry-selected,
.llnb-entry-selected:hover {
  background: rgba(66, 133, 244, 0.22);
  border-left-color: rgba(66, 133, 244, 0.9);
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
.llnb-btn-save { flex: 0 0 auto; align-self: flex-start; margin-bottom: 3px; }
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
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 6px;
}
.llnb-status { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.llnb-status-text {
  color: var(--descrip-text, #999);
  font-size: 10px;
  overflow-wrap: anywhere;
  white-space: normal;
}
.llnb-status-actions { display: flex; gap: 4px; }
.llnb-status-actions:empty { display: none; }
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
    selectedName: null,
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
    // DOM refs, filled in by buildUi() — only elements later functions need
    // to reach back into are tracked here (e.g. `newBtn` isn't, since
    // nothing but renderFooter() itself ever touches it).
    root: null,
    leftPane: null,
    listEl: null,
    footerEl: null,
    textarea: null,
    saveBtn: null,
    statusTextEl: null,
    statusActionsEl: null,
    deleteBtn: null
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

  state.textarea = el('textarea', {
    className: 'llnb-textarea',
    attrs: {
      placeholder: 'Select an entry on the left, or click ＋ New to create one.',
      spellcheck: 'false'
    }
  })
  state.saveBtn = el('button', { className: 'llnb-btn llnb-btn-save', text: 'Save' })
  state.statusTextEl = el('div', { className: 'llnb-status-text' })
  state.statusActionsEl = el('div', { className: 'llnb-status-actions' })
  const statusRow = el('div', { className: 'llnb-status' }, [state.statusTextEl, state.statusActionsEl])
  const bottomRow = el('div', { className: 'llnb-bottom-row' }, [state.saveBtn, statusRow])
  const rightPane = el('div', { className: 'llnb-pane llnb-pane-right' }, [state.textarea, bottomRow])

  state.root = el('div', { className: 'llnb-root' }, [state.leftPane, splitter, rightPane])

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

function wireFileWidget(state) {
  const widget = state.fileWidget
  const original = widget.callback
  widget.callback = function (value, ...rest) {
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
  // Invalidate any in-flight fetches so their `.then` handlers no-op.
  state.loadToken += 1
  state.selectToken += 1
}

// ---------------------------------------------------------------------------
// Loading the notebook list + auto-select
// ---------------------------------------------------------------------------

async function reloadNow(state) {
  const token = ++state.loadToken
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)
  clearConflict(state)

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
  state.exists = data.exists !== false
  renderList(state)
  setStatus(state, baselineStatus(state, data.problems))

  const currentEntryName = state.entryWidget.value
  if (currentEntryName && state.entries.some((entry) => entry.name === currentEntryName)) {
    await selectEntry(state, currentEntryName)
  } else {
    clearEditor(state)
  }
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
// Selecting an entry
// ---------------------------------------------------------------------------

async function selectEntry(state, name) {
  cancelDeleteConfirm(state)
  closeNewEntryRow(state)

  const loadToken = state.loadToken
  const selectToken = ++state.selectToken
  const previousSelected = state.selectedName
  state.selectedName = name
  renderList(state)

  let data
  try {
    data = await api.getJson('/lora_library/notebook/entry', { file: state.file, name })
  } catch (error) {
    if (loadToken !== state.loadToken || selectToken !== state.selectToken) return
    api.warn('failed to load notebook entry', error)
    state.selectedName = previousSelected
    renderList(state)
    setStatus(state, `Could not load "${name}": ${error.message}`)
    return
  }
  if (loadToken !== state.loadToken || selectToken !== state.selectToken) return

  state.textarea.value = data.text ?? ''
  state.lastSavedText = state.textarea.value
  state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
  state.textarea.disabled = false
  setDirty(state, false)
  updateEntryWidget(state, name)
  updateDeleteButtonEnabled(state)
  clearConflict(state)
}

function clearEditor(state) {
  state.selectedName = null
  state.textarea.value = ''
  state.lastSavedText = ''
  state.baseMtime = null
  state.textarea.disabled = true
  setDirty(state, false)
  renderList(state)
  updateDeleteButtonEnabled(state)
}

/**
 * Sets the `entry` STRING widget's value through its normal setter +
 * callback (FORMAT.md §7.2: "Selection writes the entry STRING widget so
 * serialization needs no custom code") and nudges the canvas to redraw so
 * the change is visible immediately. Mirrors the pattern ComfyUI's own
 * `scripts/widgets.ts` (`applyWidgetControl`) uses to drive one widget's
 * value from other logic: `targetWidget.value = next;
 * targetWidget.callback?.(next)`.
 */
function updateEntryWidget(state, name) {
  const widget = state.entryWidget
  if (widget.value === name) return
  widget.value = name
  try {
    widget.callback?.(name)
  } catch (error) {
    api.warn('entry widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// Entry list rendering
// ---------------------------------------------------------------------------

function renderList(state) {
  state.listEl.replaceChildren()

  if (!state.entries.length) {
    state.listEl.append(
      el('div', {
        className: 'llnb-empty',
        text: state.exists ? 'No entries yet.' : 'File not found yet.'
      })
    )
    return
  }

  let lastCategory
  for (const entry of state.entries) {
    const category = entry.category || ''
    if (category !== lastCategory) {
      lastCategory = category
      if (category) {
        state.listEl.append(el('div', { className: 'llnb-category', text: category }))
      }
    }

    const selected = entry.name === state.selectedName
    const row = el('div', {
      className: `llnb-entry${selected ? ' llnb-entry-selected' : ''}`,
      text: entry.name,
      attrs: { tabindex: '0', title: entry.name }
    })
    row.addEventListener('click', () => {
      selectEntry(state, entry.name).catch((error) => api.warn('select entry failed', error))
    })
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        selectEntry(state, entry.name).catch((error) => api.warn('select entry failed', error))
      }
    })
    state.listEl.append(row)
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
      attrs: { type: 'text', placeholder: 'Entry name…' }
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
  state.creatingNew = true
  renderFooter(state)
}

function closeNewEntryRow(state) {
  if (!state.creatingNew) return
  state.creatingNew = false
  renderFooter(state)
}

async function confirmNewEntry(state, rawName) {
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

    state.selectedName = name
    state.textarea.value = ''
    state.lastSavedText = ''
    state.baseMtime = typeof data.mtime === 'number' ? data.mtime : null
    state.textarea.disabled = false
    setDirty(state, false)
    updateEntryWidget(state, name)
    renderList(state)
    updateDeleteButtonEnabled(state)
    setStatus(state, `Created "${name}".`)
  } catch (error) {
    state.busy = false
    api.warn('failed to create notebook entry', error)
    setStatus(state, `Could not create "${name}": ${error.message}`)
  }
}

// ---------------------------------------------------------------------------
// Delete (two-step inline confirm)
// ---------------------------------------------------------------------------

function onDeleteClick(state) {
  if (!state.selectedName || state.busy) return

  if (!state.deleteConfirmActive) {
    state.deleteConfirmActive = true
    if (state.deleteBtn) {
      state.deleteBtn.textContent = 'Really delete?'
      state.deleteBtn.classList.add('llnb-btn-danger')
    }
    state.deleteConfirmTimer = setTimeout(() => cancelDeleteConfirm(state), DELETE_CONFIRM_MS)
    return
  }

  cancelDeleteConfirm(state)
  performDelete(state).catch((error) => api.warn('delete failed', error))
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

async function performDelete(state, { force = false } = {}) {
  const name = state.selectedName
  if (!name || state.busy) return

  state.busy = true
  updateDeleteButtonEnabled(state)
  setStatus(state, 'Deleting…')
  try {
    const body = { file: state.file, name }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/delete', body)
    state.busy = false
    state.entries = Array.isArray(data.entries) ? data.entries : state.entries

    if (state.selectedName === name) {
      clearEditor(state)
      updateEntryWidget(state, '')
    } else {
      renderList(state)
    }
    setStatus(state, 'Deleted.')
  } catch (error) {
    state.busy = false
    updateDeleteButtonEnabled(state)
    if (error?.status === 409) {
      showConflict(state, 'File changed on disk', {
        onReload: () => reloadNow(state),
        onOverwrite: () => performDelete(state, { force: true })
      })
    } else {
      api.warn('failed to delete notebook entry', error)
      setStatus(state, `Delete failed: ${error.message}`)
    }
  }
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

async function performSave(state, { force = false } = {}) {
  if (!state.selectedName || state.busy) return

  const name = state.selectedName
  const text = state.textarea.value

  state.busy = true
  updateSaveButtonEnabled(state)
  setStatus(state, 'Saving…')
  try {
    const body = { file: state.file, name, text }
    if (!force && typeof state.baseMtime === 'number') body.base_mtime = state.baseMtime

    const data = await api.postJson('/lora_library/notebook/entry', body)
    state.busy = false
    if (state.selectedName !== name) {
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

// ---------------------------------------------------------------------------
// Dirty / button enablement
// ---------------------------------------------------------------------------

function setDirty(state, value) {
  state.dirty = value
  updateSaveButtonEnabled(state)
}

function updateSaveButtonEnabled(state) {
  if (!state.saveBtn) return
  state.saveBtn.disabled = state.busy || !state.selectedName || !state.dirty
}

function updateDeleteButtonEnabled(state) {
  if (!state.deleteBtn) return
  state.deleteBtn.disabled = state.busy || !state.selectedName
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
