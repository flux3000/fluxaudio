"""
merge_canonical_artists.py — Fold "leader + ensemble" performers into their
canonical artist so the sidebar nav shows one root entry per real artist.

Background: ingest auto-creates a 1:1 canonical Artist for every distinct
artist_name string typed at ingest time (see app/api/ingest.py). That means
"Bill Evans Trio", "Bill Evans Trio + Friends", and "Bill Evans Trio With
Warne Marsh" each got their own canonical Artist row instead of being linked
as Performers under the existing canonical "Bill Evans" artist.

This script is intentionally conservative: it only merges a case that's in
MERGES below (approved by Ryan on 2026-07-08), rather than guessing at every
"leader + ensemble" name in the library. Joint-billing acts with no existing
separate canonical artist (Bob Marley & The Wailers, Alison Krauss & Union
Station, Chick Corea Acoustic Quartet, Stan Getz Sextet, Sonny Rollins & Don
Cherry Quartet, Dr. John with The Meters) are deliberately left alone.

For each (source_name, target_name) pair:
  1. Find the source canonical Artist and its 1:1 linked Performer.
  2. Find the target canonical Artist.
  3. Repoint the PerformerArtist link from source -> target artist_id.
  4. Delete the now-orphaned source Artist row.

The Performer's own display name (e.g. "Bill Evans Trio") is untouched —
only the canonical grouping changes. Recordings, tracks, and FLAC tags are
unaffected.

Run once:
    cd ~/Workshop/dev/fluxaudio
    python3 scripts/merge_canonical_artists.py
    python3 scripts/merge_canonical_artists.py --dry-run   # preview only
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import create_app
from app.extensions import db
from app.models.artist import Artist
from app.models.performer import Performer, PerformerArtist

MERGES = [
    ("Bill Evans Trio",                "Bill Evans"),
    ("Bill Evans Trio + Friends",      "Bill Evans"),
    ("Bill Evans Trio With Warne Marsh", "Bill Evans"),
    ("Sonny Rollins Sextet",           "Sonny Rollins"),
    ("D'Angelo & the Soultronics",     "D'Angelo"),
]


def merge_one(source_name, target_name, dry_run):
    source = db.session.query(Artist).filter_by(name=source_name).first()
    target = db.session.query(Artist).filter_by(name=target_name).first()

    if not source:
        print(f"  SKIP — no canonical artist named {source_name!r} (already merged?)")
        return
    if not target:
        print(f"  SKIP — target canonical artist {target_name!r} not found")
        return

    links = db.session.query(PerformerArtist).filter_by(artist_id=source.id).all()
    if not links:
        print(f"  SKIP — {source_name!r} has no linked performers")
        return

    for link in links:
        performer = db.session.get(Performer, link.performer_id)
        already_linked = db.session.query(PerformerArtist).filter_by(
            performer_id=link.performer_id, artist_id=target.id
        ).first()
        if already_linked:
            print(f"  {performer.name!r} already linked to {target_name!r} — dropping stale link to {source_name!r}")
            if not dry_run:
                db.session.delete(link)
        else:
            print(f"  Relinking performer {performer.name!r}: {source_name!r} -> {target_name!r}")
            if not dry_run:
                link.artist_id = target.id

    if not dry_run:
        db.session.flush()
        remaining = db.session.query(PerformerArtist).filter_by(artist_id=source.id).count()
        if remaining == 0:
            print(f"  Deleting now-orphaned canonical artist {source_name!r} (id={source.id})")
            db.session.delete(source)
        else:
            print(f"  NOT deleting {source_name!r} — {remaining} link(s) still reference it")
    else:
        print(f"  [dry-run] Would delete canonical artist {source_name!r} (id={source.id})")


def main():
    dry_run = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        print(f"{'DRY RUN — ' if dry_run else ''}Merging {len(MERGES)} canonical artist pair(s)...\n")
        for source_name, target_name in MERGES:
            print(f"{source_name!r} -> {target_name!r}")
            merge_one(source_name, target_name, dry_run)
            print()

        if dry_run:
            db.session.rollback()
            print("Dry run complete — no changes committed.")
        else:
            db.session.commit()
            print("Done — changes committed.")


if __name__ == "__main__":
    main()
