"""One-shot private research on the production Fair Play executor.

Configure SHARKBOT_RESEARCH_COHORTS_B64 with base64-encoded JSON containing
100 usernames in each of 'closed' and 'comparison'. No public repository data.
"""
import asyncio
import base64
import io
import json
import os
from collections import Counter

import discord
from fairplay_analysis import review
from fairplay_evidence_payload import normalize
from fairplay_data import username

OWNER_ID = 362606514764251137
BANDS = ("LOW", "MODERATE", "HIGH", "VERY HIGH")


def read_cohorts():
    payload = json.loads(base64.b64decode(os.environ["SHARKBOT_RESEARCH_COHORTS_B64"], validate=True))
    if set(payload) != {"closed", "comparison"}:
        raise ValueError("Research requires closed and comparison cohorts")
    for key in ("closed", "comparison"):
        if not isinstance(payload[key], list) or len(payload[key]) != 100:
            raise ValueError("Research requires exactly 100 accounts per cohort")
        payload[key] = [username(value) for value in payload[key]]
    if len(set(payload["closed"] + payload["comparison"])) != 200:
        raise ValueError("Research usernames must be distinct")
    return payload


def band(priority):
    value = str(priority).strip().upper().replace(" ", "_").replace("-", "_")
    if value in BANDS:
        return value
    # Keep unrecognized production priorities explicit; never manufacture
    # LOW/HIGH classifications from an undocumented scoring scale.
    return "UNCLASSIFIED"


async def owner_dm(client, content, *, attachment=None):
    owner = client.get_user(OWNER_ID) or await client.fetch_user(OWNER_ID)
    if attachment is None:
        return await owner.send(content, allowed_mentions=discord.AllowedMentions.none())
    data = json.dumps(normalize(attachment), ensure_ascii=False).encode("utf-8")
    if len(data) > 7_000_000:
        raise RuntimeError("Private evidence exceeds the safe Discord attachment limit")
    return await owner.send(content, file=discord.File(io.BytesIO(data), filename="fairplay-research-private.json"),
                            allowed_mentions=discord.AllowedMentions.none())


async def run_research(service):
    """Pause new live scans, finish the running scan, then reuse its executor."""
    records = []
    try:
        cohorts = read_cohorts()
        # The queue worker checks research_hold before beginning the next scan.
        while service.active_job is not None:
            await asyncio.sleep(1)
        await owner_dm(service.client, "Fair Play research started: 200 accounts. Normal reviews are queued.")
        loop = asyncio.get_running_loop()
        for group in ("closed", "comparison"):
            for account in cohorts[group]:
                if service.closed or service.client.is_closed():
                    return
                entry = {"group": group, "account": account}
                try:
                    result = await loop.run_in_executor(
                        service.executor, lambda name=account: review(name, lambda *_: None))
                    entry.update(status="complete", priority=band(result.priority),
                                 raw_priority=str(result.priority),
                                 confidence=str(result.confidence),
                                 partial=bool(result.partial),
                                 selected_games=int(result.selected_games),
                                 evidence=normalize(result))
                except Exception as error:
                    entry.update(status="failed", error_type=type(error).__name__)
                records.append(entry)
                # No usernames, account decisions or case evidence in logs.
                if len(records) % 25 == 0:
                    print("Private Fair Play research completed %d/200 accounts" % len(records), flush=True)
        counts = {}
        for group in ("closed", "comparison"):
            rows = [r for r in records if r["group"] == group]
            good = [r for r in rows if r["status"] == "complete" and not r["partial"]]
            distribution = Counter(r["priority"] for r in good)
            counts[group] = {"complete": len(good), "failed_or_partial": 100-len(good),
                             "bands": distribution}
        lines = ["**Fair Play Research — 200 accounts**",
                 "Observational detection results; these are not cheating verdicts."]
        for group in ("closed", "comparison"):
            info = counts[group]
            lines.append("**%s:** %d/100 complete; %d failed/partial" %
                         (group.title(), info["complete"], info["failed_or_partial"]))
            for key in (*BANDS, "UNCLASSIFIED"):
                n = info["bands"][key]
                pct = "%.1f%%" % (100*n/info["complete"]) if info["complete"] else "N/A"
                lines.append("%s: %d (%s)" % (key.replace("_", " "), n, pct))
        # Owner receives full per-account evidence, never a public artifact.
        # Deliver small chunks so a single oversized payload cannot lose all
        # completed work.
        for offset in range(0, len(records), 5):
            await owner_dm(service.client,
                           "Private research evidence %d-%d / 200." %
                           (offset+1, min(offset+5, len(records))),
                           attachment=records[offset:offset+5])
        await owner_dm(service.client, "\n".join(lines))
        await service.channel.send("\n".join(lines),
                                   allowed_mentions=discord.AllowedMentions.none())
    except Exception as error:
        print("Private Fair Play research failed: %s" % type(error).__name__, flush=True)
        try:
            await owner_dm(service.client,
                           "Fair Play research interrupted. Completed %d/200; normal review queue will resume." %
                           len(records))
        except Exception:
            print("Private research owner delivery unavailable.", flush=True)
    finally:
        service.research_hold.set()
