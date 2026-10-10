"""Complete, owner-delivered Fair Play evidence; never a cheating verdict."""
import gzip
import hashlib
import json
import math
from dataclasses import fields, is_dataclass

SCHEMA = "sharkbot-fairplay-evidence-v1"
MAX_BYTES = 8 * 1024 * 1024


def normalize(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()
                if k != "_private_evidence"}
    if isinstance(value, (list, tuple)):
        return [normalize(x) for x in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: normalize(getattr(value, f.name)) for f in fields(value)}
    return {"unsupported_type": type(value).__name__}


def pack(payload):
    raw = json.dumps(normalize(payload), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def evidence(result):
    """Record every observed game/position before large move trees are cleared."""
    games = sorted(result.games, key=lambda g: (g.ended, g.identity))
    chunks, batch = [], []
    def flush():
        if not batch:
            return
        part = {"schema": SCHEMA, "games": list(batch)}
        data = pack(part)
        if len(data) > MAX_BYTES:
            raise ValueError("One evidence chunk is too large for Discord")
        chunks.append((f"fairplay-part-{len(chunks)+1:03}.json.gz", data))
        batch.clear()
    for game in games:
        record = normalize(game)
        if batch and (len(batch) >= 25 or
                      len(pack({"schema": SCHEMA, "games": batch+[record]})) > MAX_BYTES):
            flush()
        batch.append(record)
        if len(pack({"schema": SCHEMA, "games": batch})) > MAX_BYTES:
            raise ValueError("One game is too large for the evidence export")
    flush()
    fields_to_keep = ("username", "version", "engine", "priority", "confidence",
                      "partial", "selected_games", "coverage", "skipped", "totals",
                      "classes", "performance", "context", "families", "reasons",
                      "deep_confirmed", "deep_coverage", "timing", "clusters",
                      "history", "diagnostics", "elapsed")
    manifest = {k: normalize(getattr(result, k)) for k in fields_to_keep}
    manifest.update(schema=SCHEMA, fully_reviewed_games=len(games),
                    position_records=sum(len(g.decisions) for g in games),
                    files=[{"name": n, "sha256": hashlib.sha256(blob).hexdigest()}
                           for n, blob in chunks],
                    interpretation="Review priority, not proof of misconduct.")
    header = ("fairplay-manifest.json.gz", pack(manifest))
    if len(header[1]) > MAX_BYTES:
        raise ValueError("The evidence manifest is too large")
    return (header, *chunks)



def owner_zip(files, *, max_bytes=MAX_BYTES):
    """One downloadable, verified owner archive; retain original SHA-256 chain.

    The original gzipped 25-game parts and manifest remain separately stored
    for resumable encrypted audit retention. A too-large archive returns None
    and the UI falls back to the existing individual parts.
    """
    import io
    import zipfile
    if not files or len(files)<2 or files[0][0]!="fairplay-manifest.json.gz":
        return None
    verify(files[0][1],files[1:])
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_STORED,
                         allowZip64=False) as archive:
        for name,blob in files:
            if (not isinstance(name,str) or "/" in name or "\\" in name
                    or not name.endswith(".json.gz")):
                raise ValueError("Invalid owner evidence filename")
            archive.writestr(name,blob)
    packed=stream.getvalue()
    return ("fairplay-owner-evidence.zip",packed) if len(packed)<=max_bytes else None


def verify(manifest_file, chunks):
    meta = json.loads(gzip.decompress(manifest_file))
    if meta.get("schema") != SCHEMA or len(meta["files"]) != len(chunks):
        raise ValueError("Invalid evidence manifest")
    count = positions = 0
    for entry, (name, blob) in zip(meta["files"], chunks):
        if entry["name"] != name or entry["sha256"] != hashlib.sha256(blob).hexdigest():
            raise ValueError("Evidence checksum mismatch")
        games = json.loads(gzip.decompress(blob))["games"]
        count += len(games)
        positions += sum(len(g["decisions"]) for g in games)
    if count != meta["fully_reviewed_games"] or positions != meta["position_records"]:
        raise ValueError("Incomplete evidence")
    return count, positions
