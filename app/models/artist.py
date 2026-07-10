"""
models/artist.py — Artist and the Artist↔CanonicalArtist junction.

An Artist is the specific performing credit that took the stage for a given
performance — what goes in the FLAC ARTIST tag. It may be a solo act or any
ensemble ("Bill Evans", "Bill Evans Trio", "Sonny Rollins & Don Cherry
Quartet"). Artists are grouped for navigation under a CanonicalArtist via the
ArtistCanonical junction (many-to-many).

(2026-07-09 rename: this class was formerly `Performer`/table `performer`.
The grouping node formerly named `Artist`/`artist` is now CanonicalArtist —
see canonical_artist.py.)
"""

from datetime import datetime, timezone
from app.extensions import db


class Artist(db.Model):
    __tablename__ = "artist"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), nullable=False)  # performing credit (FLAC ARTIST tag)
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    canonical_links = db.relationship("ArtistCanonical", back_populates="artist",
                                      cascade="all, delete-orphan",
                                      order_by="ArtistCanonical.order")
    performances    = db.relationship("Performance",     back_populates="artist")

    def __repr__(self):
        return f"<Artist {self.name}>"


class ArtistCanonical(db.Model):
    """
    Junction linking an Artist (performing credit) to a CanonicalArtist
    (navigation grouping). `order` controls display sequence.

    (2026-07-09 rename: formerly `PerformerArtist`/table `performer_artist`,
    with columns performer_id/artist_id → artist_id/canonical_artist_id.)
    """
    __tablename__ = "artist_canonical"

    id                  = db.Column(db.Integer, primary_key=True)
    artist_id           = db.Column(db.Integer, db.ForeignKey("artist.id"),           nullable=False)
    canonical_artist_id = db.Column(db.Integer, db.ForeignKey("canonical_artist.id"), nullable=False)
    order               = db.Column(db.Integer, nullable=False, default=0)

    # Relationships
    artist           = db.relationship("Artist",          back_populates="canonical_links")
    canonical_artist = db.relationship("CanonicalArtist", back_populates="artist_links")

    def __repr__(self):
        return f"<ArtistCanonical artist={self.artist_id} canonical={self.canonical_artist_id}>"
