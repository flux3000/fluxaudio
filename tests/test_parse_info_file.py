"""
tests/test_parse_info_file.py — regression tests for parse_info_file's
track-listing extraction (app/utils/ingest.py).

Covers two bugs Ryan reported 2026-07-16 from a real CSNY info file:
1. A trailing "Notes:" section with its own numbered lines (e.g. "1 - Noise
   at 2:51 from the guys goofing around.") was being misparsed as tracks,
   clobbering the real tracks 1-4.
2. Multi-disc listings restart numbering at 1 each disc ("*** Disc Two ***"
   then "1. Song"); the raw numbers collided instead of coming out
   sequential across the whole recording.
"""

import tempfile
import os

from app.utils.ingest import parse_info_file


def _parse(text):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        return parse_info_file(path)
    finally:
        os.unlink(path)


CSNY_INFO_FILE = """Crosby, Stills, Nash & Young
Fillmore East, New York, NY
6/6/1970
SBD

*** Disc One (49:01) ***
    1. Suite: Judy Blue Eyes
    2. Blackbird
    3. On The Way Home
    4. Teach Your Children [1]
    5. Tell Me Why
    6. Triad
    7. Guinnevere
    8. Simple Man
*** Disc Two (37:57) ***
    1. King Midas In Reverse [2]
    2. The Loner>Cinnamon Girl>Down By The River
    3. Black Queen
    4. 4 + 20
    5. 49 Bye-Byes/America's Children [3]
    6. Love The One You're With (incomplete/fade-out)
*** Disc Three (73:17) ***
    1. Pre-Road Downs
    2. Long Time Gone
    3. Helplessly Hoping
    4. Ohio
    5. As I Come Of Age [4]
    6. Southern Man
    7. Carry On
    8. Woodstock
    9. Find The Cost Of Freedom [4]

Notes:
 1 - Noise at 2:51 & 2:54 from the guys goofing around.
 2 - Skip at 0:32 during Graham's ramblings.
 3 - Remains of static at 0:05-0:08 & 0:15-0:16
 4 - Sound levels have some low moments.
"""


def test_multidisc_tracklist_renumbers_sequentially():
    result = _parse(CSNY_INFO_FILE)
    tracks = result["tracks"]
    assert [t["number"] for t in tracks] == list(range(1, 24))
    assert len(tracks) == 23


def test_notes_section_excluded_from_tracks():
    result = _parse(CSNY_INFO_FILE)
    titles = [t["title"] for t in result["tracks"]]
    assert not any("goofing around" in t.lower() for t in titles)
    assert not any("ramblings" in t.lower() for t in titles)
    assert not any("static" in t.lower() for t in titles)
    assert not any("low moments" in t.lower() for t in titles)


def test_first_and_last_disc_titles_preserved():
    result = _parse(CSNY_INFO_FILE)
    tracks = result["tracks"]
    assert tracks[0]["title"] == "Suite: Judy Blue Eyes"
    assert tracks[-1]["title"] == "Find the Cost of Freedom [4]"
    # Track 9 is the first track of Disc Two — this is exactly the number
    # that used to collide with Disc One's own track 9 (there is none here,
    # since Disc One only has 8, but the offset math is what matters).
    assert tracks[8]["title"] == "King Midas in Reverse [2]"


def test_single_disc_no_notes_still_works():
    text = """Some Band
Some Venue
1/1/2000

1. First Song
2. Second Song
3. Third Song
"""
    result = _parse(text)
    tracks = result["tracks"]
    assert [t["number"] for t in tracks] == [1, 2, 3]
    assert [t["title"] for t in tracks] == ["First Song", "Second Song", "Third Song"]
