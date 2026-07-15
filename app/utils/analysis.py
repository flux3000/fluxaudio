"""
utils/analysis.py — Per-track audio analysis via Librosa.

Called by the reprocess endpoint. Each track is analysed independently;
results are upserted into the track_analysis table.

Metrics produced
────────────────
  rms_db               Average RMS level (dBFS)
  peak_db              True peak (dBFS)
  noise_floor_db       Estimated noise floor — 5th-percentile RMS frame (dBFS)
  dynamic_range_db     peak_db − noise_floor_db
  clipping_pct         Percentage of samples at full scale (|x| >= 0.999)
  dc_offset            Mean sample value (ideal = 0.0)
  spectral_centroid_hz Average spectral centroid (Hz) — perceptual brightness
  bpm                  Estimated tempo (librosa.beat.tempo)
  waveform_json        JSON {"min": [...], "max": [...]} — true per-bucket peak
                       envelope (signed, -1.0-1.0) at WAVEFORM_POINTS resolution,
                       covering the full track duration, for waveform display.
                       (v1 stored a single mirrored RMS-magnitude array instead —
                       the frontend still renders that shape for tracks that
                       haven't been re-analysed since the v2 bump.)
"""

import json
import logging
import numpy as np

log = logging.getLogger(__name__)

ANALYSIS_VERSION = "2"   # bumped: waveform_json is now a real min/max peak envelope
WAVEFORM_POINTS  = 2000  # resolution of waveform envelope — was 300 (v1)


def _to_db(amplitude, min_db=-80.0):
    """Convert linear amplitude to dBFS, clamped to min_db."""
    if amplitude <= 0:
        return min_db
    return float(np.clip(20.0 * np.log10(amplitude), min_db, 0.0))


def analyse_track(abs_path):
    """
    Load a FLAC (or any soundfile-compatible format) and return a dict of
    analysis metrics. Returns None and logs a warning on failure.

    Uses sr=None to preserve the native sample rate (avoids costly resample).
    mono=True collapses channels for RMS/spectral analysis.
    """
    try:
        import librosa
    except ImportError:
        log.error("librosa not installed — pip install librosa soundfile")
        return None

    # ── Header metadata (mutagen — no decode) ────────────────────────────────
    sample_rate_hz = None
    bit_depth      = None
    bitrate_kbps   = None
    try:
        import mutagen
        mf = mutagen.File(abs_path)
        if mf and hasattr(mf, 'info'):
            info = mf.info
            sample_rate_hz = getattr(info, 'sample_rate', None)
            bit_depth      = getattr(info, 'bits_per_sample', None)
            # For lossy formats mutagen gives bitrate in bps
            br = getattr(info, 'bitrate', None)
            if br and not bit_depth:
                bitrate_kbps = round(br / 1000)
    except Exception as e:
        log.warning("mutagen header read failed for %s: %s", abs_path, e)

    try:
        # Load at native SR, mono for analysis
        y, sr = librosa.load(abs_path, sr=None, mono=True)
    except Exception as e:
        log.warning("Could not load %s: %s", abs_path, e)
        return None

    # sr from librosa is authoritative (int); use as fallback if mutagen missed it
    if not sample_rate_hz and sr:
        sample_rate_hz = int(sr)

    if len(y) == 0:
        log.warning("Empty audio: %s", abs_path)
        return None

    # ── RMS & peak ────────────────────────────────────────────────────────────
    # Frame-level RMS (hop ~23 ms at 44.1 kHz) gives us both per-frame detail
    # and a rolling view for the waveform envelope.
    hop_length = 1024
    frame_rms  = librosa.feature.rms(y=y, hop_length=hop_length)[0]  # shape: (n_frames,)

    rms_mean   = float(np.mean(frame_rms))
    peak_amp   = float(np.max(np.abs(y)))
    rms_db     = _to_db(rms_mean)
    peak_db    = _to_db(peak_amp)

    # ── Noise floor ───────────────────────────────────────────────────────────
    # 5th-percentile frame RMS — quietest sustained sections ≈ noise floor
    noise_amp      = float(np.percentile(frame_rms, 5))
    noise_floor_db = _to_db(noise_amp)
    dynamic_range  = round(peak_db - noise_floor_db, 1)

    # ── Clipping ──────────────────────────────────────────────────────────────
    clipped     = np.sum(np.abs(y) >= 0.999)
    clipping_pct = round(float(clipped) / len(y) * 100.0, 4)

    # ── DC offset ─────────────────────────────────────────────────────────────
    dc_offset = round(float(np.mean(y)), 6)

    # ── Spectral analysis (STFT shared by centroid + cutoff) ─────────────────
    # n_fft=4096 gives ~10 Hz bin resolution at 44.1 kHz — fine enough to
    # pinpoint the MP3 brick-wall cutoff without being too slow.
    S = np.abs(librosa.stft(y, n_fft=4096)) ** 2   # power spectrogram
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)

    # Centroid (perceptual brightness)
    centroid = librosa.feature.spectral_centroid(S=S, sr=sr)
    spectral_centroid_hz = round(float(np.mean(centroid)), 1)

    # ── Spectral cutoff ───────────────────────────────────────────────────────
    # Average power spectrum across time, then find the highest frequency bin
    # still carrying meaningful energy (> −40 dB relative to spectral peak).
    # A hard wall well below Nyquist is the classic lossy-transcode fingerprint.
    avg_power   = S.mean(axis=1)                       # shape: (n_fft/2+1,)
    peak_power  = avg_power.max()
    threshold   = peak_power * (10 ** (-40.0 / 10.0)) # −40 dB
    active_bins = np.where(avg_power > threshold)[0]
    spectral_cutoff_hz = round(float(freqs[active_bins[-1]])) if len(active_bins) else None

    # ── BPM ───────────────────────────────────────────────────────────────────
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = round(float(np.atleast_1d(tempo)[0]), 1)
    except Exception:
        bpm = None

    # ── Waveform envelope (signed min/max peaks, -1..1) ───────────────────────
    # Split the raw signal itself (not the already-smoothed frame_rms) into
    # WAVEFORM_POINTS buckets and keep each bucket's true min and max sample —
    # a real bipolar peak envelope rather than a mirrored RMS average. This is
    # what actually looks "punchy" instead of smooth/rounded.
    n = len(y)
    if n >= WAVEFORM_POINTS:
        buckets = np.array_split(y, WAVEFORM_POINTS)
        wf_min = np.array([b.min() for b in buckets])
        wf_max = np.array([b.max() for b in buckets])
    else:
        pad = np.zeros(WAVEFORM_POINTS - n)
        wf_min = np.concatenate([y, pad])
        wf_max = np.concatenate([y, pad])

    norm = peak_amp or 1.0
    waveform = {
        "min": [round(float(v) / norm, 4) for v in wf_min],
        "max": [round(float(v) / norm, 4) for v in wf_max],
    }

    return {
        "sample_rate_hz":       sample_rate_hz,
        "bit_depth":            bit_depth,
        "bitrate_kbps":         bitrate_kbps,
        "rms_db":               round(rms_db, 1),
        "peak_db":              round(peak_db, 1),
        "noise_floor_db":       round(noise_floor_db, 1),
        "dynamic_range_db":     dynamic_range,
        "clipping_pct":         clipping_pct,
        "dc_offset":            dc_offset,
        "spectral_centroid_hz": spectral_centroid_hz,
        "spectral_cutoff_hz":   spectral_cutoff_hz,
        "bpm":                  bpm,
        "waveform_json":        json.dumps(waveform),
        "analysis_version":     ANALYSIS_VERSION,
    }


def analyse_recording(recording, library_root, db_session):
    """
    Run analyse_track() on every track in a Recording and upsert results
    into the track_analysis table. Returns (n_ok, errors).
    """
    from app.models.track_analysis import TrackAnalysis
    from datetime import datetime, timezone

    n_ok   = 0
    errors = []

    for track in recording.tracks:
        abs_path = f"{library_root}/{recording.folder_path}/{track.file_path}"
        log.info("Analysing %s", abs_path)

        result = analyse_track(abs_path)
        if result is None:
            errors.append((track.file_path, "Analysis failed"))
            continue

        # Upsert — replace existing row if present
        ta = db_session.query(TrackAnalysis).filter_by(track_id=track.id).first()
        if ta is None:
            ta = TrackAnalysis(track_id=track.id)
            db_session.add(ta)

        ta.sample_rate_hz       = result["sample_rate_hz"]
        ta.bit_depth            = result["bit_depth"]
        ta.bitrate_kbps         = result["bitrate_kbps"]
        ta.rms_db               = result["rms_db"]
        ta.peak_db              = result["peak_db"]
        ta.noise_floor_db       = result["noise_floor_db"]
        ta.dynamic_range_db     = result["dynamic_range_db"]
        ta.clipping_pct         = result["clipping_pct"]
        ta.dc_offset            = result["dc_offset"]
        ta.spectral_centroid_hz = result["spectral_centroid_hz"]
        ta.spectral_cutoff_hz   = result["spectral_cutoff_hz"]
        ta.bpm                  = result["bpm"]
        ta.waveform_json        = result["waveform_json"]
        ta.analysis_version     = result["analysis_version"]
        ta.analyzed_at          = datetime.now(timezone.utc)

        # Commit after each track — avoids accumulating 25 pending rows
        # and hitting the SQLite lock timeout on long-running analysis sessions
        db_session.commit()
        n_ok += 1

    return n_ok, errors
