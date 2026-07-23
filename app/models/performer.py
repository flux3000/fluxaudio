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

    # Personnel resolution mode new Performances of this act start in.
    # 'inherit' (default) = act roster/stints apply; 'explicit' = every show
    # starts with an empty lineup that must be entered per-show (rotating
    # billings like "Acoustic All-Stars" set this once and never fight the
    # default again). See app/utils/personnel.py. No manual UI control since
    # the 2026-07-22 Members/Guests redesign — the field and its automatic
    # behavior (new-performance default, case-5 auto-flip) are unchanged.
    default_personnel_mode = db.Column(db.String(16), nullable=False, default="inherit")

    # Profile picture (2026-07-22). The file itself lives on disk, not in the
    # DB — LIBRARY_ROOT/<sanitized name>/_images/profile<image_ext>, see
    # app/api/performers.py's image upload/serve routes. image_ext is
    # nullable — None means no picture uploaded. NOTE: the folder path is
    # derived from the Performer's CURRENT name at request time, not stored —
    # renaming an act without a corresponding folder rename would orphan an
    # already-uploaded picture. Matches how recording folders already behave
    # on a Performer rename (nothing renames those either); flagged here
    # rather than solved, per Ryan's scope-discipline call on this feature.
    image_ext = db.Column(db.String(8), nullable=True)

    # Latest AI Dossier research pass (2026-07-22) — a drafted biography +
    # suggested external resource links, JSON-encoded like
    # Recording.ai_research_json. Nothing is auto-applied: the Dossier UI
    # only ever proposes; Ryan copies the bio into `bio` and picks which
    # resource links to add, by hand — same "AI suggests, human approves"
    # rule as the ingest-side AI Assist (see the 2026-07-20/21 AI Assist
    # Refinement spec in Context Library — auto-apply was removed there
    # after it silently overwrote a recording with a wrong date).
    dossier_json = db.Column(db.Text, nullable=True)

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
        """
        Member Artists (people) in billing order — each person appears once,
        even if they have multiple Membership STINTS (e.g. Mickey Hart's two
        Dead tenures). `memberships` is already ordered by Membership.order,
        so taking the first occurrence per artist naturally applies the
        design doc's "dedupe by artist, earliest stint wins the order" rule.
        """
        seen = {}
        for m in self.memberships:
            seen.setdefault(m.artist_id, m.artist)
        return list(seen.values())

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
