"""Owner-only Discord failure diagnostics with encrypted Actions persistence.

Never put case identifiers, usernames, PGNs, FENs, raw exceptions or tokens in
public logs. The button is sent by DM to the verified Sharkmeister Discord ID;
ephemeral interactions re-check authorization even if the message is forwarded.
"""
import asyncio
import io
import json
import os
import re
from datetime import datetime, timezone

import discord

from fairplay_config import CONFIG, VERSION
from fairplay_data import AccountNotFound, DeadlineReached, ReviewError
from shark_admin import ADMIN_ID

TOKEN_RE = re.compile(r"^[0-9a-f]{12}$")
STAGES = (("Depth-18", "depth18"), ("Deep confirmation", "deep"),
          ("Fast engine scan", "fast"), ("Comparing human alternatives", "maia"),
          ("Building human-move", "human"), ("Collecting rated games", "collect"),
          ("Fetching profile", "profile"), ("Building report", "report"),
          ("Comparing personal timing", "timing"),
          ("Analyzing sessions", "sessions"), ("Resuming", "resume"))


def version_metadata(env=None):
    env = os.environ if env is None else env
    sha = str(env.get("GITHUB_SHA", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        sha = None
    run = str(env.get("GITHUB_RUN_ID", ""))
    repo = str(env.get("GITHUB_REPOSITORY", ""))
    attempt = str(env.get("GITHUB_RUN_ATTEMPT", ""))
    url = (f"https://github.com/{repo}/actions/runs/{run}"
           if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
           and re.fullmatch(r"[0-9]{1,20}", run) else None)
    return {"screening_version":VERSION, "git_commit":sha,
            "github_actions_run":run if run.isdigit() else None,
            "github_run_attempt":attempt if attempt.isdigit() else None,
            "github_run_url":url, "merged_pr_number":None,
            "revision_note":"Commit SHA is authoritative; a main-branch Actions run does not reliably expose the source PR."}


def stage_code(stage):
    return next((label for prefix,label in STAGES if str(stage).startswith(prefix)), "unknown")


def classify(error):
    """Stable, privacy-safe categories; raw exception text is never reported."""
    cause = getattr(error, "__cause__", None)
    if isinstance(error, AccountNotFound):
        return "profile_not_found"
    if isinstance(cause, TimeoutError) or isinstance(error, TimeoutError):
        return "stockfish_search_timeout"
    if isinstance(cause, OSError):
        return "engine_transport_or_process_failure"
    if isinstance(error, DeadlineReached):
        return "scan_deadline"
    if isinstance(error, ReviewError):
        message = str(error)
        if message.startswith("A Stockfish search timed out"):
            return "stockfish_search_timeout"
        if message.startswith("Stockfish failed"):
            return "engine_transport_or_process_failure"
        if "Maia" in message:
            return "maia_reference_incomplete"
        if "runtime limit" in message:
            return "scan_deadline"
        return "review_coverage_or_data_gate"
    return "unexpected_internal_error"


def failure_record(job, error, *, pool=None, checkpoints=None, env=None):
    """Only whitelisted engine metrics enter the encrypted report."""
    engine = getattr(pool, "last_failure", None)
    safe = None
    if isinstance(engine, dict):
        keys = ("category","phase","attempt","budget","multipv",
                "elapsed_seconds","timeout_seconds","worker_restarted","terminal")
        safe = {k:engine[k] for k in keys if k in engine
                and isinstance(engine[k], (str,int,float,bool))}
    from fairplay_progress import duration
    elapsed = round(job.timing_elapsed(), 2)
    record = {
        "schema":"sharkbot-fairplay-owner-failure-v1",
        "review_id":job.token if TOKEN_RE.fullmatch(job.token) else "invalid",
        "failed_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason_code":classify(error),
        "stage":stage_code(job.stage),
        "progress_indicator_percent":int(max(0,min(100,job.progress_percent))),
        "elapsed_seconds":elapsed,
        "elapsed_text":duration(elapsed),
        "model":"Stockfish 19 + Maia-3 (if installed)",
        "search_contract":{"fast_multipv":CONFIG.fast_multipv,
                           "deep_multipv":CONFIG.deep_multipv,
                           "depth18_rapid_blitz":18,
                           "bullet_depth":CONFIG.bullet_deep_depth,
                           "full_depth_mode":str((env or os.environ).get("FAIRPLAY_FULL_DEPTH18", ""))=="1"},
        "engine_last_failure":safe,
        "checkpoint_enabled":bool(getattr(checkpoints, "enabled", False)),
        "checkpoint_remote_ok":bool(getattr(checkpoints, "remote_ok", False)) if checkpoints else None,
        "checkpoint_note":"Completed exact work may resume; a failed review never issues a verdict.",
        "warning":"Categories only. No raw exception messages, username, PGN, FEN or private player data.",
    }
    record.update(version_metadata(env))
    return record


def pretty_report(record):
    return json.dumps(record,indent=2,ensure_ascii=True,sort_keys=True)


def _embed(record):
    e=discord.Embed(title="Sharkmeister · Fair Play failure diagnostic",
                    description=(
                        f"**Reason:** `{record['reason_code']}`\n"
                        f"**Phase:** `{record['stage']}`\n"
                        f"**Elapsed:** {record['elapsed_text']}\n"
                        f"**Commit:** `{(record.get('git_commit') or 'unknown')[:12]}`\n"
                        f"**Review ID:** `{record['review_id']}`\n"
                        "Use the button below for the complete copyable audit."
                    ),color=0xB84B4B)
    if record.get("github_run_url"):
        e.add_field(name="GitHub Actions",
                    value=record["github_run_url"],inline=False)
    e.set_footer(text="Review ID: "+record["review_id"])
    return e


def _store_for_interaction():
    from fairplay_ui import _service
    return _service


async def get_owner_record(review_id):
    service = _store_for_interaction()
    if service is None:
        return None
    report = service.failures.get(review_id)
    if report is not None:
        return report
    if service.checkpoints is None:
        return None
    return await asyncio.to_thread(service.checkpoints.get_failure, review_id)


async def send_report(ctx, review_id):
    if ctx.user.id != ADMIN_ID:
        await ctx.response.send_message(
            "Only Sharkmeister can view Fair Play failure diagnostics.",ephemeral=True)
        return
    if not isinstance(review_id,str) or not TOKEN_RE.fullmatch(review_id):
        await ctx.response.send_message("Invalid 12-character Review ID.",ephemeral=True)
        return
    await ctx.response.defer(ephemeral=True,thinking=True)
    record = await get_owner_record(review_id)
    if record is None:
        await ctx.followup.send(
            "No encrypted diagnostic found for this Review ID. "
            "It may be older than seven days, or the checkpoint was unavailable.",
            ephemeral=True)
        return
    payload = pretty_report(record).encode("utf-8")
    await ctx.followup.send(
        content="Private diagnostic report. You can share this JSON with the developer.",
        file=discord.File(io.BytesIO(payload),filename="fairplay_failure_"+review_id+".json"),
        ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class FailureView(discord.ui.View):
    """Persistent in owner DM; cannot reveal reports to another Discord account."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="View failure insights",emoji="🔒",
                       custom_id="shark:fairplay:owner-failure-insights")
    async def insight(self,ctx,button):
        if ctx.user.id != ADMIN_ID:
            await ctx.response.send_message(
                "Only Sharkmeister can view this diagnostic.",ephemeral=True)
            return
        footer = (ctx.message.embeds[0].footer.text if ctx.message.embeds
                  and ctx.message.embeds[0].footer else "")
        match = re.fullmatch(r"Review ID: ([0-9a-f]{12})",footer or "")
        if match is None:
            await ctx.response.send_message(
                "Cannot verify the Review ID in this message.",ephemeral=True)
            return
        await send_report(ctx,match[1])


async def owner_failure_notice(client, record, *, durable):
    """Best-effort DM; the slash command remains available if DM is disabled."""
    try:
        owner = client.get_user(ADMIN_ID) or await client.fetch_user(ADMIN_ID)
        embed = _embed(record)
        if not durable:
            embed.add_field(name="Persistence warning",
                value="Encrypted remote save was not confirmed. Export while this worker is running.",
                inline=False)
        await owner.send(embed=embed,view=FailureView(),
                         allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.Forbidden,discord.HTTPException):
        # Never post owner metadata to the public Fair Play channel.
        return False
