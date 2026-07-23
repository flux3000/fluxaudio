"""
tests/test_ingest_utils.py — move_to_library()'s Move-behavior cleanup
(2026-07-23): once a recording folder is moved into the library, its
immediate parent (the "Performer Name" staging folder in a typical Bulk
Import layout) should be removed too if left empty — but only ONE level up,
only ever for behavior="move" (never "copy"), and never anything that isn't
unambiguously a disposable staging folder. Pure filesystem logic, no DB/app
context needed.
"""

from pathlib import Path

from app.utils.ingest import move_to_library


def _make_show(parent, show_name="1994-07-30 Show", filename="track.flac"):
    show = parent / show_name
    show.mkdir(parents=True)
    (show / filename).write_bytes(b"x" * 100)
    return show


def test_move_deletes_empty_parent_staging_folder(tmp_path):
    """The common Bulk Import case: Import/Performer Name/Show Folder/ — once
    the Show Folder is moved (its only content), the now-empty "Performer
    Name" folder should go too. Its own parent (Import) is left alone —
    cleanup is ONE level only, not a climb toward the root."""
    import_dir = tmp_path / "Import"
    performer_dir = import_dir / "Performer Name"
    show = _make_show(performer_dir)
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show), str(lib), "Performer Name", "1994-07-30 Show",
                     behavior="move")

    assert not show.exists()
    assert not performer_dir.exists()
    assert import_dir.exists()   # one level only — not also removed


def test_move_keeps_nonempty_parent(tmp_path):
    """A sibling show folder still under "Performer Name" means it's not
    empty — must survive."""
    performer_dir = tmp_path / "Performer Name"
    show1 = _make_show(performer_dir, "Show 1")
    _make_show(performer_dir, "Show 2")
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show1), str(lib), "Performer Name", "Show 1", behavior="move")

    assert not show1.exists()
    assert performer_dir.exists()
    assert (performer_dir / "Show 2").exists()


def test_move_ignores_ds_store_when_checking_empty(tmp_path):
    """A folder Finder has visited almost always has a stray .DS_Store —
    that alone shouldn't block cleanup, and the junk file itself should be
    removed along with the folder."""
    performer_dir = tmp_path / "Performer Name"
    show = _make_show(performer_dir)
    (performer_dir / ".DS_Store").write_bytes(b"junk")
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show), str(lib), "Performer Name", "1994-07-30 Show",
                     behavior="move")

    assert not performer_dir.exists()


def test_copy_never_touches_source(tmp_path):
    """behavior="copy" must never remove the source show folder OR its
    parent, regardless of emptiness — copy's whole contract is "source stays
    untouched."""
    performer_dir = tmp_path / "Performer Name"
    show = _make_show(performer_dir)
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show), str(lib), "Performer Name", "1994-07-30 Show",
                     behavior="copy")

    assert show.exists()
    assert (show / "track.flac").exists()
    assert performer_dir.exists()


def test_move_never_deletes_protected_dir_name(tmp_path):
    """Even if a staging show folder's immediate parent happens to be named
    like a standard macOS user folder (Desktop, Downloads, ...), it must
    never be auto-deleted just because it's empty afterward — this cleanup
    is for disposable staging folders, not general-purpose ones."""
    desktop = tmp_path / "Desktop"
    show = _make_show(desktop)
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show), str(lib), "Desktop", "1994-07-30 Show", behavior="move")

    assert not show.exists()
    assert desktop.exists()   # protected by name, even though now empty


def test_move_never_deletes_home_directory(tmp_path, monkeypatch):
    """If the show folder's parent happens to resolve to the user's home
    directory (e.g. a show folder dropped directly in $HOME), that must
    never be removed even if briefly empty."""
    home = tmp_path / "home_stand_in"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    show = _make_show(home)
    lib = tmp_path / "lib"; lib.mkdir()

    move_to_library(str(show), str(lib), "whoever", "1994-07-30 Show", behavior="move")

    assert not show.exists()
    assert home.exists()
