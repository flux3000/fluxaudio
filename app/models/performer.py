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
    # External reference resources (databases, discographies) — see PerformerResource.
    resources    = db.relationship("PerformerResource", back_populates="performer",
                                   cascade="all, delete-orphan",
                                   order_by="PerformerResource.order")

    @property
    def artists(self):
        """Member Artists (people) in billing order."""
        return [m.artist for m in self.memberships]

    def __repr__(self):
        return f"<Performer {self.name}>"


class PerformerResource(db.Model):
    """
    An external reference for a Performer — a discography, tape database, or
    fan-maintained source of truth (e.g. the PMDB for Pat Metheny). Lives at the
    act level, not the person level.
    """
    __tablename__ = "performer_resource"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer, db.ForeignKey("performer.id"), nullable=False)
    label        = db.Column(db.String(255),  nullable=True)   # display name; falls back to URL
    url          = db.Column(db.String(1024), nullable=False)
    order        = db.Column(db.Integer, nullable=False, default=0)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    performer = db.relationship("Performer", back_populates="resources")

    def __repr__(self):
        return f"<PerformerResource {self.url} performer={self.performer_id}>"
