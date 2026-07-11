"""
models/performance.py — Performance model.

A Performance is a unique live event: a specific performer on a specific
night at a specific place. Multiple recordings (different tapes, sources)
can exist for the same performance.

Location resolution order (app layer):
  1. performance.venue_id       — known venue record
  2. performance.city/state     — known city but no venue record
  3. event.venue_id / city      — fall back to parent event location
  4. Unknown
"""

from datetime import datetime, timezone
from app.extensions import db


class Performance(db.Model):
    __tablename__ = "performance"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer, db.ForeignKey("performer.id"), nullable=False)
    venue_id     = db.Column(db.Integer, db.ForeignKey("venue.id"),  nullable=True)
    event_id  = db.Column(db.Integer, db.ForeignKey("event.id"),  nullable=True)

    # Optional title for named performances (e.g. "The Last Waltz")
    title  = db.Column(db.String(255), nullable=True)

    # Sub-venue stage (e.g. "Omega Tent", "Main Stage") — used within events
    stage  = db.Column(db.String(128), nullable=True)

    # Date range — nullable integers support partial and multi-day dates
    start_year  = db.Column(db.Integer, nullable=True)
    start_month = db.Column(db.Integer, nullable=True)
    start_day   = db.Column(db.Integer, nullable=True)
    end_year    = db.Column(db.Integer, nullable=True)   # null unless multi-day
    end_month   = db.Column(db.Integer, nullable=True)
    end_day     = db.Column(db.Integer, nullable=True)

    # Location fallback when venue_id is null but city is known
    city    = db.Column(db.String(128), nullable=True)
    state   = db.Column(db.String(64),  nullable=True)
    country = db.Column(db.String(64),  nullable=True)

    notes      = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    performer  = db.relationship("Performer",  back_populates="performances")
    venue      = db.relationship("Venue",      back_populates="performances")
    event      = db.relationship("Event",      back_populates="performances")
    recordings = db.relationship("Recording",  back_populates="performance",
                                 cascade="all, delete-orphan")

    def __repr__(self):
        date = f"{self.start_year}-{self.start_month:02d}-{self.start_day:02d}" \
               if all([self.start_year, self.start_month, self.start_day]) else "unknown date"
        return f"<Performance {self.performer_id} @ {date}>"
