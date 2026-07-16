/**
 * player.js — Audio player controller.
 *
 * Owns the <audio> element and player bar UI.
 * Queue is a flat array of track objects: { id, title, duration, streamUrl, meta }
 * where `meta` is a display string for the player bar subtitle.
 */

const Player = (() => {

  const audio   = document.getElementById('audio-el')
  const btnPlay = document.getElementById('btn-play')
  const btnPrev = document.getElementById('btn-prev')
  const btnNext = document.getElementById('btn-next')
  const progress  = document.getElementById('progress-bar')
  const volBar    = document.getElementById('volume-bar')
  const timeCur   = document.getElementById('time-current')
  const timeDur   = document.getElementById('time-duration')
  const titleEl   = document.getElementById('player-title')
  const metaEl    = document.getElementById('player-meta')
  const recEl     = document.getElementById('player-rec')
  const infoEl    = document.getElementById('player-info')   // clickable wrapper
  const iconPlay  = btnPlay.querySelector('.icon-play')
  const iconPause = btnPlay.querySelector('.icon-pause')

  let queue      = []
  let queueIdx   = -1
  let startedAt  = null   // Date when play started (for PlayLog)

  // Set by whichever page is currently showing a recording with nothing
  // loaded in the player yet — pressing the bar's play button with an empty
  // queue then plays that recording's first track instead of no-op'ing
  // (Ryan, 2026-07-15). Cleared on every navigation (see App.setMainHTML).
  let fallbackPlay = null
  function setFallbackPlay(fn) { fallbackPlay = fn }

  // Navigate back to the currently-playing recording when user clicks the info area
  infoEl.addEventListener('click', () => {
    const recId = infoEl.dataset.recId
    if (recId) location.hash = `#/recording/${recId}`
  })

  // ── Helpers ──────────────────────────────────────────────────────────────

  function fmtTime(secs) {
    if (!secs || isNaN(secs)) return '0:00'
    const m = Math.floor(secs / 60)
    const s = Math.floor(secs % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  function setPct(pct) {
    progress.style.setProperty('--pct', `${pct}%`)
  }

  function updateTrackUI(track) {
    titleEl.textContent = track ? track.title : '—'
    metaEl.textContent  = track ? track.meta  : 'No track selected'
    if (recEl) recEl.textContent = track?.recLabel || ''
    timeDur.textContent = track ? fmtTime(track.duration) : '0:00'
    timeCur.textContent = '0:00'
    setPct(0)
    progress.value = 0
    const recId = track?.recordingId ?? null
    infoEl.dataset.recId = recId || ''
    infoEl.classList.toggle('has-recording', !!recId)
    infoEl.title = recId ? 'Go to recording' : ''
  }

  function currentTrack() {
    return queue[queueIdx] || null
  }

  // ── Playback ──────────────────────────────────────────────────────────────

  function playIdx(idx, opts) {
    if (idx < 0 || idx >= queue.length) return
    queueIdx  = idx
    const trk = currentTrack()
    const autoplay = !opts || opts.autoplay !== false

    audio.src = trk.streamUrl
    audio.load()
    audio.volume = parseFloat(volBar.value)
    if (autoplay) {
      audio.play().catch(() => {})
      startedAt = Date.now()
    } else {
      startedAt = null
    }

    updateTrackUI(trk)
    App.onTrackChange(trk.id)
  }

  function togglePlay() {
    if (!currentTrack()) {
      if (fallbackPlay) fallbackPlay()
      return
    }
    if (audio.paused) {
      audio.play().catch(() => {})
      startedAt = startedAt || Date.now()
    } else {
      audio.pause()
    }
  }

  function prev() {
    if (queueIdx > 0) playIdx(queueIdx - 1)
  }

  function next() {
    if (queueIdx < queue.length - 1) playIdx(queueIdx + 1)
  }

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Load a new queue and start playing at startIndex.
   * @param {Array}  tracks     — array of { id, title, duration, streamUrl, meta }
   * @param {number} startIndex — index in tracks to begin playing
   * @param {Object} [opts]     — pass { autoplay: false } to load paused
   *                              (e.g. readying a recording for the waveform
   *                              to scrub without interrupting silence)
   */
  function loadQueue(tracks, startIndex, opts) {
    queue    = tracks
    playIdx(startIndex, opts)
  }

  function isPlaying() { return !audio.paused }

  function pause() {
    if (!audio.paused) audio.pause()
  }

  function currentId() {
    return currentTrack()?.id ?? null
  }

  // ── Audio element events ──────────────────────────────────────────────────

  audio.addEventListener('timeupdate', () => {
    if (!audio.duration || isNaN(audio.duration)) return
    const pct = (audio.currentTime / audio.duration) * 100
    setPct(pct)
    progress.value  = pct
    timeCur.textContent = fmtTime(audio.currentTime)
  })

  audio.addEventListener('play',  () => { iconPlay.style.display = 'none'; iconPause.style.display = '' })
  audio.addEventListener('pause', () => { iconPlay.style.display = '';     iconPause.style.display = 'none' })

  audio.addEventListener('ended', () => {
    // Log the completed play
    const trk = currentTrack()
    if (trk && startedAt) {
      const elapsed = Math.round((Date.now() - startedAt) / 1000)
      API.tracks.logPlay(trk.id, { duration_played: elapsed, completed: true }).catch(() => {})
    }
    startedAt = null
    next()
  })

  audio.addEventListener('error', () => {
    metaEl.textContent = 'Stream error'
  })

  // ── Controls ──────────────────────────────────────────────────────────────

  btnPlay.addEventListener('click', togglePlay)
  btnPrev.addEventListener('click', prev)
  btnNext.addEventListener('click', next)

  progress.addEventListener('input', () => {
    if (!audio.duration || isNaN(audio.duration)) return
    const pct = parseFloat(progress.value)
    audio.currentTime = (pct / 100) * audio.duration
    setPct(pct)
  })

  volBar.addEventListener('input', () => {
    audio.volume = parseFloat(volBar.value)
  })

  // Expose state for debug panel
  window.fluxPlayer = {
    get currentTitle() { return currentTrack()?.title ?? null },
    get queueLength()  { return queue.length },
    get queueIdx()     { return queueIdx },
  }

  return { loadQueue, isPlaying, pause, currentId, setFallbackPlay }

})()
