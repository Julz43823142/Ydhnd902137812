"""Read-only rowid indexes for uniform, fast selection from the existing RP pool."""
from array import array
from functools import lru_cache
from pathlib import Path
import sqlite3
from contextlib import closing


def band_row_ids(path, band):
    path = Path(path).resolve()
    stat = path.stat()
    # Rebuilt/replaced pools invalidate indexes without changing database schema.
    signature = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return _load_row_ids(signature, int(band))


@lru_cache(maxsize=6)
def _load_row_ids(signature, band):
    with closing(sqlite3.connect(f'file:{signature[0]}?mode=ro', uri=True, timeout=5)) as con:
        return array('q', (row[0] for row in con.execute('SELECT rowid FROM puzzles WHERE band = ?', (band,))))
