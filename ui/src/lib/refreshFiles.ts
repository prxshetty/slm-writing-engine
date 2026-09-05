import { API_BASE } from './api'
import { useEditorStore } from '../stores/editorStore'

// Merge-refresh of the sidebar file list after harness file writes: fetches
// the workspace listing and addFile()s anything new. The store dedupes by
// path, so open tabs and their content are never touched. Debounced so a
// burst of agent writes triggers one fetch.
let timer: ReturnType<typeof setTimeout> | null = null

export function scheduleFileRefresh(delayMs = 500) {
  if (timer) clearTimeout(timer)
  timer = setTimeout(async () => {
    timer = null
    try {
      const res = await fetch(`${API_BASE}/api/workspace/files`)
      if (!res.ok) return
      const files = await res.json()
      const { addFile } = useEditorStore.getState()
      for (const file of files) addFile({ name: file.name, path: file.path, content: '', originalContent: '' })
    } catch {
      // Next tool event or harness_done retries.
    }
  }, delayMs)
}
