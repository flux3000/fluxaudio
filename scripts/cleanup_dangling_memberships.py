"""
scripts/cleanup_dangling_memberships.py

Removes Membership rows whose performer_id no longer points to an existing
Performer (a historical artifact — e.g. a performer removed before membership
cascade was in place). These crash the Artist page's performer list.

DRY-RUN by default; add --commit to apply.

    python3 scripts/cleanup_dangling_memberships.py
    python3 scripts/cleanup_dangling_memberships.py --commit
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.artist import Artist, Membership
from app.models.performer import Performer


def main(commit):
    app = create_app()
    with app.app_context():
        dangling = [m for m in db.session.query(Membership).all()
                    if db.session.get(Performer, m.performer_id) is None]
        print(f"Dangling memberships: {len(dangling)}")
        for m in dangling:
            a = db.session.get(Artist, m.artist_id)
            print(f"  - membership {m.id}: artist {a.name if a else '?'!r} → missing performer {m.performer_id}")
            db.session.delete(m)
        if commit:
            db.session.commit()
            print("COMMITTED.")
        else:
            db.session.rollback()
            print("DRY-RUN — nothing written. Re-run with --commit to apply.")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
