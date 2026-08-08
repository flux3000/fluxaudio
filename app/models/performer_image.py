"""
models/performer_image.py — multiple images per Performer, one designated primary.

Replaces the single `Performer.image_ext` slot (2026-08-07). That column is left
in place rather than dropped: SQLite cannot drop a column without a full table
rebuild, and this project has already been burned once by rebuild-adjacent DDL
(the 07-22 stale-FK episode). It is now LEGACY — nothing reads it. The migration
backfills every populated `image_ext` into a row here as the primary image, and
deliberately does not move the file on disk, so a half-run migration can never
orphan a photo.

Files live beside the old one, at
    LIBRARY_ROOT/<sanitized performer name>/_images/<filename>
with `filename` stored explicitly rather than derived from the row id. Two
reasons: the id isn't known until after flush (so a derived name needs a second
write), and an explicit column lets the backfilled legacy file keep its original
`profile.jpg` name without special-casing it forever.

PRIMARY IS ENFORCED IN APP LOGIC, not a DB constraint. SQLite can't express
"at most one row per performer with is_primary = 1" as a partial unique index
portably through SQLAlchemy's create_all, so `set_primary()` clears siblings in
the same transaction and is the ONLY sanctioned way to set the flag.
"""

from datetime import datetime, timezone
from app.extensions import db


class PerformerImage(db.Model):
    __tablename__ = "performer_image"

    id           = db.Column(db.Integer, primary_key=True)
    performer_id = db.Column(db.Integer,
                             db.ForeignKey("performer.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    # Basename only, inside the performer's _images dir. Never a full path —
    # the library root is config, and recording paths are deliberately never
    # exposed to the frontend (see the file-obfuscation rule in CONTEXT.md).
    filename   = db.Column(db.String(255), nullable=False)
    ext        = db.Column(db.String(8), nullable=False)

    is_primary = db.Column(db.Boolean, nullable=False, default=False,
                           server_default="0")
    sort_order = db.Column(db.Integer, nullable=False, default=0,
                           server_default="0")

    # Where it came from — 'upload' today, 'commons'/'ai' once the fetch job
    # lands. Kept from the start so auto-fetched images are distinguishable
    # from ones a human chose, which is what makes "candidates, not commits"
    # enforceable later rather than a retrofit.
    origin     = db.Column(db.String(24), nullable=False, default="upload",
                           server_default="upload")
    caption    = db.Column(db.String(255), nullable=True)
    credit     = db.Column(db.String(255), nullable=True)

    # Upstream identifier for a fetched image — the Commons filename. Its whole
    # job is DEDUPLICATION: clicking "Find a free photo" a second time must
    # return a different photo, and comparing bytes or credit strings would be
    # a guess where the source filename is exact. NULL for uploads.
    source_ref = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime,
                           default=lambda: datetime.now(timezone.utc))

    performer = db.relationship("Performer", back_populates="images")

    def __repr__(self):
        return (f"<PerformerImage perf={self.performer_id} "
                f"{self.filename}{' PRIMARY' if self.is_primary else ''}>")


def set_primary(image):
    """
    Make `image` its performer's primary, clearing any sibling that held it.

    The single enforcement point for the one-primary rule — see the module
    docstring. Does NOT commit: callers decide the transaction boundary.
    """
    (db.session.query(PerformerImage)
     .filter(PerformerImage.performer_id == image.performer_id,
             PerformerImage.id != image.id,
             PerformerImage.is_primary.is_(True))
     .update({"is_primary": False}, synchronize_session=False))
    image.is_primary = True
    return image


def primary_for(performer_id):
    """
    The performer's primary image, or the oldest image if none is flagged.

    The fallback matters: deleting the primary must not leave a performer with
    photos but no face on the card. Callers get a usable image whenever one
    exists at all, and `is_primary` becomes a preference rather than a
    precondition.
    """
    rows = (db.session.query(PerformerImage)
            .filter(PerformerImage.performer_id == performer_id)
            .order_by(PerformerImage.is_primary.desc(),
                      PerformerImage.sort_order,
                      PerformerImage.id)
            .first())
    return rows
