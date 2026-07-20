/**
 * @file Apply LoRA Set frontend behavior (FORMAT.md §7.4): keeps every
 * `LoraLibraryApplySet` node's `set` combo fresh without a page reload.
 *
 * Mechanism: each ApplySet node's combo gets `options.values` swapped for a
 * FUNCTION returning a module-level cache — the same (deprecated-but-
 * supported) dynamic-combo pattern controller.js uses, chosen deliberately
 * so both files ride the same litegraph code path and age together. The
 * values function also kicks a THROTTLED async refetch, so the flow is:
 * open the dropdown → see the cache (usually current) → cache refreshes in
 * the background → the next open is exact. No cross-module coupling with
 * controller.js: its CRUD lands in the same backend the refetch reads.
 *
 * Server-side `VALIDATE_INPUTS` already returns True for unseen values
 * (FORMAT.md §6.2), so a *stale* combo can still queue a just-created set;
 * this module only closes the UX gap, not a correctness one.
 *
 * 2026-07-19c addition: FORMAT.md §6.2's `mirrors loader` tag (owner:
 * "set different Apply LoRA Set nodes to different Power Lora Loaders as
 * targets") — `attachMirrorsWidget()`. A second frontend-only combo per
 * Apply node, appended AFTER the server widgets (`set`, `strength_scale`)
 * on every `nodeCreated`. It is a pure GROUPING TAG read by controller.js's
 * selective Push State (`selectPushTargets()`/`mirrorsTagMatches()` there)
 * — it never changes what this node executes, and the server never sees it
 * (not a Python-declared widget, so it never appears in `INPUT_TYPES` or
 * the API prompt).
 *
 * Positional-restore safety ("append LAST", FORMAT.md §6.2): litegraph
 * saves `widgets_values[i]` at each widget's own array INDEX and restores
 * by walking a plain counter over `node.widgets` in order (verified once,
 * in detail, against this exact rig's frontend bundle — see controller.js's
 * file header's "save/restore ordering hazard" citation for the concrete
 * lines; not re-derived here to avoid duplicating that citation across two
 * files that already don't import each other). The practical consequence
 * for THIS widget: as long as it's appended after every widget ComfyUI's
 * own Python `INPUT_TYPES` already created for the node — true on every
 * `nodeCreated` call, fresh-add or workflow-load alike, since the server
 * widgets are already in `node.widgets` by the time this extension's
 * `nodeCreated` hook runs (the SAME assumption `attachApplySetBehavior()`
 * below already relies on to find the existing `set` widget by name) — its
 * position stays the LAST slot in both `node.widgets` and `widgets_values`
 * on every save/restore, so a plain by-index restore never misaligns it
 * against the server widgets.
 *
 * Self-healing staleness ("tolerates the id disappearing", FORMAT.md §6.2):
 * the widget's `options.values` function is itself the refresh point — it
 * is a live, litegraph-invoked function (called on every draw via
 * `ComboWidget._displayValue`'s `t()` call, same as the `refreshSetsCache`
 * idiom above and controller.js's own dynamic combos), so it doubles as a
 * validity check: if the tagged PLL id no longer resolves to a live node,
 * it resets the widget to "(any)" right there — no separate heartbeat or
 * `onDrawForeground` hook needed.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

const NODE_CLASS = 'LoraLibraryApplySet'
const WIDGET_NAME = 'set'

/** Exact rgthree type string — same literal as controller.js's `POWER_LORA_LOADER_TYPE` (constants.js `addRgthree("Power Lora Loader")`); duplicated by hand rather than imported, per this file's no-cross-module-coupling design (see above). */
const POWER_LORA_LOADER_TYPE = 'Power Lora Loader (rgthree)'

/** FORMAT.md §6.2 `mirrors loader` tag — widget name + its "no PLL selected" default value. controller.js's `MIRRORS_WIDGET_NAME`/`MIRRORS_ANY_VALUE` mirror these by hand (same convention as NODE_CLASS/WIDGET_NAME above). */
const MIRRORS_WIDGET_NAME = 'mirrors loader'
const MIRRORS_ANY_VALUE = '(any)'

/** §7.4 freshness beats thrift here, but don't hammer on every redraw. */
const REFRESH_THROTTLE_MS = 2000

/** Module-level cache shared by every ApplySet node in the graph. */
let cachedValues = ['None']
let lastFetchStarted = 0
let fetchInFlight = false

async function refreshSetsCache(force = false) {
  const now = Date.now()
  if (fetchInFlight) return
  if (!force && now - lastFetchStarted < REFRESH_THROTTLE_MS) return
  lastFetchStarted = now
  fetchInFlight = true
  try {
    const data = await api.getJson('/lora_library/sets')
    const slugs = (data.sets ?? []).map((row) => row.slug)
    cachedValues = ['None', ...slugs]
  } catch (error) {
    api.warn('refreshing set list failed (keeping previous combo values)', error)
  } finally {
    fetchInFlight = false
  }
}

/** Every live PLL node in the graph, labeled "<title> #<id>" — same label shape controller.js's `target` combo uses, so a Push State toast and this tag's on-canvas display both read the same identity string. */
function findPllCandidates() {
  const nodes = app.graph?._nodes || app.graph?.nodes || []
  const out = []
  for (const node of nodes) {
    if (node && node.type === POWER_LORA_LOADER_TYPE) {
      out.push({ id: node.id, label: `${node.title || node.type} #${node.id}` })
    }
  }
  return out
}

/** FORMAT.md §6.2: the node id embedded in a "<title> #<id>" label, or null for "(any)"/anything else — same regex shape as controller.js's `pllIdFromLabel()` (duplicated by hand, not imported; see file header). */
function pllIdFromLabel(label) {
  const match = /#(-?\d+)\s*$/.exec(String(label || ''))
  return match ? match[1] : null
}

/**
 * FORMAT.md §6.2 `mirrors loader` tag. Idempotent (checked by widget name)
 * so a double `nodeCreated` fire can never add it twice. See file header
 * for the "append LAST"/self-healing design notes.
 * @param {object} node
 */
function attachMirrorsWidget(node) {
  if ((node.widgets ?? []).some((w) => w.name === MIRRORS_WIDGET_NAME)) return
  const widget = node.addWidget('combo', MIRRORS_WIDGET_NAME, MIRRORS_ANY_VALUE, () => {}, {
    values: [MIRRORS_ANY_VALUE] // placeholder; replaced below once `widget` exists — same two-step idiom attachApplySetBehavior() already uses for the `set` combo.
  })
  widget.options.values = () => {
    const candidates = findPllCandidates()
    const id = pllIdFromLabel(widget.value)
    if (id != null && !candidates.some((c) => String(c.id) === id)) {
      widget.value = MIRRORS_ANY_VALUE // tagged PLL vanished — fall back to "(any)" (FORMAT.md §6.2)
    }
    return [MIRRORS_ANY_VALUE, ...candidates.map((c) => c.label)]
  }
}

/**
 * Per-instance hook (called from lora_library.js `nodeCreated`); no-op for
 * every node type except LoraLibraryApplySet.
 * @param {object} node
 */
export function attachApplySetBehavior(node) {
  const comfyClass = node?.comfyClass ?? node?.constructor?.comfyClass
  if (comfyClass !== NODE_CLASS) return
  const widget = (node.widgets ?? []).find((w) => w.name === WIDGET_NAME)
  if (widget && widget.options) {
    widget.options.values = () => {
      // Fire-and-forget: today's open shows the cache, the refetch it kicks
      // makes the next open exact (see file header).
      refreshSetsCache()
      return cachedValues
    }
  }
  // 2026-07-19c: append the `mirrors loader` tag AFTER the `set` combo wiring
  // above so it always lands after every server widget in `node.widgets`
  // (file header "append LAST" note) — this call only ADDS a widget, it
  // doesn't touch the `set` combo, so the relative order between the two
  // blocks above/below doesn't itself matter, only that this runs after
  // ComfyUI's own Python-declared widgets already exist, which is always
  // true by the time `nodeCreated` fires.
  attachMirrorsWidget(node)
}

/** One-time wiring at extension setup: seed the cache so the first dropdown
 * open is already current, and refresh immediately whenever the controller
 * announces a CRUD (`lora_library:sets-changed`, see controller.js) so the
 * FIRST open after a capture/update/delete is already exact — the throttled
 * open-time refetch is only the fallback for out-of-band changes (another
 * machine editing the shared library, curl, etc.). */
export function initSetsFreshness() {
  window.addEventListener('lora_library:sets-changed', () => refreshSetsCache(true))
  refreshSetsCache(true)
}
