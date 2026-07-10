"""
models/track.py — Track model.

A Track is a single audio file within a Recording. Track titles default
to "Track {n}" when unknown so the UI always has something to display.

`set` is a free string (nullable) — examples: "Set 1", "Set 2",
"Early Set", "Late Set", "Encore", "Soundcheck". No enumeration enforced
because collector conventions vary widely.

`file_path` is relative to the recording's folder_path. Full resolution:
  LIBRARY_ROOT / recording.folder_path / track.file_path
"""

from datetime import datetime, timezone
from app.extensions import db


class Track(db.Model):
    __tablename__ = "track"

    id           = db.Column(db.Integer, primary_key=True)
    recording_id = db.Column(db.Integer, db.ForeignKey("recording.id"), nullable=False)

    track_number = db.Column(db.Integer,     nullable=False)
    title        = db.Column(db.String(255), nullable=False)   # defaults to "Track {n}" on creation
    set          = db.Column(db.String(64),  nullable=True)    # e.g. "Set 1", "Encore"

    # Duration in seconds, read from FLAC metadata on ingestion
    duration     = db.Column(db.Integer, nullable=True)

    # Filename relative to recording folder — never sent to frontend directly
    file_path    = db.Column(db.String(512), nullable=False)

    # True when this track has been officially released by the copyright holder
    is_official  = db.Column(db.Boolean, nullable=False, default=False)

    # JSON array of flag strings. Valid values (see TRACK_FLAGS in app.js —
    # that frontend registry is the single source of truth):
    #   start_truncated, end_truncated, incomplete, unknown_title, banter,
    #   tuning, audience, medley, announcement, interview, introduction,
    #   band_intros
    flags        = db.Column(db.Text, nullable=True)

    # Composer / songwriter credit (optional, not in standard FLAC tags)
    songwriter   = db.Column(db.String(255), nullable=True)

    notes      = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    recording  = db.relationship("Recording", back_populates="tracks")
    play_logs  = db.relationship("PlayLog",   back_populates="track")

    def __repr__(self):
        return f"<Track {self.track_number}: {self.title} (recording={self.recording_id})>"
