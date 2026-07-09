"""
scripts/batch_analyze.py — Run Librosa analysis on every track in the library.

Skips tracks that already have a current-version analysis row.
Continues past individual failures so one bad file doesn't stall the whole run.

Usage:
    cd ~/Workshop/dev
    env/bin/python3 scripts/batch_analyze.py

Options (env vars):
    REANALYZE=1   — reprocess even tracks that already have analysis data
    DRY_RUN=1     — list what would be processed, don't actually run Librosa
"""

import os
import sys
import time
from pathlib import Path

# ── Bootstrap Flask app context ───────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from app import create_app
from app.extensions import db
from app.models.recording import Recording
from app.models.track import Track
from app.models.track_analysis import TrackAnalysis
from app.utils.analysis import analyse_track, ANALYSIS_VERSION
from config import Config
from datetime import datetime, timezone

REANALYZE = os.environ.get("REANALYZE", "0") == "1"
DRY_RUN   = os.environ.get("DRY_RUN",   "0") == "1"

app = create_app()


def run():
    library_root = str(Config.LIBRARY_ROOT)

    with app.app_context():
        recordings = (
            db.session.query(Recording)
            .order_by(Recording.id)
            .all()
        )

        total_recordings = len(recordings)
        total_tracks     = sum(len(r.tracks) for r in recordings)

        print(f"\n{'='*60}")
        print(f"  Flux Audio — Batch Analysis")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        print(f"  Library root : {library_root}")
        print(f"  Recordings   : {total_recordings}")
        print(f"  Tracks       : {total_tracks}")
        print(f"  Reanalyze    : {'yes (forced)' if REANALYZE else 'no (skip existing)'}")
        print(f"  Dry run      : {'yes' if DRY_RUN else 'no'}")
        print(f"{'='*60}\n")

        if DRY_RUN:
            for rec in recordings:
                print(f"  [{rec.id}] {rec.folder_path}  ({len(rec.tracks)} tracks)")
            print(f"\nDry run complete — {total_tracks} tracks would be processed.")
            return

        t_start        = time.time()
        track_ok       = 0
        track_skipped  = 0
        track_failed   = 0
        rec_count      = 0

        for rec in recordings:
            rec_count += 1
            folder_abs = os.path.join(library_root, rec.folder_path)
            print(f"\n[{rec_count}/{total_recordings}] {rec.folder_path}")

            if not os.path.isdir(folder_abs):
                print(f"  ⚠  Folder missing on disk — skipping all {len(rec.tracks)} tracks")
                track_failed += len(rec.tracks)
                continue

            for track in rec.tracks:
                abs_path = os.path.join(folder_abs, track.file_path)

                # Check for existing analysis
                if not REANALYZE:
                    existing = (
                        db.session.query(TrackAnalysis)
                        .filter_by(track_id=track.id, analysis_version=ANALYSIS_VERSION)
                        .first()
                    )
                    if existing:
                        print(f"  ✓  skip  {track.file_path}")
                        track_skipped += 1
                        continue

                if not os.path.isfile(abs_path):
                    print(f"  ✗  missing  {track.file_path}")
                    track_failed += 1
                    continue

                print(f"  ⏳ analyzing  {track.file_path}", end="", flush=True)
                t0     = time.time()
                result = analyse_track(abs_path)
                elapsed = time.time() - t0

                if result is None:
                    print(f"  ← FAILED ({elapsed:.1f}s)")
                    track_failed += 1
                    continue

                # Upsert
                ta = db.session.query(TrackAnalysis).filter_by(track_id=track.id).first()
                if ta is None:
                    ta = TrackAnalysis(track_id=track.id)
                    db.session.add(ta)

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

                db.session.commit()   # commit per-track — avoids SQLite lock on long runs
                track_ok += 1
                print(f"  ← ok ({elapsed:.1f}s)")

        elapsed_total = time.time() - t_start
        mins, secs    = divmod(int(elapsed_total), 60)

        print(f"\n{'='*60}")
        print(f"  Done in {mins}m {secs}s")
        print(f"  Analyzed : {track_ok}")
        print(f"  Skipped  : {track_skipped}  (already current)")
        print(f"  Failed   : {track_failed}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    run()
