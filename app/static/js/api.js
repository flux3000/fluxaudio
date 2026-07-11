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

    // ── Artists ─────────────────────────────────────────────────────────────
    artists: {
      list:            ()                   => get('/api/artists/'),
      search:          (q)                  => get(`/api/artists/search?q=${encodeURIComponent(q)}`),
      allRecordings:   ()                   => get('/api/artists/all-recordings'),
      get:             (id)                 => get(`/api/artists/${id}`),
      recordings:      (id)                 => get(`/api/artists/${id}/recordings`),
      create:          (data)               => post('/api/artists/', data),
      update:          (id, data)           => put(`/api/artists/${id}`, data),
      allPerformers:   ()                   => get('/api/artists/performers'),
      linkPerformer:   (id, performer_id)   => post(`/api/artists/${id}/performers`, { performer_id }),
      unlinkPerformer: (id, performer_id)   => request('DELETE', `/api/artists/${id}/performers/${performer_id}`),
    },

    // ── Performances ─────────────────────────────────────────────────────────
    performances: {
      get:    (id)       => get(`/api/performances/${id}`),
      create: (data)     => post('/api/performances/', data),
      update: (id, data) => put(`/api/performances/${id}`, data),
    },

    // ── Recordings ───────────────────────────────────────────────────────────
    recordings: {
      get:        (id)       => get(`/api/recordings/${id}`),
      scan:       (folder)   => post('/api/recordings/scan', { folder_path: folder }),
      update:     (id, data) => put(`/api/recordings/${id}`, data),
      delete:     (id)       => request('DELETE', `/api/recordings/${id}`),
      writeTags:  (id)       => post(`/api/recordings/${id}/write-tags`),
      fileTags:   (id)       => get(`/api/recordings/${id}/tags`),
      reprocess:  (id)       => post(`/api/recordings/${id}/reprocess`),
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
      confirm:    (data)       => post('/api/ingest/confirm', data),
      aiAssist:       (payload) => post('/api/ingest/ai-assist', payload),
      aiAssistStatus: (jobId)   => get(`/api/ingest/ai-assist/${jobId}`),
      health:         (scan)    => post('/api/ingest/health', scan),
      batchScan:  (source_dir) => post('/api/ingest/batch-scan', { source_dir }),
    },
  }
})()
