/**
 * @file "LoRA Library" settings section (FORMAT.md §7.3): backend+frontend
 * version display (mismatch = "pulled but not restarted" hint, the
 * comfyui-photoshop-bridge pattern) and the library_dir setting.
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

const CATEGORY = 'LoRA Library'

let backendVersion = null

/** ComfyUI settings-panel entries, passed to registerExtension. */
export const SETTINGS = [
  {
    id: 'loraLibrary.libraryDir',
    category: [CATEGORY, 'Library', 'Folder'],
    name: 'Library folder',
    tooltip:
      'Absolute path of the shared library folder (holds loras.md and sets/). ' +
      'May be a NAS/network path readable by every machine that shares it. ' +
      'Leave empty for the per-user default. Applied on change; the value ' +
      'lives server-side (FORMAT.md §1), so every browser sees the same one.',
    type: 'text',
    defaultValue: '',
    onChange: onLibraryDirChanged
  },
  {
    id: 'loraLibrary.versions',
    category: [CATEGORY, 'About', 'Versions'],
    name: 'Backend / frontend versions',
    type: () => versionRow(),
    defaultValue: ''
  }
]

let suppressOnChange = false

async function onLibraryDirChanged(value) {
  if (suppressOnChange) return
  const trimmed = (value ?? '').trim()
  try {
    await api.postJson('/lora_library/config', { library_dir: trimmed })
  } catch (error) {
    api.warn('saving library_dir failed', error)
    app.extensionManager?.toast?.add?.({
      severity: 'error',
      summary: 'LoRA Library',
      detail: `Could not set library folder: ${error.message}`,
      life: 6000
    })
  }
}

/**
 * One-time setup: pull backend config/version, seed the settings text field
 * with the server-side value, and toast on version mismatch.
 */
export async function initSettings() {
  try {
    const [config, version] = await Promise.all([
      api.getJson('/lora_library/config'),
      api.getJson('/lora_library/version')
    ])
    backendVersion = version.version
    // Reflect the server-side truth into the settings field without
    // re-POSTing it back (the guard below).
    suppressOnChange = true
    try {
      const current = config.configured ? config.library_dir : ''
      await app.extensionManager?.setting?.set?.('loraLibrary.libraryDir', current)
    } finally {
      suppressOnChange = false
    }
    if (backendVersion && backendVersion !== api.FRONTEND_VERSION) {
      app.extensionManager?.toast?.add?.({
        severity: 'warn',
        summary: 'LoRA Library version mismatch',
        detail:
          `backend v${backendVersion}, frontend v${api.FRONTEND_VERSION} — if you ` +
          'just updated, restart the ComfyUI server (backend) or hard-refresh ' +
          'the browser (frontend).',
        life: 8000
      })
    }
  } catch (error) {
    api.warn('initSettings failed (backend not reachable?)', error)
  }
}

function versionRow() {
  const el = document.createElement('div')
  el.style.opacity = '0.85'
  el.textContent = backendVersion
    ? `backend v${backendVersion} · frontend v${api.FRONTEND_VERSION}` +
      (backendVersion === api.FRONTEND_VERSION ? '' : '  ⚠ mismatch — restart server or hard-refresh')
    : `frontend v${api.FRONTEND_VERSION} · backend unreachable`
  return el
}
