"""
One-time fix: reconcile DB folder_path + file_path for recording ID 2
(Bill Evans Trio 1979-10-30) with the actual files on disk.

Run after stopping the Flask server:
    cd ~/Workshop/dev && python3 scripts/fix_bill_evans_1979_paths.py
"""
import os
import re
import sqlite3

LIBRARY_ROOT = os.path.expanduser("~/Workshop/_audio_library")
DB_PATH      = os.path.join(os.path.dirname(__file__), "../db/fluxaudio.db")

# Actual folder on disk (relative to LIBRARY_ROOT)
ACTUAL_REL = "Bill Evans Trio/Bill Evans 1979-10-30 Lulu White's.Boston,MA.fm.flac16"
ACTUAL_ABS = os.path.join(LIBRARY_ROOT, ACTUAL_REL)

if not os.path.isdir(ACTUAL_ABS):
    raise SystemExit(f"ERROR: folder not found: {ACTUAL_ABS}")

# Map track_number -> actual filename by leading digits
file_map = {}
for f in os.listdir(ACTUAL_ABS):
    if f.endswith(".flac"):
        m = re.match(r"^(\d+)[.\s]", f)
        if m:
            file_map[int(m.group(1))] = f

con = sqlite3.connect(DB_PATH)

# 1. Fix folder_path
con.execute("UPDATE recording SET folder_path = ? WHERE id = 2", (ACTUAL_REL,))

# 2. Fix each track's file_path
tracks = con.execute(
    "SELECT id, track_number FROM track WHERE recording_id = 2"
).fetchall()

for tid, tnum in tracks:
    new_fp = file_map.get(tnum)
    if new_fp:
        con.execute("UPDATE track SET file_path = ? WHERE id = ?", (new_fp, tid))
    else:
        print(f"  WARNING: no file found on disk for track {tnum}")

con.commit()

# Verify
print("\nVerification:")
rows = con.execute("""
    SELECT t.track_number, t.file_path, r.folder_path
    FROM track t JOIN recording r ON r.id = t.recording_id
    WHERE r.id = 2 ORDER BY t.track_number
""").fetchall()
for tnum, fp, folder in rows:
    full   = os.path.join(LIBRARY_ROOT, folder, fp)
    status = "✓" if os.path.isfile(full) else "✗ MISSING"
    print(f"  {status}  {tnum:02d}  {fp}")
