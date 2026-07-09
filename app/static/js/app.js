/**
 * app.js — Flux Audio SPA: router, state, and view renderers.
 *
 * Hash-based routing:
 *   #/                  → catalog (artist selected or empty state)
 *   #/artist/:id        → artist recordings list
 *   #/recording/:id     → recording detail (tracks + info file)
 *   #/ingest            → ingest wizard (stub for MVP)
 */

const App = (() => {

  // ── State ──────────────────────────────────────────────────────────────────
  const state = {
    user:            null,
    artists:         [],
    selectedArtist:  null,   // { id, name, ... }
    currentRecId:    null,   // recording id currently in detail view
    playingTrackId:  null,   // track id currently in player
    skipNonMusic:    false,  // filter announcements/banter/tuning from queue
  }

  // Flags treated as "non-music" for the skip filter
  const NON_MUSIC_FLAGS = ['announcement', 'banter', 'tuning', 'audience', 'interview', 'introduction', 'band_intros']

  /** Apply/remove the skip-filter visual state to all track rows in the current view. */
  function applySkipFilter() {
    document.querySelectorAll('.track-row[data-flags]').forEach(row => {
      const flags = (row.dataset.flags || '').split(',').filter(Boolean)
      const isNonMusic = flags.some(f => NON_MUSIC_FLAGS.includes(f))
      row.classList.toggle('track-row--skipped', state.skipNonMusic && isNonMusic)
    })
  }

  /** Single source of truth for toggling the filter — syncs all UIs. */
  function setSkipFilter(v) {
    state.skipNonMusic = v
    document.querySelectorAll('.skip-filter-cb').forEach(cb => { cb.checked = v })
    applySkipFilter()
  }

  // ── Waveform RAF loop ─────────────────────────────────────────────────────
  // Cancelled whenever we navigate away from the recording view.
  let _waveformRAF     = null   // requestAnimationFrame handle
  let _waveformMap     = {}     // trackId → Float32Array of 300 normalised RMS values
  let _waveformTrackId = null   // which track is currently displayed
  let _waveformBg      = null   // offscreen ImageData — waveform without playhead

  function _cancelWaveform() {
    if (_waveformRAF) { cancelAnimationFrame(_waveformRAF); _waveformRAF = null }
    _waveformBg      = null
    _waveformTrackId = null
  }

  function _drawWaveformBg(canvas, waveform) {
    /** Paint the static waveform (no playhead) onto canvas, cache as ImageData. */
    const W = canvas.width, H = canvas.height
    const ctx = canvas.getContext('2d')

    // Background gradient — dark navy, like a DAW
    const bg = ctx.createLinearGradient(0, 0, 0, H)
    bg.addColorStop(0, '#0b0e14')
    bg.addColorStop(1, '#080b10')
    ctx.fillStyle = bg
    ctx.fillRect(0, 0, W, H)

    // Horizontal centre line
    const mid = Math.floor(H / 2)
    ctx.strokeStyle = 'rgba(80,180,160,0.15)'
    ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(0, mid + 0.5); ctx.lineTo(W, mid + 0.5); ctx.stroke()

    // Subtle vertical time grid every ~10%
    ctx.strokeStyle = 'rgba(80,180,160,0.07)'
    for (let g = 1; g < 10; g++) {
      const x = Math.round(g * W / 10) + 0.5
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
    }

    if (!waveform || !waveform.length) {
      _waveformBg = ctx.getImageData(0, 0, W, H)
      return
    }

    const n = waveform.length

    // Style B — thin outline only, no fill
    // Top edge
    ctx.beginPath()
    ctx.strokeStyle = 'rgba(0,210,185,0.75)'
    ctx.lineWidth   = 1.5
    ctx.lineJoin    = 'round'
    for (let i = 0; i < n; i++) {
      const x   = (i / (n - 1)) * W
      const amp = waveform[i] * (mid - 4)
      if (i === 0) ctx.moveTo(x, mid - amp); else ctx.lineTo(x, mid - amp)
    }
    ctx.stroke()

    // Bottom edge (mirror)
    ctx.beginPath()
    ctx.strokeStyle = 'rgba(0,210,185,0.35)'
    ctx.lineWidth   = 1
    for (let i = 0; i < n; i++) {
      const x   = (i / (n - 1)) * W
      const amp = waveform[i] * (mid - 4)
      if (i === 0) ctx.moveTo(x, mid + amp); else ctx.lineTo(x, mid + amp)
    }
    ctx.stroke()

    _waveformBg = ctx.getImageData(0, 0, W, H)
  }

  function _drawPlayhead(canvas, pct) {
    if (!_waveformBg) return
    const W = canvas.width, H = canvas.height
    const ctx = canvas.getContext('2d')
    ctx.putImageData(_waveformBg, 0, 0)

    const x = Math.round(pct * W)

    // Dim overlay to the left of playhead — "played" region
    ctx.fillStyle = 'rgba(0,0,0,0.28)'
    ctx.fillRect(0, 0, x, H)

    // Playhead line
    ctx.strokeStyle = 'rgba(255,220,80,0.95)'
    ctx.lineWidth   = 1.5
    ctx.shadowColor = 'rgba(255,220,80,0.7)'
    ctx.shadowBlur  = 6
    ctx.beginPath()
    ctx.moveTo(x + 0.5, 0)
    ctx.lineTo(x + 0.5, H)
    ctx.stroke()
    ctx.shadowBlur = 0
  }

  function _startWaveformLoop(canvas, trackId) {
    _cancelWaveform()
    _waveformTrackId = trackId
    const waveform = _waveformMap[trackId] || null
    const dpr = window.devicePixelRatio || 1
    const cssW = canvas.offsetWidth  || 800
    const cssH = canvas.offsetHeight || 80
    canvas.width  = Math.round(cssW * dpr)
    canvas.height = Math.round(cssH * dpr)
    _drawWaveformBg(canvas, waveform)

    const audio = document.getElementById('audio-el')

    function tick() {
      const isThisTrack = Player.currentId() === trackId
      const pct = (isThisTrack && audio && audio.duration)
        ? audio.currentTime / audio.duration
        : 0
      _drawPlayhead(canvas, pct)
      _waveformRAF = requestAnimationFrame(tick)
    }
    tick()
  }

  // Ingest wizard state — persists across step renders
  const ingest = {
    step:       'folder',  // 'folder' | 'review' | 'tracks' | 'confirm'
    folderPath: null,
    scan:       null,      // full scan API response
    behavior:   'move',    // 'copy' | 'move'
    form: {},              // resolved metadata (populated on review step)
    tracks:     [],        // array of { track_number, title, set, duration, filename }
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const loginScreen = document.getElementById('login-screen')
  const appShell    = document.getElementById('app-shell')
  const artistList  = document.getElementById('artist-list')
  const mainContent = document.getElementById('main-content')
  const userAvatar  = document.getElementById('user-avatar')
  const userName    = document.getElementById('user-name')
  const navLibrary       = document.getElementById('nav-library')
  const navIncoming      = document.getElementById('nav-incoming')
  const navBatch         = document.getElementById('nav-batch')
  const navIngest        = document.getElementById('nav-ingest')
  const navVenues        = document.getElementById('nav-venues')
  const navArtistsIndex  = document.getElementById('nav-artists-index')

  // ── Theme toggle ───────────────────────────────────────────────────────────
  ;(function () {
    const btn = document.getElementById('theme-btn')
    if (!btn) return
    btn.addEventListener('click', () => {
      const isLight = document.body.classList.toggle('theme-light')
      localStorage.setItem('fluxTheme', isLight ? 'light' : 'dark')
    })
  })()

  // ── Utilities ──────────────────────────────────────────────────────────────

  function fmtDate(year, month, day) {
    if (!year) return 'Unknown date'
    if (month && day) return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`
    if (month) return `${year}-${String(month).padStart(2,'0')}`
    return String(year)
  }

  function fmtDateLong(year, month, day) {
    if (!year) return 'Unknown date'
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    if (month && day) return `${MONTHS[month-1]} ${day}, ${year}`
    if (month) return `${MONTHS[month-1]} ${year}`
    return String(year)
  }

  function fmtLocation(city, state, country) {
    if (city && state)   return `${city}, ${state}`
    if (city && country) return `${city}, ${country}`
    return city || state || country || ''
  }

  function fmtDuration(secs) {
    if (!secs) return '—'
    const m = Math.floor(secs / 60)
    const s = Math.floor(secs % 60)
    return `${m}:${s.toString().padStart(2,'0')}`
  }

  function sourceBadge(source) {
    if (!source) return ''
    const cls = ['SBD','AUD','MTX','FM'].includes(source) ? `badge-${source}` : 'badge-src'
    return `<span class="badge ${cls}">${escHtml(source)}</span>`
  }

  function qualityClass(q) {
    if (!q) return ''
    const first = q[0].toUpperCase()
    if (first === 'A') return q.includes('+') ? 'quality-Ap' : q.includes('-') ? 'quality-Am' : 'quality-A'
    if (first === 'B') return q.includes('+') ? 'quality-Bp' : 'quality-B'
    if (first === 'C') return 'quality-C'
    return ''
  }

  function escHtml(s) {
    if (s == null) return ''
    return String(s)
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function esc(s) { return escHtml(s) }

  // Title-case a string: capitalize each word, lowercase the rest.
  // Keeps short connective words lowercase unless they're the first word.
  const _lcWords = new Set(['a','an','the','and','but','or','for','nor','on','at',
                             'to','by','in','of','up','as','is','with','vs','feat'])
  function titleCase(s) {
    if (!s) return s
    return s.split(' ').map((w, i) => {
      if (!w) return w
      const lo = w.toLowerCase()
      return (i === 0 || !_lcWords.has(lo))
        ? lo.charAt(0).toUpperCase() + lo.slice(1)
        : lo
    }).join(' ')
  }

  // ── Track flag auto-detection ─────────────────────────────────────────────
  // JS port of app/utils/ingest.py::detect_track_flags — kept deliberately
  // conservative. Words like "talk"/"speak"/"crowd" also show up in real
  // song titles ("Don't Talk", "Speak Low"), so ambiguous flags only fire on
  // a whole-segment match, never a loose substring. These are suggestions
  // pre-checked in the ingest wizard for the archivist to approve or remove
  // — never applied silently.
  const _FLAG_START_TRUNC = /^\s*\/\//
  const _FLAG_END_TRUNC   = /\/\/\s*$/
  const _FLAG_INCOMPLETE  = /\(\s*x\s*\)\s*$/i
  const _FLAG_TRAILING_PAREN = /^(.*?)\s*\([^)]*\)\s*$/
  const _FLAG_SEGMENT_SPLIT  = /\s*(?:,|\/|&|\band\b)\s*/i
  const _FLAG_SEGMENT_PATTERNS = [
    ['tuning',       /^tunings?$/i],
    ['banter',       /^(banter|dialogue)s?$/i],
    ['audience',     /^(audience|crowd)s?$/i],
    ['band_intros',  /^band intro(duction)?s?$/i],
    ['introduction', /^intro(duction)?s?\.?$/i],
  ]
  const _FLAG_WORD_PATTERNS = [
    ['announcement', /\bannouncements?\b/i],
    ['interview',    /\binterviews?\b/i],
  ]

  function detectTrackFlags(title) {
    if (!title) return []
    const flags = new Set()
    const raw = title.trim()

    if (_FLAG_START_TRUNC.test(raw)) flags.add('start_truncated')
    if (_FLAG_END_TRUNC.test(raw))   flags.add('end_truncated')
    if (_FLAG_INCOMPLETE.test(raw))  flags.add('incomplete')

    const parenMatch = raw.match(_FLAG_TRAILING_PAREN)
    const base = parenMatch ? parenMatch[1].trim() : raw

    base.split(_FLAG_SEGMENT_SPLIT).forEach(segment => {
      segment = segment.trim()
      if (!segment) return
      _FLAG_SEGMENT_PATTERNS.forEach(([key, pattern]) => {
        if (pattern.test(segment)) flags.add(key)
      })
    })

    _FLAG_WORD_PATTERNS.forEach(([key, pattern]) => {
      if (pattern.test(base)) flags.add(key)
    })

    return [...flags].sort()
  }

  // True for roles that may edit library metadata (admin/archivist).
  // Listener is read-only. Doesn't yet distinguish an archivist's specific
  // artist permissions (all_artists / user_artist_permission) — the frontend
  // has no per-artist gating anywhere else either, so this matches the
  // existing (coarser) enforcement level rather than building that out here.
  function canEditLibrary() {
    const role = state.user?.role
    return role === 'admin' || role === 'archivist'
  }

  function setMainHTML(html) {
    _cancelWaveform()   // stop any running waveform RAF before replacing DOM
    mainContent.innerHTML = html
  }

  function setLoading() {
    mainContent.innerHTML = `
      <div class="empty-state">
        <div class="loading-spinner"></div>
      </div>`
  }

  // ── Resizable split panel ──────────────────────────────────────────────────

  let _resizeCleanup = null

  function wireResizablePanel(shellEl, leftEl, handleEl, minLeft = 200, minRight = 200) {
    // Remove any previous listeners to avoid stacking on re-renders
    if (_resizeCleanup) { _resizeCleanup(); _resizeCleanup = null }
    if (!shellEl || !leftEl || !handleEl) return

    let dragging = false, startX = 0, startWidth = 0

    const onDown = e => {
      dragging   = true
      startX     = e.clientX
      startWidth = leftEl.offsetWidth
      document.body.style.cursor    = 'col-resize'
      document.body.style.userSelect = 'none'
      e.preventDefault()
    }

    const onMove = e => {
      if (!dragging) return
      const max  = shellEl.offsetWidth - minRight - handleEl.offsetWidth
      const newW = Math.max(minLeft, Math.min(startWidth + (e.clientX - startX), max))
      leftEl.style.width     = newW + 'px'
      leftEl.style.flexBasis = newW + 'px'
    }

    const onUp = () => {
      if (!dragging) return
      dragging = false
      document.body.style.cursor    = ''
      document.body.style.userSelect = ''
    }

    handleEl.addEventListener('mousedown', onDown)
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup',   onUp)

    _resizeCleanup = () => {
      handleEl.removeEventListener('mousedown', onDown)
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup',   onUp)
    }
  }

  // ── Nav helpers ────────────────────────────────────────────────────────────

  function setActiveNav(active) {
    navLibrary.classList.toggle('active',           active === 'library')
    navIncoming?.classList.toggle('active',          active === 'incoming')
    navBatch?.classList.toggle('active',             active === 'batch')
    navIngest.classList.toggle('active',            active === 'ingest')
    navVenues?.classList.toggle('active',           active === 'venues')
    navArtistsIndex?.classList.toggle('active',     active === 'artists-index')
  }

  function setActiveArtist(id) {
    document.querySelectorAll('.artist-item').forEach(el => {
      el.classList.toggle('active', parseInt(el.dataset.artistId) === id)
    })
  }

  // ── Artist sidebar ─────────────────────────────────────────────────────────

  // Which canonical artists are expanded to show sub-artists — persists across
  // re-renders (batch ingest, admin edits) for the duration of the session.
  state.expandedArtists = state.expandedArtists || new Set()

  async function loadArtistList() {
    try {
      state.artists = await API.artists.list()
    } catch (e) {
      artistList.innerHTML = `<div style="padding:12px 16px; color:var(--t2); font-size:12px;">Failed to load</div>`
      return
    }

    if (!state.artists.length) {
      artistList.innerHTML = `<div style="padding:12px 16px; color:var(--t2); font-size:12px;">No artists yet</div>`
      return
    }

    artistList.innerHTML = state.artists.map(a => {
      const subArtists = a.sub_artists || []
      const hasSubs    = subArtists.length > 0
      const expanded   = hasSubs && state.expandedArtists.has(a.id)
      const caret      = hasSubs
        ? `<span class="artist-expand-caret ${expanded ? 'expanded' : ''}" data-artist-id="${a.id}">▸</span>`
        : `<span class="artist-expand-caret artist-expand-caret--spacer"></span>`
      const subRows = expanded
        ? subArtists.map(name => `
            <div class="artist-item artist-subitem" data-artist-id="${a.id}">
              <span class="artist-expand-caret artist-expand-caret--spacer"></span>
              <span class="artist-name truncate">${esc(name)}</span>
            </div>`).join('')
        : ''
      return `
        <div class="artist-item" data-artist-id="${a.id}">
          ${caret}
          <span class="artist-name truncate">${esc(a.name)}</span>
          <span class="artist-count">${a.recording_count || ''}</span>
        </div>
        ${subRows}`
    }).join('')

    // Expand/collapse caret — toggles without navigating
    artistList.querySelectorAll('.artist-expand-caret:not(.artist-expand-caret--spacer)').forEach(el => {
      el.addEventListener('click', (ev) => {
        ev.stopPropagation()
        const id = parseInt(el.dataset.artistId)
        if (state.expandedArtists.has(id)) state.expandedArtists.delete(id)
        else state.expandedArtists.add(id)
        loadArtistList()
      })
    })

    // Root and sub-artist rows both navigate to the canonical artist's
    // catalog page — that page already aggregates recordings from every
    // linked performer, sub-artists included.
    artistList.querySelectorAll('.artist-item').forEach(el => {
      el.addEventListener('click', () => {
        const id = parseInt(el.dataset.artistId)
        window.location.hash = `#/artist/${id}`
      })
    })
  }

  // ── Views ──────────────────────────────────────────────────────────────────

  /** Default library view — all artists, all shows, alpha → oldest first */
  async function renderLibraryView() {
    setActiveNav('library')
    setActiveArtist(null)
    state.selectedArtist = null
    setLoading()

    let allArtists
    try {
      allArtists = await API.artists.allRecordings()
    } catch (e) {
      setMainHTML(`<div class="empty-state"><div class="empty-title">Failed to load library</div></div>`)
      return
    }

    if (!allArtists.length) {
      setMainHTML(`
        <div class="empty-state">
          <div class="empty-icon" style="color:var(--t2)">◈</div>
          <div class="empty-title">No recordings yet</div>
          <div class="empty-sub">Add a recording to get started</div>
        </div>`)
      return
    }

    const totalRecordings = allArtists.reduce((n, a) => n + a.recording_count, 0)
    const totalPerfs      = allArtists.reduce((n, a) => n + a.performance_count, 0)

    const artistBlocks = allArtists.map(artist => {
      const perfRows = artist.performances.map(p => {
        const loc   = fmtLocation(p.city, p.state, p.country)
        const date  = fmtDate(p.start_year, p.start_month, p.start_day)
        const venue = p.venue_name || ''
        const title = p.title ? `<em>${esc(p.title)}</em> · ` : ''

        return p.recordings.map((r, ri) => {
          const modifier   = r.source_modifier ? ` · ${esc(r.source_modifier)}` : ''
          const quality    = r.quality || ''
          return `
            <div class="rec-row" data-rec-id="${r.id}" data-perf-id="${p.performance_id}">
              <span class="rec-date truncate">${ri === 0 ? esc(date) : ''}</span>
              <span class="rec-venue truncate">${ri === 0 ? title + esc(venue || '(unknown venue)') : ''}</span>
              <span class="rec-location truncate">${ri === 0 ? esc(loc) : ''}</span>
              <span>${sourceBadge(r.source)}</span>
              <span class="quality ${qualityClass(quality)}">${esc(quality)}</span>
              <span class="rec-tracks">${r.track_count}t</span>
              <button class="rec-play-btn" data-rec-id="${r.id}" title="Play">▶</button>
            </div>`
        }).join('')
      }).join('')

      return `
        <div class="year-group">
          <div class="year-divider lib-artist-divider" data-artist-id="${artist.artist_id}">
            ${esc(artist.artist_name)}
            <span class="year-divider-count">${artist.recording_count} recording${artist.recording_count !== 1 ? 's' : ''}</span>
          </div>
          ${perfRows}
        </div>`
    }).join('')

    setMainHTML(`
      <div class="artist-header">
        <h1>Library</h1>
        <div class="subtitle">${totalRecordings} recording${totalRecordings !== 1 ? 's' : ''} · ${totalPerfs} performance${totalPerfs !== 1 ? 's' : ''} · ${allArtists.length} artist${allArtists.length !== 1 ? 's' : ''}</div>
      </div>
      ${artistBlocks}
    `)

    // Row clicks → recording detail
    mainContent.querySelectorAll('.rec-row').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('.rec-play-btn')) return
        window.location.hash = `#/recording/${el.dataset.recId}`
      })
    })

    // Play buttons
    mainContent.querySelectorAll('.rec-play-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation()
        const recId = parseInt(btn.dataset.recId)
        playRecording(recId, 0, null)
      })
    })

    // Artist header click → filter to that artist
    mainContent.querySelectorAll('.lib-artist-divider').forEach(el => {
      el.addEventListener('click', () => {
        const id = parseInt(el.dataset.artistId)
        if (id) window.location.hash = `#/artist/${id}`
      })
    })
  }

  /** Artist recordings — the main catalog browser */
  async function renderArtistView(artistId) {
    setActiveNav('library')
    setActiveArtist(artistId)
    setLoading()

    let artist, performances
    try {
      [artist, performances] = await Promise.all([
        API.artists.get(artistId),
        API.artists.recordings(artistId),
      ])
    } catch (e) {
      setMainHTML(`<div class="empty-state"><div class="empty-title">Failed to load</div></div>`)
      return
    }

    state.selectedArtist = artist

    const totalRecordings = performances.reduce((n, p) => n + p.recordings.length, 0)

    // Group performances by year
    const byYear = {}
    performances.forEach(p => {
      const yr = p.start_year || 'Unknown'
      ;(byYear[yr] = byYear[yr] || []).push(p)
    })

    const yearKeys = Object.keys(byYear).sort((a, b) => b - a)

    const rows = yearKeys.map(yr => {
      const perfs = byYear[yr]
      const perfRows = perfs.map(p => {
        const loc    = fmtLocation(p.city, p.state, p.country)
        const date   = fmtDate(p.start_year, p.start_month, p.start_day)
        const venue  = p.venue_name || ''
        const title  = p.title ? `<em>${esc(p.title)}</em> · ` : ''

        // Each performance row, then child rows per recording
        return p.recordings.map((r, ri) => {
          const modifier = r.source_modifier ? ` · ${esc(r.source_modifier)}` : ''
          const quality  = r.quality || ''
          const qcls     = qualityClass(quality)
          return `
            <div class="rec-row" data-rec-id="${r.id}" data-perf-id="${p.performance_id}">
              <span class="rec-date truncate">${ri === 0 ? esc(date) : ''}</span>
              <span class="rec-venue truncate">${ri === 0 ? title + esc(venue || '(unknown venue)') : ''}</span>
              <span class="rec-location truncate">${ri === 0 ? esc(loc) : ''}</span>
              <span>${sourceBadge(r.source)}</span>
              <span class="quality ${qcls}">${esc(quality)}</span>
              <span class="rec-tracks">${r.track_count}t</span>
              <button class="rec-play-btn" data-rec-id="${r.id}" title="Play first track">▶</button>
            </div>`
        }).join('')
      }).join('')

      return `
        <div class="year-group">
          <div class="year-divider">${yr}</div>
          ${perfRows}
        </div>`
    }).join('')

    setMainHTML(`
      <div class="artist-header">
        <h1>${esc(artist.name)}</h1>
        <div class="subtitle">${totalRecordings} recording${totalRecordings !== 1 ? 's' : ''} · ${performances.length} performance${performances.length !== 1 ? 's' : ''}</div>
      </div>
      ${rows || '<div class="empty-state" style="min-height:200px"><div class="empty-title">No recordings yet</div></div>'}
    `)

    // Wire up recording row clicks → detail view
    mainContent.querySelectorAll('.rec-row').forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.rec-play-btn')) return
        window.location.hash = `#/recording/${el.dataset.recId}`
      })
    })

    // Wire up play buttons → load queue for that recording
    mainContent.querySelectorAll('.rec-play-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        const recId = parseInt(btn.dataset.recId)
        playRecording(recId, 0)
      })
    })
  }

  /** Recording detail — split panel: tracks + info file */
  async function renderRecordingView(recordingId) {
    setActiveNav('library')
    setLoading()
    state.currentRecId      = recordingId
    state._lastTrackCount   = null   // reset until rec loads

    let rec
    try {
      rec = await API.recordings.get(recordingId)
      state._lastTrackCount = rec.tracks?.length ?? null
    } catch (e) {
      setMainHTML(`<div class="empty-state"><div class="empty-title">Recording not found</div></div>`)
      return
    }

    // Determine back label from current artist
    const backLabel = state.selectedArtist ? `← ${esc(state.selectedArtist.name)}` : '← Back'
    const backHash  = state.selectedArtist ? `#/artist/${state.selectedArtist.id}` : '#/'

    // We need performance info to show the date/venue
    let perf = null
    try { perf = await API.performances.get(rec.performance_id) } catch (_) {}

    const dateStr    = perf ? fmtDateLong(perf.start_year, perf.start_month, perf.start_day) : ''
    const venueStr   = perf?.venue_name || ''
    const venueId    = perf?.venue_id   || null
    const locStr     = perf ? fmtLocation(perf.city, perf.state, perf.country) : ''
    const perfName   = perf?.performer || ''
    const modifier   = rec.source_modifier
      ? `<span class="badge-modifier">${esc(rec.source_modifier)}</span>` : ''

    // Date line — venue is a clickable link if we have a venue_id
    const venueHtml  = venueId
      ? `<span class="venue-link" data-venue-id="${venueId}">${esc(venueStr)}</span>`
      : (venueStr ? esc(venueStr) : '')
    const dateLineParts = [dateStr ? esc(dateStr) : '', venueHtml, locStr ? esc(locStr) : ''].filter(Boolean)
    const dateLineHtml  = dateLineParts.join(' · ')

    // Staged changes: metadata_updated events after the last tags_written
    // events array is ascending by created_at (oldest first)
    const events = rec.events || []
    let lastWritePos = -1
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].event_type === 'tags_written') { lastWritePos = i; break }
    }
    const stagedCount = events
      .slice(lastWritePos + 1)   // everything after the last write (or all if never written)
      .filter(e => e.event_type === 'metadata_updated')
      .length

    // Flat track list — no disc/set grouping
    const trackRows = (() => {
      const tracks = rec.tracks || []
      const tRows = tracks.map(t => {
        const isPlaying  = t.id === state.playingTrackId
        const playingCls = isPlaying ? ' playing' : ''
        const playIcon   = isPlaying ? '▶' : '▷'
        // Flag chips
        const FLAG_LABELS = {
          start_truncated: 'Start Trunc', end_truncated: 'End Trunc',
          incomplete: 'Incomplete', banter: 'Banter', medley: 'Medley',
          announcement: 'Announcement', tuning: 'Tuning',
          audience: 'Audience', unknown_title: 'Unknown Title',
          interview: 'Interview', introduction: 'Introduction',
          band_intros: 'Band Intros',
        }
        const flagChips = (t.flags || []).map(f =>
          `<span class="track-flag-chip">${FLAG_LABELS[f] || f}</span>`
        ).join('')
        const officialBadge = t.is_official
          ? `<span class="track-official-badge" title="Officially released">©</span>` : ''

        // Note preview — faint, same weight as the track number, sits after
        // the flag chips on the title line. Editable inline via the pencil
        // icon (admin/archivist only); empty span still rendered so the
        // click handler has somewhere to swap in the input.
        const noteText = t.notes
          ? `<span class="track-note-inline truncate" title="${esc(t.notes)}">${esc(t.notes)}</span>`
          : ''
        const noteDisplay = `<span class="track-note-display" data-track-id="${t.id}">${noteText}</span>`
        const noteEditBtn = canEditLibrary()
          ? `<button class="track-note-edit-btn" data-track-id="${t.id}" type="button" title="${t.notes ? 'Edit note' : 'Add note'}">✎</button>`
          : ''

        const songwriterInline = t.songwriter
          ? `<span class="track-songwriter-inline truncate">${esc(t.songwriter)}</span>` : ''

        return `
          <div class="track-row${playingCls}" data-track-id="${t.id}" data-flags="${(t.flags||[]).join(',')}">
            <span class="track-play">${playIcon}</span>
            <span class="track-num">${String(t.track_number || '').padStart(2,'0')}</span>
            <span class="track-title-wrap">
              <span class="track-title truncate">${esc(t.title)}${officialBadge}${flagChips ? ' ' + flagChips : ''}${noteText ? ' ' + noteDisplay : noteDisplay}${noteEditBtn}</span>
            </span>
            <span class="track-meta-right">
              ${songwriterInline}
              <span class="track-dur">${fmtDuration(t.duration)}</span>
            </span>
          </div>`
      }).join('')
      return tRows
    })()

    const infoContent = rec.info_file_content
      ? `<pre class="info-file-content">${esc(rec.info_file_content)}</pre>`
      : `<div class="info-panel-empty">No info file attached</div>`

    // Build per-track waveform map for the RAF loop
    _waveformMap = {}
    ;(rec.tracks || []).forEach(t => {
      if (t.analysis?.waveform?.length) _waveformMap[t.id] = t.analysis.waveform
    })
    const hasAnalysis = Object.keys(_waveformMap).length > 0

    // Fidelity metrics: pull from first analysed track
    const firstAnalysed = rec.tracks?.find(t => t.analysis) ?? null
    const rmsDb    = firstAnalysed?.analysis?.rms_db
    const dynDb    = firstAnalysed?.analysis?.dynamic_range_db
    const srHz     = firstAnalysed?.analysis?.sample_rate_hz
    const bitDepth = firstAnalysed?.analysis?.bit_depth
    const bitrateK = firstAnalysed?.analysis?.bitrate_kbps
    const cutoffHz = firstAnalysed?.analysis?.spectral_cutoff_hz
    const fmtDb  = v => (v != null ? `${v.toFixed(1)} dB` : '—')
    const fmtSr  = hz => hz ? `${(hz / 1000).toFixed(1).replace(/\.0$/, '')} kHz` : '—'
    const fmtBit = bd => bd ? `${bd}-bit` : (bitrateK ? `${bitrateK} kbps` : '—')

    // Flag likely transcodes: cutoff more than 2 kHz below Nyquist
    const nyquist         = srHz ? srHz / 2 : 22050
    const looksTranscoded = cutoffHz && cutoffHz < (nyquist - 2000)
    const fmtCutoff = hz => {
      if (!hz) return '—'
      const khz = `${(hz / 1000).toFixed(1)} kHz`
      return looksTranscoded ? `${khz} ⚠` : khz
    }

    // ── Interpretive hints ────────────────────────────────────────────────────
    const hint = s => `<span class="hm-hint">${s}</span>`

    const formatLabel = (() => {
      if (bitrateK && !bitDepth) return hint('Lossy')
      if (!bitDepth) return ''
      if (srHz >= 88200)                       return hint('Hi-Res')
      if (bitDepth >= 24)                      return hint('Studio')
      if (bitDepth <= 16 && srHz <= 44100)     return hint('CD Quality')
      return hint('Lossless')
    })()

    // RMS: live recordings typically sit -20 to -12 dB
    const rmsHint = (() => {
      if (rmsDb == null) return ''
      if (rmsDb > -6)    return hint('Very hot')
      if (rmsDb > -10)   return hint('Hot — compressed')
      if (rmsDb > -14)   return hint('Loud')
      if (rmsDb > -20)   return hint('Normal')
      if (rmsDb > -28)   return hint('Quiet')
      return hint('Very quiet')
    })()

    // Dynamic range: higher = more natural dynamics
    const dynHint = (() => {
      if (dynDb == null) return ''
      if (dynDb > 40)    return hint('Excellent')
      if (dynDb > 25)    return hint('Good')
      if (dynDb > 15)    return hint('Moderate')
      if (dynDb > 8)     return hint('Compressed')
      return hint('Heavily limited')
    })()

    const cutoffHint = looksTranscoded ? hint('Possible transcode') : (cutoffHz ? hint('Full spectrum') : '')

    // Right panel: collapsible sections — Recording info + Fidelity metrics
    const trunc          = (s, n) => s && s.length > n ? s.slice(0, n) + '…' : s
    const sourceDisplay  = [rec.source, rec.source_modifier].filter(Boolean).join(' · ')
    const lineageDisplay = rec.lineage ? trunc(rec.lineage, 220) : null

    // Top-right panel: always show Source + Lineage + Quality + Rating, then Fidelity if analysed
    const infoRows = `
      <div class="hm-row"><span class="hm-label">Source</span><span class="hm-val">${esc(sourceDisplay || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Lineage</span><span class="hm-val">${esc(lineageDisplay || rec.lineage || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Quality</span><span class="hm-val ${qualityClass(rec.quality)}">${esc(rec.quality || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Rating</span><span class="hm-val">${rec.rating != null ? `<span class="rating-badge">${rec.rating}</span>` : '—'}</span></div>`

    const metricsSection = firstAnalysed
      ? `<hr class="hm-divider">
         <div class="hm-section-header">
           <button class="rev-panel-toggle" data-panel="hm-panel-metrics">▾</button>
           <span class="hm-section-title">Fidelity</span>
         </div>
         <div id="hm-panel-metrics">
           <div class="hm-row"><span class="hm-label">Format</span><span class="hm-val hm-metric">${fmtBit(bitDepth)} / ${fmtSr(srHz)}</span>${formatLabel}</div>
           <div class="hm-row"><span class="hm-label">Cutoff</span><span class="hm-val hm-metric${looksTranscoded ? ' hm-warn' : ''}">${fmtCutoff(cutoffHz)}</span>${cutoffHint}</div>
           <div class="hm-row"><span class="hm-label">RMS</span><span class="hm-val hm-metric">${fmtDb(rmsDb)}</span>${rmsHint}</div>
           <div class="hm-row"><span class="hm-label">Dyn Range</span><span class="hm-val hm-metric">${fmtDb(dynDb)}</span>${dynHint}</div>
         </div>`
      : ''

    const headerMetaRows = infoRows + metricsSection

    // Which track to show by default: currently playing (if in this rec) else first track
    const firstTrack    = rec.tracks?.[0] ?? null
    const defaultTrackId = (state.playingTrackId && _waveformMap[state.playingTrackId])
      ? state.playingTrackId
      : (firstTrack?.id ?? null)

    setMainHTML(`
      <div class="rec-view-shell">
      <div class="rec-detail-header">
        <div class="rec-header-left">
          <div class="breadcrumb" id="back-btn">${backLabel}</div>
          <h2>${esc(perfName)}</h2>
          <div class="rec-date-line">${dateLineHtml}</div>
          ${rec.notes ? `<div class="rec-header-notes">${esc(rec.notes)}</div>` : ''}
          ${rec.is_official ? `<div class="badge-row"><span class="badge-official" title="Contains officially released material">© Official</span></div>` : ''}
        </div>
        ${headerMetaRows ? `<div class="rec-header-right">${headerMetaRows}</div>` : ''}
      </div>
      <canvas id="rec-waveform" class="rec-waveform-canvas"
        title="${hasAnalysis ? 'Click to seek' : 'Click Analyze Audio to generate waveform'}">
      </canvas>
      <div class="action-bar">
        <!-- Left: playback actions -->
        <button class="btn btn-ghost btn-sm" id="btn-play-all">▶ Play All</button>
        <label class="skip-toggle skip-toggle--action" title="Skip announcements, banter &amp; tuning from queue">
          <input type="checkbox" class="skip-filter-cb" id="skip-filter-action" ${state.skipNonMusic ? 'checked' : ''} />
          <span class="skip-toggle-track"></span>
          <span class="skip-toggle-label">Skip Non-Music</span>
        </label>
        <!-- Right: editing actions -->
        <div class="action-bar-right">
          <button class="btn btn-ghost btn-sm" id="btn-edit-meta">Edit Recording</button>
          <button class="btn btn-ghost btn-sm" id="btn-analyze-audio">Re-Analyze</button>
          <button class="btn btn-sm ${stagedCount > 0 ? 'btn-staged' : 'btn-ghost'}" id="btn-write-tags">Write Tags to Files</button>
          <span class="action-bar-sep"></span>
          <button class="btn btn-danger btn-sm" id="btn-delete-rec" title="Delete this recording from the database (files are not removed)">Delete</button>
        </div>
      </div>
      <div class="detail-panels" id="detail-panels">
        <div class="track-panel" id="track-panel">
          ${trackRows || '<div class="info-panel-empty">No tracks</div>'}
        </div>

        <!-- Slide-in right panel — collapsed by default, expands to ~40% -->
        <div class="slide-panel" id="slide-panel">
          <div class="slide-panel-body" id="slide-panel-body">

            <!-- Info File pane -->
            <div class="slide-pane" id="sp-info">
              <div class="slide-pane-header">Info File</div>
              <div class="slide-pane-scroll">
                ${infoContent}
              </div>
            </div>

            <!-- Spectrogram pane -->
            <div class="slide-pane" id="sp-spectrogram">
              <div class="slide-pane-header">
                Spectrogram<span class="spectrogram-track-name" id="spectrogram-track-name"></span>
              </div>
              <div class="slide-pane-scroll">
                ${hasAnalysis
                  ? `<div id="spectrogram-wrap">
                       <div class="spectrogram-img-wrap" id="spectrogram-img-wrap">
                         <div class="spectrogram-loading" id="spectrogram-loading">Generating…</div>
                         <img id="spectrogram-img" class="spectrogram-img" style="display:none" />
                       </div>
                     </div>`
                  : `<div class="info-panel-empty">No analysis yet — click Analyze Audio to generate.</div>`
                }
              </div>
            </div>

            <!-- Debug pane (DEV_MODE only) -->
            <div class="slide-pane" id="sp-debug">
              <div class="slide-pane-header">Debug <span class="dbg-badge dbg-badge-dev">DEV</span></div>
              <div class="slide-pane-scroll" id="sp-debug-body"></div>
            </div>

          </div>

          <!-- Vertical tab strip anchored to the right edge -->
          <div class="slide-tabs">
            <button class="slide-tab" data-pane="info">Info File</button>
            <button class="slide-tab" data-pane="spectrogram">Spectrogram</button>
            ${window.fluxDebug ? `<button class="slide-tab slide-tab--dev" data-pane="debug">Debug</button>` : ''}
          </div>
        </div>

      </div>
      </div>
    `)

    // Back button
    document.getElementById('back-btn')?.addEventListener('click', () => {
      window.location.hash = backHash
    })

    // Venue name → venue page
    document.querySelector('.venue-link')?.addEventListener('click', () => {
      if (venueId) window.location.hash = `#/venue/${venueId}`
    })

    // Right panel collapsible sections
    document.querySelectorAll('.rec-header-right .rev-panel-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const panel = document.getElementById(btn.dataset.panel)
        if (!panel) return
        const collapsed = panel.style.display === 'none'
        panel.style.display = collapsed ? '' : 'none'
        btn.textContent = collapsed ? '▾' : '▸'
      })
    })

    // Info-panel section toggle (Info file)
    ;['btn-info-toggle'].forEach(id => {
      document.getElementById(id)?.addEventListener('click', function () {
        const panel = document.getElementById(this.dataset.panel)
        if (!panel) return
        const collapsed = panel.style.display === 'none'
        panel.style.display = collapsed ? '' : 'none'
        this.textContent = collapsed ? '▾' : '▸'
      })
    })

    // Play all
    document.getElementById('btn-play-all')?.addEventListener('click', () => {
      playRecording(recordingId, 0, rec.tracks)
    })

    // Track row clicks — skip grayed-out rows; use track ID to find correct queue index
    mainContent.querySelectorAll('.track-row[data-track-id]').forEach(row => {
      row.addEventListener('click', () => {
        if (row.classList.contains('track-row--skipped')) return
        const tid = parseInt(row.dataset.trackId)
        const idx = rec.tracks.findIndex(t => t.id === tid)
        if (idx >= 0) playRecording(recordingId, idx, rec.tracks)
      })
    })

    // Inline note editor — pencil icon swaps the note display for a text
    // input, right there in the track list, so people can jot a note while
    // they're listening instead of hopping over to Edit Recording.
    mainContent.querySelectorAll('.track-note-edit-btn').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation()   // don't trigger the row's play-on-click
        const tid  = parseInt(btn.dataset.trackId)
        const disp = mainContent.querySelector(`.track-note-display[data-track-id="${tid}"]`)
        if (!disp || disp.querySelector('.track-note-input')) return   // already editing

        const track   = rec.tracks.find(t => t.id === tid)
        const current = track?.notes || ''
        disp.innerHTML = `<input type="text" class="track-note-input" value="${esc(current)}" placeholder="Add a note…" />`
        const input = disp.querySelector('.track-note-input')
        input.addEventListener('click', e => e.stopPropagation())
        input.focus()
        input.select()

        let saved = false
        const save = async () => {
          if (saved) return
          saved = true
          const value = input.value.trim() || null
          if (track) track.notes = value
          try {
            await API.tracks.update(tid, { notes: value })
          } catch (e) {
            console.error('Failed to save note:', e)
          }
          renderRecordingView(recordingId)
        }
        input.addEventListener('blur', save)
        input.addEventListener('keydown', e => {
          e.stopPropagation()
          if (e.key === 'Enter')  { e.preventDefault(); input.blur() }
          if (e.key === 'Escape') { saved = true; renderRecordingView(recordingId) }
        })
      })
    })

    // Edit metadata
    document.getElementById('btn-edit-meta')?.addEventListener('click', () => {
      renderRecordingEdit(recordingId, rec, perf)
    })

    // Analyze Audio — run Librosa analysis on all tracks
    document.getElementById('btn-analyze-audio')?.addEventListener('click', async () => {
      const btn = document.getElementById('btn-analyze-audio')
      btn.disabled = true
      btn.textContent = 'Analyzing…'
      try {
        const result = await API.recordings.reprocess(recordingId)
        btn.textContent = `Done (${result.analysed} track${result.analysed === 1 ? '' : 's'})`
        setTimeout(() => {
          if (btn) { btn.disabled = false; btn.textContent = 'Analyze Audio' }
          renderRecordingView(recordingId)  // reload to show waveform/spectrogram
        }, 1500)
        if (result.errors?.length) {
          console.warn('Analysis errors:', result.errors)
        }
      } catch (e) {
        btn.disabled = false
        btn.textContent = 'Analyze Audio'
        alert('Analysis failed: ' + e.message)
      }
    })

    // Skip non-music toggle (action bar instance)
    document.getElementById('skip-filter-action')?.addEventListener('change', function () {
      setSkipFilter(this.checked)
    })
    // Apply filter to current view immediately (in case state was already on)
    applySkipFilter()

    // Write FLAC tags
    document.getElementById('btn-write-tags')?.addEventListener('click', async () => {
      const ok = confirm('Write current metadata as FLAC tags to all tracks in this recording?\n\nThis replaces all existing Vorbis comments in the files.')
      if (!ok) return
      try {
        const btn = document.getElementById('btn-write-tags')
        btn.disabled = true
        btn.textContent = 'Writing…'
        const result = await API.recordings.writeTags(recordingId)
        if (result.errors?.length) {
          alert(`Tags written to ${result.written} file(s).\n\nWarnings:\n${result.errors.map(([f, e]) => `${f}: ${e}`).join('\n')}`)
        }
        // Reload to clear the staged indicator
        renderRecordingView(recordingId)
      } catch (e) {
        alert('Error writing tags: ' + e.message)
        const btn = document.getElementById('btn-write-tags')
        if (btn) { btn.disabled = false; btn.textContent = 'Write Tags to Files' }
      }
    })

    document.getElementById('btn-delete-rec')?.addEventListener('click', async () => {
      if (!confirm('Delete this recording from the database?\n\nAudio files on disk are not removed.')) return
      const btn = document.getElementById('btn-delete-rec')
      btn.disabled = true
      btn.textContent = 'Deleting…'
      try {
        await API.recordings.delete(recordingId)
        // Navigate back to artist or library root
        const backHash = state.selectedArtist ? `#/artist/${state.selectedArtist.id}` : '#/'
        window.location.hash = backHash
      } catch (err) {
        btn.disabled = false
        btn.textContent = 'Delete'
        alert('Delete failed: ' + err.message)
      }
    })

    // ── Slide panel tab wiring ───────────────────────────────────────────────
    ;(function () {
      const panel = document.getElementById('slide-panel')
      if (!panel) return
      let activePane = null

      document.querySelectorAll('.slide-tab').forEach(tab => {
        tab.addEventListener('click', () => {
          const pane = tab.dataset.pane
          const isOpen = panel.classList.contains('open')

          if (isOpen && activePane === pane) {
            // Same tab clicked again → collapse
            panel.classList.remove('open')
            document.querySelectorAll('.slide-pane').forEach(p => p.classList.remove('active'))
            document.querySelectorAll('.slide-tab').forEach(t => t.classList.remove('active'))
            if (activePane === 'debug') window.fluxDebug?.detach()
            activePane = null
          } else {
            // Open/switch pane
            panel.classList.add('open')
            document.querySelectorAll('.slide-pane').forEach(p => p.classList.remove('active'))
            document.querySelectorAll('.slide-tab').forEach(t => t.classList.remove('active'))
            const paneEl = document.getElementById(`sp-${pane}`)
            if (paneEl) paneEl.classList.add('active')
            tab.classList.add('active')
            activePane = pane

            // Lazy-load spectrogram the first time the tab opens
            if (pane === 'spectrogram' && defaultTrackId) {
              const defaultTrack = rec.tracks.find(t => t.id === defaultTrackId)
              loadSpectrogram(defaultTrackId, defaultTrack?.title)
            }

            // Mount/refresh debug panel
            if (pane === 'debug') {
              window.fluxDebug?.attach(document.getElementById('sp-debug-body'))
            } else {
              // Not navigating away from panel itself — just switching panes
            }
          }
        })
      })
    })()

    // ── Waveform canvas — start RAF loop ────────────────────────────────────
    const waveCanvas = document.getElementById('rec-waveform')
    if (waveCanvas) {
      if (hasAnalysis && defaultTrackId) {
        _startWaveformLoop(waveCanvas, defaultTrackId)
      } else {
        // No analysis yet — draw empty placeholder
        const dpr = window.devicePixelRatio || 1
        const cssW = waveCanvas.offsetWidth  || 800
        const cssH = waveCanvas.offsetHeight || 80
        waveCanvas.width  = Math.round(cssW * dpr)
        waveCanvas.height = Math.round(cssH * dpr)
        _drawWaveformBg(waveCanvas, null)
        _drawPlayhead(waveCanvas, 0)
      }

      // Click to seek within the current track
      waveCanvas.addEventListener('click', e => {
        const audio = document.getElementById('audio-el')
        if (!audio || !audio.duration || isNaN(audio.duration)) return
        const pct = e.offsetX / waveCanvas.offsetWidth
        audio.currentTime = pct * audio.duration
      })
    }

    // ── Spectrogram — load for default track, reload when track changes ───────
    function loadSpectrogram(trackId, trackTitle) {
      const wrap    = document.getElementById('spectrogram-wrap')
      const imgEl   = document.getElementById('spectrogram-img')
      const loading = document.getElementById('spectrogram-loading')
      const label   = document.getElementById('spectrogram-track-name')
      if (!wrap || !imgEl) return

      if (label) label.textContent = trackTitle ? ` — ${trackTitle}` : ''
      imgEl.style.display = 'none'
      if (loading) { loading.style.display = ''; loading.textContent = 'Generating…' }

      const url = `/api/tracks/${trackId}/spectrogram?t=${Date.now()}`
      imgEl.onload  = () => { imgEl.style.display = 'block'; if (loading) loading.style.display = 'none' }
      imgEl.onerror = async () => {
        // Fetch the URL as text to get the actual error from the server
        try {
          const r = await fetch(url)
          const body = await r.json()
          if (loading) loading.textContent = `Error: ${body.error || r.status}`
        } catch (_) {
          if (loading) loading.textContent = 'Spectrogram failed'
        }
      }
      imgEl.src = url
    }

    // Spectrogram loads lazily when the tab is opened (see slide tab wiring above)

    // Reload spectrogram when a new track is clicked (only if the pane is open)
    mainContent.querySelectorAll('.track-row[data-track-id]').forEach(row => {
      row.addEventListener('click', () => {
        const tid   = parseInt(row.dataset.trackId)
        const title = row.querySelector('.track-title')?.textContent || ''
        const spPane = document.getElementById('sp-spectrogram')
        if (tid && _waveformMap[tid] && spPane?.classList.contains('active')) {
          loadSpectrogram(tid, title)
        }
      })
    })
  }

  // ── Ingest wizard ─────────────────────────────────────────────────────────

  // Step indicators — pass optional steps array; defaults to 3-step wizard
  function stepDots(current, steps) {
    steps = steps || ['folder', 'review', 'confirm']
    const idx = steps.indexOf(current)
    return `<div class="step-indicator">
      ${steps.map((s, i) => {
        const cls = i < idx ? 'done' : i === idx ? 'active' : ''
        return `<div class="step-dot ${cls}" title="Step ${i + 1}"></div>`
      }).join('')}
    </div>`
  }

  // ── Batch Import ────────────────────────────────────────────────────────────

  // State for the batch import session
  const batch = {
    sourceDir:   null,   // scanned directory path
    results:     null,   // full scan response
    ingestedIds: new Map(), // path → recording_id for items ingested this session
    expandedPaths: new Set(), // expanded row paths
  }

  async function renderBatchImportView() {
    setActiveNav('batch')
    if (!batch.results) { renderBatchPickerView(); return }
    renderBatchResultsView()
  }

  function renderBatchPickerView() {
    const defaultDir = '/Volumes/music/Live Music Archive/Workshop/Import'
    setMainHTML(`
      <div class="batch-shell">
        <div class="batch-header">
          <h2>Batch Import</h2>
          <p class="batch-subtitle">Scan a folder — each subfolder is graded green / yellow / red. You decide what to ingest.</p>
        </div>
        <div class="batch-pick-form">
          <label class="batch-pick-label">Source directory</label>
          <div class="batch-pick-row">
            <input type="text" id="batch-dir-input" class="batch-dir-input"
                   value="${esc(batch.sourceDir || defaultDir)}"
                   placeholder="/path/to/folder" />
            <button class="btn btn-ghost btn-sm" id="batch-pick-btn">Browse…</button>
          </div>
          <button class="btn btn-primary" id="batch-scan-btn" style="margin-top:16px">Scan Folder</button>
        </div>
      </div>`)

    document.getElementById('batch-pick-btn').addEventListener('click', async () => {
      try {
        const path = await window.pywebview.api.pick_folder()
        if (path) document.getElementById('batch-dir-input').value = path
      } catch (e) { /* no-op outside pywebview */ }
    })

    document.getElementById('batch-scan-btn').addEventListener('click', async () => {
      const dir = document.getElementById('batch-dir-input').value.trim()
      if (!dir) return
      setMainHTML(`<div class="empty-state">Scanning <code>${esc(dir)}</code>…</div>`)
      try {
        batch.sourceDir    = dir
        batch.results      = await API.ingest.batchScan(dir)
        batch.ingestedIds  = new Map()
        batch.expandedPaths = new Set()
        renderBatchResultsView()
      } catch (e) {
        setMainHTML(`<div class="empty-state" style="color:var(--red)">Scan failed: ${esc(e.message)}</div>`)
      }
    })
  }

  function _batchDateStr(e) {
    return [e.year,
      e.month ? String(e.month).padStart(2,'0') : null,
      e.day   ? String(e.day).padStart(2,'0')   : null,
    ].filter(Boolean).join('-')
  }

  function _batchTierLabel(tier) {
    return tier === 'green' ? '🟢' : tier === 'yellow' ? '🟡' : '🔴'
  }

  // Render a single compact row (all tiers share this shell)
  function _batchRow(item) {
    const e       = item.extracted
    const conf    = item.confidence
    const ingestedId = batch.ingestedIds.get(item.path)
    const ingested   = ingestedId != null
    const expanded = batch.expandedPaths.has(item.path)
    const dateStr  = _batchDateStr(e)
    const loc      = [e.city, e.state].filter(Boolean).join(', ')

    // Confidence flags (yellow/red only)
    const confFlags = []
    if (conf.artist && conf.artist !== 'high') confFlags.push(`artist: ${conf.artist}`)
    if (conf.date   && conf.date   !== 'high') confFlags.push(`date: ${conf.date}`)
    if (conf.tracks && conf.tracks !== 'high') confFlags.push(`tracks: ${conf.tracks}`)

    // Issue chips
    const issueChips = item.issues.map(iss =>
      `<span class="batch-issue-${iss.severity}">${esc(iss.msg)}</span>`
    ).join('')

    // Action button
    let actionBtn = ''
    if (ingested) {
      actionBtn = `<span class="batch-done-check">✓ Ingested</span>
                   <a class="batch-rec-link" href="#/recording/${ingestedId}">View →</a>`
    } else if (item.tier === 'green') {
      actionBtn = `<button class="btn btn-primary btn-sm batch-ingest-btn" data-path="${esc(item.path)}">Ingest</button>`
    } else if (item.tier === 'yellow') {
      actionBtn = `<button class="btn btn-secondary btn-sm batch-review-btn" data-path="${esc(item.path)}">Review →</button>`
    } else {
      actionBtn = `<button class="btn btn-ghost btn-sm batch-review-btn" data-path="${esc(item.path)}">Manual →</button>`
    }

    // Expanded detail panel
    const detail = expanded ? `
      <div class="batch-expand-panel">
        <div class="batch-expand-grid">
          <div class="batch-expand-row"><span class="batch-expand-label">Artist</span><span class="batch-expand-val ${conf.artist !== 'high' ? 'batch-val-uncertain' : ''}">${esc(e.artist || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Date</span><span class="batch-expand-val ${conf.date !== 'high' ? 'batch-val-uncertain' : ''}">${esc(dateStr || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Venue</span><span class="batch-expand-val">${esc(e.venue || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Location</span><span class="batch-expand-val">${esc(loc || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Source</span><span class="batch-expand-val">${esc(e.source || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Tracks</span><span class="batch-expand-val">${(() => {
            const audio = e.track_count
            const tagged = e.tracks_titled
            const infoCount = e.info_track_count || 0
            const tagLine = tagged > 0 ? `${tagged}/${audio} titled in tags` : `0 titled in tags`
            if (infoCount > 0) {
              const match = infoCount === audio
              const matchIcon = match ? '✓' : '⚠'
              const matchCls  = match ? '' : 'batch-val-uncertain'
              return `${audio} audio · ${tagLine} · <span class="${matchCls}">${matchIcon} ${infoCount} in info file${match ? '' : ' — count mismatch'}</span>`
            }
            return `${audio} audio · ${tagLine} · no info file track list`
          })()}</span></div>
          ${issueChips ? `
          <div class="batch-expand-row"><span class="batch-expand-label">Issues</span><span class="batch-expand-val">${issueChips}</span></div>` : ''}
          <div class="batch-expand-row"><span class="batch-expand-label">Path</span><span class="batch-expand-val batch-path-mono">${esc(item.path)}</span></div>
        </div>
      </div>` : ''

    // Summary line for collapsed state
    const summaryParts = [e.artist || '?', dateStr || '?']
    if (e.venue) summaryParts.push(e.venue)
    else if (loc) summaryParts.push(loc)
    summaryParts.push(`${item.audio_count} tracks`)

    return `
      <div class="batch-item-row batch-item-${item.tier} ${ingested ? 'batch-item-ingested' : ''}"
           data-path="${esc(item.path)}">
        <div class="batch-item-main">
          <button class="batch-expand-btn" data-path="${esc(item.path)}" title="${expanded ? 'Collapse' : 'Expand'}">
            ${expanded ? '▾' : '▸'}
          </button>
          <div class="batch-item-info">
            <div class="batch-item-name">
              <span class="batch-tier-icon">${_batchTierLabel(item.tier)}</span>
              ${esc(item.name)}
            </div>
            <div class="batch-item-summary">
              ${summaryParts.map(p => `<span class="batch-meta-field">${esc(p)}</span>`).join('<span class="batch-meta-sep">·</span>')}
              ${confFlags.map(f => `<span class="batch-conf-flag">${esc(f)}</span>`).join('')}
            </div>
          </div>
          <div class="batch-item-actions">
            <span class="batch-ingest-status" id="batch-status-${item.path.replace(/[^a-zA-Z0-9]/g,'_')}"></span>
            ${actionBtn}
          </div>
        </div>
        ${detail}
      </div>`
  }

  function renderBatchResultsView() {
    const r = batch.results
    if (!r) { renderBatchPickerView(); return }

    const greens  = r.items.filter(i => i.tier === 'green')
    const yellows = r.items.filter(i => i.tier === 'yellow')
    const reds    = r.items.filter(i => i.tier === 'red')
    const nDone   = batch.ingestedIds.size

    const tierPill = (label, count, cls) => count > 0
      ? `<span class="batch-tier-pill batch-tier-${cls}">${count} ${label}</span>` : ''

    const allRows = r.items.map(item => _batchRow(item)).join('')

    setMainHTML(`
      <div class="batch-shell">
        <div class="batch-header">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <h2 style="margin:0">Batch Import</h2>
            <span class="batch-dir-label">${esc(r.source_dir)}</span>
            <button class="btn btn-ghost btn-sm" id="batch-rescan-btn">↺ New Scan</button>
          </div>
          <div class="batch-tier-pills" style="margin-top:10px">
            ${tierPill('green', greens.length, 'green')}
            ${tierPill('yellow', yellows.length, 'yellow')}
            ${tierPill('red', reds.length, 'red')}
            ${nDone > 0 ? `<span class="batch-tier-pill batch-tier-done">${nDone} ingested</span>` : ''}
            ${greens.filter(i => !batch.ingestedIds.has(i.path)).length > 0
              ? `<button class="btn btn-primary btn-sm" id="batch-ingest-all-btn" style="margin-left:8px">
                   ⇉ Ingest All Green (${greens.filter(i => !batch.ingestedIds.has(i.path)).length})
                 </button>`
              : ''}
            <span class="batch-tier-pill batch-tier-total">${r.total} total</span>
          </div>
        </div>
        <div class="batch-list">${allRows}</div>
        ${r.total === 0 ? `<div class="empty-state">No subfolders found.</div>` : ''}
      </div>`)

    // ── Events ──────────────────────────────────────────────────────────────

    document.getElementById('batch-rescan-btn')?.addEventListener('click', () => {
      batch.results = null
      renderBatchImportView()
    })

    // Ingest All Green
    document.getElementById('batch-ingest-all-btn')?.addEventListener('click', async () => {
      const btn = document.getElementById('batch-ingest-all-btn')
      const pending = batch.results.items.filter(i => i.tier === 'green' && !batch.ingestedIds.has(i.path))
      if (!pending.length) return
      btn.disabled = true

      for (let idx = 0; idx < pending.length; idx++) {
        const item = pending[idx]
        btn.textContent = `⏳ ${idx + 1} / ${pending.length}`

        // Update the row's status inline
        const sid = 'batch-status-' + item.path.replace(/[^a-zA-Z0-9]/g,'_')
        const statusEl = document.getElementById(sid)
        if (statusEl) statusEl.textContent = '⏳'
        const rowBtn = mainContent.querySelector(`.batch-ingest-btn[data-path="${CSS.escape(item.path)}"]`)
        if (rowBtn) { rowBtn.disabled = true; rowBtn.textContent = '⏳' }

        try {
          const recId = await _batchIngestOne(item)
          batch.ingestedIds.set(item.path, recId)
          if (statusEl) statusEl.textContent = '✓'
          if (rowBtn) rowBtn.textContent = '✓ Done'
        } catch (err) {
          if (statusEl) statusEl.textContent = '✗ failed'
          if (rowBtn) { rowBtn.disabled = false; rowBtn.textContent = 'Ingest' }
          console.error('Bulk ingest failed for', item.name, err)
          // Continue to next item rather than aborting the whole run
        }
      }

      // Final re-render to show all ingested state cleanly
      renderBatchResultsView()
      loadArtistList()  // refresh sidebar — new artists/counts from this run
    })

    // Expand/collapse
    mainContent.querySelectorAll('.batch-expand-btn').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation()
        const path = btn.dataset.path
        if (batch.expandedPaths.has(path)) batch.expandedPaths.delete(path)
        else batch.expandedPaths.add(path)
        renderBatchResultsView()
      })
    })

    // Green: direct ingest
    mainContent.querySelectorAll('.batch-ingest-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const path = btn.dataset.path
        const item = r.items.find(i => i.path === path)
        if (!item) return
        btn.disabled = true
        btn.textContent = '⏳ Ingesting…'
        try {
          const recId = await _batchIngestOne(item)
          batch.ingestedIds.set(path, recId)
          renderBatchResultsView()  // re-render only on success (shows ✓ Ingested + View →)
          loadArtistList()          // refresh sidebar — may be a new artist
        } catch (err) {
          btn.disabled = false
          btn.textContent = 'Ingest'
          const msg = err.message || 'Unknown error'
          // Show inline (ID now uses raw path — no esc() mismatch)
          const sid = 'batch-status-' + path.replace(/[^a-zA-Z0-9]/g,'_')
          const el  = document.getElementById(sid)
          if (el) el.textContent = '✗ ' + msg
          // Always alert as fallback so errors are never silently swallowed
          else alert('Ingest failed: ' + msg)
          console.error('Ingest failed:', err)
        }
      })
    })

    // Yellow/red: open wizard
    mainContent.querySelectorAll('.batch-review-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const path = btn.dataset.path
        const item = r.items.find(i => i.path === path)
        if (item) _batchOpenReview(item)
      })
    })
  }

  // Direct ingest of a single item (green path — no wizard)
  async function _batchIngestOne(item) {
    const scan = await API.recordings.scan(item.path)
    const e    = item.extracted

    const tracks = scan.audio_files.map((af, idx) => {
      const tagTrack  = scan.suggestions.from_tags.tracks?.[idx]
      const infoTrack = scan.suggestions.from_info_file.tracks?.[idx]
      return {
        track_number: tagTrack?.track_number ? parseInt(tagTrack.track_number) : idx + 1,
        title:        tagTrack?.title || infoTrack?.title || `Track ${idx + 1}`,
        set:          af.set || null,
        duration:     tagTrack?.duration || null,
        filename:     af.rel_path || af.filename,
      }
    })

    const result = await API.ingest.confirm({
      source_folder_path: item.path,
      artist_name:        e.artist,
      start_year:         e.year,
      start_month:        e.month,
      start_day:          e.day,
      venue_name:         e.venue   || null,
      city:               e.city    || null,
      state:              e.state   || null,
      country:            e.country || null,
      source:             e.source  || null,
      lineage:            e.lineage || null,
      is_complete:        true,
      info_file_content:  scan.info_file_content || null,
      fingerprints:       scan.fingerprints || [],
      tracks,
    })
    return result.recording_id
  }

  // Open ingest wizard pre-scanned (yellow / red / manual green)
  // Scan FIRST, then set state, then navigate — so renderIngestView's reset guard
  // sees step='review' + non-null scan and doesn't wipe everything.
  async function _batchOpenReview(item) {
    // Show loading on the button while we scan
    const btn = mainContent.querySelector(`.batch-review-btn[data-path="${CSS.escape(item.path)}"]`)
    if (btn) { btn.disabled = true; btn.textContent = '⏳' }

    try {
      const scan        = await API.recordings.scan(item.path)
      ingest.scan       = scan
      ingest.step       = 'review'  // set BEFORE hash change — bypasses reset guard
      ingest.folderPath = item.path
      ingest.form       = {}
      ingest.tracks     = []
      window.location.hash = '#/ingest'
    } catch (err) {
      if (btn) { btn.disabled = false; btn.textContent = 'Review →' }
      console.error('Batch review scan failed:', err)
      // Show inline error
      const sid = 'batch-status-' + item.path.replace(/[^a-zA-Z0-9]/g,'_')
      const el  = document.getElementById(sid)
      if (el) el.textContent = '✗ scan failed'
    }
  }

  // ── Incoming queue ──────────────────────────────────────────────────────────

  async function renderIncomingView() {
    setActiveNav('incoming')
    setActiveArtist(null)
    setLoading()

    let folders = []
    try {
      folders = await API.ingest.incoming()
    } catch (e) {
      setMainHTML(`<div class="empty-state"><div style="color:var(--red)">Failed to load incoming: ${esc(e.message)}</div></div>`)
      return
    }

    if (!folders.length) {
      setMainHTML(`
        <div class="action-bar">
          <span style="font-size:13px; font-weight:500; color:var(--t0)">Incoming</span>
        </div>
        <div class="empty-state">
          <div class="empty-icon">↓</div>
          <div>No recordings in <code>_incoming/</code></div>
          <div style="font-size:11px; color:var(--t2); margin-top:6px">Drop folders here to queue them for ingest.</div>
        </div>`)
      return
    }

    const rows = folders.map(f => {
      const issueChips = (f.issues || []).map(issue =>
        `<span class="incoming-issue incoming-issue-${esc(issue.severity)}">${esc(issue.msg)}</span>`
      ).join('')

      return `
      <div class="incoming-row" data-path="${esc(f.path)}">
        <div class="incoming-info">
          <div class="incoming-name">${esc(f.name)}</div>
          ${issueChips ? `<div class="incoming-issues">${issueChips}</div>` : ''}
        </div>
        <div class="incoming-meta">
          <span class="incoming-stat">${f.audio_count} track${f.audio_count !== 1 ? 's' : ''}</span>
          <span class="incoming-stat">${f.size_mb} MB</span>
        </div>
        <button class="btn btn-primary btn-sm incoming-ingest-btn" data-path="${esc(f.path)}">Ingest →</button>
      </div>`
    }).join('')

    setMainHTML(`
      <div class="action-bar">
        <span style="font-size:13px; font-weight:500; color:var(--t0)">Incoming</span>
        <span style="margin-left:8px; font-size:11px; color:var(--t2)">${folders.length} folder${folders.length !== 1 ? 's' : ''}</span>
        <button class="btn btn-ghost btn-sm" id="btn-refresh-incoming" style="margin-left:auto">↺ Refresh</button>
      </div>
      <div class="incoming-list">${rows}</div>`)

    // Refresh button
    document.getElementById('btn-refresh-incoming').addEventListener('click', renderIncomingView)

    // Ingest buttons — jump straight to scan/review, bypassing folder picker
    mainContent.querySelectorAll('.incoming-ingest-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const path = btn.dataset.path
        // Reset wizard state then jump to scan
        ingest.step       = 'folder'
        ingest.scan       = null
        ingest.folderPath = path
        ingest.form       = {}
        ingest.tracks     = []
        window.location.hash = '#/ingest'
        // Brief tick so the hash change fires renderIngestView, then run scan
        setTimeout(() => runScan(path), 50)
      })
    })
  }

  function renderIngestView() {
    setActiveNav('ingest')
    setActiveArtist(null)
    // Reset to folder step when arriving fresh or after a completed ingest
    if (ingest.step === 'folder' || ingest.step === 'success' || !ingest.scan) {
      ingest.step       = 'folder'
      ingest.scan       = null
      ingest.folderPath = null
      ingest.form       = {}
      ingest.tracks     = []
    }
    renderIngestStep()
  }

  function renderIngestStep() {
    switch (ingest.step) {
      case 'folder':  renderIngestFolder();  break
      case 'review':  renderIngestReview();  break
      case 'tracks':  renderIngestReview();  break  // merged into review step
      case 'confirm': renderIngestConfirm(); break
      case 'success': renderIngestSuccess(); break
    }
  }

  // ── Step 1: Choose folder ──────────────────────────────────────────────────

  function renderIngestFolder() {
    const pathDisplay = ingest.folderPath
      ? `<div class="folder-path">${esc(ingest.folderPath)}</div>`
      : `<div class="folder-label">Click to choose source folder</div>`

    setMainHTML(`
      <div class="ingest-view">
        <div class="ingest-step-header">
          <h2>Add Recording</h2>
          ${stepDots('folder')}
        </div>
        <div class="sub">Select a folder containing FLAC files to scan and add to the library.</div>
        <div class="folder-picker" id="folder-picker">
          <span class="folder-icon" style="font-size:20px">📂</span>
          <div>${pathDisplay}</div>
        </div>
        <div id="scan-status"></div>
      </div>`)

    document.getElementById('folder-picker').addEventListener('click', async () => {
      let path = null
      if (window.pywebview?.api?.pick_folder) {
        path = await window.pywebview.api.pick_folder()
      } else {
        path = prompt('Folder path (dev mode):')
      }
      if (path) {
        ingest.folderPath = path
        runScan(path)
      }
    })
  }

  async function runScan(folderPath) {
    ingest.folderPath = folderPath  // re-set in case the render-reset cleared it
    const statusEl = document.getElementById('scan-status')
    if (!statusEl) return
    statusEl.innerHTML = `
      <div class="empty-state" style="min-height:100px">
        <div class="loading-spinner"></div>
        <div style="margin-top:8px; color:var(--t2); font-size:12px">Scanning ${esc(folderPath.split('/').pop())}...</div>
      </div>`
    try {
      const scan = await API.recordings.scan(folderPath)
      ingest.scan = scan
      ingest.step = 'review'
      renderIngestStep()
    } catch (e) {
      statusEl.innerHTML = `
        <div style="color:var(--red); font-size:13px; margin-top:12px; padding:12px 16px; background:rgba(224,85,85,0.08); border-radius:var(--r-sm);">
          Scan failed: ${esc(e.message)}
        </div>`
    }
  }

  // ── Step 2: Combined metadata + track review ──────────────────────────────

  function pick(tags, info, field) {
    return tags?.[field] || info?.[field] || ''
  }

  function hintChips(fieldId, tagVal, infoVal) {
    const chips = []
    if (tagVal)  chips.push({ label: `Tags: ${tagVal}`, val: tagVal })
    if (infoVal && infoVal !== tagVal) chips.push({ label: `Info: ${infoVal}`, val: infoVal })
    if (!chips.length) return ''
    return `<div class="field-hints">
      ${chips.map((c, i) => `
        <span class="hint-chip ${i === 0 ? 'active' : ''}"
              data-field="${fieldId}" data-val="${esc(c.val)}">${esc(c.label)}</span>
      `).join('')}
    </div>`
  }

  function renderIngestReview() {
    const tags = ingest.scan.suggestions.from_tags
    const info = ingest.scan.suggestions.from_info_file

    // Build track list from scan data on first load (preserve any edits on back-nav)
    if (!ingest.tracks.length) {
      const tagTracks  = tags.tracks  || []
      const infoTracks = info.tracks  || []
      const infoMap    = {}
      infoTracks.forEach(t => { infoMap[t.number] = t.title })

      // Build a map from filename → set label + rel_path (from scan subdir detection)
      const audioSetMap  = {}
      const audioRelPath = {}  // filename → rel_path (preserves subdir prefix if any)
      ;(ingest.scan.audio_files || []).forEach(af => {
        if (af.set)      audioSetMap[af.filename]  = af.set
        if (af.rel_path) audioRelPath[af.filename] = af.rel_path
      })
      const setsDetected = ingest.scan.sets_detected || false

      ingest.tracks = tagTracks.map(t => {
        const title = titleCase(t.title || infoMap[t.index]) || `Track ${t.index}`
        return {
          track_number: t.track_number ? parseInt(t.track_number) : t.index,
          title,
          set:          audioSetMap[t.filename] || '',
          duration:     t.duration,
          // Use rel_path so the DB file_path includes any subdir prefix (e.g. "flac/01.flac")
          filename:     t.rel_path || audioRelPath[t.filename] || t.filename,
          // Pre-suggested from the title text — archivist approves/removes via flag pills
          flags:        detectTrackFlags(title),
        }
      })

      if (!ingest.tracks.length) {
        const scanFiles = ingest.scan.audio_files || []
        ingest.tracks = infoTracks.map(t => {
          const scanFile = scanFiles[t.number - 1] || {}
          const title = titleCase(t.title) || `Track ${t.number}`
          return {
            track_number: t.number,
            title,
            set:          audioSetMap[scanFile.filename] || '',
            duration:     null,
            filename:     scanFile.rel_path || scanFile.filename || '',
            flags:        detectTrackFlags(title),
          }
        })
      }
    }

    // Pre-fill metadata form on first load
    const f = ingest.form
    if (!f._filled) {
      let tagYear = null, tagMonth = null, tagDay = null
      if (tags.concert_date) {
        const p  = tags.concert_date.split('-')
        tagYear  = parseInt(p[0]) || null
        tagMonth = parseInt(p[1]) || null
        tagDay   = parseInt(p[2]) || null
      }
      f.artist_name     = titleCase(pick(tags, info, 'artist')) || ''
      f.sort_name       = ''
      f.start_year      = tagYear  || info.year  || ''
      f.start_month     = tagMonth || info.month || ''
      f.start_day       = tagDay   || info.day   || ''
      f.venue_name      = pick(tags, info, 'venue') || ''
      f.venue_id        = null
      f.city            = info.city    || tags.city    || ''
      f.state           = info.state   || tags.state   || ''
      f.country         = info.country || tags.country || ''
      f.source          = pick(tags, info, 'source') || ''
      f.source_modifier = ''
      f.quality         = ''
      f.lineage         = tags?.lineage || ''
      f.notes           = ''
      f.end_year        = ''
      f.end_month       = ''
      f.end_day         = ''
      f.event_name      = ''
      f.event_id        = null
      f.is_official     = false
      f._filled         = true
    }

    // Right panel: FLAC Tags — container fields + per-track sub-section
    const tagKeys = ['artist', 'concert_date', 'venue', 'location', 'source', 'lineage']
    const rawTagRows = tagKeys.map(k => `
      <div class="rev-raw-row">
        <span class="rev-raw-key">${k}</span>
        <span class="rev-raw-val ${tags[k] ? '' : 'rev-raw-empty'}">${tags[k] ? esc(tags[k]) : '—'}</span>
      </div>`).join('')

    const tagTracks = tags.tracks || []
    const rawTrackRows = tagTracks.length ? tagTracks.map(t => `
      <div class="rev-raw-row rev-raw-track-row">
        <span class="rev-raw-key">${String(t.track_number || t.index).padStart(2,'0')}</span>
        <span class="rev-raw-val ${t.title ? '' : 'rev-raw-empty'}">${t.title ? esc(t.title) : '—'}</span>
      </div>`).join('') : ''

    const rawTracksSection = rawTrackRows ? `
      <div class="rev-raw-tracks-header">
        <span>Tracks (${tagTracks.length})</span>
        <button class="rev-panel-toggle" data-panel="panel-flac-tracks">▾</button>
      </div>
      <div id="panel-flac-tracks" style="display:none">${rawTrackRows}</div>` : ''

    // Right panel: parsed info file — arrows on LEFT of label
    const infoDate = (info.year && info.month && info.day)
      ? `${info.year}-${String(info.month).padStart(2,'0')}-${String(info.day).padStart(2,'0')}`
      : info.year ? String(info.year) : null

    const parsedFields = [
      { label: 'Artist', val: titleCase(info.artist),  action: 'apply-artist' },
      { label: 'Date',   val: infoDate,                action: 'apply-date'   },
      { label: 'Venue',  val: titleCase(info.venue),   action: 'apply-venue'  },
      { label: 'City',   val: titleCase(info.city),    action: 'apply-city'   },
      { label: 'State',  val: info.state,              action: 'apply-state'  },
      { label: 'Country',val: titleCase(info.country), action: 'apply-country'},
    ].filter(f => f.val)

    const parsedTrackCount = info.tracks?.length || 0

    // Arrow button is now LEFT of the label
    const parsedRows = parsedFields.map(f => `
      <div class="rev-parsed-row">
        <button class="btn-parsed-apply" data-action="${f.action}" data-val="${esc(f.val)}"
                data-year="${info.year||''}" data-month="${info.month||''}" data-day="${info.day||''}">←</button>
        <span class="rev-parsed-key">${f.label}</span>
        <span class="rev-parsed-val">${esc(f.val)}</span>
      </div>`).join('')

    // Tracks row: apply button + expandable track list
    const parsedTrackItems = (info.tracks || []).map(t =>
      `<div class="rev-parsed-track-item">${String(t.number).padStart(2,'0')}. ${esc(titleCase(t.title))}</div>`
    ).join('')

    const parsedTracksRow = parsedTrackCount ? `
      <div class="rev-parsed-row">
        <button class="btn-parsed-apply" data-action="apply-tracks">←</button>
        <span class="rev-parsed-key">Tracks</span>
        <span class="rev-parsed-val">
          ${parsedTrackCount} found
          <button class="btn-parsed-tracks-toggle" id="btn-parsed-tracks-toggle">▴</button>
        </span>
      </div>
      <div class="rev-parsed-tracklist" id="rev-parsed-tracklist">
        ${parsedTrackItems}
      </div>` : ''

    const parsedPanelBody = (parsedRows || parsedTracksRow)
      ? `<div class="rev-parsed-section">${parsedRows}${parsedTracksRow}</div>`
      : `<div class="rev-raw-empty" style="padding:8px 16px 12px">No data parsed</div>`

    // Right panel: info file text (selectable) + switcher when multiple candidates
    const textCandidates = ingest.scan.text_file_candidates || []
    const textSwitcher = textCandidates.length > 1
      ? `<div class="info-file-switcher">
          <span class="info-file-switcher-label">Info file:</span>
          ${textCandidates.map((tf, i) => `
            <button class="info-file-btn ${i === (ingest._activeTextIdx || 0) ? 'active' : ''}"
                    data-idx="${i}">${esc(tf.filename)}</button>`).join('')}
         </div>`
      : ''
    const infoText = ingest.scan.info_file_content
      ? `${textSwitcher}<pre class="rev-info-text">${esc(ingest.scan.info_file_content)}</pre>`
      : `<div class="rev-raw-empty">No info file found</div>`

    // Track count mismatch detection
    const audioCount     = ingest.scan.audio_file_count
    const infoTrackCount = info.tracks?.length || 0
    const hasMismatch    = infoTrackCount > 0 && audioCount !== infoTrackCount
    const mismatchBanner = hasMismatch ? `
      <div class="track-mismatch-warn">
        ⚠ ${audioCount} audio file${audioCount !== 1 ? 's' : ''} on disk · ${infoTrackCount} track${infoTrackCount !== 1 ? 's' : ''} in info file — use playback to verify
      </div>` : ''

    // Track table rows — play preview + is_official + expandable optional details
    const _ingestFlagDefs = [
      { key: 'start_truncated', label: 'Start Truncated' },
      { key: 'end_truncated',   label: 'End Truncated'   },
      { key: 'incomplete',      label: 'Incomplete'       },
      { key: 'unknown_title',   label: 'Unknown Title'    },
      { key: 'banter',          label: 'Banter'           },
      { key: 'tuning',          label: 'Tuning'           },
      { key: 'audience',        label: 'Audience'         },
      { key: 'medley',          label: 'Medley'           },
      { key: 'announcement',    label: 'Announcement'     },
      { key: 'interview',       label: 'Interview'        },
      { key: 'introduction',    label: 'Introduction'     },
      { key: 'band_intros',     label: 'Band Intros'      },
    ]
    const trackRows = ingest.tracks.map((t, i) => {
      const flagPills = _ingestFlagDefs.map(f => {
        const active = (t.flags || []).includes(f.key)
        return `<button class="flag-pill ${active ? 'active' : ''}" data-flag="${f.key}" data-idx="${i}" type="button">${f.label}</button>`
      }).join('')
      return `
        <tr>
          <td class="num">${t.track_number}</td>
          <td class="play-cell">
            <button class="btn-preview-track" data-filename="${esc(t.filename || '')}" title="${esc(t.filename || 'no file')}">▶</button>
          </td>
          <td><input type="text" class="t-title" data-idx="${i}" value="${esc(t.title)}" /></td>
          <td class="dur">${fmtDur(t.duration)}</td>
          <td class="et-expand-cell">
            <button class="it-expand-btn" data-idx="${i}" type="button" title="Track details">⋯</button>
          </td>
        </tr>
        <tr class="it-detail-row" id="it-detail-${i}" style="display:none">
          <td colspan="5">
            <div class="et-detail-body">
              <div class="et-detail-optional-label">Optional track details</div>
              <div class="et-detail-field">
                <label>Songwriter</label>
                <input type="text" class="t-songwriter" data-idx="${i}" value="${esc(t.songwriter || '')}" placeholder="" />
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label>Flags</label>
                <div class="flag-pill-row">${flagPills}</div>
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label>Track notes</label>
                <textarea class="t-track-notes" data-idx="${i}" style="min-height:32px">${esc(t.notes || '')}</textarea>
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label class="check-label" title="Mark this track as an official release">
                  <input type="checkbox" class="t-official" data-idx="${i}" ${t.is_official ? 'checked' : ''} />
                  <span>Official release</span>
                </label>
              </div>
            </div>
          </td>
        </tr>`
    }).join('')

    setMainHTML(`
      <div class="ingest-review-shell">

        <!-- Left: metadata form + track list -->
        <div class="ingest-review-form">
          <div class="ingest-step-header" style="padding:8px 20px 6px; flex-shrink:0">
            <h2>Add Recording: <span class="rev-header-folder">${esc(ingest.folderPath?.split('/').pop() || '')}</span></h2>
          </div>
          <div class="ingest-review-form-body">

            <!-- Artist with autocomplete -->
            <div class="ingest-field-grid" style="grid-template-columns:2fr 1fr; gap:10px">
              <div class="ingest-field">
                <label>Artist</label>
                <div class="artist-picker-wrap">
                  <input type="text" id="f-artist" value="${esc(f.artist_name)}" autocomplete="off" placeholder="Search or type artist name…" />
                  <div class="artist-dropdown" id="f-artist-dropdown" style="display:none"></div>
                </div>
              </div>
              <div class="ingest-field">
                <label>Sort name <span style="color:var(--t3); font-weight:400">(if new)</span></label>
                <input type="text" id="f-sort-name" value="${esc(f.sort_name || '')}" placeholder="Last, First" />
              </div>
            </div>

            <!-- Date: Year / Month / Day (no "Start") -->
            <div class="ingest-field-grid date-grid" style="margin-top:5px">
              <div class="ingest-field"><label>Year</label><input type="number" id="f-year" value="${esc(f.start_year)}" min="1900" max="2099" /></div>
              <div class="ingest-field"><label>Mo</label><input type="number" id="f-month" value="${esc(f.start_month)}" min="1" max="12" /></div>
              <div class="ingest-field"><label>Day</label><input type="number" id="f-day" value="${esc(f.start_day)}" min="1" max="31" /></div>
            </div>
            <div id="end-date-toggle-row" style="margin-top:2px">
              <a class="field-toggle-link" id="btn-toggle-end-date" href="#">+ End date</a>
            </div>
            <div class="ingest-field-grid date-grid" id="end-date-row" style="margin-top:4px; display:none">
              <div class="ingest-field"><label>End yr</label><input type="number" id="f-end-year" value="${esc(f.end_year)}" min="1900" max="2099" /></div>
              <div class="ingest-field"><label>Mo</label><input type="number" id="f-end-month" value="${esc(f.end_month)}" min="1" max="12" /></div>
              <div class="ingest-field"><label>Day</label><input type="number" id="f-end-day" value="${esc(f.end_day)}" min="1" max="31" /></div>
            </div>

            <!-- Venue -->
            <div class="ingest-field" style="margin-top:8px">
              <label>Venue</label>
              <div class="venue-picker-wrap">
                <input type="text" id="f-venue-name" value="${esc(f.venue_name)}" autocomplete="off" placeholder="Search or type venue name…" />
                <input type="hidden" id="f-venue-id" value="${esc(String(f.venue_id || ''))}" />
                <div class="venue-dropdown" id="f-venue-dropdown" style="display:none"></div>
              </div>
            </div>

            <!-- City / State / Country — state is narrow -->
            <div class="ingest-field-grid" style="grid-template-columns:1fr 58px 1fr; gap:6px; margin-top:5px" id="f-location-row">
              <div class="ingest-field"><label>City</label><input type="text" id="f-city" value="${esc(f.city)}" /></div>
              <div class="ingest-field"><label>St</label><input type="text" id="f-state" value="${esc(f.state)}" maxlength="6" /></div>
              <div class="ingest-field"><label>Country</label><input type="text" id="f-country" value="${esc(f.country)}" /></div>
            </div>

            <!-- Event (festival) — optional -->
            <div class="ingest-field" style="margin-top:6px">
              <label>Festival / Event <span style="font-weight:400; opacity:0.6">(optional)</span></label>
              <div class="event-picker-wrap">
                <input type="text" id="f-event-name" value="${esc(f.event_name || '')}" autocomplete="off" placeholder="e.g. Bonnaroo 2009" />
                <input type="hidden" id="f-event-id" value="${esc(String(f.event_id || ''))}" />
                <div class="event-dropdown" id="f-event-dropdown" style="display:none"></div>
              </div>
            </div>

            <!-- Source / Detail / Quality -->
            <div class="ingest-field-grid" style="grid-template-columns:72px 1fr 66px; gap:6px; margin-top:8px">
              <div class="ingest-field">
                <label>Source</label>
                <select id="f-source">
                  <option value="">—</option>
                  ${['SBD','AUD','MTX','FM','Other'].map(s =>
                    `<option value="${s}" ${f.source === s ? 'selected' : ''}>${s}</option>`
                  ).join('')}
                </select>
              </div>
              <div class="ingest-field">
                <label>Source detail</label>
                <input type="text" id="f-modifier" value="${esc(f.source_modifier)}" />
              </div>
              <div class="ingest-field">
                <label>Quality</label>
                <input type="text" id="f-quality" value="${esc(f.quality)}" />
              </div>
            </div>

            <div class="ingest-field" style="margin-top:6px">
              <label>Lineage</label>
              <textarea id="f-lineage" style="min-height:40px">${esc(f.lineage)}</textarea>
            </div>

            <div class="ingest-field" style="margin-top:6px">
              <label>Notes</label>
              <textarea id="f-notes" style="min-height:50px">${esc(f.notes)}</textarea>
            </div>

            <label class="official-release-check" style="margin-top:8px">
              <input type="checkbox" id="f-is-official" ${f.is_official ? 'checked' : ''} />
              <span>Official release</span>
              <span class="official-release-note">Cascades to all tracks</span>
            </label>

            <!-- Track table -->
            <div class="rev-section-title" style="margin-top:16px; padding-top:12px; border-top:1px solid var(--bd-1)">
              Tracks <span style="font-weight:400; text-transform:none; letter-spacing:0; color:var(--t2)">(${ingest.tracks.length})</span>
            </div>
            ${mismatchBanner}
            <div style="overflow:auto; margin-bottom:4px">
              <table class="track-review-table">
                <thead>
                  <tr>
                    <th style="width:32px">#</th>
                    <th style="width:28px"></th>
                    <th>Title</th>
                    <th style="width:44px">Time</th>
                    <th style="width:24px"></th>
                  </tr>
                </thead>
                <tbody>${trackRows || '<tr><td colspan="5" style="color:var(--t2);padding:12px">No tracks found</td></tr>'}</tbody>
              </table>
            </div>

          </div>
          <div class="ingest-actions" style="padding:10px 20px; border-top:1px solid var(--bd-0)">
            <button class="btn btn-ghost btn-sm" id="btn-back-folder">← Back</button>
            <!-- Audio preview player lives here so it's always visible above the fold -->
            <div id="ingest-audio-bar" class="ingest-audio-footer" style="display:none">
              <span id="ingest-audio-label"></span>
              <audio id="ingest-preview-audio" preload="none" controls></audio>
            </div>
            <button class="btn btn-primary" id="btn-confirm">Confirm →</button>
          </div>
        </div>

        <!-- Resize handle -->
        <div class="rev-resize-handle" id="rev-divider"></div>

        <!-- Right: collapsible reference panels (FLAC Tags → Parsed → Info file) -->
        <div class="ingest-review-raw">

          <!-- Panel 1: FLAC Tags -->
          <div class="rev-panel">
            <div class="rev-panel-header">
              <button class="rev-panel-toggle" data-panel="panel-flac">▾</button>
              <span>FLAC Tags</span>
              <span class="rev-panel-badge">read-only</span>
            </div>
            <div class="rev-panel-body" id="panel-flac">
              <div class="rev-raw-section">${rawTagRows}</div>
              ${rawTracksSection}
            </div>
          </div>

          <!-- Panel 2: Parsed from info file -->
          <div class="rev-panel">
            <div class="rev-panel-header rev-panel-header--highlight">
              <button class="rev-panel-toggle" data-panel="panel-parsed">▾</button>
              <span>Parsed from info file</span>
            </div>
            <div class="rev-panel-body" id="panel-parsed">
              ${parsedPanelBody}
            </div>
          </div>

          <!-- Panel 3: Info file text (takes remaining height) -->
          <div class="rev-panel rev-panel-grow">
            <div class="rev-panel-header">
              <button class="rev-panel-toggle" data-panel="panel-info">▾</button>
              <span>Info file</span>
            </div>
            <div class="rev-panel-body rev-info-scroll" id="panel-info">
              <div class="rev-raw-section">${infoText}</div>
            </div>
          </div>

        </div>

      </div>`)

    // Parsed info file — apply buttons
    ;(function () {
      mainContent.querySelectorAll('.btn-parsed-apply').forEach(btn => {
        btn.addEventListener('click', e => {
          e.preventDefault()
          const action = btn.dataset.action
          const val    = btn.dataset.val || ''

          if (action === 'apply-artist') {
            document.getElementById('f-artist').value = val

          } else if (action === 'apply-date') {
            document.getElementById('f-year').value  = btn.dataset.year  || ''
            document.getElementById('f-month').value = btn.dataset.month || ''
            document.getElementById('f-day').value   = btn.dataset.day   || ''

          } else if (action === 'apply-venue') {
            document.getElementById('f-venue-name').value = val
            document.getElementById('f-venue-id').value   = ''  // clear any locked venue

          } else if (action === 'apply-city') {
            document.getElementById('f-city').value = val

          } else if (action === 'apply-state') {
            document.getElementById('f-state').value = val

          } else if (action === 'apply-country') {
            document.getElementById('f-country').value = val

          } else if (action === 'apply-tracks') {
            const titles  = (info.tracks || []).map(t => titleCase(t.title))
            const inputs  = [...mainContent.querySelectorAll('.t-title')]
            inputs.forEach((inp, i) => { if (titles[i] != null) inp.value = titles[i] })
          }

          // Quick flash to confirm
          btn.textContent = '✓'
          setTimeout(() => { btn.textContent = '←' }, 800)
        })
      })
    })()

    // Ingest track preview — play/pause individual audio files
    ;(function () {
      const audioEl  = document.getElementById('ingest-preview-audio')
      const audioBar = document.getElementById('ingest-audio-bar')
      const audioLbl = document.getElementById('ingest-audio-label')
      if (!audioEl) return

      let activeBtn = null

      mainContent.querySelectorAll('.btn-preview-track').forEach(btn => {
        btn.addEventListener('click', e => {
          e.preventDefault()
          const filename = btn.dataset.filename
          if (!filename) return

          // Toggle off if clicking the currently playing track
          if (activeBtn === btn && !audioEl.paused) {
            audioEl.pause()
            btn.textContent = '▶'
            activeBtn = null
            return
          }

          // Stop whatever was playing before
          if (activeBtn && activeBtn !== btn) {
            audioEl.pause()
            activeBtn.textContent = '▶'
          }

          const url = `/api/stream/ingest-preview?folder=${encodeURIComponent(ingest.folderPath)}&file=${encodeURIComponent(filename)}`
          audioEl.src = url
          audioEl.play()
          btn.textContent = '■'
          activeBtn = btn
          audioLbl.textContent = filename
          audioBar.style.display = 'flex'
        })
      })

      audioEl.addEventListener('ended', () => {
        if (activeBtn) { activeBtn.textContent = '▶'; activeBtn = null }
      })
      audioEl.addEventListener('pause', () => {
        // Only reset button if pause wasn't triggered by our own toggle handler
        // (that handler sets activeBtn = null itself)
      })
    })()

    // Right panel — collapsible panels
    ;(function () {
      mainContent.querySelectorAll('.rev-panel-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
          const panel = document.getElementById(btn.dataset.panel)
          if (!panel) return
          const collapsed = panel.style.display === 'none'
          panel.style.display = collapsed ? '' : 'none'
          btn.textContent = collapsed ? '▾' : '▸'
        })
      })
    })()

    // Text file switcher — swap which info file drives the parsed panel + raw text
    ;(function () {
      const candidates = ingest.scan.text_file_candidates || []
      if (candidates.length <= 1) return

      mainContent.querySelectorAll('.info-file-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const idx = parseInt(btn.dataset.idx)
          if (isNaN(idx) || !candidates[idx]) return

          ingest._activeTextIdx = idx
          const chosen = candidates[idx]

          // Swap active scan data so re-renders pick it up
          ingest.scan.info_file_content = chosen.content
          ingest.scan.suggestions.from_info_file = chosen.suggestions

          // Re-render the whole review step to update parsed panel + raw text
          renderIngestReview()
        })
      })
    })()

    // Ingest track expand buttons — optional detail rows
    mainContent.querySelectorAll('.it-expand-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const row = document.getElementById(`it-detail-${btn.dataset.idx}`)
        if (!row) return
        const open = row.style.display !== 'none'
        row.style.display = open ? 'none' : ''
        btn.classList.toggle('active', !open)
      })
    })

    // Ingest flag pill toggles — update ingest.tracks in memory on click
    mainContent.querySelectorAll('.flag-pill[data-idx]').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.classList.toggle('active')
        const idx = parseInt(btn.dataset.idx)
        const flag = btn.dataset.flag
        const t = ingest.tracks[idx]
        if (!t) return
        t.flags = t.flags || []
        if (btn.classList.contains('active')) {
          if (!t.flags.includes(flag)) t.flags.push(flag)
        } else {
          t.flags = t.flags.filter(f => f !== flag)
        }
      })
    })

    // is_official checkbox on recording form — cascade to all track checkboxes
    document.getElementById('f-is-official')?.addEventListener('change', function () {
      if (this.checked) {
        mainContent.querySelectorAll('.t-official').forEach(cb => { cb.checked = true })
      }
    })

    // Parsed tracks toggle — expand/collapse the track list
    ;(function () {
      const toggleBtn = document.getElementById('btn-parsed-tracks-toggle')
      const trackList = document.getElementById('rev-parsed-tracklist')
      if (!toggleBtn || !trackList) return
      toggleBtn.addEventListener('click', e => {
        e.stopPropagation()  // don't bubble to panel toggle
        const visible = trackList.style.display !== 'none'
        trackList.style.display = visible ? 'none' : ''
        toggleBtn.textContent = visible ? '▾' : '▴'  // ▴=visible, ▾=collapsed
      })
    })()

    // Artist autocomplete
    ;(function () {
      const nameEl  = document.getElementById('f-artist')
      const dropEl  = document.getElementById('f-artist-dropdown')
      if (!nameEl || !dropEl) return
      let debounce = null

      function closeDropdown() { dropEl.style.display = 'none'; dropEl.innerHTML = '' }

      function showResults(names, q) {
        dropEl.innerHTML = ''
        const rows = names.map(name => `
          <div class="artist-result" data-name="${esc(name)}">${esc(name)}</div>`
        ).join('')
        // Always offer "use as typed" create option if text doesn't exactly match
        const exactMatch = names.some(n => n.toLowerCase() === q.toLowerCase())
        const createRow = (!exactMatch && q)
          ? `<div class="artist-result artist-result-new" data-name="${esc(q)}">+ New artist: "${esc(q)}"</div>`
          : ''
        dropEl.innerHTML = rows + createRow
        dropEl.style.display = (rows || createRow) ? 'block' : 'none'

        dropEl.querySelectorAll('.artist-result').forEach(el => {
          el.addEventListener('mousedown', e => {
            e.preventDefault()
            nameEl.value = el.dataset.name
            closeDropdown()
          })
        })
      }

      nameEl.addEventListener('input', () => {
        const q = nameEl.value.trim()
        clearTimeout(debounce)
        if (q.length < 2) { closeDropdown(); return }
        debounce = setTimeout(async () => {
          try { showResults(await API.artists.search(q), q) }
          catch (_) { closeDropdown() }
        }, 220)
      })

      nameEl.addEventListener('blur',  () => setTimeout(closeDropdown, 200))
      nameEl.addEventListener('focus', () => {
        if (nameEl.value.trim().length >= 2) nameEl.dispatchEvent(new Event('input'))
      })
    })()

    // End date toggle — show/hide the row; pre-fill from start date on first reveal
    ;(function () {
      const toggleBtn = document.getElementById('btn-toggle-end-date')
      const endRow    = document.getElementById('end-date-row')
      if (!toggleBtn || !endRow) return

      // If end date was already set (back-nav), show immediately
      if (ingest.form.end_year) {
        endRow.style.display = ''
        toggleBtn.textContent = '− End date'
      }

      toggleBtn.addEventListener('click', e => {
        e.preventDefault()
        const visible = endRow.style.display !== 'none'
        if (visible) {
          // Hide and clear
          endRow.style.display = 'none'
          toggleBtn.textContent = '+ End date'
          document.getElementById('f-end-year').value  = ''
          document.getElementById('f-end-month').value = ''
          document.getElementById('f-end-day').value   = ''
        } else {
          // Show and pre-fill from start date
          endRow.style.display = ''
          toggleBtn.textContent = '− End date'
          const yr = document.getElementById('f-year').value
          const mo = document.getElementById('f-month').value
          const dy = document.getElementById('f-day').value
          document.getElementById('f-end-year').value  = yr
          document.getElementById('f-end-month').value = mo
          document.getElementById('f-end-day').value   = dy
          document.getElementById('f-end-year').focus()
        }
      })
    })()

    // Venue picker — autocomplete with lock/unlock of city/state/country
    ;(function () {
      const nameEl  = document.getElementById('f-venue-name')
      const idEl    = document.getElementById('f-venue-id')
      const dropEl  = document.getElementById('f-venue-dropdown')
      const cityEl  = document.getElementById('f-city')
      const stateEl = document.getElementById('f-state')
      const cntryEl = document.getElementById('f-country')
      let debounce  = null

      function lockLocation(venue) {
        cityEl.value  = venue.city    || ''
        stateEl.value = venue.state   || ''
        cntryEl.value = venue.country || ''
        cityEl.disabled  = true
        stateEl.disabled = true
        cntryEl.disabled = true
      }

      function unlockLocation() {
        cityEl.disabled  = false
        stateEl.disabled = false
        cntryEl.disabled = false
      }

      // Restore lock on back-nav if a venue was previously selected
      if (ingest.form.venue_id) {
        API.venues.get(ingest.form.venue_id).then(v => lockLocation(v)).catch(() => {})
      }

      function closeDropdown() { dropEl.style.display = 'none'; dropEl.innerHTML = '' }

      function showResults(venues, q) {
        dropEl.innerHTML = ''
        const rows = venues.map(v => {
          const loc = [v.city, v.state, v.country].filter(Boolean).join(', ')
          return `<div class="venue-result" data-id="${v.id}" data-name="${esc(v.name)}">
            <span class="venue-result-name">${esc(v.name)}</span>
            ${loc ? `<span class="venue-result-loc">${esc(loc)}</span>` : ''}
          </div>`
        }).join('')
        const createRow = q
          ? `<div class="venue-result venue-result-create" data-id="" data-name="${esc(q)}">+ Create "${esc(q)}"</div>`
          : ''
        dropEl.innerHTML = rows + createRow
        dropEl.style.display = (rows || createRow) ? 'block' : 'none'

        dropEl.querySelectorAll('.venue-result').forEach(el => {
          el.addEventListener('mousedown', async e => {
            e.preventDefault()
            if (el.dataset.id) {
              // Existing venue — lock location fields to venue's stored values
              idEl.value   = el.dataset.id
              nameEl.value = el.dataset.name
              try {
                const v = await API.venues.get(parseInt(el.dataset.id))
                lockLocation(v)
              } catch (_) {}
            } else {
              // New venue — just set the name, leave ID empty so confirm endpoint
              // creates it with city/state/country from the form fields
              nameEl.value = q
              idEl.value   = ''
              unlockLocation()
            }
            closeDropdown()
          })
        })
      }

      nameEl.addEventListener('input', () => {
        idEl.value = ''     // clear selection when user edits
        unlockLocation()    // re-enable location fields when typing
        const q = nameEl.value.trim()
        clearTimeout(debounce)
        if (q.length < 2) { closeDropdown(); return }
        debounce = setTimeout(async () => {
          try { showResults(await API.venues.list(q), q) }
          catch (_) { closeDropdown() }
        }, 220)
      })

      nameEl.addEventListener('blur',  () => setTimeout(closeDropdown, 200))
      nameEl.addEventListener('focus', () => {
        if (nameEl.value.trim().length >= 2) nameEl.dispatchEvent(new Event('input'))
      })
    })()

    // Event picker — simple autocomplete (no location lock, just name+id)
    ;(function () {
      const nameEl = document.getElementById('f-event-name')
      const idEl   = document.getElementById('f-event-id')
      const dropEl = document.getElementById('f-event-dropdown')
      let debounce = null

      function closeDropdown() { dropEl.style.display = 'none'; dropEl.innerHTML = '' }

      function showResults(events, q) {
        dropEl.innerHTML = ''
        const rows = events.map(ev => `
          <div class="event-result" data-id="${ev.id}" data-name="${esc(ev.name)}">
            ${esc(ev.name)}
          </div>`).join('')
        const createRow = q
          ? `<div class="event-result event-result-create" data-id="" data-name="${esc(q)}">+ Create "${esc(q)}"</div>`
          : ''
        dropEl.innerHTML = rows + createRow
        dropEl.style.display = (rows || createRow) ? 'block' : 'none'

        dropEl.querySelectorAll('.event-result').forEach(el => {
          el.addEventListener('mousedown', async e => {
            e.preventDefault()
            if (el.dataset.id) {
              idEl.value   = el.dataset.id
              nameEl.value = el.dataset.name
            } else {
              // Create new event record on the fly
              try {
                const created = await API.events.create({ name: q })
                idEl.value   = created.id
                nameEl.value = created.name
              } catch (err) { console.error('Failed to create event:', err) }
            }
            closeDropdown()
          })
        })
      }

      nameEl.addEventListener('input', () => {
        idEl.value = ''
        const q = nameEl.value.trim()
        clearTimeout(debounce)
        if (q.length < 2) { closeDropdown(); return }
        debounce = setTimeout(async () => {
          try { showResults(await API.events.search(q), q) }
          catch (_) { closeDropdown() }
        }, 220)
      })

      nameEl.addEventListener('blur',  () => setTimeout(closeDropdown, 200))
      nameEl.addEventListener('focus', () => {
        if (nameEl.value.trim().length >= 2) nameEl.dispatchEvent(new Event('input'))
      })
    })()

    // Enter key on track title → select next track's title
    const titleInputs = [...mainContent.querySelectorAll('.t-title')]
    titleInputs.forEach((el, i) => {
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault()
          const next = titleInputs[i + 1]
          if (next) { next.focus(); next.select() }
        }
      })
    })

    document.getElementById('btn-back-folder').addEventListener('click', () => {
      ingest.step = 'folder'
      renderIngestStep()
    })

    document.getElementById('btn-confirm').addEventListener('click', () => {
      // Collect metadata
      const f = ingest.form
      f.artist_name     = document.getElementById('f-artist').value.trim()
      f.sort_name       = document.getElementById('f-sort-name').value.trim() || null
      f.start_year      = parseInt(document.getElementById('f-year').value)      || null
      f.start_month     = parseInt(document.getElementById('f-month').value)     || null
      f.start_day       = parseInt(document.getElementById('f-day').value)       || null
      f.end_year        = parseInt(document.getElementById('f-end-year').value)  || null
      f.end_month       = parseInt(document.getElementById('f-end-month').value) || null
      f.end_day         = parseInt(document.getElementById('f-end-day').value)   || null
      f.venue_name      = document.getElementById('f-venue-name').value.trim()
      f.venue_id        = parseInt(document.getElementById('f-venue-id').value) || null
      f.city            = document.getElementById('f-city').value.trim()
      f.state           = document.getElementById('f-state').value.trim()
      f.country         = document.getElementById('f-country').value.trim()
      f.event_name      = document.getElementById('f-event-name').value.trim()
      f.event_id        = parseInt(document.getElementById('f-event-id').value) || null
      f.is_official     = document.getElementById('f-is-official').checked
      f.source          = document.getElementById('f-source').value
      f.source_modifier = document.getElementById('f-modifier').value.trim()
      f.quality         = document.getElementById('f-quality').value.trim()
      f.lineage         = document.getElementById('f-lineage').value.trim()
      f.notes           = document.getElementById('f-notes').value.trim()

      if (!f.artist_name) { alert('Artist name is required.'); return }

      // Collect all track field edits into ingest.tracks
      mainContent.querySelectorAll('.t-title').forEach(el => {
        const t = ingest.tracks[parseInt(el.dataset.idx)]; if (t) t.title = el.value.trim()
      })
      mainContent.querySelectorAll('.t-official').forEach(el => {
        const t = ingest.tracks[parseInt(el.dataset.idx)]; if (t) t.is_official = el.checked
      })
      mainContent.querySelectorAll('.t-songwriter').forEach(el => {
        const t = ingest.tracks[parseInt(el.dataset.idx)]; if (t) t.songwriter = el.value.trim() || null
      })
      mainContent.querySelectorAll('.t-track-notes').forEach(el => {
        const t = ingest.tracks[parseInt(el.dataset.idx)]; if (t) t.notes = el.value.trim() || null
      })
      // Flags are already kept live in ingest.tracks by the pill click handler

      ingest.step = 'confirm'
      renderIngestStep()
    })

    // Resize handle
    wireResizablePanel(
      mainContent.querySelector('.ingest-review-shell'),
      mainContent.querySelector('.ingest-review-form'),
      document.getElementById('rev-divider'),
      260, 200
    )
  }

  // ── Step 3: Track list review ──────────────────────────────────────────────

  function fmtDur(s) {
    if (!s) return '—'
    const m = Math.floor(s / 60), sec = Math.floor(s % 60)
    return `${m}:${String(sec).padStart(2,'0')}`
  }

  function renderIngestTracks() {
    const rows = ingest.tracks.map((t, i) => `
      <tr>
        <td class="num">${t.track_number}</td>
        <td><input type="text" class="t-title" data-idx="${i}" value="${esc(t.title)}" /></td>
        <td class="dur">${fmtDur(t.duration)}</td>
        <td class="fname">${esc(t.filename)}</td>
      </tr>`).join('')

    setMainHTML(`
      <div class="ingest-view" style="max-width:760px">
        <div class="ingest-step-header">
          <h2>Review tracks</h2>
          ${stepDots('tracks')}
        </div>
        <div class="sub" style="margin-bottom:16px">
          Edit track titles. Changes are saved when you continue.
        </div>

        <div class="ingest-section" style="padding:0; overflow:auto; max-height:460px">
          <table class="track-review-table">
            <thead>
              <tr>
                <th style="width:32px">#</th>
                <th>Title</th>
                <th style="width:44px">Time</th>
                <th style="width:160px">Filename</th>
              </tr>
            </thead>
            <tbody id="track-tbody">${rows}</tbody>
          </table>
        </div>

        <div class="ingest-actions" style="margin-top:16px">
          <button class="btn btn-ghost btn-sm" id="btn-back-review">← Back</button>
          <button class="btn btn-primary" id="btn-next-confirm">Confirm →</button>
        </div>
      </div>`)

    document.getElementById('btn-back-review').addEventListener('click', () => {
      ingest.step = 'review'
      renderIngestStep()
    })

    document.getElementById('btn-next-confirm').addEventListener('click', () => {
      // Collect current title edits back into ingest.tracks
      mainContent.querySelectorAll('.t-title').forEach(el => {
        ingest.tracks[parseInt(el.dataset.idx)].title = el.value.trim()
      })
      ingest.step = 'confirm'
      renderIngestStep()
    })
  }

  // ── Step 4: Confirm & submit ───────────────────────────────────────────────

  function buildFolderName() {
    // Mirror the Python build_folder_name logic for preview purposes
    const f = ingest.form
    const pad = n => n ? String(n).padStart(2,'0') : null
    let date = f.start_year
      ? (f.start_month && f.start_day
          ? `${f.start_year}-${pad(f.start_month)}-${pad(f.start_day)}`
          : f.start_month ? `${f.start_year}-${pad(f.start_month)}`
          : String(f.start_year))
      : 'Unknown Date'
    const venue = f.venue_name || 'Unknown Venue'
    const loc   = f.city && f.state ? `${f.city}, ${f.state}`
                : f.city ? f.city
                : f.state || 'Unknown Location'
    const src   = f.source
      ? (f.source_modifier ? `${f.source} - ${f.source_modifier}` : f.source)
      : null
    let name = `${f.artist_name || 'Unknown Artist'} - ${date} - ${venue} - ${loc}`
    if (src) name += ` (${src})`
    return name
  }

  function renderIngestConfirm() {
    const f          = ingest.form
    const folderName = buildFolderName()

    const trackRows = ingest.tracks.map(t => `
      <div class="confirm-track-row">
        <span class="confirm-track-num">${String(t.track_number).padStart(2, '0')}</span>
        <span class="confirm-track-title">${esc(t.title)}</span>
        <span class="confirm-track-dur">${fmtDur(t.duration)}</span>
      </div>`).join('')

    setMainHTML(`
      <div class="ingest-view" style="max-width:640px">
        <div class="ingest-step-header">
          <h2>Confirm &amp; Add to Library</h2>
          ${stepDots('confirm')}
        </div>

        <!-- 1. Library destination -->
        <div class="confirm-summary">
          <div class="ingest-section-title">Library destination</div>
          <div class="confirm-folder-name">${esc(folderName)}</div>
          <div class="confirm-row" style="margin-top:10px; border-top:none">
            <span class="label">File behavior</span>
            <div class="behavior-toggle">
              <button class="behavior-btn ${ingest.behavior === 'copy' ? 'active' : ''}" data-beh="copy">Copy files</button>
              <button class="behavior-btn ${ingest.behavior === 'move' ? 'active' : ''}" data-beh="move">Move files</button>
            </div>
          </div>
        </div>

        <!-- 2. Recording metadata -->
        <div class="confirm-summary">
          <div class="ingest-section-title">Recording</div>
          <div class="confirm-row">
            <span class="label">Artist</span>
            <span class="value">${esc(f.artist_name)}</span>
          </div>
          <div class="confirm-row">
            <span class="label">Start date</span>
            <span class="value">${fmtDate(f.start_year, f.start_month, f.start_day)}</span>
          </div>
          ${f.end_year ? `
          <div class="confirm-row">
            <span class="label">End date</span>
            <span class="value">${fmtDate(f.end_year, f.end_month, f.end_day)}</span>
          </div>` : ''}
          <div class="confirm-row">
            <span class="label">Venue</span>
            <span class="value">${esc(f.venue_name || '—')}</span>
          </div>
          <div class="confirm-row">
            <span class="label">Location</span>
            <span class="value">${esc([f.city, f.state].filter(Boolean).join(', ') || '—')}</span>
          </div>
          ${f.event_name ? `
          <div class="confirm-row">
            <span class="label">Festival / Event</span>
            <span class="value">${esc(f.event_name)}</span>
          </div>` : ''}
          <div class="confirm-row">
            <span class="label">Source</span>
            <span class="value">${esc([f.source, f.source_modifier].filter(Boolean).join(' · ') || '—')}</span>
          </div>
          <div class="confirm-row">
            <span class="label">Quality</span>
            <span class="value">${esc(f.quality || '—')}</span>
          </div>
          ${f.lineage ? `
          <div class="confirm-row">
            <span class="label">Lineage</span>
            <span class="value" style="word-break:break-word;white-space:pre-wrap">${esc(f.lineage)}</span>
          </div>` : ''}
          ${f.notes ? `
          <div class="confirm-row">
            <span class="label">Notes</span>
            <span class="value" style="word-break:break-word;white-space:pre-wrap">${esc(f.notes)}</span>
          </div>` : ''}
        </div>

        <!-- 3. Tracks -->
        <div class="confirm-summary">
          <div class="ingest-section-title">Tracks (${ingest.tracks.length})</div>
          ${trackRows || '<div style="color:var(--t2);font-size:12px">No tracks</div>'}
        </div>

        <div id="confirm-error" style="color:var(--red); font-size:12px; margin-bottom:12px; display:none"></div>

        <div class="ingest-actions">
          <button class="btn btn-ghost btn-sm" id="btn-back-tracks">← Back</button>
          <button class="btn btn-primary" id="btn-add-library">Add to library</button>
        </div>
      </div>`)

    // Behavior toggle
    mainContent.querySelectorAll('.behavior-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        ingest.behavior = btn.dataset.beh
        mainContent.querySelectorAll('.behavior-btn').forEach(b => b.classList.remove('active'))
        btn.classList.add('active')
      })
    })

    document.getElementById('btn-back-tracks').addEventListener('click', () => {
      ingest.step = 'review'
      renderIngestStep()
    })

    document.getElementById('btn-add-library').addEventListener('click', async () => {
      const btn    = document.getElementById('btn-add-library')
      const errEl  = document.getElementById('confirm-error')
      btn.disabled = true
      btn.textContent = 'Adding to library…'
      errEl.style.display = 'none'

      const payload = {
        source_folder_path: ingest.folderPath,
        ...ingest.form,
        // Save user's behavior choice into a preference-like field
        // (backend reads from UserPreference, so we temporarily set it via the form)
        tracks: ingest.tracks,
        fingerprints: ingest.scan.fingerprints || [],
        info_file_content: ingest.scan.info_file_content || null,
      }

      try {
        const result = await API.ingest.confirm(payload)
        ingest.step     = 'success'
        ingest._lastResult = result
        renderIngestStep()
      } catch (e) {
        errEl.textContent = `Error: ${e.message}`
        errEl.style.display = 'block'
        btn.disabled = false
        btn.textContent = 'Add to library'
      }
    })
  }

  // ── Step 5: Success ────────────────────────────────────────────────────────

  function renderIngestSuccess() {
    const result = ingest._lastResult || {}
    setMainHTML(`
      <div class="ingest-view">
        <div class="success-state">
          <div class="success-icon">✓</div>
          <div class="success-title">Recording added to library</div>
          <div class="success-sub">${esc(ingest.form.artist_name)} · ${fmtDate(ingest.form.start_year, ingest.form.start_month, ingest.form.start_day)}</div>
          <div style="display:flex; gap:10px; margin-top:20px">
            <button class="btn btn-primary" id="btn-view-recording">View recording</button>
            <button class="btn btn-ghost" id="btn-add-another">Add another</button>
          </div>
        </div>
      </div>`)

    document.getElementById('btn-view-recording').addEventListener('click', () => {
      if (result.artist_id) {
        // Refresh artist list then navigate
        loadArtistList().then(() => {
          window.location.hash = `#/recording/${result.recording_id}`
        })
      }
    })

    document.getElementById('btn-add-another').addEventListener('click', () => {
      // Reset wizard
      ingest.step = 'folder'
      ingest.scan = null
      ingest.folderPath = null
      ingest.form = {}
      ingest.tracks = []
      renderIngestStep()
      loadArtistList()  // refresh sidebar counts
    })
  }

  // ── Recording metadata editor ──────────────────────────────────────────────

  function renderRecordingEdit(recordingId, rec, perf) {
    const backHash  = state.selectedArtist ? `#/artist/${state.selectedArtist.id}` : '#/'
    const perfName  = perf?.performer || ''
    const dateStr   = perf ? fmtDateLong(perf.start_year, perf.start_month, perf.start_day) : ''
    const venueStr  = perf?.venue_name || ''
    const locStr    = perf ? fmtLocation(perf.city, perf.state, perf.country) : ''
    const dateLine  = [dateStr, venueStr, locStr].filter(Boolean).join(' · ')

    const ALL_FLAGS = [
      { key: 'start_truncated', label: 'Start Truncated' },
      { key: 'end_truncated',   label: 'End Truncated'   },
      { key: 'incomplete',      label: 'Incomplete'       },
      { key: 'unknown_title',   label: 'Unknown Title'    },
      { key: 'banter',          label: 'Banter'           },
      { key: 'tuning',          label: 'Tuning'           },
      { key: 'audience',        label: 'Audience'         },
      { key: 'medley',          label: 'Medley'           },
      { key: 'announcement',    label: 'Announcement'     },
      { key: 'interview',       label: 'Interview'        },
      { key: 'introduction',    label: 'Introduction'     },
      { key: 'band_intros',     label: 'Band Intros'      },
    ]

    // Track rows — title + is_official, with expandable optional detail section
    const trackRows = (rec.tracks || []).map(t => {
      const flagPills = ALL_FLAGS.map(f => {
        const active = (t.flags || []).includes(f.key)
        return `<button class="flag-pill ${active ? 'active' : ''}" data-flag="${f.key}" data-tid="${t.id}" type="button">${f.label}</button>`
      }).join('')
      const isPlaying  = t.id === state.playingTrackId
      const playIcon   = isPlaying ? '▶' : '▷'
      return `
        <tr class="et-row${isPlaying ? ' playing' : ''}" data-id="${t.id}">
          <td class="et-play-cell"><button class="et-play" data-tid="${t.id}" type="button" title="Play this track">${playIcon}</button></td>
          <td class="num">${String(t.track_number || '').padStart(2,'0')}</td>
          <td><input class="et-title" data-id="${t.id}" type="text" value="${esc(t.title)}" /></td>
          <td class="dur">${fmtDuration(t.duration)}</td>
          <td class="et-expand-cell"><button class="et-expand-btn" data-id="${t.id}" type="button" title="Track details">⋯</button></td>
        </tr>
        <tr class="et-detail-row" id="et-detail-${t.id}" style="display:none">
          <td colspan="5">
            <div class="et-detail-body">
              <div class="et-detail-optional-label">Optional track details</div>
              <div class="et-detail-field">
                <label>Songwriter</label>
                <input type="text" class="et-songwriter" data-id="${t.id}" value="${esc(t.songwriter || '')}" placeholder="" />
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label>Flags</label>
                <div class="flag-pill-row">${flagPills}</div>
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label>Track notes</label>
                <textarea class="et-track-notes" data-id="${t.id}" style="min-height:36px">${esc(t.notes || '')}</textarea>
              </div>
              <div class="et-detail-field" style="margin-top:6px">
                <label class="check-label" title="Mark this track as an official release">
                  <input type="checkbox" class="et-official" data-id="${t.id}" ${t.is_official ? 'checked' : ''} />
                  <span>Official release</span>
                </label>
              </div>
            </div>
          </td>
        </tr>`
    }).join('')

    // Info file is editable in this view
    const infoTextarea = `<textarea id="e-info-content" class="info-file-textarea">${esc(rec.info_file_content || '')}</textarea>`

    // Venue: initial display value and stored ID for the picker
    const initVenueName = perf?.venue_name || ''
    const initVenueId   = perf?.venue_id   || ''

    // Back label: "← Performer · Date · Venue" — same compact style as recording detail breadcrumb
    const editBreadcrumb = ['←', perfName, dateStr, venueStr].filter(Boolean).join(' · ')

    setMainHTML(`
      <!-- Header: recording being edited -->
      <div class="edit-header-bar">
        <h2 class="edit-header-title">Editing ${esc(perfName)}</h2>
      </div>
      <!-- Compact single-line edit bar -->
      <div class="action-bar edit-mode-bar">
        <span class="breadcrumb" id="back-btn">${esc(editBreadcrumb)}</span>
        <div style="margin-left:auto; display:flex; gap:8px; align-items:center">
          <div id="edit-error" style="color:var(--red); font-size:11px; display:none"></div>
          <button class="btn btn-ghost btn-sm" id="btn-cancel-edit">Cancel</button>
          <button class="btn btn-primary btn-sm" id="btn-save-edit">Save changes</button>
        </div>
      </div>

      <div class="detail-panels" id="detail-panels" style="height: calc(100vh - var(--player-h) - 48px);">

        <div class="track-panel" id="track-panel" style="overflow-y:auto; padding:14px 20px 32px">

          <!-- Performance fields -->
          <div class="rev-section-title" style="margin-bottom:10px">Performance</div>

          <div class="ingest-field-grid" style="grid-template-columns:1fr 72px 52px 52px; gap:10px; margin-bottom:10px">
            <div class="ingest-field">
              <label>Artist</label>
              <input type="text" value="${esc(perf?.performer||'')}" readonly tabindex="-1"
                style="opacity:.55; cursor:default; background:var(--bg-3)" />
            </div>
            <div class="ingest-field">
              <label>Year</label>
              <input type="number" id="e-year" value="${perf?.start_year||''}" min="1900" max="2099" />
            </div>
            <div class="ingest-field">
              <label>Month</label>
              <input type="number" id="e-month" value="${perf?.start_month||''}" min="1" max="12" />
            </div>
            <div class="ingest-field">
              <label>Day</label>
              <input type="number" id="e-day" value="${perf?.start_day||''}" min="1" max="31" />
            </div>
          </div>

          <!-- Venue live-search picker -->
          <div class="ingest-field" style="margin-bottom:16px">
            <label>Venue</label>
            <div style="display:flex; align-items:flex-start; gap:14px">
              <div class="venue-picker-wrap" style="flex:1">
                <input type="text" id="e-venue-name" value="${esc(initVenueName)}" autocomplete="off" style="width:100%" />
                <input type="hidden" id="e-venue-id" value="${esc(String(initVenueId))}" />
                <div class="venue-dropdown" id="venue-dropdown" style="display:none"></div>
              </div>
              <div id="venue-location-hint" style="font-size:13px; color:var(--t1); padding-top:7px; white-space:nowrap; min-width:120px">${
                esc(fmtLocation(perf?.city, perf?.state, perf?.country))
              }</div>
            </div>
          </div>

          <!-- Recording fields — all on one row -->
          <div class="rev-section-title" style="margin-bottom:10px">Recording</div>

          <div class="ingest-field-grid" style="grid-template-columns:76px 120px 2fr 60px; gap:10px; margin-bottom:10px">
            <div class="ingest-field">
              <label>Source</label>
              <select id="e-source">
                <option value="">—</option>
                ${['SBD','AUD','MTX','FM','Other'].map(s =>
                  `<option value="${s}" ${rec.source === s ? 'selected':''}>${s}</option>`
                ).join('')}
              </select>
            </div>
            <div class="ingest-field">
              <label>Source Detail</label>
              <input type="text" id="e-modifier" value="${esc(rec.source_modifier||'')}" />
            </div>
            <div class="ingest-field">
              <label>Lineage</label>
              <input type="text" id="e-lineage" value="${esc(rec.lineage||'')}" />
            </div>
            <div class="ingest-field">
              <label>Quality</label>
              <input type="text" id="e-quality" value="${esc(rec.quality||'')}" />
            </div>
            <div class="ingest-field">
              <label>Rating <span style="color:var(--t3);font-size:10px">0–100</span></label>
              <input type="number" id="e-rating" min="0" max="100"
                     style="width:72px"
                     value="${rec.rating != null ? rec.rating : ''}"
                     placeholder="—" />
            </div>
          </div>

          <div class="rev-section-title" style="margin-bottom:8px">Tracks</div>
          <table class="track-review-table" style="margin-bottom:16px">
            <thead>
              <tr>
                <th style="width:24px"></th>
                <th style="width:32px">#</th>
                <th>Title</th>
                <th style="width:44px">Time</th>
                <th style="width:24px"></th>
              </tr>
            </thead>
            <tbody>${trackRows}</tbody>
          </table>

          <div class="ingest-field" style="margin-bottom:16px">
            <label>Notes</label>
            <textarea id="e-notes" style="min-height:80px">${esc(rec.notes||'')}</textarea>
          </div>

          <label style="display:flex; align-items:center; gap:8px; color:var(--t3); font-size:11px; margin-top:4px; cursor:pointer">
            <input type="checkbox" id="e-is-official" ${rec.is_official ? 'checked' : ''} />
            <span>Official release</span>
            <span style="color:var(--t3); font-style:italic">— marks recording and all tracks as officially released</span>
          </label>

        </div>

        <div class="detail-resize-handle" id="detail-divider"></div>

        <!-- Info panel: overflow:hidden so header stays pinned, textarea scrolls itself -->
        <div class="info-panel" style="display:flex; flex-direction:column; overflow:hidden; flex:1; min-width:0">
          <div class="info-panel-header info-panel-header--hint" style="position:relative; flex-shrink:0">
            <span>Info file</span>
            <span class="info-panel-hint">(click to edit)</span>
          </div>
          ${infoTextarea}
        </div>

      </div>`)

    // Back / cancel — both return to recording view, not artist
    document.getElementById('back-btn').addEventListener('click', () => {
      renderRecordingView(recordingId)
    })
    document.getElementById('btn-cancel-edit').addEventListener('click', () => {
      renderRecordingView(recordingId)
    })

    // Resizable split divider
    wireResizablePanel(
      document.getElementById('detail-panels'),
      document.getElementById('track-panel'),
      document.getElementById('detail-divider'),
      180, 200
    )

    // Enter key on track title → advance to next track
    const editTitleInputs = [...mainContent.querySelectorAll('.et-title')]
    editTitleInputs.forEach((el, i) => {
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault()
          const next = editTitleInputs[i + 1]
          if (next) { next.focus(); next.select() }
        }
      })
    })

    // Play buttons — audition a track without leaving the edit view
    mainContent.querySelectorAll('.et-play').forEach(btn => {
      btn.addEventListener('click', () => {
        const tid = parseInt(btn.dataset.tid)
        const idx = rec.tracks.findIndex(t => t.id === tid)
        if (idx >= 0) playRecording(recordingId, idx, rec.tracks)
      })
    })

    // Track expand buttons — show/hide optional detail row
    mainContent.querySelectorAll('.et-expand-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const detailRow = document.getElementById(`et-detail-${btn.dataset.id}`)
        if (!detailRow) return
        const open = detailRow.style.display !== 'none'
        detailRow.style.display = open ? 'none' : ''
        btn.classList.toggle('active', !open)
      })
    })

    // Flag pill toggles
    mainContent.querySelectorAll('.flag-pill').forEach(btn => {
      btn.addEventListener('click', () => btn.classList.toggle('active'))
    })

    // Recording is_official checkbox → cascade check to all track checkboxes
    document.getElementById('e-is-official')?.addEventListener('change', function () {
      if (this.checked) {
        mainContent.querySelectorAll('.et-official').forEach(cb => { cb.checked = true })
      }
    })

    // ── Venue live-search picker ──────────────────────────────────────────────
    ;(function () {
      const nameEl     = document.getElementById('e-venue-name')
      const idEl       = document.getElementById('e-venue-id')
      const dropEl     = document.getElementById('venue-dropdown')
      const hintEl     = document.getElementById('venue-location-hint')
      let   debounce   = null

      function closeDropdown() { dropEl.style.display = 'none'; dropEl.innerHTML = '' }

      function setLocationHint(v) {
        // v: venue object with city/state/country, or null to clear
        const loc = v ? fmtLocation(v.city, v.state, v.country) : ''
        hintEl.textContent = loc
      }

      function showResults(venues, q) {
        dropEl.innerHTML = ''
        const rows = venues.map(v => {
          const loc = [v.city, v.state].filter(Boolean).join(', ')
          return `<div class="venue-result" data-id="${v.id}" data-name="${esc(v.name)}">
            <span class="venue-result-name">${esc(v.name)}</span>
            ${loc ? `<span class="venue-result-loc">${esc(loc)}</span>` : ''}
          </div>`
        }).join('')
        const createRow = q
          ? `<div class="venue-result venue-result-create" data-id="" data-name="${esc(q)}">+ Create "${esc(q)}"</div>`
          : ''
        dropEl.innerHTML = rows + createRow
        dropEl.style.display = (rows || createRow) ? 'block' : 'none'

        dropEl.querySelectorAll('.venue-result').forEach(el => {
          el.addEventListener('mousedown', async e => {
            e.preventDefault()
            if (el.dataset.id) {
              // existing venue — fetch full record to show location hint
              idEl.value   = el.dataset.id
              nameEl.value = el.dataset.name
              try {
                const v = await API.venues.get(parseInt(el.dataset.id))
                setLocationHint(v)
              } catch (_) { setLocationHint(null) }
            } else {
              // new venue — just set name, confirm endpoint creates it with location
              nameEl.value = q
              idEl.value   = ''
              setLocationHint(null)
            }
            closeDropdown()
          })
        })
      }

      nameEl.addEventListener('input', () => {
        idEl.value = ''          // clear any prior selection when user edits
        setLocationHint(null)    // clear hint while typing
        const q = nameEl.value.trim()
        clearTimeout(debounce)
        if (q.length < 2) { closeDropdown(); return }
        debounce = setTimeout(async () => {
          try {
            const results = await API.venues.list(q)
            showResults(results, q)
          } catch (_) { closeDropdown() }
        }, 220)
      })

      nameEl.addEventListener('blur', () => setTimeout(closeDropdown, 200))
      nameEl.addEventListener('focus', () => {
        if (nameEl.value.trim().length >= 2) nameEl.dispatchEvent(new Event('input'))
      })
    })()

    // Save
    document.getElementById('btn-save-edit').addEventListener('click', async () => {
      const btn   = document.getElementById('btn-save-edit')
      const errEl = document.getElementById('edit-error')
      btn.disabled = true
      btn.textContent = 'Saving…'
      errEl.style.display = 'none'

      try {
        // 1. Performance fields
        const venueIdRaw = document.getElementById('e-venue-id').value
        const perfUpdate = {
          start_year:  parseInt(document.getElementById('e-year').value)  || null,
          start_month: parseInt(document.getElementById('e-month').value) || null,
          start_day:   parseInt(document.getElementById('e-day').value)   || null,
        }
        // Always send venue_id: set to int if selected, null if name cleared
        const venueName = document.getElementById('e-venue-name').value.trim()
        perfUpdate.venue_id = venueIdRaw ? parseInt(venueIdRaw) : (venueName ? undefined : null)
        if (perfUpdate.venue_id === undefined) delete perfUpdate.venue_id

        await API.performances.update(rec.performance_id, perfUpdate)

        // 2. Recording fields (is_official cascades to tracks server-side when true)
        const ratingRaw = document.getElementById('e-rating').value.trim()
        await API.recordings.update(recordingId, {
          source:            document.getElementById('e-source').value || null,
          source_modifier:   document.getElementById('e-modifier').value.trim() || null,
          lineage:           document.getElementById('e-lineage').value.trim() || null,
          quality:           document.getElementById('e-quality').value.trim() || null,
          rating:            ratingRaw !== '' ? parseInt(ratingRaw, 10) : null,
          notes:             document.getElementById('e-notes').value.trim() || null,
          info_file_content: document.getElementById('e-info-content').value.trim() || null,
          is_official:       document.getElementById('e-is-official').checked,
          change_note:       'Edited via metadata editor',
        })

        // 3. Tracks — collect title + is_official + songwriter + notes + flags per track
        const trackMap = {}  // tid → update payload
        mainContent.querySelectorAll('.et-title').forEach(el => {
          const id = parseInt(el.dataset.id)
          trackMap[id] = trackMap[id] || {}
          trackMap[id].title = el.value.trim() || null
        })
        mainContent.querySelectorAll('.et-official').forEach(el => {
          const id = parseInt(el.dataset.id)
          trackMap[id] = trackMap[id] || {}
          trackMap[id].is_official = el.checked
        })
        mainContent.querySelectorAll('.et-songwriter').forEach(el => {
          const id = parseInt(el.dataset.id)
          trackMap[id] = trackMap[id] || {}
          trackMap[id].songwriter = el.value.trim() || null
        })
        mainContent.querySelectorAll('.et-track-notes').forEach(el => {
          const id = parseInt(el.dataset.id)
          trackMap[id] = trackMap[id] || {}
          trackMap[id].notes = el.value.trim() || null
        })
        // Collect active flag pills per track
        mainContent.querySelectorAll('.flag-pill').forEach(btn => {
          const id = parseInt(btn.dataset.tid)
          trackMap[id] = trackMap[id] || {}
          if (!trackMap[id].flags) trackMap[id].flags = []
          if (btn.classList.contains('active')) trackMap[id].flags.push(btn.dataset.flag)
        })

        await Promise.all(
          Object.entries(trackMap).map(([id, payload]) =>
            API.tracks.update(parseInt(id), payload)
          )
        )

        renderRecordingView(recordingId)
      } catch (e) {
        errEl.textContent = 'Save failed: ' + e.message
        errEl.style.display = 'block'
        btn.disabled = false
        btn.textContent = 'Save changes'
      }
    })
  }

  // ── Player integration ─────────────────────────────────────────────────────

  async function playRecording(recId, startIdx, preloadedTracks) {
    let tracks  = preloadedTracks
    let recData = null
    try {
      recData = await API.recordings.get(recId)
      if (!tracks) tracks = recData.tracks
    } catch (e) { return }

    // Build meta string: Artist · Date · Venue
    const artist = state.selectedArtist?.name || ''
    const perfId = recData?.performance_id
    let dateStr = '', venueStr = '', sourceStr = '', performerName = ''
    if (perfId) {
      try {
        const perf = await API.performances.get(perfId)
        dateStr       = perf ? fmtDateLong(perf.start_year, perf.start_month, perf.start_day) : ''
        venueStr      = perf?.venue_name || ''
        performerName = perf?.performer  || ''
      } catch (_) {}
    }
    if (recData) {
      sourceStr = [recData.source, recData.source_modifier].filter(Boolean).join(' · ')
    }
    const metaParts = [performerName || artist, dateStr, venueStr].filter(Boolean)
    const meta      = metaParts.join(' · ') || sourceStr || '—'
    // Third line in player bar: artist name (not source type)
    const recLabel  = performerName || artist || ''

    // Filter out non-music tracks when the skip toggle is on
    const startTrack   = tracks[startIdx]
    const queueTracks  = state.skipNonMusic
      ? tracks.filter(t => !(t.flags || []).some(f => NON_MUSIC_FLAGS.includes(f)))
      : tracks
    // Find equivalent start position in (possibly filtered) queue
    let queueStart = 0
    if (startTrack) {
      const pos = queueTracks.findIndex(t => t.id === startTrack.id)
      queueStart = pos >= 0 ? pos : 0
    }

    const queue = queueTracks.map(t => ({
      id:          t.id,
      title:       t.title,
      duration:    t.duration,
      streamUrl:   t.stream_url,
      recordingId: recId,
      meta,
      recLabel,
    }))

    Player.loadQueue(queue, queueStart)
  }

  /** Called by Player when the track changes (for highlighting in the track list) */
  function onTrackChange(trackId) {
    state.playingTrackId = trackId

    // Highlight the active track row if the recording view is showing
    document.querySelectorAll('.track-row').forEach(el => {
      const isActive = parseInt(el.dataset.trackId) === trackId
      el.classList.toggle('playing', isActive)
      el.querySelector('.track-play').textContent = isActive ? '▶' : '▷'
    })

    // Same, but for the track table in the Edit Recording view
    document.querySelectorAll('.et-row').forEach(el => {
      const isActive = parseInt(el.dataset.id) === trackId
      el.classList.toggle('playing', isActive)
      const playBtn = el.querySelector('.et-play')
      if (playBtn) playBtn.textContent = isActive ? '▶' : '▷'
    })

    // Switch waveform to the new track if we have data for it
    const canvas = document.getElementById('rec-waveform')
    if (canvas && _waveformMap[trackId] && trackId !== _waveformTrackId) {
      _startWaveformLoop(canvas, trackId)
    }
  }

  // ── Venues admin page ──────────────────────────────────────────────────────

  async function renderVenuesPage(preSelectId = null) {
    setActiveNav('venues')
    setActiveArtist(null)
    setLoading()

    let venues = []
    try { venues = await API.venues.list() } catch (_) {}

    setMainHTML(`
      <div class="action-bar">
        <span style="font-size:13px; font-weight:500; color:var(--t0)">Venues</span>
        <button class="btn btn-ghost btn-sm" id="btn-new-venue" style="margin-left:auto">+ New venue</button>
      </div>
      <div class="venues-shell">
        <div class="venues-list-panel">
          <div class="venues-search-bar">
            <input type="text" id="venue-search-input" style="font-size:12px" placeholder="Search…" />
          </div>
          <div class="venue-list-scroll" id="venue-list-scroll"></div>
        </div>
        <div class="venues-detail-panel" id="venues-detail-panel">
          <div class="venue-detail-empty">Select a venue to view or edit</div>
        </div>
      </div>`)

    let allVenues    = venues
    let activeId     = null

    function renderList(list) {
      const scroll = document.getElementById('venue-list-scroll')
      if (!list.length) {
        scroll.innerHTML = '<div style="padding:16px 14px; font-size:12px; color:var(--t2)">No venues found</div>'
        return
      }
      scroll.innerHTML = list.map(v => `
        <div class="venue-list-row ${v.id === activeId ? 'active' : ''}" data-id="${v.id}">
          <div>
            <div class="venue-row-name">${esc(v.name)}</div>
            <div class="venue-row-loc">${esc([v.city, v.state, v.country].filter(Boolean).join(', '))}</div>
          </div>
          <div class="venue-row-count">${v.performance_count}p</div>
        </div>`).join('')

      scroll.querySelectorAll('.venue-list-row').forEach(el => {
        el.addEventListener('click', () => {
          activeId = parseInt(el.dataset.id)
          renderList(list)       // refresh active state
          loadVenueDetail(activeId)
        })
      })
    }

    async function loadVenueDetail(id) {
      const panel = document.getElementById('venues-detail-panel')
      panel.innerHTML = '<div class="venue-detail-empty" style="color:var(--t2)">Loading…</div>'
      let v
      try { v = await API.venues.get(id) } catch (_) {
        panel.innerHTML = '<div class="venue-detail-empty">Failed to load</div>'
        return
      }

      panel.innerHTML = `
        <div style="max-width:580px">
          <h2 style="font-size:18px; font-weight:500; color:var(--t0); margin:0 0 18px">${esc(v.name)}</h2>

          <div class="rev-section-title" style="margin-bottom:12px">Venue info</div>

          <div class="ingest-field" style="margin-bottom:10px">
            <label>Name</label>
            <input type="text" id="vd-name" value="${esc(v.name)}" />
          </div>

          <div class="ingest-field-grid" style="grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:10px">
            <div class="ingest-field">
              <label>City</label>
              <input type="text" id="vd-city" value="${esc(v.city||'')}" />
            </div>
            <div class="ingest-field">
              <label>State / Region</label>
              <input type="text" id="vd-state" value="${esc(v.state||'')}" />
            </div>
            <div class="ingest-field">
              <label>Country</label>
              <input type="text" id="vd-country" value="${esc(v.country||'')}" />
            </div>
          </div>

          <div class="ingest-field" style="margin-bottom:18px">
            <label>Bio / notes</label>
            <textarea id="vd-bio" style="min-height:80px">${esc(v.bio||'')}</textarea>
          </div>

          <div style="display:flex; align-items:center; gap:10px; margin-bottom:28px">
            <button class="btn btn-primary btn-sm" id="vd-save">Save</button>
            <span id="vd-msg" style="font-size:11px; color:var(--t2)"></span>
          </div>

          ${v.performance_count > 0 ? `
          <div class="rev-section-title" style="margin-bottom:10px">Performances (${v.performance_count})</div>
          <div style="display:flex; flex-direction:column; gap:2px">
            ${v.performances.map(p => `
              <div style="display:flex; align-items:center; gap:12px; padding:5px 0; border-bottom:1px solid var(--bd-0); font-size:12px">
                <span style="color:var(--t2); font-family:var(--font-mono); min-width:80px">${esc(p.date)}</span>
                <a href="#/artist/" style="color:var(--t0); text-decoration:none; flex:1">${esc(p.performer)}</a>
              </div>`).join('')}
          </div>` : `<div style="font-size:12px; color:var(--t2)">No performances linked yet</div>`}
        </div>`

      document.getElementById('vd-save').addEventListener('click', async () => {
        const saveBtn = document.getElementById('vd-save')
        const msgEl   = document.getElementById('vd-msg')
        saveBtn.disabled = true
        saveBtn.textContent = 'Saving…'
        try {
          await API.venues.update(id, {
            name:    document.getElementById('vd-name').value.trim(),
            city:    document.getElementById('vd-city').value.trim()    || null,
            state:   document.getElementById('vd-state').value.trim()   || null,
            country: document.getElementById('vd-country').value.trim() || null,
            bio:     document.getElementById('vd-bio').value.trim()     || null,
          })
          // Refresh list so the name updates in the sidebar
          allVenues = await API.venues.list()
          renderList(allVenues)
          // Update panel heading too
          document.querySelector('#venues-detail-panel h2').textContent =
            document.getElementById('vd-name').value.trim()
          msgEl.textContent = 'Saved'
          setTimeout(() => { if (msgEl) msgEl.textContent = '' }, 2000)
        } catch (e) {
          msgEl.style.color = 'var(--red)'
          msgEl.textContent = 'Save failed: ' + e.message
        } finally {
          saveBtn.disabled = false
          saveBtn.textContent = 'Save'
        }
      })
    }

    // Search filter
    document.getElementById('venue-search-input').addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase()
      const filtered = q
        ? allVenues.filter(v => v.name.toLowerCase().includes(q) ||
            (v.city  || '').toLowerCase().includes(q) ||
            (v.state || '').toLowerCase().includes(q))
        : allVenues
      renderList(filtered)
    })

    // New venue
    document.getElementById('btn-new-venue').addEventListener('click', async () => {
      const name = prompt('Venue name:')
      if (!name?.trim()) return
      try {
        const created = await API.venues.create({ name: name.trim() })
        allVenues = await API.venues.list()
        activeId  = created.id
        renderList(allVenues)
        loadVenueDetail(created.id)
      } catch (e) { alert('Failed: ' + e.message) }
    })

    renderList(allVenues)

    // Pre-select a venue when navigating from a recording's venue link
    if (preSelectId) {
      activeId = preSelectId
      renderList(allVenues)          // re-render to highlight the active row
      loadVenueDetail(preSelectId)
    }
  }

  // ── Artists Index ──────────────────────────────────────────────────────────

  async function renderArtistsIndexPage(preSelectId = null) {
    setActiveNav('artists-index')
    setActiveArtist(null)
    setLoading()

    let artists = [], allPerformers = []
    try {
      [artists, allPerformers] = await Promise.all([
        API.artists.list(),
        API.artists.allPerformers(),
      ])
    } catch (_) {}

    setMainHTML(`
      <div class="action-bar">
        <span style="font-size:13px; font-weight:500; color:var(--t0)">Artists</span>
        <button class="btn btn-ghost btn-sm" id="btn-new-artist" style="margin-left:auto">+ New Artist</button>
      </div>
      <div class="venues-shell">
        <div class="venues-list-panel">
          <div class="venues-search-bar">
            <input type="text" id="artist-search-input" style="font-size:12px" placeholder="Search…" />
          </div>
          <div class="venue-list-scroll" id="artist-list-scroll"></div>
        </div>
        <div class="venues-detail-panel" id="artists-detail-panel">
          <div class="venue-detail-empty">Select an artist to view or edit</div>
        </div>
      </div>`)

    let allArtists = artists
    let activeId   = null

    function renderList(list) {
      const scroll = document.getElementById('artist-list-scroll')
      if (!list.length) {
        scroll.innerHTML = '<div style="padding:16px 14px; font-size:12px; color:var(--t2)">No artists found</div>'
        return
      }
      scroll.innerHTML = list.map(a => `
        <div class="venue-list-row ${a.id === activeId ? 'active' : ''}" data-id="${a.id}">
          <div>
            <div class="venue-row-name">${esc(a.name)}</div>
            ${a.sort_name ? `<div class="venue-row-loc">${esc(a.sort_name)}</div>` : ''}
          </div>
          <div class="venue-row-count">${a.recording_count}r</div>
        </div>`).join('')

      scroll.querySelectorAll('.venue-list-row').forEach(el => {
        el.addEventListener('click', () => {
          activeId = parseInt(el.dataset.id)
          renderList(list)
          loadArtistDetail(activeId)
        })
      })
    }

    async function loadArtistDetail(id) {
      const panel = document.getElementById('artists-detail-panel')
      panel.innerHTML = '<div class="venue-detail-empty" style="color:var(--t2)">Loading…</div>'
      let a
      try { a = await API.artists.get(id) } catch (_) {
        panel.innerHTML = '<div class="venue-detail-empty">Failed to load</div>'
        return
      }

      // Performers not yet linked to this artist (available to add)
      const linkedIds    = new Set(a.performers.map(p => p.id))
      const unlinked     = allPerformers.filter(p => !linkedIds.has(p.id))

      panel.innerHTML = `
        <div style="max-width:600px">
          <h2 style="font-size:18px; font-weight:500; color:var(--t0); margin:0 0 18px">${esc(a.name)}</h2>

          <div class="rev-section-title" style="margin-bottom:12px">Artist info</div>

          <div class="ingest-field-grid" style="grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px">
            <div class="ingest-field">
              <label>Canonical name</label>
              <input type="text" id="ad-name" value="${esc(a.name)}" />
            </div>
            <div class="ingest-field">
              <label>Sort name <span style="color:var(--t3); font-weight:400">(e.g. Evans, Bill)</span></label>
              <input type="text" id="ad-sort-name" value="${esc(a.sort_name||'')}" placeholder="Last, First" />
            </div>
          </div>

          <div class="ingest-field" style="margin-bottom:18px">
            <label>Bio / notes</label>
            <textarea id="ad-bio" style="min-height:70px">${esc(a.bio||'')}</textarea>
          </div>

          <div style="display:flex; align-items:center; gap:10px; margin-bottom:28px">
            <button class="btn btn-primary btn-sm" id="ad-save">Save</button>
            <span id="ad-msg" style="font-size:11px; color:var(--t2)"></span>
          </div>

          <div class="rev-section-title" style="margin-bottom:10px">
            Linked Performers
            <span style="font-weight:400; text-transform:none; letter-spacing:0; font-size:11px; color:var(--t2); margin-left:6px">
              — Performers that include this artist
            </span>
          </div>

          <div id="ad-performers-list" style="margin-bottom:14px">
            ${a.performers.length
              ? a.performers.map(p => `
                <div class="ad-performer-row" data-pid="${p.id}">
                  <span class="ad-performer-name">${esc(p.name)}</span>
                  <button class="ad-unlink-btn" data-pid="${p.id}" title="Remove link">×</button>
                </div>`).join('')
              : '<div style="font-size:12px; color:var(--t2); padding:6px 0">No performers linked yet</div>'
            }
          </div>

          <div class="ad-add-performer-row">
            <select id="ad-performer-picker" style="flex:1; font-size:12px">
              <option value="">— link a performer —</option>
              ${unlinked.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}
            </select>
            <button class="btn btn-ghost btn-sm" id="ad-link-btn">Link</button>
          </div>
        </div>`

      // Save artist info
      document.getElementById('ad-save').addEventListener('click', async () => {
        const btn   = document.getElementById('ad-save')
        const msgEl = document.getElementById('ad-msg')
        btn.disabled = true; btn.textContent = 'Saving…'
        try {
          await API.artists.update(id, {
            name:      document.getElementById('ad-name').value.trim(),
            sort_name: document.getElementById('ad-sort-name').value.trim() || null,
            bio:       document.getElementById('ad-bio').value.trim()       || null,
          })
          // Refresh list so the name updates
          allArtists = await API.artists.list()
          renderList(allArtists)
          loadArtistList()  // refresh persistent left sidebar (name/sort order may have changed)
          document.querySelector('#artists-detail-panel h2').textContent =
            document.getElementById('ad-name').value.trim()
          msgEl.textContent = 'Saved'
          setTimeout(() => { if (msgEl) msgEl.textContent = '' }, 2000)
        } catch (e) {
          msgEl.style.color = 'var(--red)'
          msgEl.textContent = 'Save failed: ' + e.message
        } finally {
          btn.disabled = false; btn.textContent = 'Save'
        }
      })

      // Unlink performer
      async function doUnlink(performerId) {
        try {
          await API.artists.unlinkPerformer(id, performerId)
          // Remove from allPerformers-linked knowledge and re-render detail
          const a2 = await API.artists.get(id)
          const linkedIds2 = new Set(a2.performers.map(p => p.id))
          const unlinked2  = allPerformers.filter(p => !linkedIds2.has(p.id))
          _updatePerformerSection(a2.performers, unlinked2)
        } catch (e) { alert('Unlink failed: ' + e.message) }
      }

      // Link performer
      async function doLink() {
        const picker = document.getElementById('ad-performer-picker')
        const pid    = parseInt(picker.value)
        if (!pid) return
        try {
          const res = await API.artists.linkPerformer(id, pid)
          // Re-fetch and update section
          const a2 = await API.artists.get(id)
          const linkedIds2 = new Set(a2.performers.map(p => p.id))
          const unlinked2  = allPerformers.filter(p => !linkedIds2.has(p.id))
          _updatePerformerSection(a2.performers, unlinked2)
        } catch (e) { alert('Link failed: ' + e.message) }
      }

      function _updatePerformerSection(performers, available) {
        const list = document.getElementById('ad-performers-list')
        const picker = document.getElementById('ad-performer-picker')
        if (list) list.innerHTML = performers.length
          ? performers.map(p => `
              <div class="ad-performer-row" data-pid="${p.id}">
                <span class="ad-performer-name">${esc(p.name)}</span>
                <button class="ad-unlink-btn" data-pid="${p.id}" title="Remove link">×</button>
              </div>`).join('')
          : '<div style="font-size:12px; color:var(--t2); padding:6px 0">No performers linked yet</div>'
        if (picker) {
          picker.innerHTML = '<option value="">— link a performer —</option>' +
            available.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')
        }
        // Re-wire unlink buttons
        wireUnlinkBtns()
      }

      function wireUnlinkBtns() {
        document.querySelectorAll('.ad-unlink-btn').forEach(btn => {
          btn.addEventListener('click', () => doUnlink(parseInt(btn.dataset.pid)))
        })
      }

      wireUnlinkBtns()
      document.getElementById('ad-link-btn').addEventListener('click', doLink)
    }

    // Search
    document.getElementById('artist-search-input').addEventListener('input', e => {
      const q        = e.target.value.trim().toLowerCase()
      const filtered = q
        ? allArtists.filter(a => a.name.toLowerCase().includes(q) ||
            (a.sort_name || '').toLowerCase().includes(q))
        : allArtists
      renderList(filtered)
    })

    // New artist
    document.getElementById('btn-new-artist').addEventListener('click', async () => {
      const name = prompt('Canonical artist name:')
      if (!name?.trim()) return
      try {
        const created = await API.artists.create({ name: name.trim() })
        allArtists = await API.artists.list()
        activeId   = created.id
        renderList(allArtists)
        loadArtistDetail(created.id)
      } catch (e) { alert('Failed: ' + e.message) }
    })

    renderList(allArtists)

    if (preSelectId) {
      activeId = preSelectId
      renderList(allArtists)
      loadArtistDetail(preSelectId)
    }
  }

  // ── Router ─────────────────────────────────────────────────────────────────

  function route() {
    const hash = window.location.hash || '#/'

    if (hash.startsWith('#/recording/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderRecordingView(id)
      else    renderLibraryView()

    } else if (hash.startsWith('#/artist/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderArtistView(id)
      else    renderLibraryView()

    } else if (hash === '#/incoming') {
      renderIncomingView()

    } else if (hash === '#/batch') {
      renderBatchImportView()

    } else if (hash === '#/ingest') {
      renderIngestView()

    } else if (hash === '#/venues') {
      renderVenuesPage()

    } else if (hash.startsWith('#/venue/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderVenuesPage(id)
      else    renderVenuesPage()

    } else if (hash === '#/artists') {
      renderArtistsIndexPage()

    } else if (hash.startsWith('#/artists/')) {
      const id = parseInt(hash.split('/')[2])
      renderArtistsIndexPage(id || null)

    } else {
      renderLibraryView()
    }
  }

  // ── Auth ───────────────────────────────────────────────────────────────────

  function showLogin() {
    loginScreen.classList.remove('hidden')
    appShell.classList.add('hidden')
  }

  function showApp() {
    loginScreen.classList.add('hidden')
    appShell.classList.remove('hidden')
  }

  function setUserUI(user) {
    const initials = user.username.slice(0,2).toUpperCase()
    userAvatar.textContent = initials
    userName.textContent   = user.username
  }

  // ── Login form ─────────────────────────────────────────────────────────────

  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault()
    const username = document.getElementById('login-username').value.trim()
    const password = document.getElementById('login-password').value
    const errEl    = document.getElementById('login-error')
    const submitBtn = document.getElementById('login-submit')

    errEl.classList.add('hidden')
    submitBtn.disabled = true
    submitBtn.textContent = 'Signing in...'

    try {
      const user = await API.auth.login(username, password)
      state.user = user
      setUserUI(user)
      showApp()
      await loadArtistList()
      route()
    } catch (e) {
      errEl.textContent = e.message || 'Invalid credentials'
      errEl.classList.remove('hidden')
    } finally {
      submitBtn.disabled = false
      submitBtn.textContent = 'Sign in'
    }
  })

  // ── Logout ─────────────────────────────────────────────────────────────────

  document.getElementById('logout-btn').addEventListener('click', async () => {
    try { await API.auth.logout() } catch (_) {}
    state.user = null
    showLogin()
  })

  // ── Hash routing ───────────────────────────────────────────────────────────

  window.addEventListener('hashchange', route)

  // ── Init ───────────────────────────────────────────────────────────────────

  async function init() {
    try {
      const user = await API.auth.me()
      state.user = user
      setUserUI(user)
      showApp()
      await loadArtistList()
      route()
    } catch (e) {
      // Not logged in — show login screen
      showLogin()
      document.getElementById('login-screen').classList.remove('hidden')
    }
  }

  init()

  // Wire player bar skip toggle (always present in the DOM)
  document.getElementById('skip-filter-player')?.addEventListener('change', function () {
    setSkipFilter(this.checked)
  })

  // Expose minimal state for debug panel
  window.fluxState = {
    get recordingId() { return state.currentRecId },
    get trackCount()  { return state._lastTrackCount || null },
  }

  return { onTrackChange }

})()
