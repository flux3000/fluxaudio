"""
models/artist.py — Artist (a person/musician) and the Membership junction.

An Artist is an individual musician (Béla Fleck, Jerry Douglas, Sandip Burman).
Artists are members of one or more Performers (acts) via Membership. Browsing
"everything by Béla Fleck" = every Performer he is a member of.

(2026-07-11 remodel: Artist now means a PERSON. The old Artist — a performing
act — is now Performer; the old CanonicalArtist grouping is gone.)
"""

from datetime import datetime, timezone
from app.extensions import db


class Artist(db.Model):
    __tablename__ = "artist"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), nullable=False)   # person name
    sort_name  = db.Column(db.String(255), nullable=True)
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    memberships = db.relationship("Membership", back_populates="artist",
                                  cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Artist {self.name}>"


class Membership(db.Model):
    """Links an Artist (person) to a Performer (act). Ordered many-to-many."""
    __tablename__ = "membership"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer, db.ForeignKey("performer.id"), nullable=False)
    artist_id    = db.Column(db.Integer, db.ForeignKey("artist.id"),    nullable=False)
    order        = db.Column(db.Integer, nullable=False, default=0)

    performer = db.relationship("Performer", back_populates="memberships")
    artist    = db.relationship("Artist",    back_populates="memberships")

    def __repr__(self):
        return f"<Membership performer={self.performer_id} artist={self.artist_id}>"
