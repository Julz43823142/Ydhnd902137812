import argparse
import csv
import io
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

import requests
import zstandard as zstd

SOURCE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
OUTPUT_FILE = "rp_puzzle_pool.sqlite3"
POOL_BUILD = "rp-pool-v1-2026-09-03"

# Equal-sized buckets: 50% of RP puzzles are 2100+, one third are 2400+.
BANDS = (
    (1200, 1499),
    (1500, 1799),
    (1800, 2099),
    (2100, 2399),
    (2400, 2699),
    (2700, 3199),
)


def band_for_rating(rating):
    for index, (minimum, maximum) in enumerate(BANDS):
        if minimum <= rating <= maximum:
            return index
    return None


def acceptable(row):
    try:
        rating = int(row["Rating"])
        deviation = int(row["RatingDeviation"])
        popularity = int(row["Popularity"])
        plays = int(row["NbPlays"])
    except Exception:
        return False

    if band_for_rating(rating) is None:
        return False

    # Keep puzzles with enough community signal while retaining a huge pool.
    if deviation > 200:
        return False
    if popularity < 0:
        return False
    if plays < 10:
        return False

    moves = str(row.get("Moves", "")).split()
    if len(moves) < 2 or len(moves) > 18:
        return False

    if not row.get("PuzzleId") or not row.get("FEN"):
        return False

    return True


def reservoir_add(reservoir, seen_count, record, limit, rng):
    if len(reservoir) < limit:
        reservoir.append(record)
        return

    slot = rng.randrange(seen_count)
    if slot < limit:
        reservoir[slot] = record


def stream_samples(per_band, seed):
    rng = random.Random(seed)
    reservoirs = [[] for _ in BANDS]
    seen = [0 for _ in BANDS]
    accepted_total = 0
    rows_total = 0

    headers = {
        "User-Agent": "ChessDailyBot-RP-PoolBuilder/1.0",
        "Accept": "application/octet-stream",
    }

    with requests.get(
        SOURCE_URL,
        headers=headers,
        stream=True,
        timeout=(30, 300),
    ) as response:
        response.raise_for_status()
        response.raw.decode_content = False

        decompressor = zstd.ZstdDecompressor()
        with decompressor.stream_reader(response.raw) as binary_reader:
            with io.TextIOWrapper(
                binary_reader,
                encoding="utf-8",
                newline="",
            ) as text_reader:
                reader = csv.DictReader(text_reader)

                for row in reader:
                    rows_total += 1

                    if not acceptable(row):
                        if rows_total % 500_000 == 0:
                            print_progress(rows_total, accepted_total, seen)
                        continue

                    rating = int(row["Rating"])
                    band = band_for_rating(rating)
                    seen[band] += 1
                    accepted_total += 1

                    record = (
                        str(row["PuzzleId"]),
                        str(row["FEN"]),
                        str(row["Moves"]),
                        rating,
                        band,
                    )

                    reservoir_add(
                        reservoirs[band],
                        seen[band],
                        record,
                        per_band,
                        rng,
                    )

                    if rows_total % 500_000 == 0:
                        print_progress(rows_total, accepted_total, seen)

    print_progress(rows_total, accepted_total, seen)
    return reservoirs, seen


def print_progress(rows_total, accepted_total, seen):
    counts = ", ".join(
        f"{BANDS[i][0]}-{BANDS[i][1]}:{seen[i]:,}"
        for i in range(len(BANDS))
    )
    print(
        f"Scanned {rows_total:,} rows; accepted {accepted_total:,}; {counts}",
        flush=True,
    )


def build_sqlite(output_path, reservoirs, seen, per_band, seed):
    output_path = Path(output_path)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    if temp_path.exists():
        temp_path.unlink()

    con = sqlite3.connect(temp_path)

    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("PRAGMA locking_mode=EXCLUSIVE")

        con.executescript(
            """
            CREATE TABLE puzzles (
                puzzle_id TEXT PRIMARY KEY,
                fen TEXT NOT NULL,
                moves TEXT NOT NULL,
                rating INTEGER NOT NULL,
                band INTEGER NOT NULL
            );

            CREATE INDEX idx_puzzles_band ON puzzles(band);
            CREATE INDEX idx_puzzles_rating ON puzzles(rating);

            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        total = 0
        band_counts = {}

        for band, records in enumerate(reservoirs):
            if len(records) < per_band:
                minimum, maximum = BANDS[band]
                raise RuntimeError(
                    f"Only {len(records):,} qualifying puzzles were sampled "
                    f"for {minimum}-{maximum}; requested {per_band:,}. "
                    "Lower --per-band or loosen the quality filters."
                )

            # Randomize physical row order once so OFFSET sampling is cheap and
            # naturally random without SQLite ORDER BY RANDOM().
            random.Random(seed + band + 1000).shuffle(records)

            con.executemany(
                """
                INSERT INTO puzzles(puzzle_id, fen, moves, rating, band)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )

            band_counts[str(band)] = len(records)
            total += len(records)

        metadata = {
            "build": POOL_BUILD,
            "source": SOURCE_URL,
            "built_at": str(int(time.time())),
            "total": str(total),
            "per_band": str(per_band),
            "bands": json.dumps(BANDS),
            "band_counts": json.dumps(band_counts, sort_keys=True),
            "eligible_seen": json.dumps(seen),
            "seed": str(seed),
        }

        con.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            list(metadata.items()),
        )

        con.commit()
        con.execute("VACUUM")
        con.commit()

    finally:
        con.close()

    temp_path.replace(output_path)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(
        f"Built {output_path}: {sum(len(x) for x in reservoirs):,} puzzles, "
        f"{size_mb:.1f} MiB",
        flush=True,
    )

    if output_path.stat().st_size >= 95 * 1024 * 1024:
        raise RuntimeError(
            "Pool file is too close to GitHub's 100 MiB single-file limit. "
            "Rebuild with a smaller --per-band value."
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-band", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    if args.per_band < 5_000:
        parser.error("--per-band must be at least 5000")

    reservoirs, seen = stream_samples(args.per_band, args.seed)
    build_sqlite(args.output, reservoirs, seen, args.per_band, args.seed)


if __name__ == "__main__":
    main()
