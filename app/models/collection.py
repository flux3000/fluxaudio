"""
models/collection.py — Collection and its Recording junction.

A Collection is an optional, user-defined grouping of Recordings (a curated
set, a box, a project). Many-to-many: a Recording can be in many Collections,
a Collection holds many Recordings.
"""

from datetime import datetime, timezone
from app.extensions import db


class Collection(db.Model):
    __tablename__ = "collection"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text,        nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    recording_links = db.relationship("CollectionRecording", back_populates="collection",
                                      cascade="all, delete-orphan",
                                      order_by="CollectionRecording.order")

    @property
    def recordings(self):
        return [l.recording for l in self.recording_links]

    def __repr__(self):
        return f"<Collection {self.name}>"


class CollectionRecording(db.Model):
    """Junction linking a Recording to a Collection (ordered)."""
    __tablename__ = "collection_recording"

    id            = db.Column(db.Integer, primary_key=True)
    collection_id = db.Column(db.Integer, db.ForeignKey("collection.id"), nullable=False)
    recording_id  = db.Column(db.Integer, db.ForeignKey("recording.id"),  nullable=False)
    order         = db.Column(db.Integer, nullable=False, default=0)
    added_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    collection = db.relationship("Collection", back_populates="recording_links")
    recording  = db.relationship("Recording")

    def __repr__(self):
        return f"<CollectionRecording collection={self.collection_id} recording={self.recording_id}>"
