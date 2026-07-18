/**
 * @file Apply LoRA Set frontend behavior (FORMAT.md §7.4): keeps every
 * `LoraLibraryApplySet` node's `set` combo fresh after set CRUD without a
 * page reload. STUB: implemented by the sets workstream.
 */

/**
 * Per-instance hook for LoraLibraryApplySet nodes; no-op for other types.
 * @param {object} node
 */
export function attachApplySetBehavior(node) {
  void node // TODO(sets): FORMAT.md §7.4
}

/** One-time wiring run at extension setup. */
export function initSetsFreshness() {
  // TODO(sets): FORMAT.md §7.4
}
