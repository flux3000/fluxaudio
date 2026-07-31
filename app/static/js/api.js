/**
 * api.js — Thin wrapper around fetch for all Flux Audio API calls.
 * All functions return parsed JSON or throw on non-2xx responses.
 * Authentication is cookie-based (Flask-Login session).
 */

const API = (() => {

  async function request(method, path, body) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    }
    if (body !== undefined) opts.body = JSON.stringify(body)

    const res = await fetch(path, opts)
    if (res.status === 204) return null

    const data = await res.json()
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
    return data
  }

  const get  = (path)        => request('GET',  path)
  const post = (path, body)  => request('POST', path, body)
  const put  = (path, body)  => request('PUT',  path, body)

  return {
    // ── Auth ────────────────────────────────────────────────────────────────
    auth: {
      me:     ()             => get('/api/auth/me'),
      login:  (username, password) => post('/api/auth/login', { username, password }),
      logout: ()             => post('/api/auth/logout'),
    },

    // ── Artists (people) ──────────────────────────────────────────────────────
    artists: {
      search: (q)        => get(`/api/artists/search?q=${encodeURIComponent(q)}`),
      list:   ()         => get('/api/artists/'),
      get:    (id)       => get(`/api/artists/${id}`),
      create: (data)     => post('/api/artists/', data),
      update: (id, data) => put(`/api/artists/${id}`, data),
      remove: (id)       => request('DELETE', `/api/artists/${id}`),
      addPerformer:    (id, data)   => post(`/api/artists/${id}/performers`, data),
      removePerformer: (id, perfId) => request('DELETE', `/api/artists/${id}/performers/${perfId}`),
    },

    // ── Collections ───────────────────────────────────────────────────────────
    collections: {
      list:            ()               => get('/api/collections/'),
      get:             (id)             => get(`/api/collections/${id}`),
      create:          (data)           => post('/api/collections/', data),
      update:          (id, data)       => put(`/api/collections/${id}`, data),
      remove:          (id)             => request('DELETE', `/api/collections/${id}`),
      addRecording:    (id, recId)      => post(`/api/collections/${id}/recordings`, { recording_id: recId }),
      removeRecording: (id, recId)      => request('DELETE', `/api/collections/${id}/recordings/${recId}`),
    },

    // ── Performers (acts) ─────────────────────────────────────────────────────
    performers: {
      search:        (q)         => get(`/api/performers/search?q=${encodeURIComponent(q)}`),
      list:          ()          => get('/api/performers/'),
      allRecordings: ()          => get('/api/performers/all-recordings'),
      get:           (id)        => get(`/api/performers/${id}`),
      recordings:    (id)        => get(`/api/performers/${id}/recordings`),
      create:        (data)      => post('/api/performers/', data),
      update:        (id, data)  => put(`/api/performers/${id}`, data),
      remove:        (id)        => request('DELETE', `/api/performers/${id}`),
      addStint:      (id, artistId, data) => post(`/api/performers/${id}/members/${artistId}/stints`, data),
      updateStint:   (stintId, data)      => put(`/api/performers/stints/${stintId}`, data),
      removeStint:   (stintId)            => request('DELETE', `/api/performers/stints/${stintId}`),

      // Profile picture (2026-07-22) — a raw upload, not JSON, so it bypasses
      // request()'s JSON.stringify/Content-Type: letting the browser set its
      // own multipart boundary is required for a file upload to parse
      // server-side. imageUrl() is a plain URL string for an <img src>, not a
      // fetch call — the browser requests it directly (same-origin session
      // cookie covers the @login_required check, same as the waveform/
      // spectrogram images already do).
      imageUrl: (id) => `/api/performers/${id}/image?t=${Date.now()}`,   // cache-bust on re-upload
      uploadImage: async (id, file) => {
        const form = new FormData()
        form.append('image', file)
        const res = await fetch(`/api/performers/${id}/image`, { method: 'POST', body: form, credentials: 'same-origin' })
        const data = await res.json()
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
        return data
      },
      removeImage: (id) => request('DELETE', `/api/performers/${id}/image`),

      // Dossier — AI-drafted bio + suggested resource links, background job
      // (same shape as API.ingest.aiAssist*).
      startDossier:   (id)         => post(`/api/performers/${id}/dossier`),
      dossierStatus:  (id, jobId)  => get(`/api/performers/${id}/dossier/${jobId}`),
    },

    // ── Performances ─────────────────────────────────────────────────────────
    performances: {
      get:    (id)       => get(`/api/performances/${id}`),
      create: (data)     => post('/api/performances/', data),
      update: (id, data) => put(`/api/performances/${id}`, data),
      updatePersonnelRow: (perfId, personnelId, data) =>
        put(`/api/performances/${perfId}/personnel/${personnelId}`, data),
    },

    // ── Recordings ───────────────────────────────────────────────────────────
    recordings: {
      get:        (id)       => get(`/api/recordings/${id}`),
      recent:     (limit)    => get(`/api/recordings/recent?limit=${limit || 50}`),
      scan:       (folder)   => post('/api/recordings/scan', { folder_path: folder }),
      update:     (id, data) => put(`/api/recordings/${id}`, data),
      delete:     (id)       => request('DELETE', `/api/recordings/${id}`),
      writeTags:  (id)       => post(`/api/recordings/${id}/write-tags`),
      fileTags:   (id)       => get(`/api/recordings/${id}/tags`),
      reprocess:  (id)       => post(`/api/recordings/${id}/reprocess`),
      verifyChecksums: (id)  => post(`/api/recordings/${id}/verify-checksums`),
    },

    // ── Tracks ───────────────────────────────────────────────────────────────
    tracks: {
      update:  (id, data) => put(`/api/tracks/${id}`, data),
      logPlay: (id, data) => post(`/api/tracks/${id}/play`, data),
    },

    // ── Venues ───────────────────────────────────────────────────────────────
    venues: {
      list:   (q)        => get(`/api/venues/${q ? '?q=' + encodeURIComponent(q) : ''}`),
      get:    (id)       => get(`/api/venues/${id}`),
      create: (data)     => post('/api/venues/', data),
      update: (id, data) => put(`/api/venues/${id}`, data),
      remove: (id)       => request('DELETE', `/api/venues/${id}`),
    },

    // ── Events ───────────────────────────────────────────────────────────────
    events: {
      list:   (q)        => get(`/api/events/${q ? '?q=' + encodeURIComponent(q) : ''}`),
      search: (q)        => get(`/api/events/search?q=${encodeURIComponent(q)}`),
      get:    (id)       => get(`/api/events/${id}`),
      create: (data)     => post('/api/events/', data),
      update: (id, data) => put(`/api/events/${id}`, data),
    },

    // ── Preferences ──────────────────────────────────────────────────────────
    preferences: {
      get:    ()     => get('/api/preferences'),
      update: (data) => put('/api/preferences', data),
    },

    // ── Ingest ───────────────────────────────────────────────────────────────
    ingest: {
      confirm:       (data)  => post('/api/ingest/confirm', data),
      confirmStatus: (jobId) => get(`/api/ingest/confirm/${jobId}`),
      // Cooperative cancel. The worker stops between files, undoes its own
      // filesystem work and rolls back its uncommitted DB session. Recordings
      // already finished earlier in a queue are untouched.
      confirmCancel: (jobId) => post(`/api/ingest/confirm/${jobId}/cancel`, {}),
      aiAssist:          (payload) => post('/api/ingest/ai-assist', payload),
      aiAssistRecording: (recId)   => post(`/api/ingest/ai-assist-recording/${recId}`),
      aiAssistStatus:    (jobId)   => get(`/api/ingest/ai-assist/${jobId}`),
      saveInfoFile:   (payload) => post('/api/ingest/save-info-file', payload),
      checkExisting:  ({ artist_name, year, month, day }) => {
        const p = new URLSearchParams({ artist_name })
        p.set('year', year)
        if (month) p.set('month', month)
        if (day)   p.set('day', day)
        return get(`/api/ingest/check-existing?${p.toString()}`)
      },
      health:         (scan)    => post('/api/ingest/health', scan),
      batchScan:  (source_dir) => post('/api/ingest/batch-scan', { source_dir }),
    },

    // ── Listening Quality ────────────────────────────────────────────────────
    // Stage 1+2 of the unified ingestion flow (2026-07-30). See
    // app/api/quality.py for the endpoint contracts.
    quality: {
      analyze: (source_dir, reanalyze) => post('/api/quality/analyze', { source_dir, reanalyze: !!reanalyze }),
      analyzeStatus: (jobId, sourceDir) =>
        get(`/api/quality/analyze/${jobId}?source_dir=${encodeURIComponent(sourceDir)}`),
      triage:     (folder_path, status) => post('/api/quality/triage', { folder_path, status }),
      triageBulk: (folder_paths, status) => post('/api/quality/triage-bulk', { folder_paths, status }),
      staging:         (sourceDir)  => get(`/api/quality/staging?source_dir=${encodeURIComponent(sourceDir)}`),
      stagingFeatures: (folderPath) => get(`/api/quality/staging/features?folder_path=${encodeURIComponent(folderPath)}`),
      forRecording:    (recId)      => get(`/api/quality/recording/${recId}`),
      // Physically moves a show out of the queue into Backlog or Working.
      // Touches real files — see app/api/quality.py::move_out_of_queue for the
      // guards (allowlisted destinations, import-root check, never overwrites).
      move: (folder_path, destination) => post('/api/quality/move', { folder_path, destination }),
      browse: (path) => get(`/api/quality/browse?path=${encodeURIComponent(path || '')}`),
    },
  }
})()
