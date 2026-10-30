"""Resumable private production Fair Play research with public *numeric-only* GitHub status.

Only encrypted checkpoints and account-free commit status messages may leave the
private Discord DM channel. Never log names, evidence or the cohort secret.
"""
import asyncio
import base64
import gzip
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

import discord
from fairplay_data import username
from fairplay_evidence_payload import normalize

OWNER_ID = 362606514764251137
BANDS = ("LOW", "MODERATE", "HIGH", "VERY HIGH")
TOTAL = 200
MAX_PART_BYTES = 6_000_000
STATUS_CONTEXT = "sharkbot/fairplay-research-200"


def read_cohorts():
    from pathlib import Path
    path = Path(__file__).with_name("research_cohorts_200.json")
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict) or set(decoded) != {"closed", "comparison"}:
        raise ValueError("Expected closed and comparison cohorts")
    for group in ("closed", "comparison"):
        names = decoded[group]
        if not isinstance(names, list) or len(names) != 100:
            raise ValueError("Exactly 100 account names required per cohort")
        decoded[group] = [username(name) for name in names]
    names = decoded["closed"] + decoded["comparison"]
    if len({name.casefold() for name in names}) != TOTAL:
        raise ValueError("Research accounts must be unique")
    digest = hashlib.sha256(json.dumps(names, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()
    return decoded, digest


def band(priority):
    name = str(priority).strip().upper().replace("_", " ").replace("-", " ")
    if name in BANDS:
        return name
    return "UNCLASSIFIED"


def _github_status(state, description):
    """POST a public status containing counts only; no account-derived material."""
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    sha = os.getenv("GITHUB_SHA")
    if not (token and repo and sha and os.getenv("GITHUB_ACTIONS") == "true"):
        return False
    if not all(c.isalnum() or c in "-_/" for c in repo):
        return False
    payload = {"state": state, "context": STATUS_CONTEXT,
               "description": description[:140],
               "target_url": "https://github.com/%s/actions/runs/%s" %
                             (repo, os.getenv("GITHUB_RUN_ID", ""))}
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/statuses/%s" % (repo, sha),
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json",
                 "User-Agent": "SharkBot-research-status"})
    try:
        with urllib.request.urlopen(req, timeout=15) as reply:
            return 200 <= reply.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        print("Fair Play research GitHub status delivery unavailable.", flush=True)
        return False


async def report_status(state, completed, *, reason=""):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if state == "pending":
        current = min(completed + 1, TOTAL)
        detail = "RUNNING %d/%d; account %d/%d; %s" % (
            completed, TOTAL, current, TOTAL, timestamp)
    elif state == "success":
        detail = "COMPLETE %d/%d; %s" % (completed, TOTAL, timestamp)
    elif reason:
        detail = "%s %d/%d; %s" % (reason, completed, TOTAL, timestamp)
    else:
        detail = "STOPPED %d/%d; %s" % (completed, TOTAL, timestamp)
    return await asyncio.to_thread(_github_status, state, detail)


async def owner_dm(client, message, *, filename=None, data=None):
    owner = client.get_user(OWNER_ID) or await client.fetch_user(OWNER_ID)
    for attempt in range(3):
        try:
            if data is None:
                return await owner.send(message, allowed_mentions=discord.AllowedMentions.none())
            return await owner.send(
                message, file=discord.File(io.BytesIO(data), filename=filename),
                allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, OSError):
            if attempt == 2:
                raise
            await asyncio.sleep(3 * (attempt + 1))


def pack_result(index, entry, result):
    record = {"schema": "sharkbot-private-research-v2", "index": index,
              "metadata": entry, "review": normalize(result) if result is not None else None}
    raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")


async def deliver_final_document(client, state):
    records = [json.loads(gzip.decompress(base64.b64decode(item)).decode("utf-8"))
               for item in state["evidence"]]
    if len(records) != TOTAL:
        raise RuntimeError("Missing research evidence")
    document = {"schema": "sharkbot-research-complete-v3", "total": TOTAL,
                "cohort_digest": state["digest"], "summary": summary(state["rows"]),
                "accounts": records}
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    data = gzip.compress(raw, compresslevel=9, mtime=0)
    if len(data) > MAX_PART_BYTES:
        raise RuntimeError("Final document exceeds Discord attachment limit")
    await owner_dm(client, "Fair Play research complete: 200/200. All 200 account "
                   "reviews in one private archive. SHA-256: %s" %
                   hashlib.sha256(data).hexdigest(),
                   filename="fairplay-research-all-200.json.gz", data=data)


def summary(rows):
    text = ["**Fair Play Research — 200 accounts**",
            "Observational priorities, not cheating verdicts.",
            "Cohort labels supplied by the operator; independently verified status/game "
            "eligibility is not claimed."]
    for group in ("closed", "comparison"):
        subset = [r for r in rows if r["group"] == group]
        valid = [r for r in subset if r["status"] == "complete" and not r.get("partial")]
        counts = Counter(r["priority"] for r in valid)
        text.append("**%s:** %d/100 complete; %d failed/partial" %
                    (group.title(), len(valid), 100 - len(valid)))
        for key in (*BANDS, "UNCLASSIFIED"):
            amount = counts[key]
            pct = "%.1f%%" % (100 * amount / len(valid)) if valid else "N/A"
            text.append("%s: %d (%s)" % (key, amount, pct))
    return "\n".join(text)


async def heartbeat(service, state):
    while not service.closed and not service.client.is_closed():
        await asyncio.sleep(120)
        await report_status("pending", state["cursor"])


async def save_research_with_retry(checkpoint, state, *, attempts=8):
    """Retry transient encrypted checkpoint pushes without advancing the cursor.

    Keep the completed review in memory during retries so the analyzer is not
    called again merely because GitHub rejected or timed out a push.
    """
    for attempt in range(attempts):
        if await asyncio.to_thread(checkpoint.save_research, state):
            return True
        print("Research checkpoint persistence retry %d/%d" %
              (attempt + 1, attempts), flush=True)
        if attempt + 1 < attempts:
            await asyncio.sleep(min(90, 5 * 2 ** attempt))
    return False


async def run_research(service):
    checkpoint = service.checkpoints
    state = None
    pulse = None
    try:
        cohorts, digest = read_cohorts()
        if checkpoint is None or not checkpoint.enabled:
            raise RuntimeError("Encrypted research checkpoint unavailable")
        state = await asyncio.to_thread(checkpoint.research_state)
        print("Research checkpoint restored at %d/%d." % (state.get("cursor", 0) if state else 0, TOTAL), flush=True)
        if state and state.get("digest") != digest:
            if not state.get("finished"):
                raise RuntimeError("Research cohort changed before previous batch finished")
            state = None
        if state and (state.get("archive_version") != 3 or len(state.get("evidence", [])) != state.get("cursor", 0)):
            raise RuntimeError("Existing research checkpoint incompatible; refusing silent reset")
        if not state:
            state = {"digest": digest, "cursor": 0, "rows": [], "evidence": [], "archive_version": 3,
                     "finished": False, "started_at": time.time()}
            if not await save_research_with_retry(checkpoint, state):
                raise RuntimeError("Research checkpoint cannot be saved")
        if (len(state.get("rows", [])) != state["cursor"] or len(state.get("evidence", [])) != state["cursor"] or not 0 <= state["cursor"] <= TOTAL):
            raise RuntimeError("Invalid research checkpoint cursor")
        if state.get("finished"):
            await report_status("success", TOTAL)
            return
        await report_status("pending", state["cursor"])
        pulse = asyncio.create_task(heartbeat(service, state), name="fairplay-research-heartbeat")
        print("Fair Play research active at %d/200; one final DM at completion." % state["cursor"], flush=True)
        loop = asyncio.get_running_loop()
        accounts = [("closed", name) for name in cohorts["closed"]] + [
            ("comparison", name) for name in cohorts["comparison"]]
        for index in range(state["cursor"], TOTAL):
            if service.closed or service.client.is_closed():
                return
            group, account = accounts[index]
            entry = {"group": group, "account": account}
            result = None
            try:
                # Use the *same* analysis callable/executor as normal /fairplay.
                result = await loop.run_in_executor(
                    service.executor, lambda name=account: service.analyzer(name, lambda *_: None))
                entry.update(status="complete", priority=band(result.priority),
                             raw_priority=str(result.priority),
                             confidence=str(result.confidence),
                             partial=bool(result.partial),
                             selected_games=int(result.selected_games))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                entry.update(status="failed", priority="UNCLASSIFIED",
                             error_type=type(exc).__name__, partial=True)
            # Save full evidence in the encrypted checkpoint before advancing the cursor.
            packed = pack_result(index + 1, entry, result)
            next_state = dict(state)
            next_state["rows"] = state["rows"] + [entry]
            next_state["evidence"] = state["evidence"] + [packed]
            next_state["cursor"] = index + 1
            if not await save_research_with_retry(checkpoint, next_state):
                raise RuntimeError("Research checkpoint cannot be saved; inspect GitHub push permissions, payload size and timeout")
            state.clear()
            state.update(next_state)
            await report_status("pending", state["cursor"])
            print("Private Fair Play research completed %d/%d accounts" %
                  (state["cursor"], TOTAL), flush=True)
        # Cancel heartbeat before publishing terminal status.
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        pulse = None
        report = summary(state["rows"])
        await deliver_final_document(service.client, state)
        await service.channel.send(report, allowed_mentions=discord.AllowedMentions.none())
        final_state = dict(state, finished=True)
        if not await save_research_with_retry(checkpoint, final_state):
            raise RuntimeError("Final research checkpoint cannot be saved")
        state.clear()
        state.update(final_state)
        await report_status("success", TOTAL)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        completed = state["cursor"] if state else 0
        print("Fair Play research stopped: %s; completed %d/%d; details: %s." %
              (type(exc).__name__, completed, TOTAL, str(exc)[:160] if isinstance(exc, RuntimeError) else "redacted"), flush=True)
        await report_status("failure", completed, reason="FAILED")
        try:
            await owner_dm(service.client,
                           "Fair Play research stopped after %d/200 accounts. Reason: %s. "
                           "Saved progress will be resumed on the next bot start if possible." %
                           (completed, type(exc).__name__))
        except Exception:
            print("Fair Play research owner DM unavailable.", flush=True)
    finally:
        if pulse:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
        service.research_hold.set()


def start_research(service):
    """Called before queue workers are created to eliminate the startup race."""
    from pathlib import Path
    if Path(__file__).with_name("research_cohorts_200.json").is_file():
        service.research_hold.clear()
        service.research_task = asyncio.create_task(
            run_research(service), name="fairplay-private-research")
    else:
        # Visible, falsifiable 'not running' state even when the bot is online.
        asyncio.create_task(report_status("error", 0, reason="NOT CONFIGURED"),
                            name="fairplay-research-not-configured")
