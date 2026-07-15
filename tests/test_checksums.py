"""
tests/test_checksums.py — pure-logic coverage for fingerprint file parsing
and checksum matching/verification (app/utils/checksums.py). No FLAC files
needed for parsing/matching; file_md5 uses a small real temp file since
that's just raw bytes, no audio decoding involved.
"""

import hashlib
import os
import tempfile

from app.utils.checksums import (
    parse_checksum_file,
    match_entries_to_tracks,
    verify_track_checksum,
    file_md5,
    flac_md5_signature,
)


# ── parse_checksum_file ──────────────────────────────────────────────────────

def test_parse_md5sum_style():
    content = (
        "d41d8cd98f00b204e9800998ecf8427e *01 - Dark Star.flac\n"
        "5d41402abc4b2a76b9719d911017c592  02 - St. Stephen.flac\n"
    )
    entries = parse_checksum_file(content)
    assert len(entries) == 2
    assert entries[0]["filename"] == "01 - Dark Star.flac"
    assert entries[0]["checksum"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert entries[1]["filename"] == "02 - St. Stephen.flac"


def test_parse_colon_style():
    content = "01 - Dark Star.flac:d41d8cd98f00b204e9800998ecf8427e\n"
    entries = parse_checksum_file(content)
    assert entries == [{"filename": "01 - Dark Star.flac",
                         "checksum": "d41d8cd98f00b204e9800998ecf8427e"}]


def test_parse_bare_metaflac_output_no_filenames():
    # `metaflac --show-md5sum *.flac > ffp.txt` — just one hex string per line.
    content = "d41d8cd98f00b204e9800998ecf8427e\n5d41402abc4b2a76b9719d911017c592\n"
    entries = parse_checksum_file(content)
    assert entries == [
        {"filename": None, "checksum": "d41d8cd98f00b204e9800998ecf8427e"},
        {"filename": None, "checksum": "5d41402abc4b2a76b9719d911017c592"},
    ]


def test_parse_skips_blank_and_non_data_lines():
    content = "\n  \nSome header comment with no hash\nd41d8cd98f00b204e9800998ecf8427e *track.flac\n"
    entries = parse_checksum_file(content)
    assert len(entries) == 1


def test_parse_empty_content():
    assert parse_checksum_file("") == []
    assert parse_checksum_file(None) == []


# ── match_entries_to_tracks ──────────────────────────────────────────────────

class _FakeTrack:
    def __init__(self, track_number, file_path):
        self.track_number = track_number
        self.file_path = file_path
    def __repr__(self):
        return f"Track({self.track_number}, {self.file_path})"


def test_match_by_exact_filename():
    tracks = [_FakeTrack(1, "01 - Dark Star.flac"), _FakeTrack(2, "02 - St. Stephen.flac")]
    entries = [
        {"filename": "01 - Dark Star.flac", "checksum": "aaa"},
        {"filename": "02 - St. Stephen.flac", "checksum": "bbb"},
    ]
    matched = match_entries_to_tracks(entries, tracks)
    assert matched[tracks[0]] == "aaa"
    assert matched[tracks[1]] == "bbb"


def test_match_by_stem_across_extensions():
    # fingerprint generated against the original .shn set, tracks are now .flac
    tracks = [_FakeTrack(1, "01 - Dark Star.flac")]
    entries = [{"filename": "01 - Dark Star.shn", "checksum": "aaa"}]
    matched = match_entries_to_tracks(entries, tracks)
    assert matched[tracks[0]] == "aaa"


def test_match_positional_fallback_when_no_filenames():
    tracks = [_FakeTrack(1, "01.flac"), _FakeTrack(2, "02.flac")]
    entries = [{"filename": None, "checksum": "aaa"}, {"filename": None, "checksum": "bbb"}]
    matched = match_entries_to_tracks(entries, tracks)
    assert matched[tracks[0]] == "aaa"
    assert matched[tracks[1]] == "bbb"


def test_no_positional_fallback_when_counts_differ():
    tracks = [_FakeTrack(1, "01.flac"), _FakeTrack(2, "02.flac")]
    entries = [{"filename": None, "checksum": "aaa"}]
    assert match_entries_to_tracks(entries, tracks) == {}


def test_no_guessing_when_filenames_present_but_unmatched():
    tracks = [_FakeTrack(1, "01.flac")]
    entries = [{"filename": "totally different.flac", "checksum": "aaa"}]
    assert match_entries_to_tracks(entries, tracks) == {}


# ── verify_track_checksum ────────────────────────────────────────────────────

def test_verify_md5_match_and_mismatch():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
        f.write(b"not really flac audio, just bytes for a whole-file md5 test")
        path = f.name
    try:
        real_md5 = hashlib.md5(open(path, "rb").read()).hexdigest()
        assert verify_track_checksum(path, "md5", real_md5) == "match"
        assert verify_track_checksum(path, "md5", "0" * 32) == "mismatch"
        assert verify_track_checksum(path, "md5", None) == "unverified"
    finally:
        os.unlink(path)


def test_verify_unverified_when_file_missing():
    assert verify_track_checksum("/no/such/file.flac", "md5", "d41d8cd98f00b204e9800998ecf8427e") == "unverified"
    assert verify_track_checksum("/no/such/file.flac", "ffp", "d41d8cd98f00b204e9800998ecf8427e") == "unverified"


def test_flac_md5_signature_none_for_non_flac_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".flac") as f:
        f.write(b"not a real flac file")
        path = f.name
    try:
        assert flac_md5_signature(path) is None
    finally:
        os.unlink(path)


def test_file_md5_matches_hashlib():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello world")
        path = f.name
    try:
        assert file_md5(path) == hashlib.md5(b"hello world").hexdigest()
    finally:
        os.unlink(path)
