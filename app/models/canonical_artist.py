"""
models/canonical_artist.py — CanonicalArtist model.

A CanonicalArtist is a navigation/grouping node in the sidebar. It is NOT a
metadata element and never lands in FLAC tags. One canonical groups one or
more performing Artists via the ArtistCanonical junction (e.g. canonical
"Bill Evans" groups "Bill Evans", "Bill Evans Trio", ...).

(2026-07-09 rename: formerly `Artist`/table `artist`.)
"""

from datetime import datetime, timezone
from app.extensions import db


class CanonicalArtist(db.Model):
    __tablename__ = "canonical_artist"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), unique=True, nullable=False)  # canonical name
    sort_name  = db.Column(db.String(255), nullable=True)                # e.g. "Fleck, Bela"
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    artist_links     = db.relationship("ArtistCanonical",      back_populates="canonical_artist")
    user_permissions = db.relationship("UserArtistPermission", back_populates="canonical_artist")

    def __repr__(self):
        return f"<CanonicalArtist {self.name}>"
