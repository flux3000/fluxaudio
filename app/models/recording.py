"""
models/recording.py — Recording and fingerprint models.

A Recording is one captured version of a Performance — a specific tape,
source, or transfer. Multiple recordings can exist for the same performance
(e.g. an SBD and an AUD of the same show).

File path obfuscation: folder_path is relative to LIBRARY_ROOT and is
never exposed to the frontend. The API serves audio by track ID only.
"""

from datetime import datetime, timezone
from app.extensions import db


class Recording(db.Model):
    __tablename__ = "recording"

    id             = db.Column(db.Integer, primary_key=True)
    performance_id = db.Column(db.Integer, db.ForeignKey("performance.id"), nullable=False)

    # Optional seeder-given name for this specific version
    title          = db.Column(db.String(255), nullable=True)

    # Recording method: SBD, AUD, MTX (matrix), FM, etc.
    source          = db.Column(db.String(64),  nullable=True)

    # Optional qualifier appended to source in display and folder naming.
    # e.g. source="SBD" + source_modifier="Charlie Miller" → "SBD - Charlie Miller"
    source_modifier = db.Column(db.String(128), nullable=True)

    # Full transfer chain, e.g. "Nakamichi CM300 > Sony D5 DAT > CD > EAC > FLAC"
    lineage         = db.Column(db.Text,        nullable=True)

    # Letter grade: A+, A, A-, B+, B, B-, C — expanded in V2
    quality         = db.Column(db.String(4),   nullable=True)

    # False when recording is known to be missing tracks or cut
    is_complete    = db.Column(db.Boolean, nullable=False, default=True)

    # True when all or part of this recording has been officially released
    # (restricts streaming to other users in future multi-user builds)
    is_official    = db.Column(db.Boolean, nullable=False, default=False)

    # Path to recording folder, relative to LIBRARY_ROOT — never sent to frontend
    folder_path    = db.Column(db.String(512), nullable=False)

    # Original folder name as ingested (before any renaming)
    original_folder_name = db.Column(db.String(512), nullable=True)

    # Full text content of the accompanying info/text file
    info_file_content    = db.Column(db.Text, nullable=True)

    # Listener rating: 0–100 holistic score (show quality + experience).
    # Distinct from `quality` (technical recording grade).
    rating     = db.Column(db.Integer, nullable=True)

    notes      = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    performance   = db.relationship("Performance",          back_populates="recordings")
    fingerprints  = db.relationship("RecordingFingerprint", back_populates="recording",
                                    cascade="all, delete-orphan")
    tracks        = db.relationship("Track",                back_populates="recording",
                                    cascade="all, delete-orphan",
                                    order_by="Track.track_number")
    events        = db.relationship("RecordingEvent",       back_populates="recording",
                                    order_by="RecordingEvent.created_at")

    def __repr__(self):
        return f"<Recording {self.id} [{self.source}] performance={self.performance_id}>"


class RecordingFingerprint(db.Model):
    """
    Stores the content of integrity verification files (FFP, MD5)
    that accompany ROIO recordings. Used to verify file integrity over time.
    """
    __tablename__ = "recording_fingerprint"

    id               = db.Column(db.Integer, primary_key=True)
    recording_id     = db.Column(db.Integer, db.ForeignKey("recording.id"), nullable=False)

    # "ffp" (FLAC fingerprint) or "md5"
    fingerprint_type = db.Column(db.String(8), nullable=False)

    # Original filename as ingested
    filename         = db.Column(db.String(255), nullable=True)

    # Full file content
    content          = db.Column(db.Text, nullable=True)
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship
    recording = db.relationship("Recording", back_populates="fingerprints")

    def __repr__(self):
        return f"<RecordingFingerprint {self.fingerprint_type} recording={self.recording_id}>"
