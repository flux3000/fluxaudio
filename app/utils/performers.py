"""
utils/performers.py — resolve/create Performers (acts) and their member Artists.

Shared by ingest, the Add/Edit forms, and performance reassignment. A Performer
must always have >=1 member; a brand-new Performer auto-seeds one member matching
its own name (e.g. "Grateful Dead" the act gets Artist "Grateful Dead"), which the
user enriches with real people later.
"""

from sqlalchemy import func
from app.extensions import db
from app.models.performer import Performer
from app.models.artist import Artist, Membership


def resolve_or_create_artist(name):
    """Find an Artist (person) by name (case-insensitive) or create it."""
    name = (name or "").strip()
    if not name:
        return None
    a = db.session.query(Artist).filter(func.lower(Artist.name) == name.lower()).first()
    if not a:
        a = Artist(name=name)
        db.session.add(a)
        db.session.flush()
    return a


def set_performer_members(performer, member_names):
    """
    Replace a performer's membership with the given ordered artist names
    (resolve/create each person). Always keeps >=1 member — falls back to the
    performer's own name when the list is empty.
    """
    names, seen = [], set()
    for n in (member_names or []):
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    if not names:
        names = [performer.name]

    db.session.query(Membership).filter_by(performer_id=performer.id).delete(
        synchronize_session=False)
    db.session.flush()
    for i, mn in enumerate(names):
        artist = resolve_or_create_artist(mn)
        db.session.add(Membership(performer_id=performer.id, artist_id=artist.id, order=i))
    db.session.flush()


def resolve_or_create_performer(name, member_names=None):
    """
    Find a Performer by name (case-insensitive) or create it. On create, seed
    members from member_names (or a single member matching the name if none).
    Does NOT change an existing performer's members — use set_performer_members.
    """
    name = (name or "").strip()
    performer = db.session.query(Performer).filter(
        func.lower(Performer.name) == name.lower()).first()
    if performer:
        return performer
    performer = Performer(name=name)
    db.session.add(performer)
    db.session.flush()
    set_performer_members(performer, member_names or [name])
    return performer
