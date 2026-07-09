"""
models/venue.py — Venue and alias models.

A Venue is a physical location. Historical name changes and formatting
variants are both handled via VenueAlias — the canonical record stays stable
while aliases resolve searches across different eras.
"""

from datetime import datetime, timezone
from app.extensions import db


class Venue(db.Model):
    __tablename__ = "venue"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), nullable=False)  # canonical name
    city       = db.Column(db.String(128), nullable=True)
    state      = db.Column(db.String(64),  nullable=True)
    country    = db.Column(db.String(64),  nullable=True)
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    aliases      = db.relationship("VenueAlias",   back_populates="venue",
                                   cascade="all, delete-orphan")
    performances = db.relationship("Performance",  back_populates="venue")
    events       = db.relationship("Event",        back_populates="venue")

    def __repr__(self):
        return f"<Venue {self.name}, {self.city}>"


class VenueAlias(db.Model):
    """
    Alternate name for a Venue.

    alias_type:
      "variant"   — formatting/spelling difference, no date significance
                    e.g. "The Fillmore" vs "Fillmore"
      "historical" — substantive name change; optionally bounded by year_from/year_to
                    e.g. "Carousel Ballroom" (1968–1969) → "Fillmore West" (1969–1971)
    """
    __tablename__ = "venue_alias"

    id         = db.Column(db.Integer, primary_key=True)
    venue_id   = db.Column(db.Integer, db.ForeignKey("venue.id"), nullable=False)
    alias      = db.Column(db.String(255), nullable=False)
    alias_type = db.Column(db.String(16),  nullable=False, default="variant")  # variant | historical
    year_from  = db.Column(db.Integer, nullable=True)   # inclusive start year for historical aliases
    year_to    = db.Column(db.Integer, nullable=True)   # inclusive end year for historical aliases

    # Relationship
    venue = db.relationship("Venue", back_populates="aliases")

    def __repr__(self):
        return f"<VenueAlias '{self.alias}' ({self.alias_type}) → Venue {self.venue_id}>"
