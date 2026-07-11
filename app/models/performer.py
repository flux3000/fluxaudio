"""
models/performer.py — Performer (a performing act) and its member ordering.

A Performer is the billed act: the name that goes on the FLAC ARTIST tag and
that headlines a browse row — "Béla Fleck and Jerry Douglas", "Grateful Dead",
"Béla Fleck". Its members are Artists (people), linked via Membership.

A Performer must have at least one member. A solo act's single member is just
the person of the same name.

(2026-07-11 remodel: Performer = the act; Artist = a person; CanonicalArtist
is gone — aggregation of "everything by X" hangs off the person via Membership.)
"""

from datetime import datetime, timezone
from app.extensions import db


class Performer(db.Model):
    __tablename__ = "performer"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(255), nullable=False)   # billed act name
    sort_name  = db.Column(db.String(255), nullable=True)
    bio        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Ordered members (people). Cascade so deleting a Performer clears its links.
    memberships  = db.relationship("Membership", back_populates="performer",
                                   cascade="all, delete-orphan",
                                   order_by="Membership.order")
    performances = db.relationship("Performance", back_populates="performer")

    @property
    def artists(self):
        """Member Artists (people) in billing order."""
        return [m.artist for m in self.memberships]

    def __repr__(self):
        return f"<Performer {self.name}>"
