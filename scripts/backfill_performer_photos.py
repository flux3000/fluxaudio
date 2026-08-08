"""
scripts/backfill_performer_photos.py — freely-licensed photos via Commons.

Walks performers that have a MusicBrainz match but no photo, follows their
Wikidata link to a P18 image on Wikimedia Commons, and stores it with its
licence and attribution. See app/utils/commons.py for why Commons and only
Commons.

Run AFTER backfill_musicbrainz.py — the Wikidata link comes from the MusicBrainz
match, so an unmatched performer has nothing to follow and is skipped.

Expect a LOW HIT RATE and don't read it as failure. A photo requires the act to
have a Wikidata entry AND that entry to carry a P18 image. Well-known acts
usually do; the long tail (85 of 164 performers here have a single recording)
usually doesn't. Coverage will skew hard toward the head of the library, which
is also where it matters most.

Skips performers that already have any photo, so an uploaded picture is never
displaced. The fetched image becomes primary only when the act had none.

    python3 scripts/backfill_performer_photos.py --dry-run
    python3 scripts/backfill_performer_photos.py --limit 10
    python3 scripts/backfill_performer_photos.py
"""

import sys
import time
import argparse
import secrets
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import create_app
from app.extensions import db
from app.models.performer import Performer
from app.models.performer_image import PerformerImage, set_primary
from app.utils import commons
from app.utils.ingest import _sanitize_path
from config import Config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        # Only matched performers can be followed, and only photo-less ones
        # should be touched — an existing picture is a human's choice.
        todo = [p for p in db.session.query(Performer)
                                     .filter(Performer.mbid.isnot(None))
                                     .order_by(Performer.name).all()
                if not p.images]
        if args.limit:
            todo = todo[:args.limit]

        unmatched = db.session.query(Performer).filter(Performer.mbid.is_(None)).count()

        print(f"\nPerformers with a MusicBrainz match and no photo: {len(todo)}")
        if unmatched:
            print(f"({unmatched} more have no MusicBrainz match — "
                  f"run backfill_musicbrainz.py first to include them)")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'write'}\n")

        if args.dry_run:
            for p in todo[:40]:
                print(f"  would try: {p.name}")
            if len(todo) > 40:
                print(f"  … and {len(todo) - 40} more")
            return 0

        library_root = str(Config.LIBRARY_ROOT)
        found = missed = failed = 0
        started = time.time()

        for n, p in enumerate(todo, 1):
            try:
                photo = commons.find_photo_for_performer(p)   # no exclude: these have no photos
            except Exception as e:                            # noqa: BLE001
                print(f"  [{n}/{len(todo)}] ! {p.name}: {e}")
                failed += 1
                continue

            if not photo:
                print(f"  [{n}/{len(todo)}] · {p.name}")
                missed += 1
                continue

            images_dir = Path(library_root) / _sanitize_path(p.name) / "_images"
            images_dir.mkdir(parents=True, exist_ok=True)
            fname = f"img_{secrets.token_hex(6)}{photo['ext']}"
            (images_dir / fname).write_bytes(photo["data"])

            img = PerformerImage(performer_id=p.id, filename=fname,
                                 ext=photo["ext"], origin="commons",
                                 credit=photo["credit"], caption=photo.get("caption"),
                                 source_ref=photo.get("source_ref"))
            db.session.add(img)
            db.session.flush()
            set_primary(img)          # only reached when the act had no photos
            db.session.commit()       # per performer — Ctrl-C loses nothing
            found += 1
            print(f"  [{n}/{len(todo)}] ✓ {p.name} — {photo['credit']}")

        print(f"\n  found {found} · none available {missed} · errors {failed}")
        print(f"  {(time.time() - started) / 60:.1f} min\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
