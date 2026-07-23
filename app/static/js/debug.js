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
  // 2026-07-19: entries are now logged as PENDING the moment a fetch starts,
  // not only after it resolves — a request that's still in flight (a stuck
  // Batch Import "Review" scan, say) used to be completely invisible here;
  // now it shows up immediately with a live elapsed-time ticker until it
  // finishes or errors. Complements the server-side step log below (Live
  // Server Activity) which shows WHERE inside the request things are stuck.
  const MAX_LOG  = 50
  const MAX_ERR  = 20
  const apiLog   = []
  const errorLog = []
  const _origFetch = window.fetch
  let _pendingTicker = null

  function _startPendingTicker() {
    if (_pendingTicker) return
    _pendingTicker = setInterval(() => {
      if (!apiLog.some(e => e.status === '…')) { clearInterval(_pendingTicker); _pendingTicker = null; return }
      _refreshLog()
    }, 1000)
  }

  window.fetch = async function(input, init) {
    const url    = typeof input === 'string' ? input : input.url
    const method = (init?.method || 'GET').toUpperCase()
    const t0     = performance.now()

    const entry = { kind: 'api', method, url, status: '…', ms: null, ts: new Date(), _t0: t0 }
    apiLog.unshift(entry)
    if (apiLog.length > MAX_LOG) apiLog.pop()
    _refreshLog()
    _startPendingTicker()

    try {
      const res = await _origFetch(input, init)
      entry.status = res.status
      entry.ms     = Math.round(performance.now() - t0)
      _broadcast({ type: 'api', entry })
      _sendToServer(entry)
      _refreshLog()
      return res
    } catch (err) {
      entry.status = 'ERR'
      entry.ms     = Math.round(performance.now() - t0)
      entry.err    = err.message
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

  // ── Build panel DOM — a floating overlay anchored top-right of the window ─────
  const panel = document.createElement('div')
  panel.id        = 'dbg-panel'
  panel.className = 'dbg-overlay'
  panel.innerHTML = `
    <div class="dbg-overlay-head">
      <span class="dbg-overlay-title">Debug <span class="dbg-badge dbg-badge-dev">DEV</span></span>
      <span class="dbg-overlay-actions">
        <button class="dbg-popout-btn" id="dbg-popout" title="Pop out to a window">⊞</button>
        <button class="dbg-close-btn" id="dbg-close" title="Close (\`)">×</button>
      </span>
    </div>
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
      <div class="dbg-section-head" data-target="dbg-log">API Log ▾</div>
      <div class="dbg-section-body" id="dbg-log">
        <div class="dbg-log-toolbar">
          <button class="dbg-btn dbg-btn-sm" id="dbg-btn-clear-log">Clear</button>
        </div>
        <div id="dbg-log-content" class="dbg-log-list"></div>
      </div>
    </div>

    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-steps">
        Live Server Activity ▾ <span class="dbg-hint" style="font-weight:400; text-transform:none; letter-spacing:normal">— checkpoints logged from inside slow pipelines (scan, batch-scan); shows where a hang is actually stuck</span>
      </div>
      <div class="dbg-section-body" id="dbg-steps">
        <div id="dbg-steps-content" class="dbg-log-list"></div>
      </div>
    </div>

    <div class="dbg-section">
      <div class="dbg-section-head" data-target="dbg-paula">Paula ▸</div>
      <div class="dbg-section-body" id="dbg-paula" style="display:none">
        <div id="dbg-paula-content" class="dbg-kv">No scan yet</div>
      </div>
    </div>
  `
  // Floating overlay, hidden until toggled — lives at the top level of <body>.
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

  // ── Paula breakdown ──────────────────────────────────────────────────────
  // Full scan-time flag/component/subscore dump — this is the "ample debug
  // information" Ryan asked for so the scoring model can actually be
  // eyeballed against real folders, not just trusted blind. Reads
  // window.fluxState.paula, which app.js keeps pointed at ingest.scan.paula.
  function _fmtBool(b) { return b ? '✓' : '·' }

  function _paulaAttrRow(name, a) {
    if (!a) return ''
    const c = a.components || {}
    const compStr = Object.entries(c).map(([k, v]) => `${k}:${v}`).join(' ')
    return `<div class="dbg-row" style="flex-direction:column; align-items:flex-start; gap:2px; padding:4px 0; border-bottom:1px solid var(--bd-0)">
      <div style="display:flex; width:100%; justify-content:space-between">
        <span class="dbg-key" style="font-weight:600">${esc(name)}</span>
        <span class="dbg-val">${a.subscore} × ${a.weight} = ${a.points}</span>
      </div>
      <div style="font-size:10px; color:var(--t2)">
        tag: ${esc(a.tag_value ?? '—')} ${_fmtBool(a.tag_matched)}match &nbsp;|&nbsp;
        txt: ${esc(a.txt_value ?? '—')} ${_fmtBool(a.txt_matched)}match &nbsp;|&nbsp;
        agree: ${_fmtBool(a.agree)}
      </div>
      <div style="font-size:10px; color:var(--t3)">${esc(compStr)}</div>
    </div>`
  }

  function _paulaDateRow(a) {
    if (!a) return ''
    const c = a.components || {}
    const compStr = Object.entries(c).map(([k, v]) => `${k}:${v}`).join(' ')
    return `<div class="dbg-row" style="flex-direction:column; align-items:flex-start; gap:2px; padding:4px 0; border-bottom:1px solid var(--bd-0)">
      <div style="display:flex; width:100%; justify-content:space-between">
        <span class="dbg-key" style="font-weight:600">date</span>
        <span class="dbg-val">${a.subscore} × ${a.weight} = ${a.points}</span>
      </div>
      <div style="font-size:10px; color:var(--t2)">
        tag: ${esc(JSON.stringify(a.tag_date))} (prec ${a.tag_precision}) &nbsp;|&nbsp;
        txt: ${esc(JSON.stringify(a.txt_date))} (prec ${a.txt_precision}) &nbsp;|&nbsp;
        exact agree: ${_fmtBool(a.exact_agree)}
      </div>
      <div style="font-size:10px; color:var(--t3)">${esc(compStr)}</div>
    </div>`
  }

  function _refreshPaula() {
    const el = document.getElementById('dbg-paula-content')
    if (!el || panel.style.display === 'none') return
    const paula = window.fluxState?.paula
    if (!paula) { el.innerHTML = 'No scan yet'; return }

    const attrs = paula.attributes || {}
    const tc    = paula.track_completeness || {}
    const bd    = tc.breakdown || {}

    el.innerHTML = `
      <div class="dbg-row"><span class="dbg-key">Primary Attribute Score</span><span class="dbg-val">${paula.score}</span></div>
      ${_paulaAttrRow('performer', attrs.performer)}
      ${_paulaDateRow(attrs.date)}
      ${_paulaAttrRow('venue_name', attrs.venue_name)}
      ${_paulaAttrRow('city', attrs.city)}
      ${_paulaAttrRow('state', attrs.state)}
      ${_paulaAttrRow('country', attrs.country)}
      <div class="dbg-row" style="margin-top:6px"><span class="dbg-key">Track Completeness Score</span><span class="dbg-val">${tc.score ?? '—'}</span></div>
      <div style="font-size:10px; color:var(--t2)">
        confirmed:${bd.confirmed ?? 0} tag_only:${bd.tag_only ?? 0} txt_only:${bd.txt_only ?? 0}
        conflict:${bd.conflict ?? 0} missing:${bd.missing ?? 0}
      </div>
      ${(tc.tracks || []).map(t => `<div style="font-size:10px; color:var(--t3); padding:1px 0">
        #${t.index} [${esc(t.state)}] tag: ${esc(t.tag_title ?? '—')} / txt: ${esc(t.txt_title ?? '—')}
      </div>`).join('')}
    `
  }

  // FLAC tag inspector removed 2026-07-09 — the always-on "File Tags" pane in
  // the recording view replaces it (GET /api/recordings/<id>/tags).

  // ── API log ──────────────────────────────────────────────────────────────────
  function _refreshLog() {
    const el = document.getElementById('dbg-log-content')
    if (!el || panel.style.display === 'none') return
    el.innerHTML = apiLog.map(e => {
      const isErr     = e.status === 'ERR' || (typeof e.status === 'number' && e.status >= 400)
      const isPending = e.status === '…'
      const elapsed   = isPending ? Math.round((performance.now() - e._t0) / 1000) : null
      // A pending request past 8s gets flagged the same way a stuck server
      // step does — long enough to rule out normal request latency.
      const stale = isPending && elapsed >= 8
      const cls   = isErr ? 'dbg-log-err' : stale ? 'dbg-log-stale' : isPending ? 'dbg-log-pending' : ''
      const t     = e.ts.toLocaleTimeString('en-US', { hour12: false })
      const msLabel = isPending ? `${elapsed}s…` : `${e.ms}ms`
      return `<div class="dbg-log-row ${cls}">
        <span class="dbg-log-method">${e.method}</span>
        <span class="dbg-log-status">${e.status}</span>
        <span class="dbg-log-url">${esc(e.url)}</span>
        <span class="dbg-log-ms">${msLabel}</span>
        <span class="dbg-log-ts">${t}</span>
      </div>`
    }).join('') || '<div class="dbg-hint">No calls yet</div>'
  }

  document.getElementById('dbg-btn-clear-log').addEventListener('click', () => {
    apiLog.length = 0
    _refreshLog()
  })

  // ── Live Server Activity — polls /api/debug/live for server-originated ──────
  // "step" checkpoints (2026-07-19, see utils/debug_log.py::log_step). Only
  // polls while the panel is open. Requires threaded Flask (run.py) — a
  // single-threaded server blocked on the very request being investigated
  // can't answer this poll either, which is the whole reason that fix came
  // first. Jobs (grouped by folder path / batch id) whose latest step isn't
  // "done" and hasn't updated in a while are flagged stale — that's the
  // actual "where is it stuck" answer for a hung Batch Import scan.
  const MAX_STEPS       = 150
  const STALE_AFTER_SEC = 5
  let liveSteps         = []
  let _stepsPollTimer   = null

  function _refreshSteps() {
    const el = document.getElementById('dbg-steps-content')
    if (!el || panel.style.display === 'none') return
    if (!liveSteps.length) { el.innerHTML = '<div class="dbg-hint">No server activity logged yet</div>'; return }

    const nowSec = Date.now() / 1000
    const seenJob = new Set()
    el.innerHTML = liveSteps.map(s => {
      const isLatestForJob = !seenJob.has(s.job)
      seenJob.add(s.job)
      const age   = nowSec - s.ts
      const stale = isLatestForJob && s.stage !== 'done' && age > STALE_AFTER_SEC
      const t     = new Date(s.ts * 1000).toLocaleTimeString('en-US', { hour12: false })
      return `<div class="dbg-log-row ${stale ? 'dbg-log-stale' : ''}">
        <span class="dbg-log-method" style="min-width:0">${esc(s.stage || '')}</span>
        <span class="dbg-log-status" style="min-width:0"></span>
        <span class="dbg-log-url">${esc(s.job || '')}${s.detail ? ' — ' + esc(s.detail) : ''}</span>
        <span class="dbg-log-ms">${stale ? `${Math.round(age)}s ago` : ''}</span>
        <span class="dbg-log-ts">${t}</span>
      </div>`
    }).join('')
  }

  async function _pollSteps() {
    try {
      const r = await _origFetch('/api/debug/live', { credentials: 'same-origin' })
      if (!r.ok) return
      const all = await r.json()
      liveSteps = all.filter(e => e.kind === 'step').slice(0, MAX_STEPS)
      _refreshSteps()
    } catch {}
  }

  function _startStepsPolling() {
    if (_stepsPollTimer) return
    _pollSteps()
    _stepsPollTimer = setInterval(_pollSteps, 750)
  }
  function _stopStepsPolling() {
    clearInterval(_stepsPollTimer); _stepsPollTimer = null
  }

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

  document.getElementById('dbg-popout').addEventListener('click', popOut)
  document.getElementById('dbg-close').addEventListener('click', () => hide())

  // ── Public API — floating overlay (top-right of the window) ──────────────────
  function show() {
    panel.style.display = ''
    refreshState(); refreshInfoCounts(); _refreshLog(); _refreshErrors(); _refreshPaula()
    _startStepsPolling()
  }
  function hide()   { panel.style.display = 'none'; _stopStepsPolling() }
  function toggle() { panel.style.display === 'none' ? show() : hide() }
  function refresh() {
    refreshState(); refreshInfoCounts(); _refreshLog(); _refreshErrors(); _refreshPaula()
  }

  // attach/detach kept as aliases so any older callers still work.
  window.fluxDebug = { show, hide, toggle, refresh, attach: show, detach: hide }

  // Backtick shortcut toggles the overlay from anywhere.
  document.addEventListener('keydown', e => {
    if (e.key !== '`' || e.ctrlKey || e.metaKey || e.altKey) return
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return
    toggle()
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
