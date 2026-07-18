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
 */

import * as api from './api.js'

const NODE_CLASS = 'LoraLibraryApplySet'
const WIDGET_NAME = 'set'

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

/**
 * Per-instance hook (called from lora_library.js `nodeCreated`); no-op for
 * every node type except LoraLibraryApplySet.
 * @param {object} node
 */
export function attachApplySetBehavior(node) {
  const comfyClass = node?.comfyClass ?? node?.constructor?.comfyClass
  if (comfyClass !== NODE_CLASS) return
  const widget = (node.widgets ?? []).find((w) => w.name === WIDGET_NAME)
  if (!widget || !widget.options) return
  widget.options.values = () => {
    // Fire-and-forget: today's open shows the cache, the refetch it kicks
    // makes the next open exact (see file header).
    refreshSetsCache()
    return cachedValues
  }
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
