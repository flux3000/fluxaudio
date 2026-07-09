"""
Fix garbled info_file_content in any recording where the text was stored
as a raw UTF-16 LE byte stream interpreted as Latin-1/UTF-8.

Detects affected rows by looking for U+FFFD replacement chars at the start
(the mangled UTF-16 BOM) followed by interleaved null bytes.

Run after stopping Flask:
    cd ~/Workshop/dev && python3 scripts/fix_utf16_info_files.py
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "../db/fluxaudio.db")
con = sqlite3.connect(DB_PATH)

rows = con.execute(
    "SELECT id, info_file_content FROM recording WHERE info_file_content IS NOT NULL"
).fetchall()

fixed = 0
for rec_id, content in rows:
    if not content:
        continue

    # Garbled UTF-16 LE: starts with two U+FFFD (mangled BOM) and has \x00 bytes
    is_garbled = (
        len(content) >= 4
        and ord(content[0]) == 0xFFFD
        and ord(content[1]) == 0xFFFD
        and '\x00' in content[2:20]
    )
    if not is_garbled:
        continue

    # Recover: skip mangled BOM chars, encode back to bytes as latin-1, decode UTF-16 LE
    clean = (
        content[2:]
        .encode('latin-1', errors='replace')
        .decode('utf-16-le', errors='replace')
        .replace('\r\n', '\n')
        .replace('\r', '\n')
    )

    con.execute(
        "UPDATE recording SET info_file_content = ? WHERE id = ?",
        (clean, rec_id)
    )
    print(f"  Fixed rec {rec_id} — preview: {clean[:80]!r}")
    fixed += 1

con.commit()
print(f"\nDone. Fixed {fixed} recording(s).")
