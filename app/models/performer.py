"""
models/performer.py — Performer, junction, and alias models.

A Performer is the specific grouping of artists that took the stage for a
given performance. It may be a solo act (one artist) or any ensemble.

Examples:
  "Bela Fleck and Jerry Douglas"         → two artists in performer_artist
  "Bela Fleck and the Flecktones"        → one artist + alias on performer
  "Bela Fleck, Sam Bush, Jerry Douglas"  → three artists in performer_artist
"""

from datetime import datetime, timezone
from app.extensions import db


class Performer(db.Model):
    __tablename__ = "performer"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), nullable=False)  # display name for the grouping
    bio        = db.Column(db.Text,        nullable=True)   # notes about this collaboration
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    artist_links = db.relationship("PerformerArtist", back_populates="performer",
                                   cascade="all, delete-orphan",
                                   order_by="PerformerArtist.order")
    aliases      = db.relationship("PerformerAlias",  back_populates="performer",
                                   cascade="all, delete-orphan")
    performances = db.relationship("Performance",     back_populates="performer")

    def __repr__(self):
        return f"<Performer {self.name}>"


class PerformerArtist(db.Model):
    """
    Junction table linking a Performer to its constituent Artists.
    `order` controls display sequence within the performer name.
    """
    __tablename__ = "performer_artist"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer, db.ForeignKey("performer.id"), nullable=False)
    artist_id    = db.Column(db.Integer, db.ForeignKey("artist.id"),    nullable=False)
    order        = db.Column(db.Integer, nullable=False, default=0)

    # Relationships
    performer = db.relationship("Performer", back_populates="artist_links")
    artist    = db.relationship("Artist",    back_populates="performer_links")

    def __repr__(self):
        return f"<PerformerArtist performer={self.performer_id} artist={self.artist_id}>"


class PerformerAlias(db.Model):
    """
    Alternate name for a Performer grouping.
    Example: "The Flecktones" as alias for "Bela Fleck and the Flecktones"
    """
    __tablename__ = "performer_alias"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer, db.ForeignKey("performer.id"), nullable=False)
    alias        = db.Column(db.String(255), nullable=False)

    # Relationship
    performer = db.relationship("Performer", back_populates="aliases")

    def __repr__(self):
        return f"<PerformerAlias '{self.alias}' → Performer {self.performer_id}>"
