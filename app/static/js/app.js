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
    // Generic "where did I come from" navigation tracking (2026-07-23),
    // replacing three earlier ad hoc mechanisms (a selectedArtist-based
    // back-link that only worked one hop, a one-shot recFrom that only
    // covered Recording→Performer/Venue, and several hardcoded '#/'
    // fallbacks) — see route() for how these are kept in sync, and the
    // 2026-07-23 project memory entry for the bug this fixed (Recently
    // Added → Recording → Back landed on Library instead of Recently Added).
    //   navCurrent — { hash, label } for the page ON SCREEN right now, set
    //     by that page's own render function once its label is known
    //     (setNavCurrent()). A same-page reload (direct render*View() call,
    //     not a hash change) just re-sets this to the same value — harmless.
    //   navBack — { hash, label } | null, the page that was on screen
    //     immediately before the CURRENT one. This is what every "← Back"
    //     link in the app should point to. Snapshotted from navCurrent by
    //     route() itself, and ONLY on a genuine hash change — never on the
    //     first-ever dispatch (nothing preceded it) or a same-hash
    //     re-dispatch (a reload must never overwrite the real back target).
    navCurrent:      null,
    navBack:         null,
  }

  // ── Track flag registry — single source of truth ─────────────────────────
  // Every flag list/label/skip-set is derived from this one array. `nonMusic`
  // marks flags whose tracks are skipped by the "Skip non-music" filter.
  const TRACK_FLAGS = [
    { key: 'start_truncated', label: 'Start Truncated', nonMusic: false },
    { key: 'end_truncated',   label: 'End Truncated',   nonMusic: false },
    { key: 'incomplete',      label: 'Incomplete',      nonMusic: false },
    { key: 'unknown_title',   label: 'Unknown Title',   nonMusic: false },
    { key: 'banter',          label: 'Banter',          nonMusic: true  },
    { key: 'tuning',          label: 'Tuning',          nonMusic: true  },
    { key: 'audience',        label: 'Audience',        nonMusic: true  },
    { key: 'medley',          label: 'Medley',          nonMusic: false },
    { key: 'announcement',    label: 'Announcement',    nonMusic: true  },
    { key: 'interview',       label: 'Interview',       nonMusic: true  },
    { key: 'introduction',    label: 'Introduction',    nonMusic: true  },
    { key: 'band_intros',     label: 'Band Intros',     nonMusic: true  },
  ]
  const NON_MUSIC_FLAGS = TRACK_FLAGS.filter(f => f.nonMusic).map(f => f.key)
  const FLAG_LABELS     = Object.fromEntries(TRACK_FLAGS.map(f => [f.key, f.label]))

  // ── Placeholder venue names ("Unknown Venue", "TBD", ...) ──────────────────
  // These aren't real, canonical physical places — they're a stand-in every
  // show without a known venue reuses. Must mirror app/utils/venues.py's
  // PLACEHOLDER_VENUE_NAMES exactly (Ryan, 2026-07-15 — see that module's
  // docstring for the full contamination story and the confirmed audit).
  const PLACEHOLDER_VENUE_NAMES = new Set(['unknown venue', 'unknown', 'tbd', 'n/a', 'various'])
  function isPlaceholderVenue(name) {
    return !!name && PLACEHOLDER_VENUE_NAMES.has(String(name).trim().toLowerCase())
  }

  /** Official badge + flag chips ("bubble tags") for a track, as an ordered
   *  array of individual chip HTML strings — official badge first, then each
   *  flag. Add Recording's track table (renderIngestReview) uses the array
   *  directly: first chip stays under the title, any rest go in a dedicated
   *  full-width row (Ryan, 2026-07-15 — stacking multiples under the title
   *  in that narrow input-constrained cell was pushing the title text up). */
  function trackChipsArray(t) {
    const chips = []
    if (t.is_official) chips.push(`<span class="track-official-badge" title="Officially released">©</span>`)
    ;(t.flags || []).forEach(f => chips.push(`<span class="track-flag-chip">${FLAG_LABELS[f] || f}</span>`))
    return chips
  }

  /** Official badge + flag chips joined into one string — View Recording's
   *  track title shares this one line inline (no width constraint there, so
   *  no need to split first-chip/rest like Add Recording does). */
  function trackBadgesHtml(t) {
    return trackChipsArray(t).join('')
  }

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

  // ── Waveform (wavesurfer.js) ──────────────────────────────────────────────
  // Officially adopted 2026-07-15 (was a spike prototype) — replaces the old
  // hand-rolled canvas RAF-loop renderer. Ryan: "fully wired into the
  // persistent player. It should not be separate." Deliberately does NOT use
  // wavesurfer's own `media`/`url` binding, though — that mechanism fetches
  // the whole file as a blob to decode it, which (a) defeats the browser's
  // native HTTP range-request streaming we rely on for large lossless files
  // and (b) replaces the shared #audio-el's src with a blob: URL that gets
  // revoked on destroy(), risking a playback interruption just from
  // navigating away. Instead: wavesurfer renders purely from our own
  // precomputed peaks (`_waveformMap`, already computed server-side — no
  // network fetch at all) and its OWN internal silent audio element, which
  // we never play. All REAL playback stays owned by Player/#audio-el, the
  // one true audio channel:
  //   - click/drag on the waveform → 'interaction' event → we set
  //     #audio-el's currentTime directly (loading this recording's queue
  //     first, paused, if it wasn't already the active one)
  //   - #audio-el's real timeupdate → wsInstance.setTime(...), which only
  //     moves wavesurfer's own silent cursor/renders progress, never plays
  //     anything — see the one-time listener below.
  let _waveformMap      = {}   // trackId → waveform data (also the "has analysis" check)
  let _trackDurationMap = {}   // trackId → duration, needed alongside peaks when (re)loading wavesurfer
  let _wsInstance       = null
  let _wsTrackId        = null

  function _cancelWaveform() {
    if (_wsInstance) { try { _wsInstance.destroy() } catch (_) {} }
    _wsInstance = null
    _wsTrackId  = null
  }

  /** wavesurfer's `peaks` option wants a flat array of -1..1 values per
   * channel. Our precomputed data is either v2 {min:[...], max:[...]} (real
   * peak envelope) or v1 a flat mirrored-magnitude array (pre-bump tracks) —
   * `.max` alone reads fine as a single-channel peaks array either way. */
  function _peaksForTrack(trackId) {
    const wf = _waveformMap[trackId]
    if (!wf) return null
    const arr = Array.isArray(wf) ? wf : wf.max
    return (arr && arr.length) ? [arr] : null
  }

  // One-time sync: whenever the REAL shared audio element advances, mirror
  // its position onto wavesurfer's own (silent, unplayed) cursor so the
  // waveform's progress indicator always matches actual playback — without
  // wavesurfer ever touching the real audio itself.
  ;(function () {
    const audio = document.getElementById('audio-el')
    if (!audio) return
    audio.addEventListener('timeupdate', () => {
      if (_wsInstance && _wsTrackId != null && Player.currentId() === _wsTrackId) {
        _wsInstance.setTime(audio.currentTime)
      }
    })
  })()

  // Ingest wizard state — persists across step renders
  const ingest = {
    step:       'folder',  // 'folder' | 'review' | 'success' (Confirm step removed 2026-07-15 —
                           // review's own "Add Recording →" button now submits directly)
    folderPath: null,
    scan:       null,      // full scan API response
    behavior:   'copy',    // 'copy' | 'move' — default copy: never destroy source unless asked
    form: {},              // resolved metadata (populated on review step)
    tracks:     [],        // array of { track_number, title, set, duration, filename }
    // True when this review was opened via Bulk Import's "Review →" (see
    // _batchOpenReview) rather than a fresh Add Recording nav — drives the
    // standardized back-link (top of the review page) and the post-submit
    // redirect target (Ryan, 2026-07-15: bulk reviewers need a fast way back
    // to the queue, not a forced detour through the new recording's page).
    fromBatch:  false,
  }

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const loginScreen = document.getElementById('login-screen')
  const appShell    = document.getElementById('app-shell')
  const mainContent = document.getElementById('main-content')
  const userAvatar  = document.getElementById('user-avatar')
  const userName    = document.getElementById('user-name')

  // ── Kill native spellcheck/autocorrect on text inputs ─────────────────────
  // This app runs inside PyWebView's underlying WKWebView, which applies
  // macOS's own spellcheck/text-replacement to any unmarked text input — pops
  // an unwanted correction bubble while typing artist/venue/person names
  // (proper nouns trip it constantly; Ryan, 2026-07-23, typing "Ricky
  // Simpkins" got auto-"corrected" toward "Simpkin's"). Delegated on focusin
  // at the document level rather than patched into every input's template —
  // most of these inputs (add-picker rows, inline edits) are created well
  // after their page's own setMainHTML() call, so a one-time sweep wouldn't
  // reach them; this catches every text input, present and future.
  document.addEventListener('focusin', e => {
    const el = e.target
    if (el.tagName === 'INPUT' && (el.type === 'text' || el.type === 'search')) {
      el.spellcheck = false
      el.setAttribute('autocorrect', 'off')
      el.setAttribute('autocapitalize', 'off')
    }
  })

  // ── Theme toggle ───────────────────────────────────────────────────────────
  ;(function () {
    const btn = document.getElementById('theme-btn')
    if (!btn) return
    btn.addEventListener('click', () => {
      const isLight = document.body.classList.toggle('theme-light')
      localStorage.setItem('fluxTheme', isLight ? 'light' : 'dark')
    })
  })()

  // ── Resizable sidebar ──────────────────────────────────────────────────────
  ;(function () {
    const MIN = 200, MAX = 460
    const setW = w => document.documentElement.style.setProperty('--sidebar-w', Math.round(w) + 'px')
    const saved = parseInt(localStorage.getItem('fluxSidebarW'), 10)
    if (saved && saved >= MIN && saved <= MAX) setW(saved)

    const handle = document.getElementById('sidebar-resizer')
    if (!handle) return
    let dragging = false
    handle.addEventListener('mousedown', e => {
      dragging = true
      handle.classList.add('dragging')
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      e.preventDefault()
    })
    window.addEventListener('mousemove', e => {
      if (!dragging) return
      setW(Math.max(MIN, Math.min(e.clientX, MAX)))
    })
    window.addEventListener('mouseup', () => {
      if (!dragging) return
      dragging = false
      handle.classList.remove('dragging')
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      const cur = getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w').trim()
      localStorage.setItem('fluxSidebarW', parseInt(cur, 10))
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

  // Start date, extended with an end date when the performance spans more
  // than one day (2026-07-23 — e.g. the Danny Gatton Cellar Door stand,
  // start/end a day apart). Same month+year → compact "Jan 25–26, 1979";
  // otherwise a full "Start – End" range.
  function fmtDateRangeLong(perf) {
    const start = fmtDateLong(perf.start_year, perf.start_month, perf.start_day)
    if (!perf.end_year && !perf.end_month && !perf.end_day) return start
    if (perf.end_year === perf.start_year && perf.end_month === perf.start_month && perf.end_day) {
      const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
      return `${MONTHS[perf.start_month-1]} ${perf.start_day}–${perf.end_day}, ${perf.start_year}`
    }
    const end = fmtDateLong(perf.end_year || perf.start_year, perf.end_month, perf.end_day)
    return `${start} – ${end}`
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

  // Show-length runtime, e.g. "1h 42m" or "47m" — for the catalog length column.
  function fmtRuntime(secs) {
    if (!secs) return ''
    const totalMin = Math.round(secs / 60)
    const h = Math.floor(totalMin / 60)
    const m = totalMin % 60
    return h ? `${h}h ${m}m` : `${m}m`
  }

  // Compact "date added" (ingest timestamp) for the catalog column — ISO date only.
  function fmtDateAdded(iso) {
    return iso ? iso.slice(0, 10) : ''
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
      // Words can start with punctuation ("(Bill", "\"Song", "-Encore") — find
      // the first actual letter to capitalize instead of blindly upper-casing
      // index 0, which no-ops on the punctuation and leaves the real first
      // letter (and everything else) lowercase. Minor-word lowering only
      // applies to the plain no-punctuation case, same as before.
      const m = lo.match(/[a-z]/)
      if (!m) return lo
      const idx = m.index
      if (idx === 0 && i !== 0 && _lcWords.has(lo)) return lo
      return lo.slice(0, idx) + lo.charAt(idx).toUpperCase() + lo.slice(idx + 1)
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


  // Venue autocomplete — searches venues, shows location, offers a create row.
  // onPick receives {id|null, name}.
  function wireVenuePickerDropdown(inputEl, dropEl, onPick) {
    if (!inputEl || !dropEl) return
    let debounce = null
    const close = () => { dropEl.style.display = 'none'; dropEl.innerHTML = '' }
    async function run() {
      const q = inputEl.value.trim()
      if (q.length < 2) { close(); return }
      let results = []
      try { results = await API.venues.list(q) } catch (_) {}
      const rows = results.slice(0, 10).map(v => {
        const loc = [v.city, v.state, v.country].filter(Boolean).join(', ')
        return `<div class="venue-result" data-id="${v.id}" data-name="${esc(v.name)}">${esc(v.name)}${loc ? ` <span class="venue-result-loc">${esc(loc)}</span>` : ''}</div>`
      }).join('')
      const exact = results.some(v => v.name.toLowerCase() === q.toLowerCase())
      const createRow = (!exact && q)
        ? `<div class="venue-result venue-result-new" data-id="" data-name="${esc(q)}">+ Create venue: "${esc(q)}"</div>` : ''
      dropEl.innerHTML = rows + createRow
      dropEl.style.display = (rows || createRow) ? 'block' : 'none'
      dropEl.querySelectorAll('.venue-result').forEach(el => {
        el.addEventListener('mousedown', e => {
          e.preventDefault()
          onPick({ id: el.dataset.id ? parseInt(el.dataset.id) : null, name: el.dataset.name })
          close()
        })
      })
    }
    inputEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(run, 220) })
    inputEl.addEventListener('focus', () => { if (inputEl.value.trim().length >= 2) run() })
  }

  // Generic autocomplete over {id,name} results with an optional "create" row.
  // onPick receives {id|null, name}. Used for the Performer and Member pickers.
  function wirePickerDropdown(inputEl, dropEl, searchFn, onPick, createLabel) {
    if (!inputEl || !dropEl) return
    let debounce = null
    const close = () => { dropEl.style.display = 'none'; dropEl.innerHTML = '' }
    async function run() {
      const q = inputEl.value.trim()
      if (q.length < 2) { close(); return }
      let results = []
      try { results = await searchFn(q) } catch (_) {}
      const rows = results.map(r =>
        `<div class="artist-result" data-id="${r.id}" data-name="${esc(r.name)}">${esc(r.name)}</div>`).join('')
      const exact = results.some(r => r.name.toLowerCase() === q.toLowerCase())
      const createRow = (!exact && q)
        ? `<div class="artist-result artist-result-new" data-id="" data-name="${esc(q)}">+ ${esc(createLabel)}: "${esc(q)}"</div>` : ''
      dropEl.innerHTML = rows + createRow
      dropEl.style.display = (rows || createRow) ? 'block' : 'none'
      dropEl.querySelectorAll('.artist-result').forEach(el => {
        el.addEventListener('mousedown', e => {
          e.preventDefault()
          onPick({ id: el.dataset.id ? parseInt(el.dataset.id) : null, name: el.dataset.name })
          close()
        })
      })
    }
    inputEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(run, 220) })
    inputEl.addEventListener('blur',  () => setTimeout(close, 200))
    inputEl.addEventListener('focus', () => { if (inputEl.value.trim().length >= 2) run() })
  }

  // ── Reusable Performer + Members/Guests widget ───────────────────────────────
  // Bound to a `store` object holding `.members`, `.guests` (+ .performer_name/
  // .performer_id). `ids.field` is a mount point div — renderChips() rebuilds
  // its full innerHTML each call (both rows + pills + add controls) and
  // rewires events, the same rebuild-and-rewire pattern already used for
  // buildAiResultsHtml, rather than DOM-patching individual chips.
  //
  // Members/Guests two-row redesign (2026-07-22), replacing one flat Artists
  // pill row + descriptive subtext: a small (+) button per row reveals an
  // inline add-picker input on click. Removing a pill is a plain splice —
  // this is still draft form state until Confirm, no server round-trip.
  function createMembersWidget(store, ids) {
    const pill = (p, i, role) => `
      <span class="member-chip ${role === 'guest' ? 'member-chip--guest' : ''}">
        ${esc(p.name)} <span class="member-chip-x" data-role="${role}" data-idx="${i}" title="Remove">×</span>
      </span>`
    const row = (role, label, items) => `
      <div class="mg-row">
        <span class="mg-row-label">${label}</span>
        ${items.map((p, i) => pill(p, i, role)).join('')}
        <button type="button" class="mg-add-btn" data-role="${role}" title="Add ${label === 'Members' ? 'Member' : 'Guest'} Name">+</button>
        <span class="artist-picker-wrap mg-add-picker" data-role="${role}" style="display:none">
          <input type="text" class="member-input mg-role-input" data-role="${role}" autocomplete="off" placeholder="Add ${label === 'Members' ? 'Member' : 'Guest'} Name…" />
          <div class="artist-dropdown mg-role-dd" data-role="${role}" style="display:none"></div>
        </span>
      </div>`

    function renderChips() {
      const field = document.getElementById(ids.field)
      if (!field) return
      store.members = store.members || []
      store.guests  = store.guests  || []
      field.innerHTML = row('member', 'Members', store.members) + row('guest', 'Guests', store.guests)

      field.querySelectorAll('.member-chip-x').forEach(x =>
        x.addEventListener('click', () => {
          const list = x.dataset.role === 'guest' ? store.guests : store.members
          list.splice(parseInt(x.dataset.idx), 1)
          renderChips()
        }))

      field.querySelectorAll('.mg-add-btn').forEach(btn =>
        btn.addEventListener('click', () => {
          const picker = field.querySelector(`.mg-add-picker[data-role="${btn.dataset.role}"]`)
          const input  = picker?.querySelector('.mg-role-input')
          if (!picker || !input) return
          const showing = picker.style.display !== 'none'
          // Only one add-picker open at a time.
          field.querySelectorAll('.mg-add-picker').forEach(p => { p.style.display = 'none' })
          picker.style.display = showing ? 'none' : 'inline-flex'
          if (!showing) input.focus()
        }))

      field.querySelectorAll('.mg-role-input').forEach(input => {
        const role = input.dataset.role
        const dd   = field.querySelector(`.mg-role-dd[data-role="${role}"]`)
        wirePickerDropdown(input, dd, API.artists.search,
          ({ id, name }) => { addMember(name, id, role); input.value = '' }, 'Add new artist')
        input.addEventListener('keydown', e => {
          if (e.key === 'Enter') { e.preventDefault(); addMember(input.value, null, role); input.value = '' }
        })
      })
    }

    function addMember(name, id, role = 'member') {
      name = (name || '').trim()
      if (!name) return
      const list = role === 'guest' ? (store.guests = store.guests || []) : (store.members = store.members || [])
      if (list.some(m => m.name.toLowerCase() === name.toLowerCase())) return
      list.push(id ? { id, name } : { name })
      renderChips()
    }

    // Performer picked (existing act → load its current roster into Members;
    // new act → no members by default, Artists are optional and only added
    // for special collaborations). Guests always reset — a freshly (re)picked
    // act has no per-show guests carried over from whatever was typed before.
    async function onPerformerPick({ id, name }) {
      const el = document.getElementById(ids.performerInput)
      if (el) el.value = name
      store.performer_name = name
      store.performer_id   = id || null
      store.guests = []
      if (id) {
        try { const p = await API.performers.get(id); store.members = (p.members || []).map(m => ({ id: m.id, name: m.name })) }
        catch (_) { store.members = [] }
      } else {
        store.members = []
      }
      renderChips()
    }
    function mount() {
      wirePickerDropdown(document.getElementById(ids.performerInput), document.getElementById(ids.performerDropdown),
        API.performers.search, onPerformerPick, 'Create new performer')
      renderChips()
    }
    return { renderChips, addMember, onPerformerPick, mount }
  }

  // Splits a billed-act name into candidate individual-person names, for
  // matching against existing Artists when the Performer itself doesn't
  // exist yet (2026-07-22) — e.g. "Bela Fleck & Edgar Meyer" ->
  // ["Bela Fleck", "Edgar Meyer"]. Conservative separators only; a missed
  // split is harmless (that name just stays unmatched), which is why exact
  // matching below matters more than aggressive splitting here.
  const _NAME_SPLIT_RE = /\s*(?:&|,|\/|\+|\bwith\b|\bfeat\.?\b|\bfeaturing\b|\band\b)\s*/i
  function splitPerformerNameCandidates(raw) {
    return (raw || '').split(_NAME_SPLIT_RE).map(s => s.trim()).filter(Boolean)
  }

  // Add flow: preload Members if the scanned Performer (act) already exists
  // in the DB — pulls its current roster. If the act itself is new (e.g. a
  // one-off duo billing), fall back to splitting the act name into candidate
  // person names and matching each against existing Artists — EXACT
  // (case-insensitive) name match only, never a fuzzy/substring hit, since a
  // wrong auto-attached person is worse than an unmatched name Ryan fills in
  // by hand. Ryan chose auto-fill over a click-to-confirm suggestion step
  // for this (2026-07-22), weighing it against the AI-Assist auto-apply bug
  // fixed earlier the same session.
  async function initAddPerformerMembers(widget) {
    const f = ingest.form
    const name = (f.artist_name || '').trim()
    if (f._membersInit) { widget.renderChips(); return }
    f._membersInit = true
    if (!name) { f.members = f.members || []; widget.renderChips(); return }
    try {
      const matches = await API.performers.search(name)
      const exact = matches.find(m => m.name.toLowerCase() === name.toLowerCase())
      if (exact) {
        f.performer_id = exact.id
        const p = await API.performers.get(exact.id)
        f.members = (p.members || []).map(m => ({ id: m.id, name: m.name }))
      } else {
        const found = []
        for (const cand of splitPerformerNameCandidates(name)) {
          try {
            const results = await API.artists.search(cand)
            const hit = results.find(r => r.name.toLowerCase() === cand.toLowerCase())
            if (hit) found.push({ id: hit.id, name: hit.name })
          } catch (_) { /* best-effort — a failed lookup just leaves that name unmatched */ }
        }
        f.members = found
      }
    } catch (_) { f.members = f.members || [] }
    widget.renderChips()
  }

  function setMainHTML(html) {
    _cancelWaveform()        // destroy any wavesurfer instance from the page we're leaving
    Player.setFallbackPlay(null)   // only the recording page currently shown gets to set this
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

  // Every render*View() function calls this once it knows its own display
  // label (immediately for a static-label page like "Library"; after its
  // data fetch succeeds for a dynamic one like a performer/venue/recording
  // name) — see state.navCurrent/navBack above for how "← Back" links use
  // it. A page whose data fetch FAILS (e.g. "Recording not found") simply
  // never calls this, which is deliberate: a subsequent page's Back link
  // then skips the dead page and points at the last one that actually
  // loaded, rather than back to a dead end.
  function setNavCurrent(label) {
    state.navCurrent = { hash: window.location.hash, label }
  }

  function setActiveNav(active) {
    state._activeNav = active
    const nav = document.getElementById('sidebar-nav')
    if (nav) nav.querySelectorAll('[data-nav]').forEach(el =>
      el.classList.toggle('active', el.dataset.nav === active))
  }

  function setActiveArtist(id) {
    document.querySelectorAll('#sidebar-nav .nav-record[data-dim="performers"]').forEach(el =>
      el.classList.toggle('active', parseInt(el.dataset.id) === id))
  }

  // ── Sidebar nav (all top-level in small caps; dimensions expandable) ──────────

  state.expandedDims = state.expandedDims || new Set()
  const _dimCache = {}

  function _dimSection(dim, icon, label, sub) {
    const open = state.expandedDims.has(dim)
    const singular = label.replace(/s$/, '')
    return `
      <div class="nav-section">
        <div class="nav-item ${sub ? 'nav-sub' : 'nav-top'} nav-expand nav-dim" data-dim="${dim}">
          ${icon ? `<span class="nav-icon">${icon}</span>` : ''}
          <span class="nav-dim-label truncate">${label}</span>
          <span class="nav-dim-actions">
            <span class="nav-action" data-act="new" title="Create new ${esc(singular)}">＋</span>
            <span class="nav-action" data-act="refresh" title="Refresh list">↻</span>
          </span>
          <span class="nav-caret ${open ? 'open' : ''}">▸</span>
        </div>
        <div class="nav-records ${sub ? 'nav-records--sub' : ''}" id="nav-records-${dim}" style="display:${open ? '' : 'none'}"></div>
      </div>`
  }

  async function _loadDim(dim) {
    if (_dimCache[dim]) return _dimCache[dim]
    let rows = []
    try {
      if (dim === 'venues')            rows = await API.venues.list()
      else if (dim === 'performers')   rows = await API.performers.list()
      else if (dim === 'artists')      rows = await API.artists.list()
      else if (dim === 'collections')  rows = await API.collections.list()
    } catch (_) {}
    _dimCache[dim] = rows
    return rows
  }

  async function _renderDimRecords(dim) {
    const box = document.getElementById(`nav-records-${dim}`)
    if (!box) return
    const rows = await _loadDim(dim)
    const target = { venues: 'venue', performers: 'artist', artists: 'person', collections: 'collection' }[dim]
    if (!rows.length) { box.innerHTML = `<div class="nav-record nav-record--empty">None yet</div>`; return }
    box.innerHTML = rows.map(r => `
      <div class="nav-record" data-dim="${dim}" data-id="${r.id}">
        <span class="truncate">${esc(r.name)}</span>${r.recording_count ? `<span class="nav-record-count">${r.recording_count}</span>` : ''}
      </div>`).join('')
    box.querySelectorAll('.nav-record[data-id]').forEach(el =>
      el.addEventListener('click', () => { window.location.hash = `#/${target}/${el.dataset.id}` }))
  }

  function _toggleDim(dim, forceOpen) {
    const row  = document.querySelector(`.nav-dim[data-dim="${dim}"]`)
    const box  = document.getElementById(`nav-records-${dim}`)
    const caret = row?.querySelector('.nav-caret')
    const open = state.expandedDims.has(dim)
    if (open && !forceOpen) {
      state.expandedDims.delete(dim); if (box) box.style.display = 'none'; caret?.classList.remove('open')
    } else {
      state.expandedDims.add(dim); if (box) box.style.display = ''; caret?.classList.add('open')
      _renderDimRecords(dim)
    }
  }

  function _refreshDim(dim) {
    _dimCache[dim] = null
    _toggleDim(dim, true)   // ensure open, then re-render from DB
    _renderDimRecords(dim)
  }

  // Invalidate one or more dimension caches and silently re-render any open ones.
  // Call after edits that can prune/create performers, venues, or artists.
  function invalidateDims(...dims) {
    dims.forEach(d => {
      _dimCache[d] = null
      if (state.expandedDims.has(d)) _renderDimRecords(d)
    })
  }

  // Header "+ Create new" action per dimension.
  function createInDim(dim) {
    if (dim === 'collections')     window.location.hash = '#/collection/new'
    else if (dim === 'venues')     window.location.hash = '#/venues'
    else if (dim === 'performers') _promptCreate('performer')
    else if (dim === 'artists')    _promptCreate('artist')
  }
  async function _promptCreate(kind) {
    const name = prompt(`New ${kind} name:`)
    if (!name || !name.trim()) return
    try {
      if (kind === 'performer') {
        const p = await API.performers.create({ name: name.trim() })
        _dimCache.performers = null; if (state.expandedDims.has('performers')) _renderDimRecords('performers')
        window.location.hash = `#/artist/${p.id}`
      } else {
        const a = await API.artists.create({ name: name.trim() })
        _dimCache.artists = null; if (state.expandedDims.has('artists')) _renderDimRecords('artists')
        window.location.hash = `#/person/${a.id}`
      }
    } catch (e) { alert('Failed: ' + e.message) }
  }

  async function renderSidebar() {
    const nav = document.getElementById('sidebar-nav')
    if (!nav) return
    _dimCache.venues = _dimCache.performers = _dimCache.artists = _dimCache.collections = null
    nav.innerHTML = `
      <a class="nav-add-btn" data-nav="ingest" href="#/ingest"><span class="nav-add-plus">+</span> Add Recording</a>
      <a class="nav-item nav-top" data-nav="library" href="#/"><span class="nav-icon">◈</span> Library</a>
      <a class="nav-item nav-top" data-nav="recent" href="#/recent"><span class="nav-icon">◷</span> Recently Added</a>
      ${_dimSection('collections', null, 'Collections', true)}
      ${_dimSection('venues', '◎', 'Venues')}
      ${_dimSection('performers', '✦', 'Performers')}
      ${_dimSection('artists', '♪', 'Artists')}`
    nav.querySelectorAll('.nav-expand').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('.nav-action')) return
        _toggleDim(el.dataset.dim)
      })
    })
    nav.querySelectorAll('.nav-action').forEach(el => {
      el.addEventListener('click', e => {
        e.stopPropagation()
        const dim = el.closest('.nav-dim').dataset.dim
        if (el.dataset.act === 'refresh') _refreshDim(dim)
        else createInDim(dim)
      })
    })
    state.expandedDims.forEach(dim => _renderDimRecords(dim))
    setActiveNav(state._activeNav)
  }

  // Back-compat alias — call sites still say loadArtistList().
  const loadArtistList = renderSidebar

  // ── Shared compact recording row (one line, all show info) ───────────────────
  function flatRowHtml(r, showPerformer) {
    const date    = fmtDate(r.start_year, r.start_month, r.start_day)
    const loc     = fmtLocation(r.city, r.state, r.country)
    const quality = r.quality || ''
    const rating  = r.rating != null ? `<span class="rating-badge rating-badge--sm">${r.rating}</span>` : ''
    const runtime = fmtRuntime(r.duration_sec)
    const inc     = r.is_complete ? '' : '<span class="rec-inc" title="Incomplete recording">inc</span>'
    return `
      <div class="rec-row rec-row--flat ${showPerformer ? 'with-performer' : ''}" data-rec-id="${r.id}">
        ${showPerformer ? `<span class="rec-performer-cell truncate">${esc(r.performer || '')}</span>` : ''}
        <span class="rec-date truncate">${esc(date)}</span>
        <span class="rec-venue truncate">${esc(r.venue || '(unknown venue)')}</span>
        <span class="rec-location truncate">${esc(loc)}</span>
        <span>${sourceBadge(r.source)}</span>
        <span class="quality ${qualityClass(quality)}">${esc(quality)}</span>
        <span class="rec-rating">${rating}</span>
        <span class="rec-runtime">${runtime}</span>
        <span class="rec-tracks">${r.track_count}t${inc ? ' ' + inc : ''}</span>
        <span class="rec-date-added">${esc(fmtDateAdded(r.created_at))}</span>
        <button class="rec-play-btn" data-rec-id="${r.id}" title="Play">▶</button>
      </div>`
  }

  // Minimal header row paired with flatRowHtml's grid — every cell is blank
  // except "Added", which doubles as a click-to-sort toggle (default: unsorted,
  // i.e. whatever order the page already puts rows in).
  function recTableHeadHtml(showPerformer) {
    return `
      <div class="rec-table-head ${showPerformer ? 'with-performer' : ''}">
        ${showPerformer ? '<span></span>' : ''}
        <span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span>
        <button class="rec-th-added" type="button" title="Sort by date added">Added <span class="rec-th-arrow"></span></button>
        <span></span>
      </div>`
  }

  // Wires the "Added" header's sort toggle for a rendered rec-table. `rows` is the
  // page's row-data array (left in its original/default order); sorting is purely
  // a display-time re-render, it doesn't touch how the page loads next time.
  function wireDateAddedSort(mountEl, rows, showPerformer) {
    const head = mountEl?.previousElementSibling
    const btn  = head?.querySelector('.rec-th-added')
    const arrow = head?.querySelector('.rec-th-arrow')
    if (!mountEl || !btn) return
    let dir = null   // null = default order; 'asc' | 'desc' once clicked
    btn.addEventListener('click', () => {
      dir = dir === 'desc' ? 'asc' : 'desc'
      const sorted = rows.slice().sort((a, b) => {
        const av = a.created_at || '', bv = b.created_at || ''
        return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
      })
      mountEl.innerHTML = sorted.map(r => flatRowHtml(r, showPerformer)).join('')
      wireRecordingRows(mountEl)
      arrow.textContent = dir === 'asc' ? '▲' : '▼'
    })
  }

  function wireRecordingRows(container) {
    container.querySelectorAll('.rec-row').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.closest('.rec-play-btn')) return
        window.location.hash = `#/recording/${el.dataset.recId}`
      })
      el.addEventListener('contextmenu', e => {
        e.preventDefault()
        openAddToCollectionMenu(parseInt(el.dataset.recId), e.clientX, e.clientY)
      })
    })
    container.querySelectorAll('.rec-play-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation()
        playRecording(parseInt(btn.dataset.recId), 0, null)
      })
    })
  }

  // Add a recording to a collection (or create one). onAdded({id, name}) fires on success.
  async function openAddToCollectionMenu(recId, x, y, onAdded) {
    document.getElementById('collection-menu')?.remove()
    let cols = []
    try { cols = await API.collections.list() } catch (_) {}
    const menu = document.createElement('div')
    menu.className = 'track-qmenu'; menu.id = 'collection-menu'
    menu.innerHTML = `
      <div class="track-qmenu-label">Add to collection</div>
      ${cols.map(c => `<div class="col-menu-item" data-id="${c.id}" data-name="${esc(c.name)}">${esc(c.name)}</div>`).join('')
        || '<div class="col-menu-empty">No collections yet</div>'}
      <div class="col-menu-item col-menu-new">+ Create collection…</div>`
    document.body.appendChild(menu)
    const r = menu.getBoundingClientRect()
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth  - r.width  - 8)) + 'px'
    menu.style.top  = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px'
    const close = () => menu.remove()
    async function addTo(colId, name) {
      try { await API.collections.addRecording(colId, recId); onAdded && onAdded({ id: colId, name }) }
      catch (e) { alert('Failed: ' + e.message) }
      close()
    }
    menu.querySelectorAll('.col-menu-item[data-id]').forEach(el =>
      el.addEventListener('click', () => addTo(parseInt(el.dataset.id), el.dataset.name)))
    menu.querySelector('.col-menu-new').addEventListener('click', async () => {
      const name = prompt('New collection name:')
      if (!name || !name.trim()) { close(); return }
      try { const c = await API.collections.create({ name: name.trim() }); await addTo(c.id, name.trim()) }
      catch (e) { alert('Failed: ' + e.message); close() }
    })
    setTimeout(() => document.addEventListener('mousedown', function h(e) {
      if (!menu.contains(e.target)) { close(); document.removeEventListener('mousedown', h) }
    }), 0)
  }

  // Collection tags on the recording detail (styled like flag pills).
  function collectionTagHtml(c) {
    return `<span class="collection-tag" data-id="${c.id}">${esc(c.name)}<span class="collection-tag-x" title="Remove from collection">×</span></span>`
  }
  function wireCollectionTag(tagEl, recId) {
    tagEl.querySelector('.collection-tag-x')?.addEventListener('click', async () => {
      try { await API.collections.removeRecording(parseInt(tagEl.dataset.id), recId); tagEl.remove() }
      catch (e) { alert('Failed: ' + e.message) }
    })
  }
  function wireRecCollectionArea(recId) {
    const box = document.getElementById('rec-collections')
    if (!box) return
    box.querySelectorAll('.collection-tag').forEach(t => wireCollectionTag(t, recId))
    document.getElementById('btn-add-collection')?.addEventListener('click', e => {
      openAddToCollectionMenu(recId, e.clientX, e.clientY, ({ id, name }) => {
        if (box.querySelector(`.collection-tag[data-id="${id}"]`)) return
        const span = document.createElement('span')
        span.className = 'collection-tag'; span.dataset.id = id
        span.innerHTML = `${esc(name)}<span class="collection-tag-x" title="Remove from collection">×</span>`
        box.insertBefore(span, document.getElementById('btn-add-collection'))
        wireCollectionTag(span, recId)
      })
    })
  }

  // ── Collections views ────────────────────────────────────────────────────────
  async function renderCollectionsIndex() {
    setActiveNav('collections'); setActiveArtist(null); setLoading()
    setNavCurrent('Collections')
    let cols = []
    try { cols = await API.collections.list() } catch (_) {}
    const rows = cols.map(c => `
      <div class="artist-index-row" data-id="${c.id}">
        <span class="artist-index-name">${esc(c.name)}</span>
        <span class="artist-index-members">${esc(c.description || '')}</span>
        <span class="artist-index-count">${c.recording_count} rec</span>
      </div>`).join('')
    setMainHTML(`
      <div class="action-bar">
        <span style="font-size:13px; font-weight:500; color:var(--t0)">Collections</span>
        <button class="btn btn-ghost btn-sm" id="btn-new-collection" style="margin-left:auto">+ New collection</button>
      </div>
      <div class="artist-index-list">${rows || '<div class="empty-state" style="min-height:120px"><div>No collections yet</div></div>'}</div>`)
    mainContent.querySelectorAll('.artist-index-row').forEach(el =>
      el.addEventListener('click', () => { window.location.hash = `#/collection/${el.dataset.id}` }))
    document.getElementById('btn-new-collection').addEventListener('click', async () => {
      const name = prompt('New collection name:')
      if (!name || !name.trim()) return
      try { const c = await API.collections.create({ name: name.trim() }); window.location.hash = `#/collection/${c.id}` }
      catch (e) { alert('Failed: ' + e.message) }
    })
  }

  // New collection (create only — editing happens in place on the collection page).
  async function renderCollectionForm() {
    setActiveNav('collections'); setActiveArtist(null)
    setNavCurrent('New Collection')
    setMainHTML(`
      <div class="artist-header"><h1>New collection</h1></div>
      <div style="max-width:480px; padding:0 20px">
        <div class="ingest-field" style="margin-bottom:12px">
          <label>Name</label>
          <input type="text" id="col-name" placeholder="Collection name" />
        </div>
        <div class="ingest-field" style="margin-bottom:16px">
          <label>Description <span style="color:var(--t3); font-weight:400">(optional)</span></label>
          <textarea id="col-desc" style="min-height:70px"></textarea>
        </div>
        <div style="display:flex; gap:8px; align-items:center">
          <button class="btn btn-primary btn-sm" id="col-save">Create</button>
          <button class="btn btn-ghost btn-sm" id="col-cancel">Cancel</button>
        </div>
      </div>`)
    document.getElementById('col-name').focus()
    document.getElementById('col-cancel').addEventListener('click', () => {
      window.location.hash = state.navBack ? state.navBack.hash : '#/'
    })
    document.getElementById('col-save').addEventListener('click', async () => {
      const name = document.getElementById('col-name').value.trim()
      if (!name) { alert('Name is required'); return }
      try {
        const c = await API.collections.create({
          name, description: document.getElementById('col-desc').value.trim() || null,
        })
        _dimCache.collections = null
        if (state.expandedDims.has('collections')) _renderDimRecords('collections')
        window.location.hash = `#/collection/${c.id}`
      } catch (e) { alert('Failed: ' + e.message) }
    })
  }

  // Collection page — editable name/description in place + recording catalog.
  async function renderCollectionView(id) {
    setActiveNav('collections'); setActiveArtist(null); setLoading()
    let c
    try { c = await API.collections.get(id) }
    catch (e) { setMainHTML(`<div class="empty-state"><div class="empty-title">Collection not found</div></div>`); return }
    setNavCurrent(c.name)
    const colRows = c.recordings || []
    const rows = colRows.map(r => flatRowHtml(r, true)).join('')
    const descText = c.description && c.description.trim()
    setMainHTML(`
      <div class="performer-page">
        <div class="performer-head">
          <div class="pp-name-row">
            <h1 class="pp-name pp-editable" id="col-name" title="Click to edit">${esc(c.name)}</h1>
            <button class="btn btn-ghost btn-sm pp-delete" id="col-delete" title="Delete collection">Delete</button>
          </div>
          <div class="pp-desc pp-editable ${descText ? '' : 'pp-empty'}" id="col-desc" title="Click to edit">${descText ? esc(c.description) : 'Add a description…'}</div>
          <div class="subtitle">${c.recordings.length} recording${c.recordings.length !== 1 ? 's' : ''}</div>
        </div>
        ${colRows.length ? recTableHeadHtml(true) : ''}
        <div class="rec-table" id="rec-table-collection">${rows || '<div class="empty-state" style="min-height:120px"><div>Empty — right-click a recording anywhere to add it here.</div></div>'}</div>
      </div>`)
    wireRecordingRows(mainContent)
    if (colRows.length) wireDateAddedSort(document.getElementById('rec-table-collection'), colRows, true)

    const refreshSidebar = () => { _dimCache.collections = null; if (state.expandedDims.has('collections')) _renderDimRecords('collections') }
    async function saveField(patch) {
      try { await API.collections.update(id, patch); refreshSidebar() }
      catch (e) { alert('Save failed: ' + e.message) }
    }
    makeInlineEditable(document.getElementById('col-name'), {
      get: () => c.name,
      onSave: async v => { v = v.trim(); if (!v || v === c.name) return; c.name = v; await saveField({ name: v }) },
    })
    makeInlineEditable(document.getElementById('col-desc'), {
      multiline: true, placeholder: 'Add a description…',
      get: () => c.description || '',
      onSave: async v => { v = v.trim(); c.description = v; await saveField({ description: v || null }) },
    })

    document.getElementById('col-delete').addEventListener('click', async () => {
      if (!confirm(`Delete collection "${c.name}"? Recordings are not affected.`)) return
      try { await API.collections.remove(id); refreshSidebar(); window.location.hash = '#/collections' }
      catch (e) { alert(e.message) }
    })
  }

  // Artist (person) page — editable info + Performer associations + appearances,
  // grouped by Performer alphabetically. Mirrors the Performer page.
  async function renderPersonView(id) {
    setActiveNav('artists'); setActiveArtist(null); setLoading()
    let a
    try { a = await API.artists.get(id) }
    catch (e) {
      invalidateDims('artists')   // heal the sidebar if this person was removed
      setMainHTML(`<div class="empty-state"><div class="empty-title">This artist no longer exists</div></div>`)
      return
    }
    setNavCurrent(a.name)
    // Performers the person is a member of (already sorted by the API).
    let performers = (a.performers || []).map(p => ({ id: p.id, name: p.name }))

    // Fetch each act's recordings so we can group appearances by performer.
    let perfRecs = []
    try {
      perfRecs = await Promise.all(performers.map(p =>
        API.performers.recordings(p.id).then(rs => ({ performer: p, performances: rs.filter(x => (x.recordings || []).length) }))))
    } catch (_) {}

    const totalRecordings = perfRecs.reduce((n, g) => n + g.performances.reduce((m, p) => m + p.recordings.length, 0), 0)

    // One <section> per performer (alpha), each with a header + flat recording rows.
    const groupsHtml = perfRecs.map(g => {
      const ordered = g.performances.slice().sort((x, y) =>
        (x.start_year || 0) - (y.start_year || 0) ||
        (x.start_month || 0) - (y.start_month || 0) ||
        (x.start_day || 0) - (y.start_day || 0))
      const rows = ordered.map(p =>
        p.recordings.map(r => flatRowHtml({
          id: r.id, performer: p.performer_name,
          start_year: p.start_year, start_month: p.start_month, start_day: p.start_day,
          venue: p.venue_name, city: p.city, state: p.state, country: p.country,
          source: r.source, quality: r.quality,
          rating: r.rating, is_complete: r.is_complete,
          track_count: r.track_count, duration_sec: r.duration_sec,
        }, false)).join('')).join('')
      if (!rows) return ''
      return `<div class="pp-group">
        <div class="pp-group-head"><a href="#/performer/${g.performer.id}">${esc(g.performer.name)}</a></div>
        <div class="rec-table">${rows}</div>
      </div>`
    }).join('')

    // Guest / sit-in appearances — performance_personnel rows on acts this
    // person isn't formally a Membership of (2026-07-18 Per-Show Personnel,
    // ripple item 3: "Béla's page would finally surface his All-Stars
    // sit-ins"). Grouped by performer like the section above, but kept
    // visually separate and tagged "guest" since it's not the same thing as
    // full membership — this is a different act's recording that happens to
    // include this person for one show.
    const guestAppearances = a.guest_appearances || []
    const guestByPerformer = {}
    guestAppearances.forEach(g => {
      const key = g.performer_id
      if (!guestByPerformer[key]) guestByPerformer[key] = { performer_id: g.performer_id, performer_name: g.performer_name, appearances: [] }
      guestByPerformer[key].appearances.push(g)
    })
    const totalGuestRecordings = guestAppearances.reduce((n, g) => n + (g.recordings || []).length, 0)

    const guestGroupsHtml = Object.values(guestByPerformer).map(g => {
      const ordered = g.appearances.slice().sort((x, y) =>
        (x.start_year || 0) - (y.start_year || 0) ||
        (x.start_month || 0) - (y.start_month || 0) ||
        (x.start_day || 0) - (y.start_day || 0))
      const rows = ordered.map(ap =>
        (ap.recordings || []).map(r => flatRowHtml({
          id: r.id, performer: g.performer_name,
          start_year: ap.start_year, start_month: ap.start_month, start_day: ap.start_day,
          venue: ap.venue_name, city: ap.city, state: ap.state, country: ap.country,
          source: r.source, quality: r.quality,
          rating: r.rating, is_complete: r.is_complete,
          track_count: r.track_count, duration_sec: r.duration_sec,
        }, false)).join('')).join('')
      if (!rows) return ''
      // "Guest" tag only when every appearance under this act name is
      // actually is_guest=True (2026-07-23 fix — this section is really "not
      // a formal roster member of this act," which the API named
      // guest_appearances back when that always meant a sit-in. The
      // Members/Guests two-row redesign (2026-07-22) added a real non-guest
      // case here too: a full billed appearance under a one-off act name
      // (e.g. a duo billing) that this person isn't formally on the roster
      // of. Tagging that "guest" was Ryan's bug report — Bela Fleck & Bryan
      // Sutton is a real Member appearance, not a sit-in. Each appearance
      // carries its own is_guest; only tag the group when ALL of them agree.)
      const allGuest = ordered.every(ap => ap.is_guest)
      return `<div class="pp-group">
        <div class="pp-group-head"><a href="#/performer/${g.performer_id}">${esc(g.performer_name)}</a>${allGuest ? ' <span class="pp-guest-tag">guest</span>' : ''}</div>
        <div class="rec-table">${rows}</div>
      </div>`
    }).join('')

    const descText = a.bio && a.bio.trim()
    setMainHTML(`
      <div class="performer-page">
        <div class="performer-head">
          <div class="pp-name-row">
            <h1 class="pp-name pp-editable" id="pn-name" title="Click to edit">${esc(a.name)}</h1>
            <button class="btn btn-ghost btn-sm pp-delete" id="pn-delete" title="Delete artist">Delete</button>
          </div>
          <div class="pp-desc pp-editable ${descText ? '' : 'pp-empty'}" id="pn-desc" title="Click to edit">${descText ? esc(a.bio) : 'Add a bio…'}</div>

          <div class="pp-artists-block">
            <div class="pp-artists-label">Performers</div>
            <div class="pp-artists" id="pn-performers"></div>
          </div>

          <div class="subtitle">${totalRecordings} recording${totalRecordings !== 1 ? 's' : ''} · ${performers.length} performer${performers.length !== 1 ? 's' : ''}${totalGuestRecordings ? ` · ${totalGuestRecordings} guest recording${totalGuestRecordings !== 1 ? 's' : ''}` : ''}</div>
        </div>
        ${groupsHtml || (guestGroupsHtml ? '' : '<div class="empty-state" style="min-height:160px"><div class="empty-title">No appearances yet</div></div>')}
        ${guestGroupsHtml ? `<div class="pp-section-label">Guest Appearances</div>${guestGroupsHtml}` : ''}
      </div>`)

    wireRecordingRows(mainContent)

    const refreshSidebar = () => invalidateDims('artists', 'performers')
    async function saveField(patch) {
      try { await API.artists.update(id, patch); refreshSidebar() }
      catch (e) { alert('Save failed: ' + e.message) }
    }
    makeInlineEditable(document.getElementById('pn-name'), {
      get: () => a.name,
      onSave: async v => { v = v.trim(); if (!v || v === a.name) return; a.name = v; await saveField({ name: v }) },
    })
    makeInlineEditable(document.getElementById('pn-desc'), {
      multiline: true, placeholder: 'Add a bio…',
      get: () => a.bio || '',
      onSave: async v => { v = v.trim(); a.bio = v; await saveField({ bio: v || null }) },
    })

    // ── Editable Performer associations ─────────────────────────────────────
    function renderPerformers() {
      const box = document.getElementById('pn-performers')
      box.innerHTML =
        performers.map((p, i) => `<span class="member-chip">${esc(p.name)} <span class="member-chip-x" data-i="${i}" title="Remove from this act">×</span></span>`).join('') +
        `<span class="artist-picker-wrap pp-add-wrap">
           <input type="text" class="member-input pp-add-input" autocomplete="off" placeholder="Add to a performer…" />
           <div class="artist-dropdown" id="pn-add-dd" style="display:none"></div>
         </span>`
      box.querySelectorAll('.member-chip-x').forEach(x =>
        x.addEventListener('click', async () => {
          const p = performers[parseInt(x.dataset.i)]
          try { await API.artists.removePerformer(id, p.id); invalidateDims('performers') } catch (e) { alert(e.message); return }
          renderPersonView(id)   // reload so the grouped appearances update
        }))
      const input = box.querySelector('.pp-add-input')
      wirePickerDropdown(input, document.getElementById('pn-add-dd'), API.performers.search,
        async ({ id: pid, name }) => {
          try { await API.artists.addPerformer(id, pid ? { performer_id: pid } : { performer_name: name }); invalidateDims('performers') }
          catch (e) { alert(e.message); return }
          renderPersonView(id)
        }, 'Create new performer')
    }
    renderPerformers()

    document.getElementById('pn-delete').addEventListener('click', async () => {
      if (!confirm(`Delete artist "${a.name}"? This can't be undone.`)) return
      try { await API.artists.remove(id); refreshSidebar(); window.location.hash = '#/' }
      catch (e) { alert(e.message) }
    })
  }

  // ── Views ──────────────────────────────────────────────────────────────────

  /** Default library view — all artists, all shows, alpha → oldest first */
  async function renderLibraryView() {
    setActiveNav('library')
    setActiveArtist(null)
    state.selectedArtist = null
    setNavCurrent('Library')
    setLoading()

    let allArtists
    try {
      allArtists = await API.performers.allRecordings()
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

    // Flatten to one row per recording — performer + date + venue on every line,
    // already ordered by performer (backend) then chronologically old→new.
    const rows = allArtists.flatMap(artist =>
      artist.performances.flatMap(p =>
        p.recordings.map(r => ({
          id: r.id, performer: artist.performer_name,
          start_year: p.start_year, start_month: p.start_month, start_day: p.start_day,
          venue: p.venue_name, city: p.city, state: p.state, country: p.country,
          source: r.source, quality: r.quality,
          rating: r.rating, is_complete: r.is_complete,
          track_count: r.track_count, duration_sec: r.duration_sec, created_at: r.created_at,
        }))
      )
    )
    const rowsHtml = rows.map(r => flatRowHtml(r, true)).join('')

    setMainHTML(`
      <div class="artist-header">
        <h1>Library</h1>
        <div class="subtitle">${totalRecordings} recording${totalRecordings !== 1 ? 's' : ''} · ${allArtists.length} performer${allArtists.length !== 1 ? 's' : ''}</div>
      </div>
      ${rows.length ? recTableHeadHtml(true) : ''}
      <div class="rec-table" id="rec-table-library">${rowsHtml}</div>`)

    wireRecordingRows(mainContent)
    if (rows.length) wireDateAddedSort(document.getElementById('rec-table-library'), rows, true)
  }

  /** Recently Added — virtual view, the N most recently ingested recordings.
   *  Not a collection; just a live query, always exactly correct. */
  async function renderRecentView() {
    setActiveNav('recent')
    setActiveArtist(null)
    state.selectedArtist = null
    setNavCurrent('Recently Added')
    setLoading()

    let rows
    try {
      rows = await API.recordings.recent(50)
    } catch (e) {
      setMainHTML(`<div class="empty-state"><div class="empty-title">Failed to load recent recordings</div></div>`)
      return
    }
    const rowsHtml = rows.map(r => flatRowHtml(r, true)).join('')

    setMainHTML(`
      <div class="artist-header">
        <h1>Recently Added</h1>
        <div class="subtitle">${rows.length} most recently added recording${rows.length !== 1 ? 's' : ''}</div>
      </div>
      ${rows.length ? recTableHeadHtml(true) : ''}
      <div class="rec-table" id="rec-table-recent">${rowsHtml || '<div class="empty-state" style="min-height:120px"><div class="empty-title">No recordings yet</div></div>'}</div>`)

    wireRecordingRows(mainContent)
    if (rows.length) wireDateAddedSort(document.getElementById('rec-table-recent'), rows, true)
  }

  /** Performer page — editable info + member Artists + recording catalog. */
  async function renderArtistView(performerId) {
    setActiveNav('library')
    setActiveArtist(performerId)
    setLoading()

    let performer, performances
    try {
      [performer, performances] = await Promise.all([
        API.performers.get(performerId),
        API.performers.recordings(performerId),
      ])
    } catch (e) {
      // Likely a performer that was pruned after reassignment — heal the stale
      // sidebar so the phantom entry disappears.
      invalidateDims('performers')
      setMainHTML(`<div class="empty-state"><div class="empty-title">This performer no longer exists</div><div class="empty-sub">It may have been removed after its recordings were reassigned.</div></div>`)
      return
    }
    setNavCurrent(performer.name)
    // Whatever page brought us here (2026-07-23 generic mechanism — see
    // state.navBack) — shown as a "← Back" breadcrumb below, replacing the
    // old one-shot recFrom that only covered arriving via a Recording's ↗
    // nav-link icon. Read directly, not consumed/cleared: route() already
    // refreshes it on every real navigation, and a same-page reload (e.g.
    // after an inline edit) never touches it.
    const navBack = state.navBack

    state.selectedArtist = performer
    // Local, mutable copy of the roster — edited in place, persisted on each change.
    // Each member also carries `.stints` (date-bounded tenures; usually one
    // unbounded row = "always a member") — see the stint editor below.
    let members = (performer.members || []).map(m => ({ id: m.id, name: m.name, stints: m.stints || [] }))
    let defaultPersonnelMode = performer.default_personnel_mode || 'inherit'
    let expandedMemberId = null   // which member's stint editor drawer is open, if any

    const withRecs = performances.filter(p => (p.recordings || []).length > 0)
    const totalRecordings = withRecs.reduce((n, p) => n + p.recordings.length, 0)

    // Flat one row per recording, oldest→newest. No year headers (one performer).
    const ordered = withRecs.slice().sort((a, b) =>
      (a.start_year || 0) - (b.start_year || 0) ||
      (a.start_month || 0) - (b.start_month || 0) ||
      (a.start_day || 0) - (b.start_day || 0))
    const perfRows = ordered.flatMap(p =>
      p.recordings.map(r => ({
        id: r.id, performer: p.performer_name,
        start_year: p.start_year, start_month: p.start_month, start_day: p.start_day,
        venue: p.venue_name, city: p.city, state: p.state, country: p.country,
        source: r.source, quality: r.quality,
        rating: r.rating, is_complete: r.is_complete,
        track_count: r.track_count, duration_sec: r.duration_sec, created_at: r.created_at,
      }))
    )
    const rowsHtml = perfRows.map(r => flatRowHtml(r, false)).join('')

    const descText = performer.bio && performer.bio.trim()
    setMainHTML(`
      <div class="performer-page">
        ${navBack ? `<div class="pp-back-row"><div class="breadcrumb" id="pp-back-btn">← ${esc(navBack.label)}</div></div>` : ''}
        <div class="performer-head">
          <div class="pp-head-row">
            <div class="pp-head-main">
              <div class="pp-name-row">
                <h1 class="pp-name pp-editable" id="pp-name" title="Click to edit">${esc(performer.name)}</h1>
                <button class="btn btn-ghost btn-sm pp-delete" id="pp-delete" title="Delete performer">Delete</button>
              </div>
              <div class="pp-desc pp-editable ${descText ? '' : 'pp-empty'}" id="pp-desc" title="Click to edit">${descText ? esc(performer.bio) : 'Add a description…'}</div>

              <div class="pp-artists-block">
                <div class="pp-artists-label">Members</div>
                <div class="pp-artists mg-row" id="pp-artists"></div>
                <div class="pp-stint-editor" id="pp-stint-editor" style="display:none"></div>
              </div>
            </div>
            <div class="pp-head-image" id="pp-head-image"></div>
          </div>
        </div>

        <div class="subtitle" id="pp-rec-count">${totalRecordings} recording${totalRecordings !== 1 ? 's' : ''}</div>
        ${perfRows.length ? recTableHeadHtml(false) : ''}
        <div class="rec-table" id="rec-table-performer">${rowsHtml || '<div class="empty-state" style="min-height:200px"><div class="empty-title">No recordings yet</div></div>'}</div>

        <div class="pp-bottom-section">
          <div class="pp-artists-block">
            <div class="pp-artists-label">Resources</div>
            <div class="pp-resources" id="pp-resources"></div>
          </div>

          <div class="pp-artists-block">
            <div class="pp-artists-label">Dossier <span class="pp-artists-label-note">— AI-drafted biography &amp; resource suggestions</span></div>
            <div class="pp-dossier" id="pp-dossier"></div>
          </div>
        </div>
      </div>`)

    wireRecordingRows(mainContent)
    if (perfRows.length) wireDateAddedSort(document.getElementById('rec-table-performer'), perfRows, false)

    if (navBack) {
      document.getElementById('pp-back-btn')?.addEventListener('click', () => {
        window.location.hash = navBack.hash
      })
    }

    const refreshSidebar = () => { _dimCache.performers = null; if (state.expandedDims.has('performers')) _renderDimRecords('performers') }

    // ── Inline-editable name / description ──────────────────────────────────
    async function saveField(patch) {
      try { await API.performers.update(performerId, patch); refreshSidebar() }
      catch (e) { alert('Save failed: ' + e.message) }
    }
    makeInlineEditable(document.getElementById('pp-name'), {
      get: () => performer.name,
      onSave: async v => {
        v = v.trim(); if (!v || v === performer.name) return
        performer.name = v; state.selectedArtist.name = v
        await saveField({ name: v })
      },
    })
    makeInlineEditable(document.getElementById('pp-desc'), {
      multiline: true, placeholder: 'Add a description…',
      get: () => performer.bio || '',
      onSave: async v => {
        v = v.trim(); performer.bio = v
        await saveField({ bio: v || null })
      },
    })

    // ── Editable Artists (members) + per-person stint dates ──────────────────
    // A member usually has one unbounded stint ("always a member" — zero UI
    // tax, matches every pre-2026-07-18 row). Click a chip's name to expand
    // an inline drawer for real tenure dates (era lineups, second stints like
    // Mickey Hart) — see Per-Show Personnel design doc §7.6.
    async function persistMembers() { await saveField({ members: members.map(m => m.name) }) }

    async function refreshRoster() {
      // Stint mutations happen against Membership rows directly (not via the
      // plain-name-list sync), so re-fetch rather than hand-patch local state.
      const fresh = await API.performers.get(performerId)
      members = (fresh.members || []).map(m => ({ id: m.id, name: m.name, stints: m.stints || [] }))
      defaultPersonnelMode = fresh.default_personnel_mode || 'inherit'
    }

    function isUnbounded(s) {
      return !s.start_year && !s.start_month && !s.start_day && !s.end_year && !s.end_month && !s.end_day
    }

    // Members row uses the same (+) button + inline picker style as the
    // recording page's Members/Guests rows (2026-07-22) — no Guests row here,
    // guests are a per-show concept and don't apply to the act itself.
    function renderArtists() {
      const box = document.getElementById('pp-artists')
      box.innerHTML =
        members.map((m, i) => `
          <span class="member-chip ${expandedMemberId === m.id ? 'member-chip--expanded' : ''}">
            <span class="member-chip-name" data-id="${m.id}" title="Click to edit stint dates">${esc(m.name)}</span>
            <span class="member-chip-x" data-i="${i}" title="Remove">×</span>
          </span>`).join('') +
        `<button type="button" class="mg-add-btn" id="pp-add-btn" title="Add Member Name">+</button>
         <span class="artist-picker-wrap mg-add-picker" id="pp-add-picker" style="display:none">
           <input type="text" class="member-input mg-role-input" id="pp-add-input" autocomplete="off" placeholder="Add Member Name…" />
           <div class="artist-dropdown" id="pp-add-dd" style="display:none"></div>
         </span>`
      box.querySelectorAll('.member-chip-x').forEach(x =>
        x.addEventListener('click', async () => {
          const removedId = members[parseInt(x.dataset.i)]?.id
          members.splice(parseInt(x.dataset.i), 1)
          if (expandedMemberId === removedId) expandedMemberId = null
          await persistMembers(); renderArtists(); renderStintEditor()
        }))
      box.querySelectorAll('.member-chip-name').forEach(el =>
        el.addEventListener('click', () => {
          const id = parseInt(el.dataset.id)
          expandedMemberId = (expandedMemberId === id) ? null : id
          renderArtists(); renderStintEditor()
        }))
      document.getElementById('pp-add-btn').addEventListener('click', () => {
        const picker = document.getElementById('pp-add-picker')
        const showing = picker.style.display !== 'none'
        picker.style.display = showing ? 'none' : 'inline-flex'
        if (!showing) document.getElementById('pp-add-input').focus()
      })
      const input = box.querySelector('#pp-add-input')
      wirePickerDropdown(input, document.getElementById('pp-add-dd'), API.artists.search,
        async ({ name }) => {
          name = (name || '').trim()
          if (name && !members.some(m => m.name.toLowerCase() === name.toLowerCase())) {
            members.push({ name }); await persistMembers()   // set_performer_members creates new people as needed
            await refreshRoster()
          }
          renderArtists()
        }, 'Create new artist')
    }

    function renderStintEditor() {
      const box = document.getElementById('pp-stint-editor')
      const member = members.find(m => m.id === expandedMemberId)
      if (!member) { box.style.display = 'none'; box.innerHTML = ''; return }
      box.style.display = ''
      const single = member.stints.length <= 1
      box.innerHTML = `
        <div class="pp-stint-editor-head">
          <span class="pp-stint-editor-title">Stint dates — <b>${esc(member.name)}</b></span>
          <span class="pp-stint-editor-close" title="Close">×</span>
        </div>
        <div class="pp-stint-rows">
          ${member.stints.map(s => `
            <div class="pp-stint-row" data-stint-id="${s.id}">
              ${isUnbounded(s) ? '<span class="pp-stint-always">Always a member — leave blank, or set dates for a specific tenure</span>' : ''}
              <input type="number" class="pp-stint-input pp-s-y1" placeholder="Start yr" value="${s.start_year ?? ''}" style="width:64px" />
              <input type="number" class="pp-stint-input pp-s-m1" placeholder="mo" value="${s.start_month ?? ''}" min="1" max="12" style="width:38px" />
              <input type="number" class="pp-stint-input pp-s-d1" placeholder="day" value="${s.start_day ?? ''}" min="1" max="31" style="width:38px" />
              <span class="pp-stint-dash">–</span>
              <input type="number" class="pp-stint-input pp-s-y2" placeholder="End yr" value="${s.end_year ?? ''}" style="width:64px" />
              <input type="number" class="pp-stint-input pp-s-m2" placeholder="mo" value="${s.end_month ?? ''}" min="1" max="12" style="width:38px" />
              <input type="number" class="pp-stint-input pp-s-d2" placeholder="day" value="${s.end_day ?? ''}" min="1" max="31" style="width:38px" />
              <span class="pp-stint-del" title="Remove this stint" ${single ? 'style="display:none"' : ''}>×</span>
            </div>`).join('')}
        </div>
        <button class="btn btn-ghost btn-xs pp-stint-add-btn" type="button">+ Add another stint (e.g. a second tenure)</button>`

      box.querySelector('.pp-stint-editor-close').addEventListener('click', () => {
        expandedMemberId = null; renderArtists(); renderStintEditor()
      })

      box.querySelectorAll('.pp-stint-row').forEach(row => {
        const stintId = parseInt(row.dataset.stintId)
        const read = () => ({
          start_year:  parseInt(row.querySelector('.pp-s-y1').value) || null,
          start_month: parseInt(row.querySelector('.pp-s-m1').value) || null,
          start_day:   parseInt(row.querySelector('.pp-s-d1').value) || null,
          end_year:    parseInt(row.querySelector('.pp-s-y2').value) || null,
          end_month:   parseInt(row.querySelector('.pp-s-m2').value) || null,
          end_day:     parseInt(row.querySelector('.pp-s-d2').value) || null,
        })
        row.querySelectorAll('.pp-stint-input').forEach(inp =>
          inp.addEventListener('blur', async () => {
            try { await API.performers.updateStint(stintId, read()); await refreshRoster(); renderArtists(); renderStintEditor() }
            catch (e) { alert('Failed to save stint: ' + e.message) }
          }))
        const del = row.querySelector('.pp-stint-del')
        if (del) del.addEventListener('click', async () => {
          try { await API.performers.removeStint(stintId); await refreshRoster(); renderArtists(); renderStintEditor() }
          catch (e) { alert('Failed to remove stint: ' + e.message) }
        })
      })

      box.querySelector('.pp-stint-add-btn').addEventListener('click', async () => {
        try {
          await API.performers.addStint(performerId, member.id, {})   // unbounded until edited
          await refreshRoster(); renderArtists(); renderStintEditor()
        } catch (e) { alert('Failed to add stint: ' + e.message) }
      })
    }

    renderArtists()
    renderStintEditor()

    // Performer.default_personnel_mode is still a real field (new
    // performances of this act still start in whatever mode it's set to,
    // and the case-5 auto-flip still fires per-show) — it just has no
    // manual UI control on this page anymore, per the 2026-07-22 Members/
    // Guests redesign. `defaultPersonnelMode` is kept around unused here
    // only because refreshRoster() still reads it off a fresh fetch; nothing
    // reads the local variable itself now.

    // ── Editable reference Resources (external DBs / discographies) ──────────
    let resources = (performer.resources || []).map(r => ({ label: r.label, url: r.url }))
    const persistResources = () => saveField({ resources })
    function renderResources() {
      const box = document.getElementById('pp-resources')
      if (!box) return
      box.innerHTML =
        resources.map((r, i) => `
          <div class="pp-resource-row" data-i="${i}">
            <a class="pp-resource-link" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.label || r.url)}</a>
            <span class="pp-resource-url">${esc(r.url)}</span>
            <span class="pp-resource-x" data-i="${i}" title="Remove">×</span>
          </div>`).join('') +
        `<div class="pp-resource-add">
           <input type="text" class="pp-res-label" placeholder="Label (optional)" />
           <input type="text" class="pp-res-url" placeholder="https://…" />
           <button class="btn btn-ghost btn-xs pp-res-add-btn" type="button">Add</button>
         </div>`
      box.querySelectorAll('.pp-resource-x').forEach(x =>
        x.addEventListener('click', async () => { resources.splice(parseInt(x.dataset.i), 1); await persistResources(); renderResources() }))
      const urlEl = box.querySelector('.pp-res-url')
      const labelEl = box.querySelector('.pp-res-label')
      const add = async () => {
        let url = urlEl.value.trim()
        if (!url) return
        if (!/^https?:\/\//i.test(url)) url = 'https://' + url
        resources.push({ label: labelEl.value.trim() || null, url })
        await persistResources(); renderResources()
      }
      box.querySelector('.pp-res-add-btn').addEventListener('click', add)
      urlEl.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add() } })
    }
    renderResources()

    // ── Profile picture (2026-07-22) — one image slot, top-right ─────────────
    function renderProfileImage() {
      const box = document.getElementById('pp-head-image')
      if (!box) return
      box.innerHTML = `
        <div class="pp-image-frame">
          ${performer.has_image
            ? `<img class="pp-image-img" id="pp-image-img" src="${API.performers.imageUrl(performerId)}" alt="${esc(performer.name)}" />`
            : `<div class="pp-image-placeholder">No photo</div>`}
        </div>
        <input type="file" id="pp-image-input" accept="image/png,image/jpeg,image/webp" style="display:none" />
        <div class="pp-image-actions">
          <button type="button" class="btn btn-ghost btn-xs" id="pp-image-upload-btn">${performer.has_image ? 'Replace photo' : 'Add photo'}</button>
          ${performer.has_image ? `<button type="button" class="btn btn-ghost btn-xs" id="pp-image-remove-btn">Remove</button>` : ''}
        </div>`
      document.getElementById('pp-image-upload-btn').addEventListener('click', () =>
        document.getElementById('pp-image-input').click())
      document.getElementById('pp-image-input').addEventListener('change', async e => {
        const file = e.target.files[0]
        if (!file) return
        try { await API.performers.uploadImage(performerId, file); performer.has_image = true; renderProfileImage() }
        catch (err) { alert('Upload failed: ' + err.message) }
      })
      document.getElementById('pp-image-remove-btn')?.addEventListener('click', async () => {
        if (!confirm('Remove this photo?')) return
        try { await API.performers.removeImage(performerId); performer.has_image = false; renderProfileImage() }
        catch (err) { alert('Failed: ' + err.message) }
      })
    }
    renderProfileImage()

    // ── Dossier — AI-drafted biography + suggested resource links ────────────
    // "AI suggests, human approves": nothing here writes to `bio` or
    // `resources` on its own — Copy to Description and + Add are both
    // explicit clicks (same rule as the ingest-side AI Assist auto-apply
    // fix earlier this session — see performer_research.py's module doc).
    let dossier = performer.dossier || null
    function renderDossier() {
      const box = document.getElementById('pp-dossier')
      if (!box) return
      const runLabel = dossier ? '↻ Run again' : '✨ Run Dossier'
      if (!dossier) {
        box.innerHTML = `<div class="pp-dossier-empty">No research run yet.</div>
          <button type="button" class="btn btn-ghost btn-xs" id="pp-dossier-run">${runLabel}</button>`
      } else {
        const cost = dossier.usage ? formatAiCost(dossier.usage) : ''
        const bioParas = (dossier.biography || '').split('\n').map(s => s.trim()).filter(Boolean)
        box.innerHTML = `
          <div class="ai-res-title">Biography draft ${cost} <button type="button" class="btn btn-ghost btn-xs" id="pp-dossier-run">${runLabel}</button></div>
          ${dossier.thinking ? `<p class="ai-summary">${esc(formatAiThinking(dossier.thinking))}</p>` : ''}
          <div class="pp-dossier-bio">
            ${bioParas.map(p => `<p>${esc(p)}</p>`).join('')}
          </div>
          <button type="button" class="btn btn-ghost btn-xs" id="pp-dossier-copy-bio">Copy to Description</button>
          ${(dossier.resources || []).length ? `
            <div class="ai-res-section">
              <div class="ai-res-title">Suggested resources</div>
              ${dossier.resources.map((r, i) => `
                <div class="ai-res-row">
                  <span class="ai-res-value"><a class="ai-link" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.label || r.url)}</a></span>
                  <button type="button" class="btn btn-ghost btn-xs pp-dossier-add-res" data-i="${i}">+ Add</button>
                </div>`).join('')}
            </div>` : ''}
          ${(dossier.sources || []).length ? `
            <div class="ai-res-section"><div class="ai-res-title">Sources</div>${dossier.sources.map(s =>
              `<p class="ai-res-note"><a class="ai-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></p>`).join('')}</div>` : ''}`
      }
      document.getElementById('pp-dossier-run').addEventListener('click', runDossier)
      document.getElementById('pp-dossier-copy-bio')?.addEventListener('click', async () => {
        const bio = dossier.biography || ''
        performer.bio = bio
        await saveField({ bio: bio || null })
        const descEl = document.getElementById('pp-desc')
        if (descEl) { descEl.textContent = bio || 'Add a description…'; descEl.classList.toggle('pp-empty', !bio.trim()) }
      })
      box.querySelectorAll('.pp-dossier-add-res').forEach(btn =>
        btn.addEventListener('click', async () => {
          const r = dossier.resources[parseInt(btn.dataset.i)]
          if (!r || resources.some(x => x.url === r.url)) return
          resources.push({ label: r.label || null, url: r.url })
          await persistResources(); renderResources()
          btn.disabled = true; btn.textContent = 'Added ✓'
        }))
    }

    async function runDossier() {
      const box = document.getElementById('pp-dossier')
      box.innerHTML = `<div class="ai-loading"><div class="loading-spinner"></div><div>Researching the web — this can take a minute or two… <span id="pp-dossier-elapsed">0s</span></div></div>`
      const t0 = Date.now()
      try {
        const { job_id } = await API.performers.startDossier(performerId)
        dossier = await pollDossierJob(performerId, job_id, t0)
        renderDossier()
      } catch (e) {
        box.innerHTML = `<div class="empty-state" style="color:var(--red)">Dossier failed: ${esc(e.message)}</div>`
      }
    }
    renderDossier()

    document.getElementById('pp-delete').addEventListener('click', async () => {
      if (!confirm(`Delete performer "${performer.name}"? This can't be undone.`)) return
      try { await API.performers.remove(performerId); refreshSidebar(); window.location.hash = '#/' }
      catch (e) { alert(e.message) }
    })
  }

  // Turn an element into a click-to-edit field. opts: {get, onSave, multiline, placeholder}.
  function makeInlineEditable(el, opts) {
    if (!el) return
    el.addEventListener('click', () => {
      if (el.querySelector('input, textarea')) return   // already editing
      const cur = opts.get()
      const field = document.createElement(opts.multiline ? 'textarea' : 'input')
      field.className = 'pp-inline-input'
      field.value = cur
      if (opts.multiline) field.rows = 3
      el.replaceChildren(field)
      field.focus(); field.select?.()
      let done = false
      const commit = async (save) => {
        if (done) return; done = true
        const val = field.value
        if (save) await opts.onSave(val)
        const shown = (opts.get() || '').trim()
        el.textContent = shown || (opts.placeholder || '')
        el.classList.toggle('pp-empty', !shown)
      }
      field.addEventListener('blur', () => commit(true))
      field.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); commit(false) }
        else if (e.key === 'Enter' && !opts.multiline) { e.preventDefault(); field.blur() }
        else if (e.key === 'Enter' && e.metaKey) { e.preventDefault(); field.blur() }
      })
    })
  }

  // AI Assist on the saved-recording page — open the AI tab, run a research job,
  // render interactive findings (same Apply/auto-update experience as Add
  // Recording, adapted for a live record — see renderRecAiResults).
  async function startRecAiAssist(recordingId, rec, perf) {
    // The button lives inside the (already-open) AI pane; running replaces it.
    const body = document.getElementById('ai-results')
    if (!body) return
    body.innerHTML = `<div class="ai-loading"><div class="loading-spinner"></div><div>Researching the web — this can take a minute or two… <span id="ai-elapsed">0s</span></div></div>`
    const t0 = Date.now()
    try {
      const { job_id } = await API.ingest.aiAssistRecording(recordingId)
      const result = await pollAiJob(job_id, t0)
      if (rec) rec.ai_research = result   // keep local state in sync (server has already saved it)
      renderRecAiResults(result, body, recordingId, rec, perf)
    } catch (e) {
      const secs = Math.round((Date.now() - t0) / 1000)
      const msg = /no_api_key/.test(e.message)
        ? 'No Anthropic API key set — add one in Settings (⚙).'
        : `AI Assist failed after ${secs}s: ${esc(e.message)}`
      body.innerHTML = `<div class="ai-assist-cta">
        <p class="ai-res-note" style="color:var(--red)">${msg}</p>
        <button class="btn btn-primary btn-sm iq-ai-btn" id="btn-ai-assist-retry">✨ Try again</button>
      </div>`
      document.getElementById('btn-ai-assist-retry')?.addEventListener('click', () => startRecAiAssist(recordingId, rec, perf))
    }
  }

  // ── Checksums pane — .ffp/.md5/.st5 fingerprint verification (View Recording) ──
  // Track.checksum is {type, expected, status, verified_at} or null (no
  // fingerprint file could be matched to that track). "status" is one of
  // match / mismatch / unverified, set by app/utils/checksums.py.
  const CKSUM_STATUS_LABEL = { match: '✓ Match', mismatch: '✗ Mismatch', unverified: '— Unverified' }

  function buildChecksumsPaneHtml(tracks) {
    tracks = tracks || []
    const withData = tracks.filter(t => t.checksum)
    if (!withData.length) {
      return `<div class="info-panel-empty">No checksums on file for this recording yet — click Re-validate to check the library folder for a fingerprint file.</div>`
    }
    const mismatches = withData.filter(t => t.checksum.status === 'mismatch').length
    const summary = mismatches
      ? `<div class="cksum-summary cksum-summary--warn">⚠ ${mismatches} track${mismatches === 1 ? '' : 's'} did not match ${mismatches === 1 ? 'its' : 'their'} recorded checksum.</div>`
      : `<div class="cksum-summary cksum-summary--ok">✓ All checked tracks match their recorded checksum.</div>`
    const rows = tracks.map(t => {
      const c = t.checksum
      const num = esc(String(t.track_number).padStart(2, '0'))
      const title = esc(t.title)
      if (!c) {
        return `<div class="cksum-row"><span class="cksum-num">${num}</span><span class="cksum-title">${title}</span><span class="cksum-type">—</span><span class="cksum-status">no fingerprint</span></div>`
      }
      return `
        <div class="cksum-row">
          <span class="cksum-num">${num}</span>
          <span class="cksum-title">${title}</span>
          <span class="cksum-type">${esc((c.type || '').toUpperCase())}</span>
          <span class="cksum-status cksum-status--${esc(c.status || '')}">${CKSUM_STATUS_LABEL[c.status] || esc(c.status || '')}</span>
        </div>
        ${c.status === 'mismatch' ? `<div class="cksum-detail">expected ${esc(c.expected || '')}</div>` : ''}`
    }).join('')
    const md5Note = withData.some(t => t.checksum.type === 'md5')
      ? `<p class="cksum-hint">MD5 checks the whole file, tags included — any tag edit (including Write Tags to Files) will flip a match to a mismatch. Expected, not corruption.</p>` : ''
    const st5Note = withData.some(t => t.checksum.type === 'st5')
      ? `<p class="cksum-hint">ST5 verification is best-effort — treat a mismatch as worth a second look, not a hard failure.</p>` : ''
    return `${summary}<div class="cksum-rows">${rows}</div>${md5Note}${st5Note}`
  }

  // Add Recording's Checksums pane is detection-only — the files haven't been
  // copied yet at review time, so there's nothing to verify against; real
  // verification happens automatically on Confirm (see api/ingest.py
  // _do_confirm) once the copy exists at a stable library path.
  function buildChecksumsPreviewHtml(fingerprints) {
    fingerprints = fingerprints || []
    if (!fingerprints.length) {
      return `<div class="info-panel-empty">No checksum/fingerprint files (.ffp / .md5 / .st5) found in this folder.</div>`
    }
    const rows = fingerprints.map(fp => `
      <div class="cksum-row">
        <span class="cksum-type">${esc((fp.type || '').toUpperCase())}</span>
        <span class="cksum-title">${esc(fp.filename)}</span>
      </div>`).join('')
    return `<div class="cksum-summary">Found ${fingerprints.length} fingerprint file${fingerprints.length === 1 ? '' : 's'} — verified automatically against the copied files when you confirm.</div>
      <div class="cksum-rows">${rows}</div>`
  }

  // ── Shared AI Assist results template — Add Recording + View Recording ────────
  // One HTML builder for both surfaces so they stay visually/structurally in
  // sync as the feature evolves; each caller wires its own Apply behavior
  // (draft form state vs. live API writes — see renderAiResults / renderRecAiResults).
  // city/state/country are attributes of the Venue record, not the show
  // (Ryan's call, 2026-07-13) — split into a distinct sub-group so that reads
  // clearly regardless of where the proposal ends up getting applied.
  const AI_VENUE_FIELDS = ['city', 'state', 'country']

  // Cost badge — reads the usage block ai_assist.py::_compute_cost attaches
  // to every result (2026-07-21, Problem 3 of the AI Assist Refinement spec).
  // r.usage is null when the model has no pricing entry (see _PRICING in
  // ai_assist.py) rather than showing a misleading "free" — that's the one
  // case this renders nothing.
  function formatAiCost(usage) {
    if (!usage) return ''
    const c = usage.cost_cents
    const label = c >= 100 ? `$${(c / 100).toFixed(2)}` : `${c.toFixed(c < 1 ? 3 : 2)}¢`
    const n = usage.web_search_requests || 0
    const title = `${usage.input_tokens.toLocaleString()} in / ${usage.output_tokens.toLocaleString()} out tokens`
      + (n ? ` + ${n} web search${n === 1 ? '' : 'es'}` : '')
    return `<span class="ai-cost-badge" title="${esc(title)}">${label}</span>`
  }

  function buildAiResultsHtml(r, opts = {}) {
    const proposals = r.proposals || []
    const row = (p, i) => `
      <div class="ai-res-row">
        <span class="ai-res-field">${esc(p.field)}</span>
        <span class="ai-res-value">${esc(p.proposed)}
          <span class="ai-res-conf">${esc(p.confidence || '')}</span>${p.url ? ` <a class="ai-link" href="${esc(p.url)}" target="_blank" rel="noopener">source</a>` : ''}</span>
        <button class="btn btn-ghost btn-xs ai-apply-btn" data-idx="${i}">Apply</button>
      </div>`
    const indexed    = proposals.map((p, i) => ({ p, i }))
    const perfRows   = indexed.filter(x => !AI_VENUE_FIELDS.includes(x.p.field))
    const venueRows  = indexed.filter(x =>  AI_VENUE_FIELDS.includes(x.p.field))
    const propsHtml  = perfRows.map(x => row(x.p, x.i)).join('')
      + (venueRows.length
          ? `<div class="ai-res-subhead">Venue details <span class="ai-res-subhead-note">— the venue record, not this show</span></div>${venueRows.map(x => row(x.p, x.i)).join('')}`
          : '')

    const tt = r.track_titles || []
    const trackSection = tt.length
      ? `<div class="ai-res-section">
           <div class="ai-res-title">Track Listing <button class="btn btn-ghost btn-xs" id="ai-apply-tracks">Apply to tracks</button></div>
           <div class="ai-tt-list">${tt.map(t =>
             `<div class="ai-tt-row"><span class="ai-tt-num">${esc(String(t.number).padStart(2, '0'))}</span><span class="ai-tt-title">${esc(t.title)}</span></div>`).join('')}</div>
         </div>` : ''

    const notes = (title, items) => items && items.length
      ? `<div class="ai-res-section"><div class="ai-res-title">${title}</div>${items.map(v => `<p class="ai-res-note">${esc(v)}</p>`).join('')}</div>` : ''
    const sources = (r.sources || []).length
      ? `<div class="ai-res-section"><div class="ai-res-title">Sources</div>${r.sources.map(s => `<p class="ai-res-note"><a class="ai-link" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></p>`).join('')}</div>` : ''

    const rerunBtn = opts.showRerun
      ? `<button class="btn btn-ghost btn-xs" id="btn-ai-rerun" title="Run AI Assist again">↻ Run again</button>` : ''

    return `
      <div class="ai-res-section">
        <div class="ai-res-title">Metadata Review ${formatAiCost(r.usage)} ${rerunBtn}</div>
        ${r.thinking ? `<p class="ai-summary">${esc(formatAiThinking(r.thinking))}</p>` : ''}
        ${propsHtml || '<p class="ai-res-empty">No field changes proposed.</p>'}
      </div>
      ${trackSection}
      ${notes('Verify', r.verify_items)}
      ${notes('Provenance', r.provenance_notes)}
      ${sources}`
  }

  // Apply a single AI proposal to the live, saved recording via the same
  // endpoints the page's own inline editors use. city/state/country land on
  // the linked Venue when one exists (and it's a real venue — see
  // isPlaceholderVenue), otherwise on the Performance's own fallback location
  // fields — mirrors how the app resolves location for display everywhere
  // else. `venueRef` is a small mutable holder tracking both the linked
  // venue's id AND name, so a 'venue' proposal applied earlier in the same
  // batch is visible to a 'city'/'state'/'country' proposal applied right
  // after it (and so we know whether that venue is a placeholder).
  async function applyRecProposal(field, value, perf, recordingId, venueRef) {
    const perfId = perf.id
    switch (field) {
      case 'artist':
        await API.performances.update(perfId, { performer_name: value })
        invalidateDims('performers', 'artists')
        break
      case 'date': {
        const p = String(value).split('-')
        await API.performances.update(perfId, {
          start_year:  p[0] ? parseInt(p[0]) : null,
          start_month: p[1] ? parseInt(p[1]) : null,
          start_day:   p[2] ? parseInt(p[2]) : null,
        })
        break
      }
      case 'venue': {
        if (isPlaceholderVenue(value)) {
          // AI proposing "Unknown Venue"/"TBD" isn't a real answer — don't
          // create or link a shared placeholder row. Leave venueRef as-is.
          break
        }
        const existing = await API.venues.list(value)
        let venueId = (existing || []).find(v => v.name.toLowerCase() === value.toLowerCase())?.id
        if (!venueId) { const c = await API.venues.create({ name: value }); venueId = c.id; invalidateDims('venues') }
        await API.performances.update(perfId, { venue_id: venueId })
        venueRef.venue_id = venueId
        venueRef.venue_name = value
        break
      }
      case 'event': {
        const existing = await API.events.search(value)
        let eventId = (existing || []).find(e => e.name.toLowerCase() === value.toLowerCase())?.id
        if (!eventId) { const c = await API.events.create({ name: value }); eventId = c.id }
        await API.performances.update(perfId, { event_id: eventId })
        break
      }
      case 'source':
        await API.recordings.update(recordingId, { source: value, change_note: 'AI Assist' })
        break
      case 'city': case 'state': case 'country':
        // A placeholder-named linked venue ("Unknown Venue", ...) isn't a
        // real canonical place — never write location onto it (that row is
        // shared across unrelated shows). Route to the Performance's own
        // fallback fields instead, same as the no-venue-at-all case.
        if (venueRef.venue_id && !isPlaceholderVenue(venueRef.venue_name)) {
          await API.venues.update(venueRef.venue_id, { [field]: value })
        } else {
          await API.performances.update(perfId, { [field]: value })
        }
        break
    }
  }

  // Apply the AI's researched setlist onto a saved recording's tracks.
  async function applyRecTrackTitles(titles, rec, recordingId) {
    const jobs = (titles || [])
      .map(tt => {
        const track = (rec.tracks || []).find(t => String(t.track_number) === String(tt.number))
        return (track && tt.title) ? API.tracks.update(track.id, { title: tt.title }) : null
      })
      .filter(Boolean)
    if (jobs.length) await Promise.all(jobs)
    renderRecordingView(recordingId)
  }

  // Applied fields land immediately (matches every other field on this page's
  // click-to-edit/auto-save pattern) — no Revert toggle; a full reload
  // refreshes every affected field at once, so "undo" is just editing again.
  //
  // No auto-apply, regardless of confidence (Ryan, 2026-07-20 — AI Assist
  // Refinement spec, Context Library). A rare Danny Gatton/Cellar Door
  // 1/25/79 recording got confidently, silently overwritten with a wrong
  // date twice in a row (a different wrong date each run) — proof the
  // model's own "high confidence" self-rating isn't trustworthy enough to
  // act on unsupervised. Every proposal, at every confidence level, now
  // requires an explicit click on its own Apply button.
  function renderRecAiResults(r, body, recordingId, rec, perf) {
    if (!body) return
    body.innerHTML = buildAiResultsHtml(r, { showRerun: true })
    const venueRef = { venue_id: perf?.venue_id || null, venue_name: perf?.venue || null }

    async function applyOne(idx, btn) {
      const p = (r.proposals || [])[idx]
      if (!p) return
      if (btn) { btn.disabled = true; btn.textContent = '…' }
      try {
        await applyRecProposal(p.field, p.proposed, perf, recordingId, venueRef)
        if (btn) { btn.textContent = '✓ Applied'; btn.classList.add('applied') }
      } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = 'Apply' }
        alert('Failed to apply: ' + e.message)
        throw e
      }
    }

    body.querySelectorAll('.ai-apply-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        try { await applyOne(parseInt(btn.dataset.idx), btn) }
        catch (_) { return }
        renderRecordingView(recordingId)
      })
    })
    document.getElementById('ai-apply-tracks')?.addEventListener('click', () =>
      applyRecTrackTitles(r.track_titles || [], rec, recordingId))
    document.getElementById('btn-ai-rerun')?.addEventListener('click', () =>
      startRecAiAssist(recordingId, rec, perf))
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

    // "← Back" points at whatever page immediately preceded this one — the
    // generic navBack mechanism (route()), not the old state.selectedArtist
    // hack that only worked if you'd arrived via a Performer page (Ryan's
    // 2026-07-23 bug report: Recently Added → Recording → Back landed on
    // Library, since selectedArtist was never set by Recently Added).
    const backLabel = state.navBack ? `← ${esc(state.navBack.label)}` : '← Back'
    const backHash  = state.navBack ? state.navBack.hash : '#/'

    // We need performance info to show the date/venue
    let perf = null
    try { perf = await API.performances.get(rec.performance_id) } catch (_) {}

    const dateStr    = perf ? fmtDateRangeLong(perf) : ''
    const venueStr   = perf?.venue_name || ''
    const venueId    = perf?.venue_id   || null
    const locStr     = perf ? fmtLocation(perf.city, perf.state, perf.country) : ''
    const perfName   = perf?.performer || ''
    const perfId     = perf?.performer_id || null
    const eventStr   = perf?.event_name || ''
    setNavCurrent(dateStr || perfName || 'Recording')

    // Small "go to its own page" nav icons (2026-07-23) — same treatment for
    // Performer and Venue, shown regardless of edit permission since it's
    // navigation, not editing. Plain hash links — the generic navBack
    // mechanism (route()) picks up the "came from a recording" reference
    // automatically, no per-link wiring needed.
    const perfNavLink = perfId
      ? `<a class="rec-nav-link" href="#/performer/${perfId}" title="Go to ${esc(perfName)}'s page">↗</a>` : ''
    const venueNavLink = venueId
      ? `<a class="rec-nav-link" href="#/venue/${venueId}" title="Go to ${esc(venueStr)}'s page">↗</a>` : ''

    // Date line — venue is a clickable link if we have a venue_id
    const venueHtml  = venueId
      ? `<span class="venue-link" data-venue-id="${venueId}">${esc(venueStr)}</span>${venueNavLink}`
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

    // Inner HTML of a track's title cell: title + official badge + flag chips
    // + inline note. Factored so the right-click quick-edit menu can refresh a
    // single row in place after changing flags or notes.
    function trackTitleInnerHtml(t) {
      const badges = trackBadgesHtml(t)
      return `<span class="track-title-text">${esc(t.title)}</span>${badges ? ' ' + badges : ''}`
    }

    // Flat track list — no disc/set grouping. Note/Songwriter are click-to-edit
    // directly in the row; right-click is Flags (+ Official) only — matches
    // Add Recording's track table treatment (Ryan, 2026-07-15).
    const canEdit  = canEditLibrary()
    const editHint = canEdit ? ' title="Click title to rename · right-click for flags"' : ''
    const trackRows = (rec.tracks || []).map(t => {
      const isPlaying  = t.id === state.playingTrackId
      const playingCls = isPlaying ? ' playing' : ''
      const playIcon   = isPlaying ? '▶' : '▷'
      return `
        <div class="track-row${playingCls}" data-track-id="${t.id}" data-flags="${(t.flags||[]).join(',')}"${editHint}>
          <span class="track-play">${playIcon}</span>
          <span class="track-num">${String(t.track_number || '').padStart(2,'0')}</span>
          <span class="track-title-wrap">
            <span class="track-title truncate${canEdit ? ' track-title--editable' : ''}">${trackTitleInnerHtml(t)}</span>
          </span>
          <span class="track-note-col truncate${canEdit ? ' pp-editable' : ''}${t.notes ? '' : ' pp-empty'}" id="t-note-${t.id}" title="${esc(t.notes || (canEdit ? 'Click to add a note' : ''))}">${esc(t.notes || (canEdit ? '—' : ''))}</span>
          <span class="track-sw-col truncate${canEdit ? ' pp-editable' : ''}${t.songwriter ? '' : ' pp-empty'}" id="t-sw-${t.id}" title="${esc(t.songwriter || (canEdit ? 'Click to add a songwriter' : ''))}">${esc(t.songwriter || (canEdit ? '—' : ''))}</span>
          <span class="track-dur">${fmtDuration(t.duration)}</span>
        </div>`
    }).join('')

    // Editable for admins/archivists — same textarea treatment as Add
    // Recording's Info File pane (looks like plain text until you click in;
    // Ryan, 2026-07-15: "match the info file editing capability and UX
    // treatment we have in Add Recording"). Read-only <pre> for viewers.
    const infoContent = canEdit
      ? `<textarea class="rev-info-text rev-info-edit" id="rec-info-edit"
          placeholder="No info file found — paste or type one in.">${esc(rec.info_file_content || '')}</textarea>`
      : (rec.info_file_content
          ? `<pre class="info-file-content">${esc(rec.info_file_content)}</pre>`
          : `<div class="info-panel-empty">No info file attached</div>`)

    // Per-track "has analysis" map (gates the waveform banner + Fidelity tab)
    // and duration lookup (needed alongside peaks whenever wavesurfer (re)loads).
    _waveformMap      = {}
    _trackDurationMap = {}
    ;(rec.tracks || []).forEach(t => {
      const wf = t.analysis?.waveform
      const hasWf = Array.isArray(wf) ? wf.length > 0 : !!(wf && wf.max && wf.max.length)
      if (hasWf) _waveformMap[t.id] = wf
      _trackDurationMap[t.id] = t.duration || 0
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

    // Cutoff is informational only now — NOT a transcode detector (Ryan,
    // 2026-07-15: "I don't buy them really... every recording in the
    // collection has said Possible Transcode"). Checked against the real
    // library: 99% of analysed tracks tripped the old "within 2kHz of
    // Nyquist" rule. That's not a rare warning sign, it's just what live/
    // audience recordings look like — natural mic/room/tape rolloff pushes
    // the -40dB cutoff well below Nyquist on almost everything, with no
    // relation to whether a file was ever lossy-transcoded. A single cutoff
    // frequency can't tell a gradual natural rolloff apart from a lossy
    // codec's hard bandwidth wall — that would need the actual rolloff
    // shape, which isn't captured here. So: no more warning icon, no more
    // "possible transcode" accusation — just the number, plus a positive
    // callout on the rare track that's genuinely full-spectrum.
    const nyquist    = srHz ? srHz / 2 : 22050
    const cutoffFull = cutoffHz && srHz && (cutoffHz >= nyquist - 500)
    const fmtCutoff  = hz => hz ? `${(hz / 1000).toFixed(1)} kHz` : '—'

    // ── Interpretive hints ────────────────────────────────────────────────────
    const hint = s => `<span class="hm-hint">${s}</span>`

    const formatLabel = (() => {
      if (bitrateK && !bitDepth) return hint('Lossy')
      if (!bitDepth) return ''
      if (srHz >= 88200)                       return hint('Hi-Res')
      if (bitDepth >= 24)                      return hint('Studio')
      if (bitDepth <= 16 && srHz <= 44100)     return hint('Standard')
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

    const cutoffHint = cutoffFull ? hint('Full spectrum') : ''

    // Top-right box: a vertical-tab panel now (Ryan, 2026-07-15 — "turn the
    // box into a multi-vertical tab element like the lower right one is"),
    // mirroring the lower-right slide-panel's look. Two tabs: Source (Source/
    // Lineage/Quality/Rating — shown by default, quick-editable in place for
    // admin/archivist: click the value → type → Enter to save) and Fidelity
    // (the analysis metrics + this track's spectrogram, dimmed/empty until
    // analysis exists).
    const trunc          = (s, n) => s && s.length > n ? s.slice(0, n) + '…' : s
    const sourceDisplay  = rec.source || ''
    const lineageDisplay = rec.lineage ? trunc(rec.lineage, 220) : null

    const qEditable = canEditLibrary()
    const qc  = qEditable ? ' hm-val--editable' : ''
    const qa  = f => qEditable ? ` data-qedit="${f}" title="Click to edit"` : ''
    const sourcePane = `
      <div class="hm-row"><span class="hm-label">Source</span><span class="hm-val${qc}"${qa('source')}>${esc(sourceDisplay || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Lineage</span><span class="hm-val${qc}"${qa('lineage')}>${esc(lineageDisplay || rec.lineage || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Quality</span><span class="hm-val ${qualityClass(rec.quality)}${qc}"${qa('quality')}>${esc(rec.quality || '—')}</span></div>
      <div class="hm-row"><span class="hm-label">Rating</span><span class="hm-val${qc}"${qa('rating')}>${rec.rating != null ? `<span class="rating-badge">${rec.rating}</span>` : '—'}</span></div>`

    // Spectrogram now lives here (below Dyn Range) instead of its own tab in
    // the lower-right panel, which was getting crowded (Ryan, 2026-07-15).
    // Same element ids as before — loadSpectrogram() and its wiring are
    // unchanged, just relocated.
    // Re-Analyze lives here now, not in the bottom action row (Ryan,
    // 2026-07-15) — it regenerates exactly the data this pane shows, so it
    // belongs next to it.
    const reanalyzeBtn = canEdit
      ? `<button class="btn btn-ghost btn-sm hm-reanalyze-btn" id="btn-analyze-audio">Re-Analyze</button>`
      : ''
    const fidelityPane = firstAnalysed
      ? `${reanalyzeBtn}
         <div class="hm-row"><span class="hm-label">Format</span><span class="hm-val hm-metric">${fmtBit(bitDepth)} / ${fmtSr(srHz)}</span>${formatLabel}</div>
         <div class="hm-row"><span class="hm-label">Cutoff</span><span class="hm-val hm-metric">${fmtCutoff(cutoffHz)}</span>${cutoffHint}</div>
         <div class="hm-row"><span class="hm-label">RMS</span><span class="hm-val hm-metric">${fmtDb(rmsDb)}</span>${rmsHint}</div>
         <div class="hm-row"><span class="hm-label">Dyn Range</span><span class="hm-val hm-metric">${fmtDb(dynDb)}</span>${dynHint}</div>
         <div class="hm-spectrogram">
           <div class="hm-spectrogram-label">Spectrogram <span class="spectrogram-track-name" id="spectrogram-track-name"></span></div>
           <div id="spectrogram-wrap">
             <div class="spectrogram-img-wrap" id="spectrogram-img-wrap">
               <div class="spectrogram-loading" id="spectrogram-loading">Generating…</div>
               <img id="spectrogram-img" class="spectrogram-img" style="display:none" />
             </div>
           </div>
         </div>`
      : `<div class="hm-pane-empty">No analysis yet.${reanalyzeBtn ? ` ${reanalyzeBtn}` : ''}</div>`

    // Which track to show by default: currently playing (if in this rec) else first track
    const firstTrack    = rec.tracks?.[0] ?? null
    const defaultTrackId = (state.playingTrackId && _waveformMap[state.playingTrackId])
      ? state.playingTrackId
      : (firstTrack?.id ?? null)

    // Collections moved out of the box, up to the top row alongside the back
    // link (Ryan, 2026-07-15).
    const collectionArea = `
      <div class="rec-collections" id="rec-collections">
        ${(rec.collections || []).map(collectionTagHtml).join('')}
        <button class="collection-add-btn" id="btn-add-collection">+ Add to Collection</button>
      </div>`

    setMainHTML(`
      <div class="rec-view-shell">
      ${hasAnalysis ? `
      <!-- Waveform banner — spans full width above everything, incl. the back
           link (Ryan, 2026-07-15: "placed at the top of the screen, above
           everything"). Hidden entirely until analysis exists. Rendered with
           wavesurfer.js (vendored locally under /js/vendor/ — no CDN, this
           app runs offline) — adopted officially 2026-07-15 after a spike;
           replaces the old hand-rolled canvas renderer. -->
      <div class="rec-waveform-wrap" id="rec-waveform-wrap">
        <div id="rec-waveform-ws" class="rec-waveform-ws"></div>
      </div>` : ''}
      <!-- Back link + collections, one row, horizontally aligned (Ryan, 2026-07-15) -->
      <div class="rec-top-row">
        <div class="breadcrumb" id="back-btn">${backLabel}</div>
        ${collectionArea}
      </div>
      <div class="rec-detail-header">
        <div class="rec-header-left">
          <div class="rec-name-row">
            <h2 class="rec-perf-name${canEdit ? ' pp-editable' : ''}" id="rec-perf-name"${canEdit ? ' title="Click to reassign performer"' : ''}>${esc(perfName) || (canEdit ? '<span class="pp-empty">Set performer</span>' : '')}</h2>
            ${perfNavLink}
          </div>
          <div class="rec-date-line" id="rec-date-line">
            <span class="rec-f rec-f-date${canEdit ? ' pp-editable' : ''}" id="rec-f-date">${dateStr ? esc(dateStr) : (canEdit ? '<span class="pp-empty">Add date</span>' : '')}</span>
            <span class="rec-dot">·</span>
            ${canEdit
              ? `<span class="rec-f rec-f-venue pp-editable" id="rec-f-venue">${venueStr ? esc(venueStr) : '<span class="pp-empty">Add venue</span>'}</span>${venueNavLink}`
              : (venueHtml || '')}
            ${locStr ? `<span class="rec-dot">·</span><span class="rec-f-loc">${esc(locStr)}</span>` : ''}
            ${canEdit
              ? `<span class="rec-dot">·</span><span class="rec-f rec-f-event pp-editable${eventStr ? '' : ' pp-empty'}" id="rec-f-event" title="Click to set the festival/event this show is part of">${eventStr ? esc(eventStr) : 'Add event'}</span>`
              : (eventStr ? `<span class="rec-dot">·</span><span class="rec-f-loc">${esc(eventStr)}</span>` : '')}
          </div>
          <div class="rec-artists-row" id="rec-artists"></div>
          <div class="rec-header-notes${canEdit ? ' pp-editable' : ''}${rec.notes ? '' : ' pp-empty'}" id="rec-notes"${canEdit ? ' title="Click to edit notes"' : ''}>${rec.notes ? esc(rec.notes) : (canEdit ? 'Add notes…' : '')}</div>
          ${rec.is_official ? `<div class="badge-row"><span class="badge-official" title="Contains officially released material">© Official</span></div>` : ''}
        </div>
        <!-- Source / Fidelity vertical-tab box (Ryan, 2026-07-15 — mirrors the
             lower-right slide-panel's look). Source shown by default; Fidelity
             is dimmed until analysis exists, and now also holds this track's
             spectrogram (moved out of the lower-right panel, which was
             getting crowded). -->
        <div class="rec-header-right hm-tabbed-panel">
          <div class="hm-tabbed-body">
            <div class="hm-pane active" id="hm-pane-source">${sourcePane}</div>
            <div class="hm-pane" id="hm-pane-fidelity">${fidelityPane}</div>
          </div>
          <div class="hm-tabs">
            <button class="hm-tab active" data-hmpane="source">Source</button>
            <button class="hm-tab${firstAnalysed ? '' : ' hm-tab--empty'}" data-hmpane="fidelity">Fidelity</button>
          </div>
        </div>
      </div>
      <div class="action-bar">
        <!-- Playback actions only — editing/admin actions live at the bottom -->
        <button class="btn btn-ghost btn-sm" id="btn-play-all">▶ Play All</button>
        <label class="skip-toggle skip-toggle--action" title="Skip announcements, banter &amp; tuning from queue">
          <input type="checkbox" class="skip-filter-cb" id="skip-filter-action" ${state.skipNonMusic ? 'checked' : ''} />
          <span class="skip-toggle-track"></span>
          <span class="skip-toggle-label">Skip Non-Music</span>
        </label>
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
              <div class="slide-pane-scroll"><div class="rev-raw-section">${infoContent}</div></div>
            </div>

            <!-- Spectrogram moved into the top-right Fidelity tab, 2026-07-15
                 (Ryan: this panel was getting crowded) — see hm-pane-fidelity. -->

            <!-- File Tags pane — actual on-disk Vorbis comments -->
            <div class="slide-pane" id="sp-filetags">
              <div class="slide-pane-header">File Tags <span class="filetags-hint">(Vorbis, on disk)</span></div>
              <div class="slide-pane-scroll" id="sp-filetags-body">
                <div class="info-panel-empty">Loading…</div>
              </div>
            </div>

            <!-- Checksums pane — .ffp/.md5/.st5 fingerprint verification -->
            <div class="slide-pane" id="sp-checksums">
              <div class="slide-pane-header">Checksums
                <button class="btn btn-ghost btn-xs" id="btn-cksum-revalidate" title="Re-check against the files on disk">↻ Re-validate</button>
              </div>
              <div class="slide-pane-scroll" id="sp-checksums-body">${buildChecksumsPaneHtml(rec.tracks)}</div>
            </div>

            ${canEdit ? `
            <!-- AI Assist pane (results of a web-research pass) -->
            <div class="slide-pane" id="sp-ai">
              <div class="slide-pane-header">AI Assist</div>
              <div class="slide-pane-scroll"><div class="ai-results" id="ai-results">
                <div class="ai-assist-cta">
                  <button class="btn btn-primary btn-sm iq-ai-btn" id="btn-ai-assist">✨ AI Assist</button>
                  <div class="ai-assist-hint">Research the web to verify and fill this recording's metadata.</div>
                </div>
              </div></div>
            </div>` : ''}

          </div>

          <!-- Vertical tab strip anchored to the right edge -->
          <div class="slide-tabs">
            <button class="slide-tab" data-pane="info">Info File</button>
            <button class="slide-tab" data-pane="filetags">File Tags</button>
            <button class="slide-tab" data-pane="checksums">Checksums</button>
            ${canEdit ? `<button class="slide-tab slide-tab--ai" data-pane="ai">AI Assist</button>` : ''}
          </div>
        </div>

      </div>
      ${canEdit ? `
      <div class="rec-bottom-actions">
        <button class="btn btn-sm ${stagedCount > 0 ? 'btn-staged' : 'btn-ghost'}" id="btn-write-tags">Write Tags to Files</button>
        <button class="btn btn-sm ${rec.is_official ? 'btn-staged' : 'btn-ghost'}" id="btn-official" title="Mark this recording (and its tracks) as an official release">${rec.is_official ? '✓ Official Release' : 'Mark as Official Release'}</button>
        <button class="btn btn-danger btn-sm" id="btn-delete-rec" title="Delete this recording from the database (files are not removed)">Delete Recording</button>
      </div>` : ''}
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

    // ── Quick edit: recording metadata (Source/Lineage/Quality/Rating) ────────
    // Click an editable value → inline input → Enter saves, Esc cancels.
    function metaCellDisplay(field) {
      if (field === 'source')  return esc(rec.source || '—')
      if (field === 'lineage') { const l = rec.lineage; return esc(l ? (l.length > 220 ? l.slice(0, 220) + '…' : l) : '—') }
      if (field === 'rating')  return rec.rating != null ? `<span class="rating-badge">${rec.rating}</span>` : '—'
      return esc(rec.quality || '—')  // quality
    }
    function startMetaQuickEdit(cell) {
      const field = cell.dataset.qedit
      const raw = field === 'rating'  ? (rec.rating != null ? rec.rating : '')
                : field === 'source'  ? (rec.source  || '')
                : field === 'lineage' ? (rec.lineage || '')
                :                       (rec.quality || '')
      const type  = field === 'rating' ? 'number' : 'text'
      const extra = field === 'rating' ? 'min="0" max="100"' : ''
      cell.innerHTML = `<input class="hm-qedit-input" type="${type}" ${extra} value="${esc(String(raw))}" />`
      const input = cell.querySelector('input')
      input.focus(); input.select()
      let done = false
      const finish = async (save) => {
        if (done) return; done = true
        if (save) {
          const v = input.value.trim()
          const payload = {}
          if (field === 'rating') payload.rating = v === '' ? null : Math.max(0, Math.min(100, parseInt(v, 10) || 0))
          else payload[field] = v || null
          try {
            await API.recordings.update(recordingId, { ...payload, change_note: 'Quick edit' })
            Object.assign(rec, payload)
            const wt = document.getElementById('btn-write-tags')   // now has unwritten changes
            wt?.classList.add('btn-staged'); wt?.classList.remove('btn-ghost')
          } catch (e) { console.error('Quick edit failed:', e) }
        }
        cell.innerHTML = metaCellDisplay(field)
        if (field === 'quality') cell.className = `hm-val ${qualityClass(rec.quality)} hm-val--editable`
      }
      input.addEventListener('keydown', e => {
        e.stopPropagation()
        if (e.key === 'Enter')  { e.preventDefault(); finish(true) }
        else if (e.key === 'Escape') { finish(false) }
      })
      input.addEventListener('blur', () => finish(true))
    }
    if (canEditLibrary()) {
      mainContent.querySelector('.rec-header-right')?.addEventListener('click', ev => {
        const cell = ev.target.closest('.hm-val--editable[data-qedit]')
        if (cell && !cell.querySelector('input')) startMetaQuickEdit(cell)
      })
    }

    // ── Quick edit: right-click a track → flags + note popup ──────────────────
    function markStaged() {
      const wt = document.getElementById('btn-write-tags')
      wt?.classList.add('btn-staged'); wt?.classList.remove('btn-ghost')
    }
    function refreshTrackRow(t) {
      const row = mainContent.querySelector(`.track-row[data-track-id="${t.id}"]`)
      if (!row) return
      const titleEl = row.querySelector('.track-title')
      if (titleEl) titleEl.innerHTML = trackTitleInnerHtml(t)
      const noteEl = row.querySelector('.track-note-col')
      if (noteEl) {
        noteEl.textContent = t.notes || '—'; noteEl.title = t.notes || 'Click to add a note'
        noteEl.classList.toggle('pp-empty', !t.notes)
      }
      const swEl = row.querySelector('.track-sw-col')
      if (swEl) {
        swEl.textContent = t.songwriter || '—'; swEl.title = t.songwriter || 'Click to add a songwriter'
        swEl.classList.toggle('pp-empty', !t.songwriter)
      }
      row.dataset.flags = (t.flags || []).join(',')
      applySkipFilter()
    }
    // Click a track title → inline rename (auto-saves on Enter/blur).
    function startTrackTitleEdit(titleEl, t) {
      if (titleEl.querySelector('input')) return
      titleEl.innerHTML = `<input class="track-title-input" type="text" value="${esc(t.title || '')}" />`
      const input = titleEl.querySelector('input')
      input.focus(); input.select()
      let done = false
      const finish = async (save) => {
        if (done) return; done = true
        if (save) {
          const v = input.value.trim()
          if (v && v !== t.title) {
            t.title = v
            try { await API.tracks.update(t.id, { title: v }); markStaged() }
            catch (e) { console.error('Track rename failed:', e) }
          }
        }
        titleEl.innerHTML = trackTitleInnerHtml(t)
      }
      input.addEventListener('click', e => e.stopPropagation())
      input.addEventListener('keydown', e => {
        e.stopPropagation()
        if (e.key === 'Enter') { e.preventDefault(); finish(true) }
        else if (e.key === 'Escape') { finish(false) }
      })
      input.addEventListener('blur', () => finish(true))
    }
    if (canEditLibrary()) {
      mainContent.querySelectorAll('.track-row[data-track-id]').forEach(row => {
        const track = rec.tracks.find(t => t.id === parseInt(row.dataset.trackId))
        if (!track) return
        // Click the title → rename (don't start playback)
        row.querySelector('.track-title--editable')?.addEventListener('click', ev => {
          ev.stopPropagation()
          startTrackTitleEdit(row.querySelector('.track-title'), track)
        })
        // Right-click anywhere on the row → flags (+ Official) popup. Note
        // and Songwriter used to live here too; they're click-to-edit cells
        // directly in the row now, matching Add Recording (Ryan, 2026-07-15).
        row.addEventListener('contextmenu', ev => {
          ev.preventDefault()
          openTrackMenu(track, ev.clientX, ev.clientY, {
            flagsOnly: true,
            showOfficial: true,
            onChange: async (t) => {
              try { await API.tracks.update(t.id, { flags: t.flags, is_official: t.is_official }); markStaged() }
              catch (e) { console.error(e) }
              refreshTrackRow(t)
            },
          })
        })

        // Note / Songwriter — click-to-edit directly in the row, same
        // treatment as Add Recording's track table.
        makeInlineEditable(document.getElementById(`t-note-${track.id}`), {
          placeholder: '—',
          get: () => track.notes || '',
          onSave: async v => {
            v = v.trim() || null
            track.notes = v
            try { await API.tracks.update(track.id, { notes: v }); markStaged() }
            catch (e) { alert('Failed: ' + e.message) }
            refreshTrackRow(track)
          },
        })
        makeInlineEditable(document.getElementById(`t-sw-${track.id}`), {
          placeholder: '—',
          get: () => track.songwriter || '',
          onSave: async v => {
            v = v.trim() || null
            track.songwriter = v
            try { await API.tracks.update(track.id, { songwriter: v }); markStaged() }
            catch (e) { alert('Failed: ' + e.message) }
            refreshTrackRow(track)
          },
        })
      })
    }

    // Collection tags (add / remove)
    wireRecCollectionArea(recordingId)

    // ── Inline header editing (performer / date / venue / artists / notes) ─────
    if (canEdit && perf) {
      const reload = () => renderRecordingView(recordingId)

      // Performer name → reassign (autocomplete; Enter commits typed name).
      const nameEl = document.getElementById('rec-perf-name')
      nameEl?.addEventListener('click', () => {
        if (nameEl.querySelector('input')) return
        nameEl.innerHTML = `<span class="artist-picker-wrap" style="display:inline-block; min-width:220px">
          <input type="text" class="pp-inline-input" id="rec-perf-input" value="${esc(perf.performer || '')}" autocomplete="off" />
          <div class="artist-dropdown" id="rec-perf-dd" style="display:none"></div></span>`
        const input = document.getElementById('rec-perf-input')
        input.focus(); input.select()
        let committed = false
        const commit = async name => {
          if (committed) return; committed = true
          name = (name || '').trim()
          if (name && name.toLowerCase() !== (perf.performer || '').toLowerCase()) {
            try { await API.performances.update(perf.id, { performer_name: name }); invalidateDims('performers', 'artists') }
            catch (e) { alert('Failed: ' + e.message) }
          }
          reload()
        }
        wirePickerDropdown(input, document.getElementById('rec-perf-dd'), API.performers.search,
          ({ name }) => commit(name), 'Create new performer')
        input.addEventListener('keydown', e => {
          e.stopPropagation()
          if (e.key === 'Enter') { e.preventDefault(); commit(input.value) }
          else if (e.key === 'Escape') { committed = true; reload() }
        })
      })

      // Date → inline Year / Month / Day, with an optional End Date (same
      // +/- toggle pattern as the ingest form's "+ End date", 2026-07-23 —
      // for multi-day stands, e.g. the Danny Gatton Cellar Door shows).
      // Clearing the end fields and committing removes the end date.
      const dateEl = document.getElementById('rec-f-date')
      dateEl?.addEventListener('click', () => {
        if (dateEl.querySelector('input')) return
        const hasEnd = !!(perf.end_year || perf.end_month || perf.end_day)
        dateEl.innerHTML = `
          <input type="number" class="rec-date-input" id="rec-d-y" placeholder="YYYY" value="${perf.start_year || ''}" min="1900" max="2099" style="width:52px" />
          <input type="number" class="rec-date-input" id="rec-d-m" placeholder="MM" value="${perf.start_month || ''}" min="1" max="12" style="width:38px" />
          <input type="number" class="rec-date-input" id="rec-d-d" placeholder="DD" value="${perf.start_day || ''}" min="1" max="31" style="width:38px" />
          <a class="field-toggle-link" id="rec-toggle-end-date" href="#">${hasEnd ? '− End date' : '+ End date'}</a>
          <span id="rec-end-date-fields" style="display:${hasEnd ? 'inline-flex' : 'none'}; gap:3px; margin-left:4px">
            <input type="number" class="rec-date-input" id="rec-d-y2" placeholder="YYYY" value="${perf.end_year || ''}" min="1900" max="2099" style="width:52px" />
            <input type="number" class="rec-date-input" id="rec-d-m2" placeholder="MM" value="${perf.end_month || ''}" min="1" max="12" style="width:38px" />
            <input type="number" class="rec-date-input" id="rec-d-d2" placeholder="DD" value="${perf.end_day || ''}" min="1" max="31" style="width:38px" />
          </span>`
        document.getElementById('rec-d-y').focus()
        let done = false
        const commit = async () => {
          if (done) return; done = true
          const y = parseInt(document.getElementById('rec-d-y').value) || null
          const m = parseInt(document.getElementById('rec-d-m').value) || null
          const d = parseInt(document.getElementById('rec-d-d').value) || null
          const endShown = document.getElementById('rec-end-date-fields').style.display !== 'none'
          const ey = endShown ? (parseInt(document.getElementById('rec-d-y2').value) || null) : null
          const em = endShown ? (parseInt(document.getElementById('rec-d-m2').value) || null) : null
          const ed = endShown ? (parseInt(document.getElementById('rec-d-d2').value) || null) : null
          try {
            await API.performances.update(perf.id, {
              start_year: y, start_month: m, start_day: d,
              end_year: ey, end_month: em, end_day: ed,
            })
          } catch (e) { alert('Failed: ' + e.message) }
          reload()
        }
        document.getElementById('rec-toggle-end-date').addEventListener('click', e => {
          e.preventDefault()
          const box = document.getElementById('rec-end-date-fields')
          const visible = box.style.display !== 'none'
          if (visible) {
            // Hide and clear — committing after this removes the end date.
            box.style.display = 'none'
            e.currentTarget.textContent = '+ End date'
            document.getElementById('rec-d-y2').value = ''
            document.getElementById('rec-d-m2').value = ''
            document.getElementById('rec-d-d2').value = ''
          } else {
            box.style.display = 'inline-flex'
            e.currentTarget.textContent = '− End date'
            // Pre-fill from the start date on first reveal, same as ingest.
            if (!document.getElementById('rec-d-y2').value) document.getElementById('rec-d-y2').value = document.getElementById('rec-d-y').value
            if (!document.getElementById('rec-d-m2').value) document.getElementById('rec-d-m2').value = document.getElementById('rec-d-m').value
            if (!document.getElementById('rec-d-d2').value) document.getElementById('rec-d-d2').value = document.getElementById('rec-d-d').value
            document.getElementById('rec-d-y2').focus()
          }
        })
        dateEl.querySelectorAll('input').forEach(inp => {
          inp.addEventListener('keydown', e => {
            e.stopPropagation()
            if (e.key === 'Enter') { e.preventDefault(); commit() }
            else if (e.key === 'Escape') { done = true; reload() }
          })
        })
        // commit when focus leaves the whole date group
        dateEl.addEventListener('focusout', () => setTimeout(() => {
          if (!dateEl.contains(document.activeElement)) commit()
        }, 0))
      })

      // Venue → picker (search existing / create new)
      const venueEl = document.getElementById('rec-f-venue')
      venueEl?.addEventListener('click', () => {
        if (venueEl.querySelector('input')) return
        venueEl.innerHTML = `<span class="venue-picker-wrap" style="display:inline-block; min-width:200px">
          <input type="text" class="pp-inline-input" id="rec-venue-input" value="${esc(perf.venue_name || '')}" autocomplete="off" />
          <div class="venue-dropdown" id="rec-venue-dd" style="display:none"></div></span>`
        const input = document.getElementById('rec-venue-input')
        input.focus(); input.select()
        let committed = false
        const commitVenue = async ({ id, name }) => {
          if (committed) return; committed = true
          try {
            let venueId = id
            if (!venueId && name) { const c = await API.venues.create({ name }); venueId = c.id; invalidateDims('venues') }
            if (venueId) await API.performances.update(perf.id, { venue_id: venueId })
          } catch (e) { alert('Failed: ' + e.message) }
          reload()
        }
        wireVenuePickerDropdown(input, document.getElementById('rec-venue-dd'), commitVenue)
        input.addEventListener('keydown', e => {
          e.stopPropagation()
          if (e.key === 'Enter') { e.preventDefault(); commitVenue({ id: null, name: input.value.trim() }) }
          else if (e.key === 'Escape') { committed = true; reload() }
        })
      })

      // Festival / Event → picker (search existing / create new / clear)
      const eventEl = document.getElementById('rec-f-event')
      eventEl?.addEventListener('click', () => {
        if (eventEl.querySelector('input')) return
        eventEl.innerHTML = `<span class="event-picker-wrap" style="display:inline-block; min-width:160px">
          <input type="text" class="pp-inline-input" id="rec-event-input" value="${esc(perf.event_name || '')}" autocomplete="off" />
          <div class="event-dropdown" id="rec-event-dd" style="display:none"></div></span>`
        const input = document.getElementById('rec-event-input')
        input.focus(); input.select()
        let committed = false
        const commitEvent = async ({ id, name }) => {
          if (committed) return; committed = true
          try {
            let eventId = id
            if (!eventId && name) { const c = await API.events.create({ name }); eventId = c.id }
            await API.performances.update(perf.id, { event_id: eventId || null })
          } catch (e) { alert('Failed: ' + e.message) }
          reload()
        }
        wirePickerDropdown(input, document.getElementById('rec-event-dd'), API.events.search,
          ({ id, name }) => commitEvent({ id, name }), 'Create new event')
        input.addEventListener('keydown', e => {
          e.stopPropagation()
          if (e.key === 'Enter') { e.preventDefault(); commitEvent({ id: null, name: input.value.trim() }) }
          else if (e.key === 'Escape') { committed = true; reload() }
        })
      })

      // Notes → inline multiline (recording-level)
      makeInlineEditable(document.getElementById('rec-notes'), {
        multiline: true, placeholder: 'Add notes…',
        get: () => rec.notes || '',
        onSave: async v => {
          v = v.trim(); rec.notes = v
          try { await API.recordings.update(recordingId, { notes: v || null, change_note: 'Quick edit' }); markStaged() }
          catch (e) { alert('Failed: ' + e.message) }
        },
      })

      // Info File — always-editable textarea (not click-to-reveal like Notes
      // above), matching Add Recording's treatment. Auto-saves on blur, only
      // when the text actually changed. Not a tag field, so no markStaged().
      const infoEditEl = document.getElementById('rec-info-edit')
      if (infoEditEl) {
        infoEditEl.addEventListener('blur', async () => {
          const v = infoEditEl.value
          if (v === (rec.info_file_content || '')) return
          try {
            await API.recordings.update(recordingId, { info_file_content: v || null, change_note: 'Edited info file' })
            rec.info_file_content = v
          } catch (e) { alert('Failed to save info file: ' + e.message) }
        })
      }

      // Members/Guests two-row personnel widget (2026-07-22, replacing the
      // single Artists pill row + Inherit/Explicit mode selector). Pills
      // split purely on perf.personnel[].is_guest — Members = roster/explicit
      // non-guest rows, Guests = is_guest rows — same split used by the Add
      // Recording form's createMembersWidget, matched visually here (mg-row/
      // mg-add-btn/mg-add-picker markup) so both surfaces look identical.
      // The Inherit/Explicit mode is still a real field on Performance (case
      // 5 — dropping a roster member for this one show — still auto-flips it
      // under the hood), it just no longer has a manual UI control; nothing
      // in this Phase needed one, since editing the rows already covers
      // every case the toggle used to require picking by hand.
      const persistPersonnelLists = async (memberNames, guestNames) => {
        try {
          await API.performances.update(perf.id, { members: memberNames, guests: guestNames })
          invalidateDims('artists')
        } catch (e) { alert('Failed: ' + e.message) }
        reload()
      }

      function renderRecArtists() {
        const box = document.getElementById('rec-artists')
        if (!box) return
        const personnel = perf.personnel || []
        const members = personnel.filter(p => !p.is_guest)
        const guests  = personnel.filter(p =>  p.is_guest)
        const listFor = role => role === 'guest' ? guests : members

        const pill = (p, i, role) => `
          <span class="member-chip ${role === 'guest' ? 'member-chip--guest' : ''}">
            <span class="member-chip-name rec-pill-name" data-role="${role}" data-i="${i}" title="${canEdit ? 'Click for instrument/note' : ''}">${esc(p.name)}</span>
            ${canEdit ? `<span class="member-chip-x" data-role="${role}" data-i="${i}" title="Remove">×</span>` : ''}
          </span>`
        const row = (role, label, items) => `
          <div class="mg-row">
            <span class="mg-row-label">${label}</span>
            ${items.map((p, i) => pill(p, i, role)).join('')}
            ${canEdit ? `
              <button type="button" class="mg-add-btn" data-role="${role}" title="Add ${label === 'Members' ? 'Member' : 'Guest'} Name">+</button>
              <span class="artist-picker-wrap mg-add-picker" data-role="${role}" style="display:none">
                <input type="text" class="member-input mg-role-input" data-role="${role}" autocomplete="off" placeholder="Add ${label === 'Members' ? 'Member' : 'Guest'} Name…" />
                <div class="artist-dropdown mg-role-dd" data-role="${role}" style="display:none"></div>
              </span>` : (items.length === 0 ? '<span class="mg-row-empty">—</span>' : '')}
          </div>`

        box.innerHTML =
          row('member', 'Members', members) + row('guest', 'Guests', guests) +
          `<div class="rec-personnel-detail" id="rec-personnel-detail" style="display:none"></div>`

        if (!canEdit) return

        box.querySelectorAll('.member-chip-x').forEach(x =>
          x.addEventListener('click', async () => {
            const role = x.dataset.role, idx = parseInt(x.dataset.i)
            const newMembers = (role === 'member' ? members.filter((_, i) => i !== idx) : members).map(p => p.name)
            const newGuests  = (role === 'guest'  ? guests.filter((_, i) => i !== idx)  : guests).map(p => p.name)
            await persistPersonnelLists(newMembers, newGuests)
          }))

        box.querySelectorAll('.rec-pill-name').forEach(el =>
          el.addEventListener('click', () => renderPersonnelDetail(listFor(el.dataset.role)[parseInt(el.dataset.i)])))

        box.querySelectorAll('.mg-add-btn').forEach(btn =>
          btn.addEventListener('click', () => {
            const picker = box.querySelector(`.mg-add-picker[data-role="${btn.dataset.role}"]`)
            const input  = picker?.querySelector('.mg-role-input')
            if (!picker || !input) return
            const showing = picker.style.display !== 'none'
            box.querySelectorAll('.mg-add-picker').forEach(p => { p.style.display = 'none' })
            picker.style.display = showing ? 'none' : 'inline-flex'
            if (!showing) input.focus()
          }))

        box.querySelectorAll('.mg-role-input').forEach(input => {
          const role = input.dataset.role
          const dd   = box.querySelector(`.mg-role-dd[data-role="${role}"]`)
          wirePickerDropdown(input, dd, API.artists.search,
            async ({ name }) => {
              name = (name || '').trim()
              if (!name || listFor(role).some(p => p.name.toLowerCase() === name.toLowerCase())) return
              const newMembers = members.map(p => p.name).concat(role === 'member' ? [name] : [])
              const newGuests  = guests.map(p => p.name).concat(role === 'guest'  ? [name] : [])
              await persistPersonnelLists(newMembers, newGuests)
            }, 'Create new artist')
        })
      }

      function renderPersonnelDetail(p) {
        const box = document.getElementById('rec-personnel-detail')
        if (!box) return
        if (!p.id) {
          // Purely inherited — no performance_personnel row of its own, so
          // there's nothing here to attach an instrument/note to (that would
          // mean converting them to an explicit entry first — not a plain
          // metadata edit, not built in Phase 2).
          box.style.display = ''
          box.innerHTML = `<div class="rec-personnel-detail-inner">
            <span class="pp-stint-always">${esc(p.name)} is from the act's roster — instrument/note only apply to guests or explicit-mode entries.</span>
            <span class="pp-stint-editor-close" id="rec-pd-close" title="Close">×</span>
          </div>`
          document.getElementById('rec-pd-close').addEventListener('click', () => { box.style.display = 'none' })
          return
        }
        box.style.display = ''
        box.innerHTML = `<div class="rec-personnel-detail-inner">
          <span class="pp-stint-editor-title">${esc(p.name)}</span>
          <input type="text" class="pp-stint-input rec-pd-instrument" placeholder="Instrument (optional)" value="${esc(p.instrument || '')}" style="width:140px" />
          <input type="text" class="pp-stint-input rec-pd-note" placeholder="Note (optional)" value="${esc(p.note || '')}" style="width:180px" />
          <span class="pp-stint-editor-close" id="rec-pd-close" title="Close">×</span>
        </div>`
        const commit = async () => {
          const instrument = box.querySelector('.rec-pd-instrument').value.trim() || null
          const note       = box.querySelector('.rec-pd-note').value.trim() || null
          p.instrument = instrument; p.note = note   // keep the closure's copy in sync until the next reload
          try { await API.performances.updatePersonnelRow(perf.id, p.id, { instrument, note }) }
          catch (e) { alert('Failed: ' + e.message) }
        }
        box.querySelector('.rec-pd-instrument').addEventListener('blur', commit)
        box.querySelector('.rec-pd-note').addEventListener('blur', commit)
        document.getElementById('rec-pd-close').addEventListener('click', () => { box.style.display = 'none' })
      }

      renderRecArtists()

      // AI Assist (top-right) — research the web to verify/fill this recording.
      // Scoped inside this block (like the header editors above) since applying
      // a proposal needs perf.id. Non-editors never get the pane in the DOM.
      document.getElementById('btn-ai-assist')?.addEventListener('click', () => startRecAiAssist(recordingId, rec, perf))
      // Saved research from a prior run — render it immediately instead of the CTA.
      if (rec.ai_research) {
        renderRecAiResults(rec.ai_research, document.getElementById('ai-results'), recordingId, rec, perf)
      }
    }

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

    // Re-validate checksums — re-checks against the files on disk now, and
    // opportunistically picks up any fingerprint file that was never parsed
    // (e.g. this recording predates the checksum feature). Not gated on
    // canEdit — re-checking integrity is a read-only action.
    document.getElementById('btn-cksum-revalidate')?.addEventListener('click', async (e) => {
      const btn = e.currentTarget
      btn.disabled = true
      btn.textContent = '…'
      try { await API.recordings.verifyChecksums(recordingId) }
      catch (err) { alert('Re-validate failed: ' + err.message) }
      renderRecordingView(recordingId)
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
        // Update in place (no full reload) so the side panel stays open and the
        // user sees the result instantly. Clear the staged indicator on the
        // button, then refresh the File Tags pane if it's open.
        btn.disabled = false
        btn.textContent = 'Write Tags to Files'
        btn.classList.remove('btn-staged')
        btn.classList.add('btn-ghost')
        const ftPane = document.getElementById('sp-filetags')
        if (ftPane && ftPane.classList.contains('active')) {
          loadFileTags(recordingId)
        }
      } catch (e) {
        alert('Error writing tags: ' + e.message)
        const btn = document.getElementById('btn-write-tags')
        if (btn) { btn.disabled = false; btn.textContent = 'Write Tags to Files' }
      }
    })

    // Mark / unmark as official release (cascades to tracks server-side).
    document.getElementById('btn-official')?.addEventListener('click', async () => {
      const btn = document.getElementById('btn-official')
      const next = !rec.is_official
      btn.disabled = true
      try {
        await API.recordings.update(recordingId, { is_official: next, change_note: 'Official flag' })
        rec.is_official = next
        btn.classList.toggle('btn-staged', next)
        btn.classList.toggle('btn-ghost', !next)
        btn.textContent = next ? '✓ Official Release' : 'Mark as Official Release'
        markStaged()
      } catch (e) { alert('Failed: ' + e.message) }
      finally { btn.disabled = false }
    })

    document.getElementById('btn-delete-rec')?.addEventListener('click', async () => {
      if (!confirm('Delete this recording from the database?\n\nAudio files on disk are not removed.')) return
      const btn = document.getElementById('btn-delete-rec')
      btn.disabled = true
      btn.textContent = 'Deleting…'
      try {
        await API.recordings.delete(recordingId)
        // Deleting a recording can prune its performer / venue / artists.
        invalidateDims('performers', 'venues', 'artists')
        // Navigate back to wherever the user came from (falls back to
        // Library if this recording was reached with nothing preceding it).
        window.location.hash = state.navBack ? state.navBack.hash : '#/'
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

      function openPane(pane) {
        panel.classList.add('open')
        document.querySelectorAll('.slide-pane').forEach(p => p.classList.remove('active'))
        document.querySelectorAll('.slide-tab').forEach(t => t.classList.remove('active'))
        document.getElementById(`sp-${pane}`)?.classList.add('active')
        document.querySelector(`.slide-tab[data-pane="${pane}"]`)?.classList.add('active')
        activePane = pane
        state.recLastPane = pane   // survives the reload an Apply/edit triggers
        if (pane === 'filetags') loadFileTags(recordingId)
      }

      document.querySelectorAll('.slide-tab').forEach(tab => {
        tab.addEventListener('click', () => {
          const pane = tab.dataset.pane
          if (panel.classList.contains('open') && activePane === pane) {
            // Same tab clicked again → collapse
            panel.classList.remove('open')
            document.querySelectorAll('.slide-pane').forEach(p => p.classList.remove('active'))
            document.querySelectorAll('.slide-tab').forEach(t => t.classList.remove('active'))
            activePane = null
            state.recLastPane = null
          } else {
            openPane(pane)
          }
        })
      })

      // Default: whichever pane was open before the last reload (e.g. an AI
      // Assist Apply), falling back to Info File on a fresh visit. 'spectrogram'
      // is stale from before it moved into the Fidelity tab (2026-07-15) —
      // treat it the same as no saved pane.
      openPane((state.recLastPane && state.recLastPane !== 'spectrogram') ? state.recLastPane : 'info')
    })()

    // ── Top-right Source/Fidelity tab wiring ────────────────────────────────
    ;(function () {
      const tabs  = mainContent.querySelectorAll('.hm-tab')
      if (!tabs.length) return
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          const pane = tab.dataset.hmpane
          mainContent.querySelectorAll('.hm-pane').forEach(p => p.classList.remove('active'))
          mainContent.querySelectorAll('.hm-tab').forEach(t => t.classList.remove('active'))
          document.getElementById(`hm-pane-${pane}`)?.classList.add('active')
          tab.classList.add('active')
          // Spectrogram loads lazily the first time Fidelity is opened.
          if (pane === 'fidelity' && defaultTrackId) {
            const defaultTrack = rec.tracks.find(t => t.id === defaultTrackId)
            loadSpectrogram(defaultTrackId, defaultTrack?.title)
          }
        })
      })
    })()

    // ── Waveform (wavesurfer.js) — official renderer, fully wired to the
    // persistent player, adopted 2026-07-15 ─────────────────────────────────
    // Was a spike, then briefly its own separate audio channel; Ryan: "We
    // definitely want the thing fully wired into the persistent player. It
    // should not be separate." Renders from precomputed peaks (no network
    // fetch), and its OWN internal audio element is never played — see the
    // big comment above `_waveformMap` for why. All real playback control
    // routes through the shared #audio-el via Player.
    ;(function () {
      const wrap  = document.getElementById('rec-waveform-wrap')
      if (!wrap || !defaultTrackId) return
      const wsBox = document.getElementById('rec-waveform-ws')
      const peaks = _peaksForTrack(defaultTrackId)
      const duration = _trackDurationMap[defaultTrackId]
      if (!peaks || !duration) return

      const cs = getComputedStyle(document.documentElement)
      _wsInstance = window.WaveSurfer.create({
        container: wsBox,
        peaks,
        duration,
        waveColor: (cs.getPropertyValue('--t2') || '#7a6e64').trim(),
        progressColor: (cs.getPropertyValue('--accent') || '#c4956a').trim(),
        cursorColor: (cs.getPropertyValue('--accent-lit') || '#d4aa82').trim(),
        height: 100,
        normalize: true,
        cursorWidth: 1,
      })
      _wsTrackId = defaultTrackId
      _wsInstance.registerPlugin(window.WaveSurfer.Zoom.create({ scale: 0.5, maxZoom: 300 }))

      // Click/drag → seek the REAL shared player, not wavesurfer's own
      // silent internal audio. If this recording isn't already the one
      // loaded in the player, ready it first (paused — visiting a page
      // shouldn't start blaring audio) so the seek has somewhere to land.
      _wsInstance.on('interaction', async (time) => {
        const audio = document.getElementById('audio-el')
        if (!audio) return
        if (Player.currentId() === _wsTrackId) {
          audio.currentTime = time
          return
        }
        const idx = rec.tracks.findIndex(t => t.id === defaultTrackId)
        await playRecording(recordingId, idx < 0 ? 0 : idx, rec.tracks, { autoplay: false })
        const applySeek = () => { audio.currentTime = time }
        if (audio.readyState >= 1) applySeek()
        else audio.addEventListener('loadedmetadata', applySeek, { once: true })
      })

      const zoomSlider = document.createElement('input')
      zoomSlider.type = 'range'
      zoomSlider.min = '10'
      zoomSlider.max = '400'
      zoomSlider.value = '50'
      zoomSlider.className = 'rec-waveform-zoom-slider'
      zoomSlider.title = 'Zoom'
      zoomSlider.addEventListener('input', () => _wsInstance.zoom(parseInt(zoomSlider.value, 10)))
      wrap.appendChild(zoomSlider)
    })()

    // If nothing is currently loaded in the player, pressing the persistent
    // bar's play button while viewing this page should start this
    // recording's first track (Ryan, 2026-07-15) instead of no-op'ing.
    if ((rec.tracks || []).length) {
      Player.setFallbackPlay(() => playRecording(recordingId, 0, rec.tracks))
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

    // ── File Tags pane ────────────────────────────────────────────────────────
    // Fetch the actual on-disk Vorbis comments and render them as a well-formed
    // JSON object keyed by "NN · Title", so the effect of "Write Tags to Files"
    // is visible and verifiable.
    async function loadFileTags(recId) {
      const body = document.getElementById('sp-filetags-body')
      if (!body) return
      body.innerHTML = '<div class="info-panel-empty">Loading…</div>'
      try {
        const data = await API.recordings.fileTags(recId)
        const obj = {}
        ;(data.tracks || []).forEach(t => {
          const key = `${String(t.track_number || '').padStart(2, '0')} · ${t.title || ''}`
          obj[key] = t.error ? { error: t.error } : (t.tags || {})
        })
        const json = JSON.stringify(obj, null, 2)
        body.innerHTML = `<pre class="filetags-json">${esc(json)}</pre>`
      } catch (e) {
        body.innerHTML = `<div class="info-panel-empty">Failed to read tags: ${esc(e.message || '')}</div>`
      }
    }

    // Reload spectrogram when a new track is clicked (only if the pane is open)
    mainContent.querySelectorAll('.track-row[data-track-id]').forEach(row => {
      row.addEventListener('click', () => {
        const tid   = parseInt(row.dataset.trackId)
        const title = row.querySelector('.track-title')?.textContent || ''
        const fidelityPaneEl = document.getElementById('hm-pane-fidelity')
        if (tid && _waveformMap[tid] && fidelityPaneEl?.classList.contains('active')) {
          loadSpectrogram(tid, title)
        }
      })
    })
  }

  // ── Ingest wizard ─────────────────────────────────────────────────────────

  // Step indicators — pass optional steps array; defaults to 3-step wizard
  function stepDots(current, steps) {
    steps = steps || ['folder', 'review']  // Confirm step removed 2026-07-15
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
    behavior:    null,   // 'copy' | 'move' — synced with the shared ingest_file_behavior pref
  }

  async function renderBatchImportView() {
    setActiveNav('ingest')   // Batch is reached from Add Recording; keep it lit
    if (!batch.sourceDir) { renderBatchPickerView(); return }
    // Re-scan the last directory every time we land on this route (not just
    // the first time) — so returning here always reflects current disk + DB
    // state and anything ingested (this session or otherwise) drops off the list.
    setMainHTML(`<div class="empty-state">Refreshing <code>${esc(batch.sourceDir)}</code>…</div>`)
    try {
      batch.results = await API.ingest.batchScan(batch.sourceDir)
    } catch (e) {
      if (/^Directory not found:/.test(e.message)) {
        // Not a real failure — the scanned folder itself is gone, almost
        // certainly because it WAS the "Performer Name" staging folder
        // (Bulk Import pointed directly at one act's folder), and finishing
        // its last show just deleted it as empty (move_to_library's
        // empty-parent cleanup, 2026-07-23 — Ryan hit this immediately:
        // "Mr. Sun"). There's nothing left to import here, not an error.
        // Drop back to the picker, pre-filled with the parent directory so
        // the next act's folder is one click away.
        const parentDir = batch.sourceDir.replace(/\/[^/]+\/?$/, '')
        batch.sourceDir = null
        batch.results   = null
        renderBatchPickerView({
          suggestedDir: parentDir,
          note: 'That folder is empty and was cleaned up — pick the next one to continue.',
        })
        return
      }
      setMainHTML(`<div class="empty-state" style="color:var(--red)">Scan failed: ${esc(e.message)}</div>`)
      return
    }
    renderBatchResultsView()
  }

  function renderBatchPickerView({ suggestedDir = null, note = null } = {}) {
    setNavCurrent('Batch Import')
    const defaultDir = '/Volumes/music/Live Music Archive/Workshop/Import'
    setMainHTML(`
      <div class="batch-shell">
        <div class="batch-header">
          <h2>Batch Import</h2>
          <p class="batch-subtitle">Scan a folder — each subfolder is graded green / yellow / red. You decide what to ingest.</p>
          ${note ? `<p class="batch-subtitle" style="color:var(--accent)">${esc(note)}</p>` : ''}
        </div>
        <div class="batch-pick-form">
          <label class="batch-pick-label">Source directory</label>
          <div class="batch-pick-row">
            <input type="text" id="batch-dir-input" class="batch-dir-input"
                   value="${esc(suggestedDir || batch.sourceDir || defaultDir)}"
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

  // Render a single compact row — score-driven only; no tier dots/border.
  function _batchRow(item) {
    const e       = item.extracted
    const conf    = item.confidence
    const health  = item.health || { score: 0, band: 'red' }
    const ingestedId = batch.ingestedIds.get(item.path)
    const ingested   = ingestedId != null
    const expanded = batch.expandedPaths.has(item.path)
    const dateStr  = _batchDateStr(e)
    const loc      = [e.city, e.state].filter(Boolean).join(', ')

    // Issue chips
    const issueChips = item.issues.map(iss =>
      `<span class="batch-issue-${iss.severity}">${esc(iss.msg)}</span>`
    ).join('')

    // Action buttons — every uningested row gets both: Auto-Ingest (trust the
    // bot) or Review (open the full wizard, pre-scanned), regardless of score.
    let actionBtn = ''
    if (ingested) {
      actionBtn = `<span class="batch-done-check">✓ Ingested</span>
                   <a class="batch-rec-link" href="#/recording/${ingestedId}">View →</a>`
    } else {
      actionBtn = `<button class="btn btn-primary btn-sm batch-ingest-btn" data-path="${esc(item.path)}">Auto-Ingest</button>
                   <button class="btn btn-ghost btn-sm batch-review-btn" data-path="${esc(item.path)}">Review →</button>`
    }

    // Full inferred per-track listing — exactly what Auto-Ingest would write,
    // so a person can eyeball the setlist before deciding Review vs Auto-Ingest.
    const trackRows = (e.tracks || []).map(t => `
      <div class="batch-track-row">
        <span class="batch-track-num">${t.number}</span>
        <span class="batch-track-title ${!t.title ? 'batch-val-uncertain' : ''}">${esc(t.title || '(no title)')}</span>
        <span class="batch-track-src">${t.source ? (t.source === 'tags' ? 'tag' : 'info file') : ''}</span>
      </div>`).join('')

    // Expanded detail panel — the full inferred data for every field, so a
    // person can decide whether to trust Auto-Ingest or hand-review.
    const detail = expanded ? `
      <div class="batch-expand-panel">
        <div class="batch-expand-grid">
          <div class="batch-expand-row"><span class="batch-expand-label">Artist</span><span class="batch-expand-val ${conf.artist !== 'high' ? 'batch-val-uncertain' : ''}">${esc(e.artist || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Date</span><span class="batch-expand-val ${conf.date !== 'high' ? 'batch-val-uncertain' : ''}">${esc(dateStr || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Venue</span><span class="batch-expand-val">${esc(e.venue || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Location</span><span class="batch-expand-val">${esc(loc || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Country</span><span class="batch-expand-val">${esc(e.country || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Source</span><span class="batch-expand-val">${esc(e.source || '—')}</span></div>
          <div class="batch-expand-row"><span class="batch-expand-label">Lineage</span><span class="batch-expand-val">${esc(e.lineage || '—')}</span></div>
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
          ${trackRows ? `
          <div class="batch-expand-row batch-expand-tracklist"><span class="batch-expand-label">Listing</span><div class="batch-track-list">${trackRows}</div></div>` : ''}
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
      <div class="batch-item-row ${ingested ? 'batch-item-ingested' : ''}"
           data-path="${esc(item.path)}">
        <div class="batch-item-main">
          <button class="batch-expand-btn" data-path="${esc(item.path)}" title="${expanded ? 'Collapse' : 'Expand'}">
            ${expanded ? '▾' : '▸'}
          </button>
          <div class="batch-item-info">
            <div class="batch-item-name">${esc(item.name)}</div>
            <div class="batch-item-summary">
              ${summaryParts.map(p => `<span class="batch-meta-field">${esc(p)}</span>`).join('<span class="batch-meta-sep">·</span>')}
            </div>
          </div>
          <span class="batch-score batch-score--${health.band}" title="Completeness score">${health.score}</span>
          <div class="batch-item-actions">
            <span class="batch-ingest-status" id="batch-status-${item.path.replace(/[^a-zA-Z0-9]/g,'_')}"></span>
            ${actionBtn}
          </div>
        </div>
        ${detail}
      </div>`
  }

  async function renderBatchResultsView() {
    setNavCurrent('Batch Import')
    const r = batch.results
    if (!r) { renderBatchPickerView(); return }

    // Default the file-behavior choice from the shared preference, once per session.
    if (batch.behavior == null) {
      try {
        const prefs = await API.preferences.get()
        batch.behavior = prefs.ingest_file_behavior || 'copy'
      } catch (_) { batch.behavior = 'copy' }
    }

    const greens  = r.items.filter(i => i.tier === 'green')
    const yellows = r.items.filter(i => i.tier === 'yellow')
    const reds    = r.items.filter(i => i.tier === 'red')
    const nDone   = batch.ingestedIds.size

    const tierPill = (label, count, cls) => count > 0
      ? `<span class="batch-tier-pill batch-tier-${cls}">${count} ${label}</span>` : ''

    const allRows = r.items.map(item => _batchRow(item)).join('')

    // Auto-Ingest All covers green + yellow — yellows are frequently good
    // enough to trust (Ryan, 2026-07-16: "the user may be just fine with
    // blank track titles"). Red stays manual — those are missing artist or
    // date entirely, a real gap worth a human look before it lands in the
    // library.
    const autoIngestPending = r.items.filter(i =>
      (i.tier === 'green' || i.tier === 'yellow') && !batch.ingestedIds.has(i.path))

    setMainHTML(`
      <div class="batch-shell">
        <div class="batch-header">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
            <h2 style="margin:0">Batch Import</h2>
            <span class="batch-dir-label">${esc(r.source_dir)}</span>
            <button class="btn btn-ghost btn-sm" id="batch-rescan-btn">↺ New Scan</button>
          </div>
          <div class="batch-behavior-row">
            <label class="batch-behavior-label" for="batch-behavior-select">File handling</label>
            <select id="batch-behavior-select">
              <option value="copy" ${batch.behavior === 'copy' ? 'selected' : ''}>Copy into library (keep source)</option>
              <option value="move" ${batch.behavior === 'move' ? 'selected' : ''}>Move into library (source removed)</option>
            </select>
          </div>
          <div class="batch-tier-pills" style="margin-top:10px">
            ${tierPill('green', greens.length, 'green')}
            ${tierPill('yellow', yellows.length, 'yellow')}
            ${tierPill('red', reds.length, 'red')}
            ${nDone > 0 ? `<span class="batch-tier-pill batch-tier-done">${nDone} ingested</span>` : ''}
            ${autoIngestPending.length > 0
              ? `<button class="btn btn-primary btn-sm" id="batch-ingest-all-btn" style="margin-left:8px">
                   ⇉ Auto-Ingest All Green + Yellow (${autoIngestPending.length})
                 </button>`
              : ''}
            <span class="batch-tier-pill batch-tier-total">${r.total} total</span>
          </div>
        </div>
        <div class="batch-list">${allRows}</div>
        ${r.total === 0 ? `<div class="empty-state">No subfolders found.</div>` : ''}
      </div>`)

    // ── Events ──────────────────────────────────────────────────────────────

    document.getElementById('batch-behavior-select')?.addEventListener('change', async e => {
      batch.behavior = e.target.value
      try { await API.preferences.update({ ingest_file_behavior: batch.behavior }) } catch (_) {}
    })

    document.getElementById('batch-rescan-btn')?.addEventListener('click', () => {
      // Explicit "start over" — let the user reconsider the directory, rather
      // than silently reusing it (that's what returning-to-the-page already does).
      batch.results = null
      renderBatchPickerView()
    })

    // Ingest All Green + Yellow — red stays manual (missing artist/date entirely).
    document.getElementById('batch-ingest-all-btn')?.addEventListener('click', async () => {
      const btn = document.getElementById('batch-ingest-all-btn')
      const pending = batch.results.items.filter(i =>
        (i.tier === 'green' || i.tier === 'yellow') && !batch.ingestedIds.has(i.path))
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
          if (rowBtn) { rowBtn.disabled = false; rowBtn.textContent = 'Auto-Ingest' }
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

    // Auto-Ingest — available on every row now, regardless of score
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
          btn.textContent = 'Auto-Ingest'
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

    // Review (any tier): open the same wizard used by Add Recording, pre-scanned
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

    // /api/ingest/confirm returns a job id immediately — the actual copy + DB
    // work runs in the background. Poll it to completion so we never report
    // "ingested" (or silently do nothing) before the job has actually finished.
    const { job_id } = await API.ingest.confirm({
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
      behavior:           batch.behavior || 'copy',   // synced with the shared preference
      info_file_content:  scan.info_file_content || null,
      fingerprints:       scan.fingerprints || [],
      tracks,
    })
    const result = await pollConfirmJob(job_id)
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
      ingest.step       = 'review'
      ingest.folderPath = item.path
      ingest.form       = {}
      ingest.tracks     = []
      ingest.fromBatch  = true    // drives the back-link + post-submit redirect
      ingest._resume    = true   // one-shot: tell renderIngestView to resume here
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

  function renderIngestView() {
    setActiveNav('ingest')
    setActiveArtist(null)
    setNavCurrent('Add Recording')
    // Fresh navigation to Add Recording always starts with an empty form. The one
    // exception is Batch Import opening a pre-scanned folder, which sets a
    // one-shot _resume flag so the in-progress review isn't wiped.
    if (ingest._resume) {
      ingest._resume = false
    } else {
      ingest.step       = 'folder'
      ingest.scan       = null
      ingest.folderPath = null
      ingest.form       = {}
      ingest.tracks     = []
      ingest.aiResult   = null
      ingest.fromBatch  = false
    }
    renderIngestStep()
  }

  function renderIngestStep() {
    switch (ingest.step) {
      case 'folder':  renderIngestFolder();  break
      case 'review':  renderIngestReview();  break
      case 'tracks':  renderIngestReview();  break  // merged into review step
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
        <div class="ingest-topbar">
          <button class="btn btn-ghost btn-sm" id="btn-goto-batch" title="Import many folders at once">⇪ Batch Import</button>
        </div>
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

    document.getElementById('btn-goto-batch').addEventListener('click', () => { window.location.hash = '#/batch' })

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
      window.fluxDebug?.refresh()   // update the debug panel's Paula section if it's already open
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

  // Band-based generic message — the specific factors live in the Info Quality
  // Review panel, not here (many are just parse noise).
  const HEALTH_MSG = {
    green:  'Looks complete',
    yellow: 'Minor data discrepancies — recommend manual review',
    red:    'Significant gaps — manual review needed',
  }

  // Paula's purple-border threshold — a starting point, meant to be tuned
  // once this has run against more real folders (Ryan, 2026-07-16: "let's
  // give it a try and see how it plays out"). The raw per-field subscore is
  // always visible in the debug panel regardless of where this line sits.
  const PAULA_THRESHOLD = 0.70
  function paulaCls(attrName) {
    const sub = ingest.scan?.paula?.attributes?.[attrName]?.subscore
    return (typeof sub === 'number' && sub >= PAULA_THRESHOLD) ? 'paula-recommend' : ''
  }

  // AI Assist tab body: the health score folded in (current + band message),
  // a Run button, and a container that fills with clean results after a run.
  // File Tags JSON (raw Vorbis per track) for the scan — same shape/formatting as
  // the recording view's File Tags pane.
  function scanFileTagsJson() {
    const tracks = ingest.scan?.suggestions?.from_tags?.tracks || []
    const obj = {}
    tracks.forEach(t => {
      const key = `${String(t.track_number || '').padStart(2, '0')} · ${t.title || ''}`
      obj[key] = t.raw || {}
    })
    return JSON.stringify(obj, null, 2)
  }

  // ── AI Assist ─────────────────────────────────────────────────────────────
  // Read the form's current metadata to send to the research pass.
  function collectCurrentMeta() {
    const g = id => (document.getElementById(id)?.value || '').trim()
    const y = g('f-year'), m = g('f-month'), d = g('f-day')
    const date = y
      ? `${y}${m ? '-' + String(m).padStart(2, '0') : ''}${(m && d) ? '-' + String(d).padStart(2, '0') : ''}`
      : ''
    return {
      artist:  g('f-artist'), date, venue: g('f-venue-name'),
      city:    g('f-city'),   state: g('f-state'), country: g('f-country'),
      source:  g('f-source'), lineage: g('f-lineage'), event: g('f-event-name'),
      tracks:  (ingest.tracks || []).map(t => ({
        number: t.track_number, title: t.title, duration: t.duration,
      })),
      info_file_content: ingest.scan.info_file_content || '',
    }
  }

  // Read the current value of a proposal's target field (for revert).
  function getFormField(field) {
    const g = id => document.getElementById(id)?.value || ''
    switch (field) {
      case 'artist':  return g('f-artist')
      case 'venue':   return g('f-venue-name')
      case 'city':    return g('f-city')
      case 'state':   return g('f-state')
      case 'country': return g('f-country')
      case 'event':   return g('f-event-name')
      case 'source':  return g('f-source')
      case 'date': {
        const y = g('f-year'), m = g('f-month'), d = g('f-day')
        return y ? `${y}${m ? '-' + String(m).padStart(2, '0') : ''}${(m && d) ? '-' + String(d).padStart(2, '0') : ''}` : ''
      }
    }
    return ''
  }

  // Write a value into the form field(s) for a proposal, highlighting the input.
  function setFormField(field, value) {
    const set = (id, v) => {
      const el = document.getElementById(id)
      if (el) { el.value = v; el.classList.toggle('ai-applied', v !== '' && v != null) }
    }
    switch (field) {
      case 'artist':  set('f-artist', value);      ingest.form.artist_name = value; break
      case 'venue':   set('f-venue-name', value);  ingest.form.venue_name  = value; break
      case 'city':    set('f-city', value);        ingest.form.city        = value; break
      case 'state':   set('f-state', value);       ingest.form.state       = value; break
      case 'country': set('f-country', value);     ingest.form.country     = value; break
      case 'event':   set('f-event-name', value);  ingest.form.event_name  = value; break
      case 'source': {
        const el = document.getElementById('f-source')
        if (el && [...el.options].some(o => o.value === value)) {
          el.value = value; el.classList.add('ai-applied')
        }
        ingest.form.source = value; break
      }
      case 'date': {
        const p = String(value).split('-')
        set('f-year', p[0] || '')
        set('f-month', p[1] ? parseInt(p[1]) : '')
        set('f-day',   p[2] ? parseInt(p[2]) : '')
        ingest.form.start_year  = p[0] || ''
        ingest.form.start_month = p[1] ? parseInt(p[1]) : ''
        ingest.form.start_day   = p[2] ? parseInt(p[2]) : ''
        break
      }
    }
  }

  // Apply ⇆ revert a single proposal; tracks prior value per field for revert.
  function toggleApplyProposal(p, btn) {
    ingest.aiApplied = ingest.aiApplied || {}
    if (p.field in ingest.aiApplied) {
      setFormField(p.field, ingest.aiApplied[p.field])
      delete ingest.aiApplied[p.field]
      if (btn) { btn.textContent = 'Apply'; btn.classList.remove('applied') }
    } else {
      ingest.aiApplied[p.field] = getFormField(p.field)
      setFormField(p.field, p.proposed)
      if (btn) { btn.textContent = 'Revert'; btn.classList.add('applied') }
    }
  }

  // Render a standardized LCR info-file text from the live form + tracks + AI
  // provenance notes — the "Proposed" side of the compare and the confirm regen.
  function buildInfoFileText() {
    const g = id => (document.getElementById(id)?.value || '').trim()
    const y = g('f-year'), m = g('f-month'), d = g('f-day')
    const date = y ? `${y}${m ? '-' + String(m).padStart(2, '0') : ''}${(m && d) ? '-' + String(d).padStart(2, '0') : ''}` : ''
    const loc  = [g('f-city'), g('f-state'), g('f-country')].filter(Boolean).join(', ')
    const L = []
    if (g('f-artist'))     L.push(g('f-artist'))
    if (date)              L.push(date)
    if (g('f-venue-name')) L.push(g('f-venue-name'))
    if (loc)               L.push(loc)
    if (g('f-source'))     L.push(g('f-source'))
    if (g('f-lineage') || g('f-event-name')) L.push('')
    if (g('f-lineage'))    L.push('Lineage: ' + g('f-lineage'))
    if (g('f-event-name')) L.push('Event: ' + g('f-event-name'))
    L.push('', 'Setlist:', '')
    let lastSet = null
    ;(ingest.tracks || []).forEach((t, i) => {
      if (t.set && t.set !== lastSet) { L.push(t.set); lastSet = t.set }
      L.push(`${String(t.track_number || i + 1).padStart(2, '0')}. ${t.title || ''}`.trimEnd())
    })
    const prov = ingest.aiResult?.provenance_notes || []
    if (prov.length) { L.push('', 'Notes:'); prov.forEach(n => L.push(n)) }
    return L.join('\n')
  }

  // Tidy the model's reasoning: drop any leaked tool-call syntax, and break
  // numbered findings ("1. … 2. …") onto their own lines for readability.
  function formatAiThinking(text) {
    if (!text) return ''
    let t = String(text).split(/<\/?thinking>|<parameter\b/i)[0]
    t = t.replace(/\s+/g, ' ').trim()
    t = t.replace(/\s(\d{1,2}\.)\s/g, '\n$1 ')
    return t
  }

  // Apply the AI's researched setlist onto the track rows (human-triggered).
  function applyAiTrackTitles(titles) {
    ;(titles || []).forEach(tt => {
      const idx = (ingest.tracks || []).findIndex(t => String(t.track_number) === String(tt.number))
      if (idx >= 0 && tt.title) {
        ingest.tracks[idx].title = tt.title
        const inp = mainContent.querySelector(`.t-title[data-idx="${idx}"]`)
        if (inp) inp.value = tt.title
      }
    })
    reScore()
  }

  // ── Reusable track context menu (right-click): flags + songwriter + note ──────
  // Shared by the recording view, Edit, and Add. opts.onChange(track) fires after any
  // change; the caller persists (API for saved recordings, local state for ingest)
  // and refreshes the row. The menu mutates track.flags/songwriter/notes in place.
  function _closeTrackMenu() {
    const m = document.getElementById('track-qmenu')
    if (m) { try { m._commit?.() } catch (_) {} m.remove() }
    document.removeEventListener('mousedown', _trackMenuOutside)
    document.removeEventListener('keydown', _trackMenuEsc)
  }
  function _trackMenuOutside(e) {
    const m = document.getElementById('track-qmenu')
    if (m && !m.contains(e.target)) _closeTrackMenu()
  }
  function _trackMenuEsc(e) { if (e.key === 'Escape') _closeTrackMenu() }

  function openTrackMenu(track, clientX, clientY, opts = {}) {
    _closeTrackMenu()
    const onChange = opts.onChange || (() => {})
    // flagsOnly: Add Recording's table now has Note/Songwriter as click-to-edit
    // cells directly (Ryan, 2026-07-15), so its right-click popup is Flags
    // (+ Official, if showOfficial) only. View Recording still gets the full
    // Note/Songwriter/Flags/Official grid — it doesn't pass this option.
    const flagsOnly = !!opts.flagsOnly
    const menu = document.createElement('div')
    menu.className = 'track-qmenu'
    menu.id = 'track-qmenu'
    const flagPills = TRACK_FLAGS.map(f => {
      const active = (track.flags || []).includes(f.key)
      return `<button class="flag-pill ${active ? 'active' : ''}" data-flag="${f.key}" type="button">${f.label}</button>`
    }).join('')
    // Official-release toggle — opt-in (opts.showOfficial) since View Recording
    // manages that per-track flag elsewhere; Add Recording has no other place
    // for it once the expand row goes away, so it lives here for that caller.
    const officialRow = opts.showOfficial
      ? `<div class="et-detail-field" style="margin-top:6px">
           <label class="check-label check-inline" title="Mark this track as an official release">
             <input type="checkbox" class="track-qmenu-official" ${track.is_official ? 'checked' : ''} />
             <span>Official release</span>
           </label>
         </div>`
      : ''
    const detailGrid = flagsOnly ? '' : `
      <div class="et-detail-grid2">
        <div class="et-detail-field">
          <label>Note</label>
          <textarea class="track-qmenu-note" placeholder="Add a note…">${esc(track.notes || '')}</textarea>
        </div>
        <div class="et-detail-field">
          <label>Songwriter</label>
          <input class="track-qmenu-songwriter" type="text" placeholder="Songwriter…" value="${esc(track.songwriter || '')}" />
        </div>
      </div>`
    menu.innerHTML = `
      <div class="track-qmenu-title">${esc(String(track.track_number || '').padStart(2, '0'))} · ${esc(track.title || '')}</div>
      ${detailGrid}
      <div class="track-qmenu-label">Flags</div>
      <div class="flag-pill-row track-qmenu-flags">${flagPills}</div>
      ${officialRow}`
    document.body.appendChild(menu)

    menu.querySelector('.track-qmenu-official')?.addEventListener('change', function () {
      track.is_official = this.checked
      onChange(track)
    })

    // Position at cursor, clamped to the viewport
    const r = menu.getBoundingClientRect()
    menu.style.left = Math.max(8, Math.min(clientX, window.innerWidth  - r.width  - 8)) + 'px'
    menu.style.top  = Math.max(8, Math.min(clientY, window.innerHeight - r.height - 8)) + 'px'

    // Flags — toggle notifies immediately
    menu.querySelectorAll('.flag-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.classList.toggle('active')
        track.flags = [...menu.querySelectorAll('.flag-pill.active')].map(b => b.dataset.flag)
        onChange(track)
      })
    })

    // Songwriter + Note — commit on Enter / on close. Only present when
    // !flagsOnly (Add Recording's flagsOnly popup has neither field).
    const swEl   = menu.querySelector('.track-qmenu-songwriter')
    const noteEl = menu.querySelector('.track-qmenu-note')
    if (swEl && noteEl) {
      const commit = () => {
        const sw   = swEl.value.trim() || null
        const note = noteEl.value.trim() || null
        let changed = false
        if (sw !== (track.songwriter || null)) { track.songwriter = sw; changed = true }
        if (note !== (track.notes || null))     { track.notes = note;    changed = true }
        if (changed) onChange(track)
      }
      menu._commit = commit
      // Auto-save on complete: commit when the field loses focus, and on Enter.
      swEl.addEventListener('blur', commit)
      noteEl.addEventListener('blur', commit)
      swEl.addEventListener('keydown', e => {
        e.stopPropagation()
        if (e.key === 'Enter') { e.preventDefault(); commit() }
      })
      noteEl.addEventListener('keydown', e => {
        e.stopPropagation()
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit(); _closeTrackMenu() }
      })
    }

    setTimeout(() => {
      document.addEventListener('mousedown', _trackMenuOutside)
      document.addEventListener('keydown', _trackMenuEsc)
    }, 0)
  }

  // Read-only render of a saved AI research blob (recording view AI Assist tab).
  // Same look as the interactive version, minus the Apply controls.
  // Clean, succinct AI results in the AI Assist tab — prose + simple lists, no
  // tables or colour chips. Links are neutral + theme-aware (.ai-link).
  function renderAiResults(r) {
    const body = document.getElementById('ai-results')
    if (!body) return

    body.innerHTML = buildAiResultsHtml(r)

    body.querySelectorAll('.ai-apply-btn').forEach(b =>
      b.addEventListener('click', () => { toggleApplyProposal(r.proposals[parseInt(b.dataset.idx)], b); reScore() }))
    document.getElementById('ai-apply-tracks')?.addEventListener('click', () => applyAiTrackTitles(r.track_titles || []))
    // No auto-apply, regardless of confidence — see renderRecAiResults above
    // for why (2026-07-20, AI Assist Refinement spec). Every proposal needs
    // an explicit click on its own Apply button.
    reScore()
  }

  // Re-score the current form state and update the AI tab's score header.
  async function reScore() {
    if (!ingest.scan) return
    const g = id => (document.getElementById(id)?.value || '').trim()
    const y = g('f-year'), m = g('f-month'), d = g('f-day')
    const date = y ? `${y}${m ? '-' + String(m).padStart(2, '0') : ''}${(m && d) ? '-' + String(d).padStart(2, '0') : ''}` : ''
    const clone = JSON.parse(JSON.stringify(ingest.scan))
    const t = clone.suggestions.from_tags, inf = clone.suggestions.from_info_file
    const both = (k, v) => { t[k] = v; inf[k] = v }
    both('artist', g('f-artist')); both('venue', g('f-venue-name'))
    both('city', g('f-city')); both('state', g('f-state')); both('country', g('f-country'))
    both('source', g('f-source')); both('lineage', g('f-lineage'))
    t.concert_date = date
    inf.year = parseInt(y) || null; inf.month = parseInt(m) || null; inf.day = parseInt(d) || null
    inf.tracks = (ingest.tracks || []).map((tk, i) => ({ number: tk.track_number || i + 1, title: tk.title || '' }))
    try {
      const h = await API.ingest.health(clone)
      ingest.scan.health = h
      const scoreEl = document.getElementById('iq-score')
      const msgEl   = document.getElementById('iq-msg')
      if (scoreEl) {
        scoreEl.textContent = h.score
        scoreEl.className = 'iq-score iq-score--' + h.band
      }
      if (msgEl) msgEl.textContent = HEALTH_MSG[h.band] || ''
    } catch (_) {}
  }

  // Poll a background /api/ingest/confirm job until it finishes. `onProgress`
  // (optional) is called on each running tick with (copied, total) bytes — the
  // copy step can take a while for big folders. Used by both the Add Recording
  // confirm step and batch import, so neither one can silently move on before
  // the ingest is actually done.
  async function pollConfirmJob(jobId, onProgress) {
    const sleep = ms => new Promise(r => setTimeout(r, ms))
    while (true) {
      await sleep(600)
      const s = await API.ingest.confirmStatus(jobId)
      if (s.status === 'running') {
        if (onProgress) onProgress(s.copied || 0, s.total || 0)
      } else if (s.status === 'done') {
        return s.result
      } else if (s.status === 'error') {
        throw new Error(s.error)
      }
    }
  }

  // Poll a background AI job until it finishes. The synchronous call is too slow
  // (30-90s) for the webview's fetch timeout, so we start a job and poll for it.
  function pollAiJob(jobId, t0) {
    const sleep = ms => new Promise(r => setTimeout(r, ms))
    return (async function loop() {
      while (true) {
        await sleep(2000)
        const el = document.getElementById('ai-elapsed')
        if (el) el.textContent = `${Math.round((Date.now() - t0) / 1000)}s`
        let s
        try { s = await API.ingest.aiAssistStatus(jobId) }
        catch (e) { if (/unknown job/.test(e.message)) throw new Error('Job was lost (did the app restart?)'); throw e }
        if (s.status === 'done')  return s.result
        if (s.status === 'error') throw new Error(s.error)
        if (Date.now() - t0 > 5 * 60 * 1000) throw new Error('AI research timed out after 5 minutes')
      }
    })()
  }

  // Same polling pattern as pollAiJob, for the Performer page's Dossier
  // research job (2026-07-22) — kept separate rather than parameterizing
  // pollAiJob, since the endpoint shape (performerId + jobId) and the
  // elapsed-timer element id differ.
  function pollDossierJob(performerId, jobId, t0) {
    const sleep = ms => new Promise(r => setTimeout(r, ms))
    return (async function loop() {
      while (true) {
        await sleep(2000)
        const el = document.getElementById('pp-dossier-elapsed')
        if (el) el.textContent = `${Math.round((Date.now() - t0) / 1000)}s`
        let s
        try { s = await API.performers.dossierStatus(performerId, jobId) }
        catch (e) { if (/unknown job/.test(e.message)) throw new Error('Job was lost (did the app restart?)'); throw e }
        if (s.status === 'done')  return s.result
        if (s.status === 'error') throw new Error(s.error)
        if (Date.now() - t0 > 5 * 60 * 1000) throw new Error('Dossier research timed out after 5 minutes')
      }
    })()
  }

  // Switch which right-column pane is visible in the ingest review.
  function switchIngestPane(paneId, tabEl) {
    const root = document.querySelector('.ingest-review-raw')
    if (!root) return
    root.querySelectorAll('.slide-tab').forEach(t => t.classList.toggle('active', t === tabEl))
    root.querySelectorAll('.slide-pane').forEach(p => p.classList.toggle('active', p.id === paneId))
  }

  // Lazily create the AI Assist pane + tab, inserted ABOVE Info File. The tab is
  // only born when the user runs AI Assist, and lives only for this add/edit
  // session (nothing is persisted to the recording). Returns the results div.
  function ensureAiPane() {
    let body = document.getElementById('ai-results')
    if (body) return body
    const panes = document.getElementById('ingest-panes')
    const rail  = document.getElementById('ingest-tab-rail')
    if (!panes || !rail) return null
    const pane = document.createElement('div')
    pane.className = 'slide-pane'
    pane.id = 'isp-ai'
    pane.innerHTML = `
      <div class="slide-pane-header">AI Assist</div>
      <div class="slide-pane-scroll"><div class="ai-results" id="ai-results"></div></div>`
    panes.insertBefore(pane, panes.firstChild)
    const tab = document.createElement('button')
    tab.className = 'slide-tab slide-tab--ai'
    tab.dataset.ipane = 'isp-ai'
    tab.textContent = 'AI Assist'
    tab.addEventListener('click', () => switchIngestPane('isp-ai', tab))
    rail.insertBefore(tab, rail.firstChild)
    return document.getElementById('ai-results')
  }

  async function startAiAssist() {
    const btn  = document.getElementById('btn-ai-assist')
    const body = ensureAiPane()
    if (!body) return
    switchIngestPane('isp-ai', document.querySelector('.ingest-review-raw .slide-tab--ai'))
    if (btn) { btn.disabled = true; btn.textContent = '… researching' }
    body.innerHTML = `<div class="ai-loading"><div class="loading-spinner"></div><div>Researching the web — this can take a minute or two… <span id="ai-elapsed">0s</span></div></div>`
    const t0 = Date.now()
    try {
      const { job_id } = await API.ingest.aiAssist({ folder_path: ingest.folderPath, current: collectCurrentMeta() })
      const result = await pollAiJob(job_id, t0)
      ingest.aiResult = result
      renderAiResults(result)
    } catch (e) {
      const secs = Math.round((Date.now() - t0) / 1000)
      console.error('AI Assist error after', secs, 's:', e)
      if (/no_api_key/.test(e.message)) {
        body.innerHTML = `<p class="ai-res-note">No Anthropic API key set — add one in Settings (⚙).</p>`
      } else {
        body.innerHTML = `<p class="ai-res-note" style="color:var(--red)">AI Assist failed after ${secs}s: ${esc(e.message)}</p>`
      }
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '✨ AI Assist' }
    }
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

      // Build a map from rel_path (unique — unlike bare filename, which
      // collides across a multi-disc source where each disc's "01.flac"
      // shares a name) → set label, from scan's subdir detection.
      const audioSetByRelPath = {}
      ;(ingest.scan.audio_files || []).forEach(af => {
        if (af.set && af.rel_path) audioSetByRelPath[af.rel_path] = af.set
      })
      const setsDetected = ingest.scan.sets_detected || false

      ingest.tracks = tagTracks.map(t => {
        const title   = titleCase(t.title || infoMap[t.index]) || `Track ${t.index}`
        const relPath = t.rel_path || t.filename
        return {
          // Multi-disc sources often reset TRACKNUMBER per disc (1..N on
          // disc 1, 1..M on disc 2) — trusting the tag directly collides
          // (two tracks numbered "1", etc). When sets are detected, the
          // scan's own index is already continuous across discs in the
          // right order, so it's the reliable number; the tag is only
          // trusted when there's just one set to begin with.
          // (Ryan, 2026-07-14 — this was the CD1/CD2 duplicate-numbering bug.)
          track_number: (!setsDetected && t.track_number) ? parseInt(t.track_number) : t.index,
          title,
          set:          audioSetByRelPath[relPath] || '',
          duration:     t.duration,
          filename:     relPath,
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
            set:          audioSetByRelPath[scanFile.rel_path] || '',
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
      f.start_year      = tagYear  || info.year  || ''
      f.start_month     = tagMonth || info.month || ''
      f.start_day       = tagDay   || info.day   || ''
      f.venue_name      = pick(tags, info, 'venue') || ''
      f.venue_id        = null
      // FLAC tags take priority; info file fills only what tags didn't supply.
      f.city            = tags.city    || info.city    || ''
      f.state           = tags.state   || info.state   || ''
      f.country         = tags.country || info.country || ''
      f.source          = pick(tags, info, 'source') || ''
      f.quality         = ''
      f.rating          = ''
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
    // Editable — the archivist can fix up the parsed text, or type one in from
    // scratch when the folder had no info file. Edits flow straight into
    // ingest.scan.info_file_content (the value sent on Confirm); no re-parse.
    // "Save to file" writes it to disk independent of Confirm, so a re-run of
    // AI Assist picks up the correction — Confirm still sends whatever's in
    // memory either way, saving to disk is just for round-tripping with AI.
    const infoSaveRow = `<div class="info-file-save-row">
        <button class="btn btn-ghost btn-sm" id="btn-save-info-file">Save to file</button>
        <span class="info-file-save-status" id="info-file-save-status"></span>
      </div>`
    const infoText = `${textSwitcher}<textarea class="rev-info-text rev-info-edit" id="rev-info-edit"
      placeholder="No info file found — paste or type one in.">${esc(ingest.scan.info_file_content || '')}</textarea>${infoSaveRow}`

    // Track count mismatch detection
    const audioCount     = ingest.scan.audio_file_count
    const infoTrackCount = info.tracks?.length || 0
    const hasMismatch    = infoTrackCount > 0 && audioCount !== infoTrackCount
    const mismatchBanner = hasMismatch ? `
      <div class="track-mismatch-warn">
        ⚠ ${audioCount} audio file${audioCount !== 1 ? 's' : ''} on disk · ${infoTrackCount} track${infoTrackCount !== 1 ? 's' : ''} in info file — use playback to verify
      </div>` : ''

    // Track table rows — play preview, title, and the same flag-chip layout
    // as View Recording. Note/Songwriter are click-to-edit cells right in the
    // table (staged into ingest.tracks in memory — no API call; Confirm sends
    // it all at once). Right-click a row for Flags only (openTrackMenu with
    // flagsOnly — Ryan, 2026-07-15: Note/Songwriter moved out of that popup
    // now that they're editable inline).
    // A track's chip row: the FIRST chip (official badge, then flags in
    // order) stays under the title as before; if there's more than one, the
    // rest get their own full-width row right underneath, laid out
    // horizontally — they used to all stack vertically inside the narrow
    // title-cell and push the title text up (Ryan, 2026-07-15).
    function _trackChipExpandRowHtml(i, chips) {
      // chips[0] is already rendered separately under the title (see
      // trackRows below / refreshIngestTrackRow) — this row is only for the
      // REST. Bug fixed 2026-07-23 (Ryan: "Banter" showing twice on tracks
      // titled e.g. "Banter & Tuning"): this used to join the FULL chips
      // array here, so the first chip was shown once under the title AND
      // again in this row every time a track had 2+ chips. Not a data bug —
      // t.flags itself was always clean (detectTrackFlags/detect_track_flags
      // both build off a Set, which can't hold a duplicate key) — purely a
      // rendering double-count.
      return `<tr class="track-review-chiprow" data-idx="${i}">
          <td colspan="6"><div class="track-chip-expand-row">${chips.slice(1).join('')}</div></td>
        </tr>`
    }

    const trackRows = ingest.tracks.map((t, i) => {
      const chips = trackChipsArray(t)
      const expandRow = chips.length > 1 ? _trackChipExpandRowHtml(i, chips) : ''
      return `
        <tr class="track-review-row" data-idx="${i}" title="Right-click for flags">
          <td class="num">${t.track_number}</td>
          <td class="play-cell">
            <button class="btn-preview-track" data-filename="${esc(t.filename || '')}" title="${esc(t.filename || 'no file')}">▶</button>
          </td>
          <td class="title-cell">
            <input type="text" class="t-title" data-idx="${i}" value="${esc(t.title)}" />
            <div class="track-chip-row" id="t-chips-${i}">${chips[0] || ''}</div>
          </td>
          <td class="note-cell truncate pp-editable${t.notes ? '' : ' pp-empty'}" id="t-note-${i}" title="${esc(t.notes || 'Click to add a note')}">${esc(t.notes || '—')}</td>
          <td class="sw-cell truncate pp-editable${t.songwriter ? '' : ' pp-empty'}" id="t-sw-${i}" title="${esc(t.songwriter || 'Click to add a songwriter')}">${esc(t.songwriter || '—')}</td>
          <td class="dur">${fmtDur(t.duration)}</td>
        </tr>${expandRow}`
    }).join('')

    setMainHTML(`
      <div class="ingest-review-outer">
      <div class="ingest-review-topbar">
        <a href="#" id="ingest-back-link" class="ingest-back-link">${ingest.fromBatch ? '← Back to Bulk Import' : '← Back'}</a>
        <h2 class="ingest-topbar-title">Add Recording: <span class="rev-header-folder">${esc(ingest.folderPath?.split('/').pop() || '')}</span></h2>
      </div>
      <div class="ingest-review-shell">

        <!-- Left: metadata form + track list -->
        <div class="ingest-review-form">
          <div class="ingest-review-form-body">

            <!-- Artist with autocomplete -->
            <div class="ingest-field">
              <label>Performer <span style="color:var(--t3); font-weight:400">— the act (FLAC ARTIST tag)</span></label>
              <div class="artist-picker-wrap">
                <input type="text" id="f-artist" class="${paulaCls('performer')}" value="${esc(f.artist_name)}" autocomplete="off" placeholder="Search or type the act…" />
                <div class="artist-dropdown" id="f-artist-dropdown" style="display:none"></div>
              </div>
            </div>

            <!-- Members/Guests two-row personnel widget — filled in by
                 createMembersWidget().renderChips(), see app.js. -->
            <div class="ingest-field" style="margin-top:6px">
              <div class="members-field" id="f-members-field"></div>
            </div>

            <!-- Date: Year / Month / Day (no "Start") -->
            <div class="ingest-field-grid date-grid" style="margin-top:5px">
              <div class="ingest-field"><label>Year</label><input type="number" id="f-year" class="${paulaCls('date')}" value="${esc(f.start_year)}" min="1900" max="2099" /></div>
              <div class="ingest-field"><label>Mo</label><input type="number" id="f-month" class="${paulaCls('date')}" value="${esc(f.start_month)}" min="1" max="12" /></div>
              <div class="ingest-field"><label>Day</label><input type="number" id="f-day" class="${paulaCls('date')}" value="${esc(f.start_day)}" min="1" max="31" /></div>
            </div>
            <div id="end-date-toggle-row" style="margin-top:2px">
              <a class="field-toggle-link" id="btn-toggle-end-date" href="#">+ End date</a>
            </div>
            <div class="ingest-field-grid date-grid" id="end-date-row" style="margin-top:4px; display:none">
              <div class="ingest-field"><label>End yr</label><input type="number" id="f-end-year" value="${esc(f.end_year)}" min="1900" max="2099" /></div>
              <div class="ingest-field"><label>Mo</label><input type="number" id="f-end-month" value="${esc(f.end_month)}" min="1" max="12" /></div>
              <div class="ingest-field"><label>Day</label><input type="number" id="f-end-day" value="${esc(f.end_day)}" min="1" max="31" /></div>
            </div>

            <!-- Non-blocking: already-in-library warning for this performer+date
                 (checked once both are known — see wireDupCheck). Multiple
                 recordings per show are legitimate, so this never blocks Confirm. -->
            <div class="dup-warn" id="dup-warn" style="display:none">
              <div class="dup-warn-title">⚠ Already in your library</div>
              <div class="dup-warn-body" id="dup-warn-body"></div>
            </div>

            <!-- Venue + Festival/Event on one row -->
            <div class="ingest-field-grid" style="grid-template-columns:1fr 1fr; gap:8px; margin-top:8px">
              <div class="ingest-field">
                <label>Venue</label>
                <div class="venue-picker-wrap">
                  <input type="text" id="f-venue-name" class="${paulaCls('venue_name')}" value="${esc(f.venue_name)}" autocomplete="off" placeholder="Search or type venue name…" />
                  <input type="hidden" id="f-venue-id" value="${esc(String(f.venue_id || ''))}" />
                  <div class="venue-dropdown" id="f-venue-dropdown" style="display:none"></div>
                </div>
              </div>
              <div class="ingest-field">
                <label>Festival / Event</label>
                <div class="event-picker-wrap">
                  <input type="text" id="f-event-name" value="${esc(f.event_name || '')}" autocomplete="off" />
                  <input type="hidden" id="f-event-id" value="${esc(String(f.event_id || ''))}" />
                  <div class="event-dropdown" id="f-event-dropdown" style="display:none"></div>
                </div>
              </div>
            </div>

            <!-- City / State / Country — state is narrow -->
            <div class="ingest-field-grid" style="grid-template-columns:1fr 58px 1fr; gap:6px; margin-top:5px" id="f-location-row">
              <div class="ingest-field"><label>City</label><input type="text" id="f-city" class="${paulaCls('city')}" value="${esc(f.city)}" /></div>
              <div class="ingest-field"><label>State</label><input type="text" id="f-state" class="${paulaCls('state')}" value="${esc(f.state)}" maxlength="6" /></div>
              <div class="ingest-field"><label>Country</label><input type="text" id="f-country" class="${paulaCls('country')}" value="${esc(f.country)}" /></div>
            </div>

            <!-- Source / Lineage / Quality / Rating — matches Edit form -->
            <div class="ingest-field-grid" style="grid-template-columns:76px minmax(160px,2fr) 58px 72px; gap:10px; margin-top:8px">
              <div class="ingest-field">
                <label>Source</label>
                <select id="f-source">
                  <option value="">—</option>
                  ${['SBD','AUD','MTX','FM','DVB-S','Other'].map(s =>
                    `<option value="${s}" ${f.source === s ? 'selected' : ''}>${s}</option>`
                  ).join('')}
                </select>
              </div>
              <div class="ingest-field">
                <label>Lineage <span style="color:var(--t3); font-weight:400">— transfer chain, taper, or anything distinguishing this tape</span></label>
                <input type="text" id="f-lineage" value="${esc(f.lineage)}" />
              </div>
              <div class="ingest-field">
                <label>Quality</label>
                <input type="text" id="f-quality" value="${esc(f.quality)}" />
              </div>
              <div class="ingest-field">
                <label>Rating <span style="color:var(--t3);font-size:10px">0–100</span></label>
                <input type="number" id="f-rating" min="0" max="100" style="width:100%"
                       value="${f.rating != null && f.rating !== '' ? f.rating : ''}" placeholder="—" />
              </div>
            </div>

            <!-- Track table -->
            <div class="rev-section-title" style="margin-top:16px; padding-top:12px; border-top:1px solid var(--bd-1)">
              Tracks <span style="font-weight:400; text-transform:none; letter-spacing:0; color:var(--t2)">(${ingest.tracks.length})</span>
              <span style="font-weight:400; text-transform:none; letter-spacing:0; color:var(--t3); font-size:10px">— right-click track to add flags</span>
            </div>
            <div style="overflow:auto; margin-bottom:4px">
              <table class="track-review-table">
                <thead>
                  <tr>
                    <th style="width:24px">#</th>
                    <th style="width:28px"></th>
                    <th style="width:30%">Title</th>
                    <th style="width:26%; text-align:right">Notes</th>
                    <th style="width:20%">Songwriter</th>
                    <th style="width:44px">Time</th>
                  </tr>
                </thead>
                <tbody>${trackRows || '<tr><td colspan="6" style="color:var(--t2);padding:12px">No tracks found</td></tr>'}</tbody>
              </table>
            </div>

            <div class="ingest-field" style="margin-top:12px">
              <label>Notes</label>
              <textarea id="f-notes" style="min-height:80px">${esc(f.notes)}</textarea>
            </div>

            <label style="display:flex; align-items:center; gap:8px; color:var(--t3); font-size:11px; margin-top:8px; cursor:pointer">
              <input type="checkbox" id="f-is-official" ${f.is_official ? 'checked' : ''} />
              <span>Official release</span>
              <span style="color:var(--t3); font-style:italic">— marks recording and all tracks as officially released</span>
            </label>

          </div>
          <div class="ingest-actions">
            <!-- Audio preview player lives here so it's always visible above the fold —
                 shown by default (previewing the first track), not just after a play
                 click (Ryan, 2026-07-15). Centered between the (now-empty) left column
                 and the Add Recording button on the right. -->
            <div id="ingest-audio-bar" class="ingest-audio-footer">
              <span id="ingest-audio-label">Preview Track:</span>
              <audio id="ingest-preview-audio" preload="metadata" controls></audio>
            </div>
            <button class="btn btn-primary" id="btn-confirm">Add Recording →</button>
          </div>
          <div id="review-submit-error" class="review-submit-error" style="display:none"></div>
        </div>

        <!-- Resize handle -->
        <div class="rev-resize-handle" id="rev-divider"></div>

        <!-- Right: Quality bar (score + blurb + AI Assist) over vertical-tab panel -->
        <div class="ingest-review-raw">
          <div class="ingest-quality-bar">
            <span class="iq-label">Completeness score</span>
            <span class="iq-score iq-score--${ingest.scan.health?.band || 'yellow'}" id="iq-score">${ingest.scan.health?.score ?? '—'}</span>
            <span class="iq-msg" id="iq-msg">${esc(HEALTH_MSG[ingest.scan.health?.band || 'yellow'] || '')}</span>
            <button class="btn btn-primary btn-sm iq-ai-btn" id="btn-ai-assist">✨ AI Assist</button>
          </div>
          <div class="ingest-tabs">
            <div class="slide-panel-body" id="ingest-panes">
              <div class="slide-pane active" id="isp-info">
                <div class="slide-pane-header">Info File</div>
                <div class="slide-pane-scroll"><div class="rev-raw-section">${infoText}</div></div>
              </div>
              <div class="slide-pane" id="isp-filetags">
                <div class="slide-pane-header">File Tags <span class="filetags-hint">(Vorbis, on disk)</span></div>
                <div class="slide-pane-scroll"><pre class="filetags-json">${esc(scanFileTagsJson())}</pre></div>
              </div>
              <div class="slide-pane" id="isp-checksums">
                <div class="slide-pane-header">Checksums</div>
                <div class="slide-pane-scroll">${buildChecksumsPreviewHtml(ingest.scan.fingerprints)}</div>
              </div>
            </div>
            <div class="slide-tabs" id="ingest-tab-rail">
              <button class="slide-tab active" data-ipane="isp-info">Info File</button>
              <button class="slide-tab" data-ipane="isp-filetags">File Tags</button>
              <button class="slide-tab" data-ipane="isp-checksums">Checksums</button>
            </div>
          </div>
        </div>

      </div>
      </div>`)

    // Health score — recompute on any committed field change, not just AI
    // Assist actions (Ryan, 2026-07-16: the badge must never sit stale
    // relative to what's actually on screen — this is what let a scan
    // showing "9 of 23 tracks have a title" still show a 100/"Looks
    // complete" badge). Delegated on the review container itself, which is
    // torn down by the next setMainHTML() call, so this doesn't accumulate.
    // `focusout` (unlike `blur`) bubbles, so one listener covers every field.
    mainContent.querySelector('.ingest-review-outer')?.addEventListener('focusout', e => {
      if (e.target.matches('input, textarea, select')) reScore()
    })
    reScore()   // also recompute right away, against whatever track list just rendered

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
            inputs.forEach((inp, i) => { if (titles[i] != null) ingest.tracks[i].title = titles[i] })
          }

          // Quick flash to confirm
          btn.textContent = '✓'
          setTimeout(() => { btn.textContent = '←' }, 800)

          // These buttons set field values programmatically (no real focus
          // change), so the usual focusout-triggered reScore() below never
          // fires for them — recompute explicitly (Ryan, 2026-07-16: the
          // health score must never sit stale against what's on screen).
          reScore()
        })
      })
    })()

    // Ingest track preview — play/pause individual audio files. Shown by
    // default (previewing the first track, paused) rather than only
    // appearing after a play click (Ryan, 2026-07-15).
    ;(function () {
      const audioEl  = document.getElementById('ingest-preview-audio')
      if (!audioEl) return

      let activeBtn = null
      const previewBtns = mainContent.querySelectorAll('.btn-preview-track')

      function loadTrack(btn, filename, autoplay) {
        if (activeBtn && activeBtn !== btn) activeBtn.textContent = '▶'
        const url = `/api/stream/ingest-preview?folder=${encodeURIComponent(ingest.folderPath)}&file=${encodeURIComponent(filename)}`
        audioEl.src = url
        activeBtn = btn
        if (autoplay) {
          audioEl.play()
          btn.textContent = '■'
        } else {
          btn.textContent = '▶'
        }
      }

      previewBtns.forEach(btn => {
        btn.addEventListener('click', e => {
          e.preventDefault()
          const filename = btn.dataset.filename
          if (!filename) return

          // Toggle off if clicking the currently playing track
          if (activeBtn === btn && !audioEl.paused) {
            audioEl.pause()
            btn.textContent = '▶'
            return
          }

          // Pausing the main player bar so the two don't talk over each other
          // (Ryan, 2026-07-15).
          if (window.Player && Player.isPlaying()) Player.pause()

          loadTrack(btn, filename, true)
        })
      })

      // Default preview: first track, loaded but paused, so the bar has
      // something ready to go the moment the page opens.
      if (previewBtns.length) {
        const firstBtn = previewBtns[0]
        const filename = firstBtn.dataset.filename
        if (filename) loadTrack(firstBtn, filename, false)
      }

      audioEl.addEventListener('ended', () => {
        if (activeBtn) activeBtn.textContent = '▶'
      })
    })()

    // Right-click a track row → same note/songwriter/flags/official popup as
    // View Recording (openTrackMenu), but staged: onChange just updates the
    // in-memory ingest.tracks entry (already mutated by openTrackMenu itself)
    // and repaints this row's chips/note/songwriter cells. Nothing is sent to
    // the server until Confirm.
    function refreshIngestTrackRow(i) {
      const t = ingest.tracks[i]
      if (!t) return
      const chips = trackChipsArray(t)
      const chipsEl = document.getElementById(`t-chips-${i}`)
      if (chipsEl) chipsEl.innerHTML = chips[0] || ''

      // The overflow row (2nd+ chips) doesn't have a stable id — it's a
      // sibling <tr> right after the main row. Add/update/remove it in place
      // rather than re-rendering the whole table on every flag toggle.
      const mainRow = mainContent.querySelector(`.track-review-row[data-idx="${i}"]`)
      const existingExpand = mainRow?.nextElementSibling?.classList.contains('track-review-chiprow')
        ? mainRow.nextElementSibling : null
      if (chips.length > 1) {
        if (existingExpand) {
          existingExpand.querySelector('.track-chip-expand-row').innerHTML = chips.slice(1).join('')
        } else if (mainRow) {
          mainRow.insertAdjacentHTML('afterend', _trackChipExpandRowHtml(i, chips))
        }
      } else if (existingExpand) {
        existingExpand.remove()
      }

      const noteEl = document.getElementById(`t-note-${i}`)
      if (noteEl) {
        noteEl.textContent = t.notes || '—'; noteEl.title = t.notes || 'Click to add a note'
        noteEl.classList.toggle('pp-empty', !t.notes)
      }
      const swEl = document.getElementById(`t-sw-${i}`)
      if (swEl) {
        swEl.textContent = t.songwriter || '—'; swEl.title = t.songwriter || 'Click to add a songwriter'
        swEl.classList.toggle('pp-empty', !t.songwriter)
      }
    }
    mainContent.querySelectorAll('.track-review-row[data-idx]').forEach(row => {
      const idx = parseInt(row.dataset.idx)
      row.addEventListener('contextmenu', ev => {
        ev.preventDefault()
        const t = ingest.tracks[idx]
        if (!t) return
        openTrackMenu(t, ev.clientX, ev.clientY, {
          showOfficial: true,
          flagsOnly: true,
          onChange: () => refreshIngestTrackRow(idx),
        })
      })
    })

    // Note/Songwriter — click-to-edit directly in the table (Ryan, 2026-07-15:
    // moved out of the right-click menu, which is Flags-only here now). Staged
    // into ingest.tracks in memory, same as every other field on this form —
    // nothing hits the API until Confirm.
    ingest.tracks.forEach((t, i) => {
      const noteEl = document.getElementById(`t-note-${i}`)
      makeInlineEditable(noteEl, {
        placeholder: '—',
        get: () => ingest.tracks[i].notes || '',
        onSave: v => {
          v = v.trim() || null
          ingest.tracks[i].notes = v
          if (noteEl) noteEl.title = v || 'Click to add a note'
        },
      })
      const swEl = document.getElementById(`t-sw-${i}`)
      makeInlineEditable(swEl, {
        placeholder: '—',
        get: () => ingest.tracks[i].songwriter || '',
        onSave: v => {
          v = v.trim() || null
          ingest.tracks[i].songwriter = v
          if (swEl) swEl.title = v || 'Click to add a songwriter'
        },
      })
    })

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

    // Info File textarea — editable; updates in memory only (no re-parse, no
    // re-render, so typing doesn't lose focus/cursor position).
    document.getElementById('rev-info-edit')?.addEventListener('input', e => {
      ingest.scan.info_file_content = e.target.value
    })

    // "Save to file" — write the (possibly edited) info file back to disk,
    // independent of Confirm, so a re-run of AI Assist sees the fix. Confirm
    // itself still always sends whatever's in memory, saved or not.
    document.getElementById('btn-save-info-file')?.addEventListener('click', async () => {
      const btn      = document.getElementById('btn-save-info-file')
      const status   = document.getElementById('info-file-save-status')
      const candList = ingest.scan.text_file_candidates || []
      const filename = candList[ingest._activeTextIdx || 0]?.filename || 'info.txt'
      btn.disabled = true
      status.textContent = 'Saving…'
      try {
        const res = await API.ingest.saveInfoFile({
          folder_path: ingest.folderPath,
          filename,
          content: ingest.scan.info_file_content || '',
        })
        // A from-scratch file gets a filename back — track it so the next
        // save (and a future Confirm-time re-scan) target the same file.
        if (res?.filename && !candList.length) {
          ingest.scan.text_file_candidates = [{ filename: res.filename, content: ingest.scan.info_file_content }]
          ingest._activeTextIdx = 0
        }
        status.textContent = 'Saved ✓'
        setTimeout(() => { if (status.textContent === 'Saved ✓') status.textContent = '' }, 2500)
      } catch (e) {
        status.textContent = 'Save failed: ' + e.message
      } finally {
        btn.disabled = false
      }
    })

    // is_official checkbox on recording form — cascade to every track (flags/
    // note/songwriter/official all live on ingest.tracks now; right-click a
    // row — via openTrackMenu — to edit an individual track).
    document.getElementById('f-is-official')?.addEventListener('change', function () {
      if (this.checked) {
        ingest.tracks.forEach((t, i) => { t.is_official = true; refreshIngestTrackRow(i) })
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
    // Performer + Members/Guests widget.
    const addMembersWidget = createMembersWidget(ingest.form, {
      performerInput: 'f-artist', performerDropdown: 'f-artist-dropdown',
      field: 'f-members-field',
    })
    addMembersWidget.mount()
    initAddPerformerMembers(addMembersWidget)

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

    // Duplicate-in-library check — fires once performer + year are both
    // known. Non-blocking: a second source for the same show (SBD + AUD) is
    // legitimate, so this only informs, never prevents Confirm. Debounced so
    // it doesn't hammer the API on every keystroke. (Ryan, 2026-07-14.)
    ;(function () {
      const artistEl = document.getElementById('f-artist')
      const yearEl   = document.getElementById('f-year')
      const monthEl  = document.getElementById('f-month')
      const dayEl    = document.getElementById('f-day')
      const warnEl   = document.getElementById('dup-warn')
      const bodyEl   = document.getElementById('dup-warn-body')
      if (!artistEl || !yearEl || !warnEl) return

      let debounce = null
      async function runCheck() {
        const artist_name = artistEl.value.trim()
        const year  = parseInt(yearEl.value)  || null
        const month = parseInt(monthEl.value) || null
        const day   = parseInt(dayEl.value)   || null
        if (!artist_name || !year) { warnEl.style.display = 'none'; return }
        try {
          const res   = await API.ingest.checkExisting({ artist_name, year, month, day })
          const perfs = res.performances || []
          if (!perfs.length) { warnEl.style.display = 'none'; return }
          bodyEl.innerHTML = perfs.map(p => `
            <div class="dup-warn-perf">
              <span class="dup-warn-perf-head">${esc(p.date)}${p.venue ? ' · ' + esc(p.venue) : ''}</span>
              ${p.recordings.map(r => `
                <div class="dup-warn-rec">${esc(r.source || 'Unknown source')}${r.quality ? ' · ' + esc(r.quality) : ''} \
· ${r.track_count} track${r.track_count !== 1 ? 's' : ''}${r.created_at ? ' · added ' + esc(r.created_at.slice(0, 10)) : ''}</div>`).join('')}
            </div>`).join('')
          warnEl.style.display = ''
        } catch (_) { /* best-effort — a failed check should never block ingest */ }
      }

      ;[artistEl, yearEl, monthEl, dayEl].forEach(el => {
        el.addEventListener('input', () => {
          clearTimeout(debounce)
          debounce = setTimeout(runCheck, 500)
        })
      })
      runCheck()   // also on load — covers AI Assist auto-fill / back-nav restore
    })()

    // Paula's purple border means "I pre-filled this with confidence" — the
    // moment a human edits that specific field it's their entry, not hers,
    // so the border clears immediately (no re-scoring involved, just a
    // one-time visual cue that's done its job).
    ;(function () {
      ['f-artist', 'f-year', 'f-month', 'f-day',
       'f-venue-name', 'f-city', 'f-state', 'f-country'].forEach(id => {
        const el = document.getElementById(id)
        if (el) el.addEventListener('input', () => el.classList.remove('paula-recommend'), { once: true })
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
        // Placeholder venues ("Unknown Venue", "TBD", ...) aren't one real
        // canonical place — their stored city/state/country is leftover from
        // whichever other show wrote there last, not this show's location.
        // Don't lock/prefill from it; leave the tag/info guess editable.
        // (Ryan, 2026-07-15 — see app/utils/venues.py for the full story.)
        if (isPlaceholderVenue(venue?.name)) return
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

      // Restore lock on back-nav if a venue was previously selected. On a
      // fresh scan (no venue_id yet — just a tag/info-derived name), check
      // whether that name already matches an existing venue: if so, treat it
      // like a manual pick and lock city/state/country to the venue's own
      // stored values rather than the tag/info guess, which may be stale or
      // just less precise (e.g. "Ottawa, ON" in tags vs. the venue's actual
      // "Gatineau, QC"). A genuinely new venue name is left as the tag/info
      // prefill, editable. (Ryan, 2026-07-14.)
      if (ingest.form.venue_id) {
        API.venues.get(ingest.form.venue_id).then(v => lockLocation(v)).catch(() => {})
      } else if (nameEl.value.trim().length >= 2) {
        const typed = nameEl.value.trim()
        API.venues.list(typed).then(venues => {
          if (idEl.value) return   // user already picked something while this was in flight
          const exact = venues.find(v => v.name.toLowerCase() === typed.toLowerCase())
          if (exact) {
            idEl.value = exact.id
            ingest.form.venue_id = exact.id
            lockLocation(exact)
          }
        }).catch(() => {})
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
        // Only offer "+ Create" when the typed name doesn't already exist —
        // no point suggesting creation of a venue that's right there in the list.
        const exactMatch = venues.some(v => v.name.toLowerCase() === q.toLowerCase())
        const createRow = (q && !exactMatch)
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

    // Standardized back link (top of page) — reflects how this review was
    // actually reached (Ryan, 2026-07-15: "scrub our back link logic for
    // that space"). From Bulk Import's "Review →": straight back to the
    // in-memory batch results, no rescan — speed is the whole point for a
    // bulk reviewer working through many folders. Otherwise: the folder step.
    document.getElementById('ingest-back-link').addEventListener('click', e => {
      e.preventDefault()
      if (ingest.fromBatch) {
        // renderBatchResultsView() paints the batch list directly, bypassing
        // the hash router — but window.location.hash is still '#/ingest' from
        // when we navigated in. Left uncorrected, the NEXT _batchOpenReview()
        // call sets hash to '#/ingest' again, which is a no-op (same value ⇒
        // no hashchange ⇒ route() never runs ⇒ renderIngestView() never fires).
        // The scan completes fine and ingest.* state is fully populated — the
        // review form just never gets painted, looking exactly like a stuck
        // hang even though nothing is hung. replaceState fixes the recorded
        // hash without triggering a redundant render (2026-07-20).
        history.replaceState(null, '', '#/batch')
        renderBatchResultsView()
      } else {
        ingest.step = 'folder'
        renderIngestStep()
      }
    })

    document.getElementById('btn-ai-assist')?.addEventListener('click', startAiAssist)

    // Right-column vertical tabs (Info File / File Tags; AI Assist added on demand)
    mainContent.querySelectorAll('.ingest-review-raw .slide-tab').forEach(tab => {
      tab.addEventListener('click', () => switchIngestPane(tab.dataset.ipane, tab))
    })

    document.getElementById('btn-confirm').addEventListener('click', async () => {
      // Collect metadata
      const f = ingest.form
      f.artist_name     = document.getElementById('f-artist').value.trim()
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
      f.quality         = document.getElementById('f-quality').value.trim()
      f.rating          = document.getElementById('f-rating').value.trim()
      f.lineage         = document.getElementById('f-lineage').value.trim()
      f.notes           = document.getElementById('f-notes').value.trim()

      if (!f.artist_name) { alert('Artist name is required.'); return }

      // Title is still a live input — collect its current value. Notes,
      // songwriter, flags, and official are already staged directly on
      // ingest.tracks by openTrackMenu's onChange (right-click popup).
      mainContent.querySelectorAll('.t-title').forEach(el => {
        const t = ingest.tracks[parseInt(el.dataset.idx)]; if (t) t.title = el.value.trim()
      })

      // Submit directly — the old "Confirm & Add to Library" review screen
      // is gone (Ryan, 2026-07-15: "never very useful, nothing is ever
      // something i need to change"). File-behavior (copy/move) is no longer
      // a per-add choice either — it's a standing preference (Settings ⚙,
      // right next to the Anthropic key), read silently here.
      const btn = document.getElementById('btn-confirm')
      const errEl = document.getElementById('review-submit-error')
      btn.disabled = true
      btn.textContent = 'Adding to library…'
      errEl.style.display = 'none'

      let behavior = 'copy'
      try {
        const prefs = await API.preferences.get()
        behavior = prefs.ingest_file_behavior || 'copy'
      } catch (_) { /* fall back to copy */ }

      const payload = {
        source_folder_path: ingest.folderPath,
        ...f,
        behavior,
        tracks: ingest.tracks,
        fingerprints: ingest.scan.fingerprints || [],
        info_file_content: ingest.scan.info_file_content || null,
        members: (f.members || []).map(m => m.name),
        guests:  (f.guests  || []).map(m => m.name),
        // AI Assist may have already been run on this draft (pre-save) — carry
        // the result along so it lands on the new recording instead of being
        // lost the moment confirm creates the row (2026-07-14 bug: it wasn't).
        ai_result: ingest.aiResult || null,
      }

      // Progress UI under the button (copy can take a while for big folders)
      const actions = btn.closest('.ingest-actions')
      let prog = document.getElementById('confirm-progress')
      if (!prog) {
        prog = document.createElement('div')
        prog.id = 'confirm-progress'
        prog.className = 'confirm-progress'
        prog.innerHTML = `<div class="confirm-progress-bar"><div class="confirm-progress-fill" id="confirm-progress-fill"></div></div>
                          <div class="confirm-progress-label" id="confirm-progress-label">Preparing…</div>`
        actions?.parentNode.insertBefore(prog, actions.nextSibling)
      }
      const fill  = document.getElementById('confirm-progress-fill')
      const label = document.getElementById('confirm-progress-label')
      const fmtMB = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB'

      try {
        const { job_id } = await API.ingest.confirm(payload)
        const result = await pollConfirmJob(job_id, (copied, total) => {
          const pct = total ? Math.min(100, Math.round(100 * copied / total)) : 0
          if (fill)  fill.style.width = pct + '%'
          if (label) label.textContent = total ? `Copying files… ${pct}% (${fmtMB(copied)} / ${fmtMB(total)})` : 'Copying files…'
        })
        if (fill) fill.style.width = '100%'
        ingest._lastResult = result
        if (result.recording_id) {
          if (result.checksum_mismatches > 0) {
            alert(`⚠ ${result.checksum_mismatches} track checksum${result.checksum_mismatches === 1 ? '' : 's'} did not match the fingerprint file for this show. Check the Checksums pane before trusting this copy.`)
          }
          await loadArtistList()   // new performer/venue/artist may exist
          if (ingest.fromBatch) {
            // Keep the batch list's ✓ Ingested state correct if they later
            // navigate back here some other way, then go straight back to
            // the queue — that's the whole point for a bulk reviewer working
            // through many folders (Ryan, 2026-07-15).
            batch.ingestedIds.set(ingest.folderPath, result.recording_id)
            history.replaceState(null, '', '#/batch')   // see note on the back-link handler above
            renderBatchResultsView()
          } else {
            window.location.hash = `#/recording/${result.recording_id}`
          }
        } else {
          // Fallback, shouldn't normally happen — no recording_id to jump to.
          ingest.step = 'success'
          renderIngestStep()
        }
      } catch (e) {
        errEl.textContent = `Error: ${e.message}`
        errEl.style.display = 'block'
        btn.disabled = false
        btn.textContent = 'Add Recording →'
        prog?.remove()
      }
    })

    // Resize handle
    wireResizablePanel(
      mainContent.querySelector('.ingest-review-shell'),
      mainContent.querySelector('.ingest-review-form'),
      document.getElementById('rev-divider'),
      260, 200
    )
  }

  // fmtDur is shared by the review-step track table and the confirm summary.
  function fmtDur(s) {
    if (!s) return '—'
    const m = Math.floor(s / 60), sec = Math.floor(s % 60)
    return `${m}:${String(sec).padStart(2,'0')}`
  }

  // Step 4 ("Confirm & Add to Library") removed 2026-07-15 — Ryan: "never
  // very useful, nothing is ever something i need to change... doesn't look
  // great." The review step's "Add Recording →" button now submits directly
  // (see its click handler above) instead of navigating to a separate
  // summary-then-confirm screen. File behavior (copy/move) moved from a
  // per-add choice to a standing preference (Settings ⚙).

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
      if (!result.recording_id) return
      // Refresh the sidebar (new performer/venue/artist may exist) then navigate.
      loadArtistList().then(() => {
        window.location.hash = `#/recording/${result.recording_id}`
      })
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

  // ── Player integration ─────────────────────────────────────────────────────

  async function playRecording(recId, startIdx, preloadedTracks, opts) {
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
      sourceStr = recData.source || ''
    }
    // Player bar line 2: Date · Venue (artist name is redundant here — it's
    // shown on line 3). Line 3: the artist/band name.
    const metaParts = [dateStr, venueStr].filter(Boolean)
    const meta      = metaParts.join(' · ') || sourceStr || '—'
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

    Player.loadQueue(queue, queueStart, opts)
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

    // Switch the wavesurfer waveform to the new track's peaks if we have
    // analysis data for it (mirrors the old canvas's track-follow
    // behaviour). No network fetch — same precomputed peaks used to render
    // the banner in the first place.
    if (_wsInstance && trackId !== _wsTrackId) {
      const peaks = _peaksForTrack(trackId)
      const duration = _trackDurationMap[trackId]
      if (peaks && duration) {
        _wsInstance.load('', peaks, duration)
        _wsTrackId = trackId
      }
    }
  }

  // Venue page — editable name / location / bio in place + performances.
  async function renderVenueView(id) {
    setActiveNav('venues'); setActiveArtist(null); setLoading()
    let v
    try { v = await API.venues.get(id) }
    catch (e) {
      invalidateDims('venues')
      setMainHTML(`<div class="empty-state"><div class="empty-title">This venue no longer exists</div></div>`)
      return
    }
    setNavCurrent(v.name)
    const navBack = state.navBack   // see the Performer page's identical comment
    const descText = v.bio && v.bio.trim()
    // One row per Recording at this venue (showing the performer, since a venue
    // hosts many different acts). Already ordered chronologically by the API.
    const venueRows = v.recordings || []
    const rowsHtml = venueRows.map(r => flatRowHtml(r, true)).join('')

    setMainHTML(`
      <div class="performer-page">
        ${navBack ? `<div class="pp-back-row"><div class="breadcrumb" id="vn-back-btn">← ${esc(navBack.label)}</div></div>` : ''}
        <div class="performer-head">
          <div class="pp-name-row">
            <h1 class="pp-name pp-editable" id="vn-name" title="Click to edit">${esc(v.name)}</h1>
            <button class="btn btn-ghost btn-sm pp-delete" id="vn-delete" title="Delete venue">Delete</button>
          </div>
          <div class="vn-loc">
            <span class="vn-field"><label>City</label><span class="pp-editable vn-val ${v.city ? '' : 'pp-empty'}" id="vn-city">${v.city ? esc(v.city) : '—'}</span></span>
            <span class="vn-field"><label>State / Region</label><span class="pp-editable vn-val ${v.state ? '' : 'pp-empty'}" id="vn-state">${v.state ? esc(v.state) : '—'}</span></span>
            <span class="vn-field"><label>Country</label><span class="pp-editable vn-val ${v.country ? '' : 'pp-empty'}" id="vn-country">${v.country ? esc(v.country) : '—'}</span></span>
          </div>
          <div class="pp-desc pp-editable ${descText ? '' : 'pp-empty'}" id="vn-bio" title="Click to edit">${descText ? esc(v.bio) : 'Add notes…'}</div>
        </div>
        ${venueRows.length ? recTableHeadHtml(true) : ''}
        <div class="rec-table" id="rec-table-venue">${rowsHtml || '<div class="empty-state" style="min-height:140px"><div class="empty-title">No recordings from this venue yet</div></div>'}</div>
      </div>`)

    wireRecordingRows(mainContent)
    if (venueRows.length) wireDateAddedSort(document.getElementById('rec-table-venue'), venueRows, true)

    if (navBack) {
      document.getElementById('vn-back-btn')?.addEventListener('click', () => {
        window.location.hash = navBack.hash
      })
    }

    const refreshSidebar = () => invalidateDims('venues')
    async function saveField(patch) {
      try { await API.venues.update(id, patch); refreshSidebar() }
      catch (e) { alert('Save failed: ' + e.message) }
    }
    makeInlineEditable(document.getElementById('vn-name'), {
      get: () => v.name,
      onSave: async val => { val = val.trim(); if (!val || val === v.name) return; v.name = val; await saveField({ name: val }) },
    })
    ;['city', 'state', 'country'].forEach(f => {
      makeInlineEditable(document.getElementById('vn-' + f), {
        placeholder: '—',
        get: () => v[f] || '',
        onSave: async val => { val = val.trim(); v[f] = val; await saveField({ [f]: val || null }) },
      })
    })
    makeInlineEditable(document.getElementById('vn-bio'), {
      multiline: true, placeholder: 'Add notes…',
      get: () => v.bio || '',
      onSave: async val => { val = val.trim(); v.bio = val; await saveField({ bio: val || null }) },
    })

    document.getElementById('vn-delete').addEventListener('click', async () => {
      if (!confirm(`Delete venue "${v.name}"? This can't be undone.`)) return
      try { await API.venues.remove(id); refreshSidebar(); window.location.hash = '#/venues' }
      catch (e) { alert(e.message) }
    })
  }

  // ── Venues admin page ──────────────────────────────────────────────────────

  async function renderVenuesPage(preSelectId = null) {
    setActiveNav('venues')
    setActiveArtist(null)
    setNavCurrent('Venues')
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
            <button class="btn btn-ghost btn-sm" id="vd-delete" style="margin-left:auto; color:var(--red)">Delete</button>
          </div>

          ${v.performance_count > 0 ? `
          <div class="rev-section-title" style="margin-bottom:10px">Performances (${v.performance_count})</div>
          <div style="display:flex; flex-direction:column; gap:2px">
            ${v.performances.map(p => `
              <div style="display:flex; align-items:center; gap:12px; padding:5px 0; border-bottom:1px solid var(--bd-0); font-size:12px">
                <span style="color:var(--t2); font-family:var(--font-mono); min-width:80px">${esc(p.date)}</span>
                <a href="#/artist/${p.performer_id}" style="color:var(--t0); text-decoration:none; flex:1">${esc(p.performer)}</a>
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

      document.getElementById('vd-delete').addEventListener('click', async () => {
        if (!confirm(`Delete venue "${v.name}"? This can't be undone.`)) return
        const msgEl = document.getElementById('vd-msg')
        try {
          await API.venues.remove(id)
          allVenues = await API.venues.list()
          activeId  = null
          renderList(allVenues)
          document.getElementById('venues-detail-panel').innerHTML =
            '<div class="venue-detail-empty">Select a venue to view or edit</div>'
          _dimCache.venues = null
          if (state.expandedDims.has('venues')) _renderDimRecords('venues')
        } catch (e) {
          msgEl.style.color = 'var(--red)'
          msgEl.textContent = e.message
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

  async function renderArtistsIndexPage() {
    setActiveNav('artists-index')
    setActiveArtist(null)
    setNavCurrent('Performers')
    setLoading()

    let performers = []
    try { performers = await API.performers.list() } catch (_) {}

    const rowHtml = list => list.map(p => `
      <div class="artist-index-row" data-id="${p.id}">
        <span class="artist-index-name">${esc(p.name)}</span>
        <span class="artist-index-members">${esc((p.members || []).join(', '))}</span>
        <span class="artist-index-count">${p.recording_count || 0} rec</span>
      </div>`).join('')

    setMainHTML(`
      <div class="action-bar">
        <span style="font-size:13px; font-weight:500; color:var(--t0)">Performers</span>
        <input type="text" id="artist-search-input" placeholder="Search performers or members…" style="margin-left:auto; width:240px; font-size:12px" />
      </div>
      <div class="artist-index-list" id="artist-index-list">${rowHtml(performers) || '<div class="empty-state" style="min-height:120px"><div>No performers yet</div></div>'}</div>`)

    function wireRows() {
      mainContent.querySelectorAll('.artist-index-row').forEach(el =>
        el.addEventListener('click', () => { window.location.hash = `#/artist/${el.dataset.id}` }))
    }
    wireRows()

    document.getElementById('artist-search-input').addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase()
      const filtered = q
        ? performers.filter(p => p.name.toLowerCase().includes(q) ||
            (p.members || []).some(m => m.toLowerCase().includes(q)))
        : performers
      document.getElementById('artist-index-list').innerHTML = rowHtml(filtered)
      wireRows()
    })
  }

  // ── Router ─────────────────────────────────────────────────────────────────

  // Hash of the page route() last actually dispatched to — module-scope, not
  // state.*, since this is purely a "have we already been here" bookkeeping
  // detail for the navBack snapshot below, not app state anything else reads.
  let _lastRouteHash = null

  function route() {
    const hash = window.location.hash || '#/'

    // Snapshot "where we're coming from" for the destination page's Back
    // link (state.navCurrent/navBack) — but only on a genuine navigation.
    // Guard against two false positives: the very first dispatch this
    // session (_lastRouteHash is null — nothing preceded it, navBack stays
    // null) and a same-hash re-dispatch (some code sets window.location.hash
    // to its OWN current value, or history.replaceState is used elsewhere to
    // correct the recorded hash without a real navigation — neither should
    // overwrite a real back target with the page's own info).
    if (_lastRouteHash !== null && hash !== _lastRouteHash) {
      state.navBack = state.navCurrent
    }
    _lastRouteHash = hash

    if (hash.startsWith('#/recording/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderRecordingView(id)
      else    renderLibraryView()

    } else if (hash.startsWith('#/artist/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderArtistView(id)
      else    renderLibraryView()

    } else if (hash.startsWith('#/performer/')) {
      // The performer page is edit-in-place, so #/performer/<id> and any legacy
      // /edit suffix both land on the same view.
      const id = parseInt(hash.split('/')[2])
      if (id) renderArtistView(id)
      else    renderLibraryView()

    } else if (hash === '#/recent') {
      renderRecentView()

    } else if (hash === '#/batch') {
      renderBatchImportView()

    } else if (hash === '#/ingest') {
      renderIngestView()

    } else if (hash === '#/venues') {
      renderVenuesPage()

    } else if (hash.startsWith('#/venue/')) {
      const id = parseInt(hash.split('/')[2])
      if (id) renderVenueView(id)
      else    renderVenuesPage()

    } else if (hash === '#/artists') {
      renderArtistsIndexPage()

    } else if (hash.startsWith('#/person/')) {
      // Edit-in-place, so #/person/<id> and any legacy /edit both land on the view.
      const id = parseInt(hash.split('/')[2])
      if (id) renderPersonView(id)
      else    renderLibraryView()

    } else if (hash === '#/collections') {
      renderCollectionsIndex()

    } else if (hash === '#/collection/new') {
      renderCollectionForm()

    } else if (hash.startsWith('#/collection/')) {
      // Edit-in-place, so #/collection/<id> and any legacy /edit both land on the view.
      const id = parseInt(hash.split('/')[2])
      if (id) renderCollectionView(id)
      else    renderCollectionsIndex()

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

  // ── Settings modal ───────────────────────────────────────────────────────────

  async function openSettingsModal() {
    let prefs = {}
    try { prefs = await API.preferences.get() } catch (_) {}
    const keySet   = prefs.has_api_key
    const noKeychain = prefs.keychain_available === false
    const model    = prefs.ai_model || 'claude-sonnet-5'
    const behavior = prefs.ingest_file_behavior || 'copy'

    const overlay = document.createElement('div')
    overlay.className = 'modal-overlay'
    overlay.innerHTML = `
      <div class="modal-card settings-modal">
        <div class="modal-header"><h3>Settings</h3>
          <button class="btn-icon" id="settings-close">✕</button></div>
        <div class="modal-body">
          <label class="settings-label">Anthropic API key <span class="settings-hint">(BYOK — stored in your OS keychain)</span></label>
          <div class="settings-key-row">
            <input type="password" id="settings-key" placeholder="${keySet ? '•••••••••• (key saved)' : 'sk-ant-…'}" autocomplete="off" />
            ${keySet ? '<button class="btn btn-ghost btn-sm" id="settings-clear-key">Clear</button>' : ''}
          </div>
          ${noKeychain ? '<div class="settings-warn">⚠ OS keychain unavailable on this system — key cannot be saved.</div>' : ''}

          <label class="settings-label" style="margin-top:14px">AI model</label>
          <select id="settings-model">
            <option value="claude-sonnet-5" ${model === 'claude-sonnet-5' ? 'selected' : ''}>Sonnet 5 (default — stronger research)</option>
            <option value="claude-haiku-4-5" ${model === 'claude-haiku-4-5' ? 'selected' : ''}>Haiku 4.5 (lightweight)</option>
          </select>

          <label class="settings-label" style="margin-top:14px">Ingest file handling</label>
          <select id="settings-behavior">
            <option value="copy" ${behavior === 'copy' ? 'selected' : ''}>Copy into library (keep source)</option>
            <option value="move" ${behavior === 'move' ? 'selected' : ''}>Move into library</option>
          </select>
        </div>
        <div class="modal-footer">
          <button class="btn btn-ghost btn-sm" id="settings-cancel">Cancel</button>
          <button class="btn btn-primary btn-sm" id="settings-save">Save</button>
        </div>
      </div>`
    document.body.appendChild(overlay)

    const close = () => overlay.remove()
    overlay.addEventListener('click', e => { if (e.target === overlay) close() })
    overlay.querySelector('#settings-close').addEventListener('click', close)
    overlay.querySelector('#settings-cancel').addEventListener('click', close)
    overlay.querySelector('#settings-clear-key')?.addEventListener('click', async () => {
      try { await API.preferences.update({ clear_api_key: true }); close() } catch (e) { alert(e.message) }
    })
    overlay.querySelector('#settings-save').addEventListener('click', async () => {
      const payload = {
        ai_model:             overlay.querySelector('#settings-model').value,
        ingest_file_behavior: overlay.querySelector('#settings-behavior').value,
      }
      const key = overlay.querySelector('#settings-key').value.trim()
      if (key) payload.api_key = key
      try { await API.preferences.update(payload); close() } catch (e) { alert(e.message) }
    })
  }

  document.getElementById('settings-btn')?.addEventListener('click', openSettingsModal)

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
    // Paula's full scan-time breakdown (score + every flag/component per
    // attribute + track completeness) — surfaced to the debug panel's
    // dedicated Paula section. Null outside the Add Recording flow, or
    // before a folder's been scanned.
    get paula()       { return (typeof ingest !== 'undefined' && ingest?.scan?.paula) || null },
  }

  return { onTrackChange }

})()
