"""
models/artist.py — Artist and alias models.

An Artist is an individual musician or band entity (canonical record).
ArtistAlias holds alternate/variant names that resolve to the canonical Artist.
"""

from datetime import datetime, timezone
from app.extensions import db


class Artist(db.Model):
    __tablename__ = "artist"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), unique=True, nullable=False)  # canonical name
    sort_name  = db.Column(db.String(255), nullable=True)                # e.g. "Fleck, Bela"
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    aliases          = db.relationship("ArtistAlias",          back_populates="artist",
                                       cascade="all, delete-orphan")
    performer_links  = db.relationship("PerformerArtist",      back_populates="artist")
    user_permissions = db.relationship("UserArtistPermission", back_populates="artist")

    def __repr__(self):
        return f"<Artist {self.name}>"


class ArtistAlias(db.Model):
    """
    Alternate or variant name for an Artist.
    Examples: misspellings, abbreviations, alternate orderings.
    """
    __tablename__ = "artist_alias"

    id        = db.Column(db.Integer, primary_key=True)
    artist_id = db.Column(db.Integer, db.ForeignKey("artist.id"), nullable=False)
    alias     = db.Column(db.String(255), nullable=False)

    # Relationship
    artist = db.relationship("Artist", back_populates="aliases")

    def __repr__(self):
        return f"<ArtistAlias '{self.alias}' → Artist {self.artist_id}>"
