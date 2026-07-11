"""
scripts/cleanup_self_named_artists.py

The performer-model migration auto-seeded every Performer with a single member
Artist of the same name, so the Artists list became a 1:1 mirror of Performers
("Bill Evans Trio" the act spawned "Bill Evans Trio" the artist). Under the
refined model, Artists are OPTIONAL on a Performer and are only added for special
collaborations — so these self-named seeds are noise.

This removes each self-named Membership (artist.name == performer.name,
case-insensitive), leaving those Performers with zero members, then prunes any
Artist left with no memberships.

DRY-RUN by default — prints what it WOULD do and changes nothing.
Run for real with --commit:

    python3 scripts/cleanup_self_named_artists.py            # preview
    python3 scripts/cleanup_self_named_artists.py --commit   # execute
"""

import os
import sys

# Allow running from anywhere: put the repo root (parent of scripts/) on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.performer import Performer
from app.models.artist import Artist, Membership


def main(commit):
    app = create_app()
    with app.app_context():
        memberships = db.session.query(Membership).all()
        self_named = []
        for m in memberships:
            perf = db.session.get(Performer, m.performer_id)
            art  = db.session.get(Artist, m.artist_id)
            if perf and art and perf.name.strip().lower() == art.name.strip().lower():
                self_named.append((m, perf, art))

        print(f"Self-named memberships to remove: {len(self_named)} "
              f"(of {len(memberships)} total)")
        for _, perf, art in self_named:
            print(f"  - drop member '{art.name}' from performer '{perf.name}'")

        for m, _, _ in self_named:
            db.session.delete(m)
        db.session.flush()

        # Prune artists with no remaining memberships.
        orphans = [a for a in db.session.query(Artist).all() if not a.memberships]
        print(f"\nOrphan Artists to delete: {len(orphans)}")
        for a in orphans:
            print(f"  - delete artist '{a.name}'")
            db.session.delete(a)

        remaining_perfs = db.session.query(Performer).count()
        if commit:
            db.session.commit()
            print(f"\nCOMMITTED. Performers left intact: {remaining_perfs}. "
                  f"Artists remaining: {db.session.query(Artist).count()}.")
        else:
            db.session.rollback()
            print(f"\nDRY-RUN — nothing written. Re-run with --commit to apply. "
                  f"(Performers would be untouched: {remaining_perfs}.)")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv)
