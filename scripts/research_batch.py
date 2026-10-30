"""Isolated 200-account Fair Play research. All cohort data and artifacts are secret.
No Discord client/controller is started. Reviews never use closure labels as input.
"""
from __future__ import annotations
import base64
import gzip
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile

import requests
from cryptography.fernet import Fernet
from fairplay_analysis import review
from fairplay_evidence_payload import evidence, normalize

OWNER_ID = "362606514764251137"
CATEGORIES = ("LOW", "MODERATE", "HIGH", "VERY HIGH")
BASE = "https://discord.com/api/v10"


def cohorts():
    encoded = os.environ["RESEARCH_COHORTS_B64"]
    data = json.loads(base64.b64decode(encoded, validate=True))
    if set(data) != {"closed", "comparison"}:
        raise ValueError("Cohort keys must be closed/comparison")
    from fairplay_data import username
    for label in ("closed", "comparison"):
        if not isinstance(data[label], list) or len(data[label]) != 100:
            raise ValueError("Exactly 100 accounts per group required")
        data[label] = [username(name) for name in data[label]]
    if len(set(data["closed"] + data["comparison"])) != 200:
        raise ValueError("All 200 accounts must be distinct")
    return data


def cipher():
    key = os.environ["RESEARCH_ENCRYPTION_KEY"].encode("ascii")
    return Fernet(key)


def validate_account(name, group):
    session = requests.Session()
    session.headers["User-Agent"] = "SharkBot-Research/1.0 (Chess.com PubAPI)"
    def get(suffix):
        for attempt in range(5):
            response = session.get("https://api.chess.com/pub/player/" + name + suffix,
                                   timeout=45)
            if response.status_code in (429, 503):
                time.sleep(min(60, 2 ** attempt * 5))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Chess.com API unavailable")
    profile = get("")
    status = profile.get("status", "")
    if group == "closed" and status != "closed:fair_play_violations":
        raise ValueError("Fair Play closure not confirmed")
    if group == "comparison" and status != "basic" and status != "premium":
        # Include normal public member status values, not closed or banned.
        if str(status).startswith("closed") or status not in ("staff",):
            raise ValueError("Comparison account status is not active")
    stats = get("/stats")
    # Public Chess.com totals are not the same as eligible PGN review coverage.
    games = sum(int(v.get("record", {}).get(k, 0) or 0)
                for klass in ("chess_bullet", "chess_blitz", "chess_rapid", "chess_daily")
                for k in ("win", "loss", "draw")
                for v in [stats.get(klass, {})] if isinstance(v, dict))
    if games < 1000:
        raise ValueError("Fewer than 1,000 publicly recorded standard games")
    return {"public_games": games, "status_verified": True}


def run_one():
    data = cohorts()
    index = int(os.environ["RESEARCH_INDEX"])
    if index < 0 or index >= 200:
        raise ValueError("Invalid research index")
    group = "closed" if index < 100 else "comparison"
    name = data[group][index if index < 100 else index - 100]
    start = time.monotonic()
    record = {"index": index, "group": group, "username": name,
              "state": "FAILED", "priority": None, "confidence": None}
    blobs = {}
    try:
        record.update(validate_account(name, group))
        # The existing production review excludes account ban metadata from
        # its scoring. Never pass the cohort label to the reviewer.
        result = review(name, lambda _message: None)
        record.update(state="SUCCEEDED", priority=str(result.priority).upper().replace(" ", "_"),
                      confidence=result.confidence, partial=result.partial,
                      selected_games=result.selected_games,
                      coverage=normalize(result.coverage), elapsed_seconds=round(result.elapsed, 2))
        for filename, content in evidence(result):
            blobs[filename] = content
    except Exception as error:
        # Exception text may include a username or public profile details;
        # keep a private, coarse failure type only.
        record["failure_type"] = type(error).__name__
    record["wall_seconds"] = round(time.monotonic() - start, 2)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("result.json", json.dumps(record, sort_keys=True))
        for filename, content in blobs.items():
            z.writestr("evidence/" + filename, content)
    Path("research-output").mkdir(exist_ok=True)
    Path("research-output/part-%03d.enc" % index).write_bytes(cipher().encrypt(buffer.getvalue()))
    # Do not log account names, errors, case contents or decisions.
    print("Encrypted research shard %03d completed; outcome recorded privately." % index)


def discord_call(method, path, *, payload=None, files=None):
    headers = {"Authorization": "Bot " + os.environ["DISCORD_TOKEN"]}
    for attempt in range(12):
        kw = {"headers": headers, "timeout": 90}
        if files is not None:
            kw["data"] = {"payload_json": json.dumps(payload or {})}
            kw["files"] = {"files[0]": files}
        else:
            kw["json"] = payload or {}
        response = requests.request(method, BASE + path, **kw)
        if response.status_code == 429:
            try:
                delay = float(response.json().get("retry_after", 2))
            except (ValueError, TypeError):
                delay = 2
            time.sleep(min(90, max(1, delay)))
            continue
        if response.status_code >= 400:
            raise RuntimeError("Discord delivery failed (HTTP %s)" % response.status_code)
        return response.json()
    raise RuntimeError("Discord delivery exhausted rate-limit retries")


def package_reports(folder):
    raw = []
    for index in range(200):
        path = folder / ("part-%03d.enc" % index)
        if not path.exists():
            raise RuntimeError("A research shard artifact is missing")
        try:
            plain = cipher().decrypt(path.read_bytes())
            with zipfile.ZipFile(io.BytesIO(plain)) as archive:
                record = json.loads(archive.read("result.json"))
                if record.get("index") != index:
                    raise ValueError("Shard index mismatch")
            raw.append((index, record, plain))
        except Exception as e:
            raise RuntimeError("Invalid encrypted research shard") from e
    return raw


def report(raw):
    groups = {}
    for group in ("closed", "comparison"):
        records = [entry for _, entry, _ in raw if entry["group"] == group]
        completed = [row for row in records if row["state"] == "SUCCEEDED" and not row.get("partial")]
        counts = {key: sum(row.get("priority") == key for row in completed) for key in CATEGORIES}
        groups[group] = {"requested": 100, "completed": len(completed),
                         "failed_or_partial": 100 - len(completed),
                         "categories": {k: {"count": n, "percentage": round(100 * n / len(completed), 2)
                                               if completed else None}
                                        for k, n in counts.items()},
                         "unclassified": sum(row.get("priority") not in CATEGORIES for row in completed)}
    success = all(value["completed"] == 100 for value in groups.values())
    return {"status": "SUCCEEDED" if success else "FAILED", "groups": groups,
            "note": "Research priority distributions, not cheating probabilities or proven innocence."}


def public_text(summary):
    lines = ["**SharkBot Fair Play Research: " + summary["status"] + "**",
             "Observational cohort comparison; unverified labels are not proof of misconduct."]
    for group, title in (("closed", "Fair Play-closed candidates"), ("comparison", "Active comparison candidates")):
        data = summary["groups"][group]
        lines.append("**%s** — %d/100 completed, %d failed/partial" %
                     (title, data["completed"], data["failed_or_partial"]))
        for label, stats in data["categories"].items():
            pct = "N/A" if stats["percentage"] is None else ("%.2f%%" % stats["percentage"])
            lines.append("%s: %d (%s)" % (label.replace("_", " "), stats["count"], pct))
        if data["unclassified"]:
            lines.append("Unclassified: %d" % data["unclassified"])
    return "\n".join(lines)[:1900]


def finish():
    raw = package_reports(Path("research-results"))
    summary = report(raw)
    # Owner delivery first. A private DM cannot be made public by a button or
    # channel permissions. Never provide account data in public posts.
    dm = discord_call("POST", "/users/@me/channels",
                      payload={"recipient_id": OWNER_ID})["id"]
    discord_call("POST", "/channels/" + str(dm) + "/messages",
                 payload={"content": "Private SharkBot research results. Status: " + summary["status"]})
    # Split complete compressed evidence into bounded archives. Each archive
    # retains the original per-account evidence and exact result JSON.
    packet = io.BytesIO()
    number = 1
    def send_packet(buffer, n):
        if buffer.getbuffer().nbytes == 0:
            return
        discord_call("POST", "/channels/" + str(dm) + "/messages",
                     payload={"content": "SharkBot research owner evidence, part %03d." % n},
                     files=("sharkbot-research-%03d.zip" % n, buffer.getvalue(),
                            "application/zip"))
    batch = []
    size = 0
    for index, record, plain in raw:
        # Discord attachments capped conservatively to 8 MiB.
        if len(plain) > 7_000_000:
            # Preserve full evidence by splitting the raw ZIP into numbered
            # binary chunks, with reassembly instructions in the manifest.
            pieces = [plain[i:i + 6_000_000] for i in range(0, len(plain), 6_000_000)]
        else:
            pieces = [plain]
        for part, piece in enumerate(pieces):
            name = "account-%03d-part-%03d.bin" % (index, part)
            if batch and size + len(piece) > 6_500_000:
                b = io.BytesIO()
                with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_STORED) as z:
                    for filename, blob in batch:
                        z.writestr(filename, blob)
                send_packet(b, number)
                number += 1
                batch = []
                size = 0
            batch.append((name, piece))
            size += len(piece)
    if batch:
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", compression=zipfile.ZIP_STORED) as z:
            for filename, blob in batch:
                z.writestr(filename, blob)
        send_packet(b, number)
    discord_call("POST", "/channels/" + str(dm) + "/messages",
                 files=("research-summary.json", json.dumps(summary, indent=2).encode(),
                        "application/json"),
                 payload={"content": "Research aggregate and private evidence delivery complete. "
                                     "Account ZIPs are stored as indexed binary parts; concatenate "
                                     "part-000, part-001, etc. for each account before extracting."})
    channel = os.environ.get("RESEARCH_PUBLIC_CHANNEL_ID", "").strip()
    if channel:
        if not channel.isdigit():
            raise ValueError("Invalid research public channel")
        discord_call("POST", "/channels/" + channel + "/messages",
                     payload={"content": public_text(summary),
                              "allowed_mentions": {"parse": []}})
    print("Research aggregate delivered to owner; public summary attempted if configured.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("run", "finish"):
        raise SystemExit("Use: research_batch.py run|finish")
    {"run": run_one, "finish": finish}[sys.argv[1]]()
