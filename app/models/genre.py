"""
models/genre.py — Genre model.

Genre is a proper dimension (Ryan, 2026-08-02 — see the Genre design spec in
Context Library): its own table, referenced by a single FK from Performer.
Nothing may create a Genre implicitly — pickers only ever select from this
table, never write free text into it. That's the entire point of the design:
without the FK this becomes 164 spellings of "bluegrass".

One genre per performer (a strict FK, not M2M) — Ryan's explicit call. A
migration to a `performer_genre` join table is mechanical if that ever bites,
but not built now.
"""

from datetime import datetime, timezone
from app.extensions import db


class Genre(db.Model):
    __tablename__ = "genre"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))

    performers = db.relationship("Performer", back_populates="genre")

    def __repr__(self):
        return f"<Genre {self.name}>"
