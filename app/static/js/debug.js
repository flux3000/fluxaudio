/**
 * debug.js — Flux Audio debug panel.
 *
 * Activates only in DEV_MODE (checked via /api/debug/info on load).
 * Renders into the slide panel's Debug tab when in the recording view.
 * Toggle with backtick (`) from anywhere.
 *
 * Pop-out: click ⊞ in the pane header to open a floating window.
 * BroadcastChannel('flux-debug') keeps the pop-out in sync.
 *
 * Exports: window.fluxDebug = { attach(container), detach(), refresh() }
 */

;(async function initDebug() {

  // ── API call log — wrap fetch FIRST so we catch everything ──────────────────
  const MAX_LOG  = 50
  const MAX_ERR  = 20
  const apiLog   = []
  const errorLog = []
  const _origFetch = window.fetch

  window.fetch = async function(input, init) {
    const url    = typeof input === 'string' ? input : input.url
    const method = (init?.method || 'GET').toUpperCase()
    const t0     = performance.now()

    try {
      const res = await _origFetch(input, init)
      const ms  = Math.round(performance.now() - t0)
      const entry = { kind: 'api', method, url, status: res.status, ms, ts: new Date() }
      apiLog.unshift(entry)
      if (apiLog.length > MAX_LOG) apiLog.pop()
      _broadcast({ type: 'api', entry })
      _sendToServer(entry)
      _refreshLog()
      return res
    } catch (err) {
      const ms = Math.round(performance.now() - t0)
      const entry = { kind: 'api', method, url, status: 'ERR', ms, ts: new Date(), err: err.message }
      apiLog.unshift(entry)
      if (apiLog.length > MAX_LOG) apiLog.pop()
      _broadcast({ type: 'api', entry })
      _sendToServer(entry)
      _refreshLog()
      throw err
    }
  }

  // ── JS error capture ─────────────────────────────────────────────────────────
  window.addEventListener('error', e => {
    const entry = {
      kind:    'jserror',
      message:  e.message,
      source:   e.filename ? e.filename.split('/').pop() + ':' + e.lineno : '?',
      ts:       new Date(),
    }
    errorLog.unshift(entry)
    if (errorLog.length > MAX_ERR) errorLog.pop()
    _broadcast({ type: 'jserror', entry })
    _sendToServer(entry)
    _refreshErrors()
  })

  window.addEventListener('unhandledrejection', e => {
    const entry = {
      kind:    'jserror',
      message: String(e.reason?.message || e.reason || 'Unhandled rejection'),
      source:  'promise',
      ts:      new Date(),
    }
    errorLog.unshift(entry)
    if (errorLog.length > MAX_ERR) errorLog.pop()
    _broadcast({ type: 'jserror', entry })
    _sendToServer(entry)
    _refreshErrors()
  })

  // ── Check DEV_MODE ──────────────────────────────────────────────────────────
  let appInfo
  try {
    const res = await _origFetch('/api/debug/info', { credentials: 'same-origin' })
    if (!res.ok) return   // 404 = not in DEV_MODE, bail silently
    appInfo = await res.json()
  } catch {
    return
  }

  // ── Forward log entries to Flask (for pop-out browser window) ───────────────
  function _sendToServer(entry) {
    // Fire-and-forget; skip /api/debug/log itself to avoid infinite loop
    if (entry.url?.includes('/api/debug/')) return
    _origFetch('/api/debug/log', {
      method:      'POST',
      credentials: 'same-origin',
      headers:     { 'Content-Type': 'application/json' },
      body:        JSON.stringify({ ...entry, ts: entry.ts?.toISOString?.() ?? entry.ts }),
    }).catch(() => {})  // swallow — debug shouldn't break the app
  }

  // ── BroadcastChannel — syncs state to pop-out window (same-browser only) ────
  let bc = null
  try {
    bc = new BroadcastChannel('flux-debug')
    bc.onmessage = e => {
      if (e.data?.type === 'request-state') {
        // Pop-out window just opened and wants a full dump
        bc.postMessage({ type: 'full-state', apiLog: [...apiLog], errorLog: [...errorLog] })
      }
    }
  } catch {}

  function _broadcast(msg) {
    try { bc?.postMessage(msg) } catch {}
  }

  // ── Build panel DOM (hidden — mounted into slide pane on demand) ─────────────
  const panel = document.createElement('div')
  panel.id        = 'dbg-panel'
  panel.className = 'dbg-panel-inner'
  panel.innerHTML = `
    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-errors">
        JS Errors ▾ <span class="dbg-err-badge" id="dbg-err-badge" style="display:none"></span>
      </div>
      <div class="dbg-section-body" id="dbg-errors">
        <div id="dbg-errors-content" class="dbg-log-list"></div>
      </div>
    </div>

    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-state">App State ▾</div>
      <div class="dbg-section-body" id="dbg-state">
        <div class="dbg-kv" id="dbg-state-content">Loading…</div>
      </div>
    </div>

    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-tags">FLAC Tags ▾</div>
      <div class="dbg-section-body" id="dbg-tags">
        <div class="dbg-tags-toolbar">
          <button class="dbg-btn" id="dbg-btn-tags">Read file tags</button>
          <span class="dbg-hint" id="dbg-tags-hint">for active recording</span>
        </div>
        <div id="dbg-tags-content"></div>
      </div>
    </div>

    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-log">API Log ▾</div>
      <div class="dbg-section-body" id="dbg-log">
        <div class="dbg-log-toolbar">
          <button class="dbg-btn dbg-btn-sm" id="dbg-btn-clear-log">Clear</button>
        </div>
        <div id="dbg-log-content" class="dbg-log-list"></div>
      </div>
    </div>
  `
  // Keep in body but hidden — attach() moves it into the slide pane
  panel.style.display = 'none'
  document.body.appendChild(panel)

  // ── Collapsible sections ─────────────────────────────────────────────────────
  panel.querySelectorAll('.dbg-section-head').forEach(head => {
    head.addEventListener('click', () => {
      const body      = document.getElementById(head.dataset.target)
      const collapsed = body.style.display === 'none'
      body.style.display = collapsed ? '' : 'none'
      head.textContent   = head.textContent.replace(
        collapsed ? '▸' : '▾',
        collapsed ? '▾' : '▸'
      )
    })
  })

  // ── JS error display ─────────────────────────────────────────────────────────
  function _refreshErrors() {
    const el    = document.getElementById('dbg-errors-content')
    const badge = document.getElementById('dbg-err-badge')
    if (badge) {
      badge.style.display = errorLog.length ? '' : 'none'
      badge.textContent   = errorLog.length
    }
    if (!el || panel.style.display === 'none') return
    el.innerHTML = errorLog.map(e => {
      const t = e.ts.toLocaleTimeString('en-US', { hour12: false })
      return `<div class="dbg-log-row dbg-log-err">
        <span class="dbg-log-method" style="color:var(--red)">ERR</span>
        <span class="dbg-log-url" style="flex:1">${esc(e.message)}</span>
        <span class="dbg-log-ms" style="color:var(--t2)">${esc(e.source)}</span>
        <span class="dbg-log-ts">${t}</span>
      </div>`
    }).join('') || '<div class="dbg-hint" style="color:var(--green, #6a9)">No errors ✓</div>'
  }

  // ── App state ────────────────────────────────────────────────────────────────
  function refreshState() {
    const st     = window.fluxState  || {}
    const player = window.fluxPlayer || {}

    const rows = [
      ['Route',         location.hash || '/'],
      ['Recording ID',  st.recordingId   ?? '—'],
      ['Track count',   st.trackCount    ? `${st.trackCount} tracks` : '—'],
      ['Player track',  player.currentTitle ?? '—'],
      ['Queue',         player.queueLength  ? `${player.queueIdx + 1} / ${player.queueLength}` : '—'],
      ['Library root',  appInfo.library_root],
      ['DB',            (appInfo.db_path || '').replace('sqlite:///', '')],
      ['Artists',       appInfo.counts?.artists    ?? '—'],
      ['Recordings',    appInfo.counts?.recordings ?? '—'],
      ['Tracks',        appInfo.counts?.tracks     ?? '—'],
    ]

    const el = document.getElementById('dbg-state-content')
    if (el) el.innerHTML = rows.map(([k, v]) =>
      `<div class="dbg-row">
         <span class="dbg-key">${k}</span>
         <span class="dbg-val">${v ?? ''}</span>
       </div>`
    ).join('')
  }

  async function refreshInfoCounts() {
    try {
      const r = await _origFetch('/api/debug/info', { credentials: 'same-origin' })
      if (r.ok) { appInfo = await r.json(); refreshState() }
    } catch {}
  }

  // ── FLAC tag inspector ───────────────────────────────────────────────────────
  document.getElementById('dbg-btn-tags').addEventListener('click', async () => {
    const recId = window.fluxState?.recordingId
    const hint  = document.getElementById('dbg-tags-hint')
    const out   = document.getElementById('dbg-tags-content')

    if (!recId) {
      hint.textContent = '⚠ no recording active'
      return
    }

    hint.textContent = `reading rec #${recId}…`
    out.innerHTML    = ''

    try {
      const r = await _origFetch(`/api/debug/tags/${recId}`, { credentials: 'same-origin' })
      const d = await r.json()
      hint.textContent = `rec #${recId} — ${d.files?.length ?? 0} file(s)`
      out.innerHTML    = renderTagInspector(d)

      out.querySelectorAll('.dbg-file-head').forEach(h => {
        h.addEventListener('click', () => {
          const body = h.nextElementSibling
          body.style.display = body.style.display === 'none' ? '' : 'none'
          h.textContent = h.textContent.replace(
            body.style.display === 'none' ? '▾' : '▸',
            body.style.display === 'none' ? '▸' : '▾'
          )
        })
      })
    } catch (e) {
      hint.textContent = '⚠ error'
      out.innerHTML    = `<div class="dbg-err">${e.message}</div>`
    }
  })

  function renderTagInspector(d) {
    const containerKeys = [
      'ARTIST', 'ALBUM', 'DATE', 'CONCERTDATE',
      'CONCERTVENUE', 'CONCERTLOCATION', 'RECORDINGSOURCE', 'LINEAGE'
    ]

    let html = `<div class="dbg-sub-head">Expected (from DB)</div>
                <div class="dbg-tag-grid">`
    for (const key of containerKeys) {
      const val = d.db_tags?.[key]
      html += `<div class="dbg-tag-key">${esc(key)}</div>
               <div class="dbg-tag-val">${esc(val ?? '—')}</div>`
    }
    html += `</div>
             <div class="dbg-sub-head" style="margin-top:10px">
               Files (${d.files?.length ?? 0})
             </div>`

    for (const f of (d.files || [])) {
      const filename = f.filename.split('/').pop()
      html += `<div class="dbg-file-row">
        <div class="dbg-file-head">▸ ${esc(filename)}${f.error ? ' ⚠' : ''}</div>
        <div class="dbg-file-body" style="display:none">`

      if (f.error) {
        html += `<div class="dbg-err">${esc(f.error)}</div>`
      } else if (f.tags) {
        const dbTrack  = d.db_tracks?.[f.track_number] || {}
        const priority = ['TITLE', 'TRACKNUMBER', 'TRACKTOTAL']
        const allKeys  = [...new Set([...priority, ...Object.keys(f.tags).sort()])]

        html += '<div class="dbg-tag-grid">'
        for (const key of allKeys) {
          const fileVal = f.tags[key]
          const dbVal   = dbTrack[key] ?? d.db_tags?.[key]
          const match   = fileVal !== undefined && dbVal !== undefined
                          ? (fileVal === dbVal ? 'match' : 'mismatch')
                          : fileVal === undefined ? 'absent' : ''
          const dot = match === 'match'    ? '<span class="dbg-dot dbg-dot-ok">●</span>'
                    : match === 'mismatch' ? '<span class="dbg-dot dbg-dot-warn">●</span>'
                    : match === 'absent'   ? '<span class="dbg-dot dbg-dot-dim">○</span>'
                    : ''
          html += `<div class="dbg-tag-key">${dot}${esc(key)}</div>
                   <div class="dbg-tag-val ${match === 'mismatch' ? 'dbg-mismatch' : ''}">
                     ${esc(fileVal ?? '—')}
                   </div>`
        }
        html += '</div>'
      } else {
        html += '<div class="dbg-hint">No tags</div>'
      }

      html += '</div></div>'
    }

    return html
  }

  // ── API log ──────────────────────────────────────────────────────────────────
  function _refreshLog() {
    const el = document.getElementById('dbg-log-content')
    if (!el || panel.style.display === 'none') return
    el.innerHTML = apiLog.map(e => {
      const isErr = e.status === 'ERR' || (typeof e.status === 'number' && e.status >= 400)
      const cls   = isErr ? 'dbg-log-err' : e.status === '…' ? 'dbg-log-pending' : ''
      const t     = e.ts.toLocaleTimeString('en-US', { hour12: false })
      return `<div class="dbg-log-row ${cls}">
        <span class="dbg-log-method">${e.method}</span>
        <span class="dbg-log-status">${e.status}</span>
        <span class="dbg-log-url">${esc(e.url)}</span>
        <span class="dbg-log-ms">${e.ms}ms</span>
        <span class="dbg-log-ts">${t}</span>
      </div>`
    }).join('') || '<div class="dbg-hint">No calls yet</div>'
  }

  document.getElementById('dbg-btn-clear-log').addEventListener('click', () => {
    apiLog.length = 0
    _refreshLog()
  })

  // ── Pop-out window ───────────────────────────────────────────────────────────
  function popOut() {
    const url = `${location.origin}/static/debug-popup.html`
    if (window.pywebview?.api?.open_in_browser) {
      // PyWebView: open in the system default browser
      window.pywebview.api.open_in_browser(url)
    } else {
      // Regular browser: open new window
      const w = window.open(url, 'flux-debug', 'width=480,height=680,resizable=yes,scrollbars=yes')
      if (!w) alert('Pop-up blocked — allow pop-ups for localhost')
    }
  }

  // ── Public API ───────────────────────────────────────────────────────────────
  function attach(container) {
    if (!container) return
    if (!container.contains(panel)) {
      // Inject pop-out button into pane header
      const header = container.closest('.slide-pane')?.querySelector('.slide-pane-header')
      if (header && !header.querySelector('.dbg-popout-btn')) {
        const btn = document.createElement('button')
        btn.className   = 'dbg-popout-btn'
        btn.textContent = '⊞'
        btn.title       = 'Pop out debug panel'
        btn.addEventListener('click', popOut)
        header.appendChild(btn)
      }
      container.appendChild(panel)
    }
    panel.style.display = ''
    refreshState()
    refreshInfoCounts()
    _refreshLog()
    _refreshErrors()
  }

  function detach() {
    panel.style.display = 'none'
  }

  function refresh() {
    refreshState()
    refreshInfoCounts()
    _refreshLog()
    _refreshErrors()
  }

  window.fluxDebug = { attach, detach, refresh }

  // Backtick shortcut
  document.addEventListener('keydown', e => {
    if (e.key !== '`' || e.ctrlKey || e.metaKey || e.altKey) return
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return
    const debugTab = document.querySelector('.slide-tab[data-pane="debug"]')
    if (debugTab) debugTab.click()
  })

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function esc(s) {
    if (s == null) return ''
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  }

  console.log('[FluxDebug] ready — backtick or Debug tab to open')

})()
