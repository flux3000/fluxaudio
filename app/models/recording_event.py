"""
models/recording_event.py — Irrevocable recording event log.

Rows are INSERT-ONLY. Never update or delete entries here.
Every significant action on a recording gets a row:
  - ingested       : recording first added to library
  - tags_written   : FLAC tags written to all files in recording
  - metadata_updated: recording or track metadata changed in DB

This is the MVP seed of the V2 full audit/lineage system.
"""

from datetime import datetime, timezone
from app.extensions import db


class RecordingEvent(db.Model):
    __tablename__ = "recording_event"

    id           = db.Column(db.Integer, primary_key=True)
    recording_id = db.Column(db.Integer, db.ForeignKey("recording.id"), nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"),      nullable=False)

    # ingested | tags_written | metadata_updated
    event_type   = db.Column(db.String(32),  nullable=False)
    note         = db.Column(db.Text,        nullable=True)
    created_at   = db.Column(db.DateTime,    nullable=False,
                             default=lambda: datetime.now(timezone.utc))

    # Relationships (read-only context — no cascade, rows are permanent)
    recording = db.relationship("Recording", back_populates="events")
    user      = db.relationship("User")

    def __repr__(self):
        return f"<RecordingEvent {self.event_type} recording={self.recording_id} by user={self.user_id}>"
