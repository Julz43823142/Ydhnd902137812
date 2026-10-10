"""Add private report access to the existing Discord Fair Play view."""
import io
import asyncio
from fairplay_export_persistence import OwnerEvidenceStore
import discord
from fairplay_evidence_payload import evidence, owner_zip
from shark_admin import ADMIN_ID


def install():
    import fairplay_ui as ui
    if getattr(ui, "_owner_export_installed", False):
        return
    original_capture = ui.capture_human_examples
    original_view = ui.ReportView
    old_panel = ui.panel_embed
    def new_panel():
        card = old_panel()
        # v21 panel text is written in fairplay_ui; do not overwrite scoped
        # 100+25 claims with the legacy "all 500 depth18" promise.
        if 'deeply reviews selected games' in card.description:
            card.description=card.description.replace('up to 200','up to 500')
        return card
    ui.panel_embed = new_panel

    def capture(result, limit=4):
        # Executed in the Fair Play scan thread before the full move trees are
        # deleted. Optional sidecars cannot influence the screening priority.
        # Whole-game resampling and same-position joint signals are diagnostic
        # only; they cannot alter HIGH/VERY HIGH gates or make an accusation.
        try:
            from fairplay_research import summarize_research
            result.diagnostics["research_validation"]=summarize_research(result.games)
        except Exception:
            result.diagnostics["research_validation"]={
                "scoring_influence":False,"status":"unavailable",
                "reason":"optional_research_summary_failed"}
        from fairplay_multimodel import run_external_models
        try:
            observations = run_external_models(result)
            result.diagnostics["independent_model_observations"] = observations
            from fairplay_model_readiness import summarize_model_audit
            result.diagnostics["model_coverage_summary"] = summarize_model_audit(observations)
        except Exception:
            result.diagnostics["independent_model_observations"] = {
                "role": "Optional research-only comparisons; scoring unchanged",
                "status": "unavailable"}
            result.diagnostics["model_coverage_summary"] = {
                "status": "unavailable", "reason": "optional_model_runner_failed",
                "scoring_influence": False}
        try:
            result.diagnostics["_private_evidence"] = evidence(result)
        except Exception:
            result.diagnostics["_private_evidence_error"] = "Full evidence export unavailable"
        return original_capture(result, limit=limit)

    class OwnerView(original_view):
        def __init__(self, target=None):
            super().__init__(target)
            button = discord.ui.Button(label="Owner Evidence", emoji="🔒",
                                       custom_id="shark:fairplay:owner-evidence")

            async def deliver(ctx):
                if ctx.user.id != ADMIN_ID:
                    await ctx.response.send_message("Only Sharkmeister may access this export.",
                                                    ephemeral=True)
                    return
                await ctx.response.defer(ephemeral=True, thinking=True)
                result = ui._service.result_for(ctx.message.id) if ui._service else None
                files = result.diagnostics.get("_private_evidence") if result else None
                if not files:
                    files = await asyncio.to_thread(OwnerEvidenceStore().get, ctx.message.id)
                if not files:
                    await ctx.followup.send(
                        "Full evidence unavailable or expired. No partial audit was sent.",
                        ephemeral=True)
                    return
                # One verified ZIP is much easier for the owner to share.
                # Discord's historical 8 MB ceiling remains a safe default;
                # only if the bundle exceeds it send the exact old parts.
                try:
                    archive=owner_zip(files)
                except (ValueError,KeyError,TypeError):
                    archive=None
                outgoing=(archive,) if archive else files
                for name,payload in outgoing:
                    await ctx.followup.send(
                        file=discord.File(io.BytesIO(payload), filename=name),
                        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

            button.callback = deliver
            self.add_item(button)

    base_process = ui.FairPlayService.process

    async def process_with_durable_audit(self, job):
        await base_process(self, job)
        if not job.report_published or job.message is None:
            return
        entry = self.results.get(job.message.id)
        bundle = entry[1].diagnostics.get("_private_evidence") if entry else None
        if bundle:
            durable = await asyncio.to_thread(
                OwnerEvidenceStore().save, job.message.id, bundle)
            entry[1].diagnostics["_private_evidence_durable"] = bool(durable)

    ui.FairPlayService.process = process_with_durable_audit
    ui.capture_human_examples = capture
    ui.ReportView = OwnerView
    ui._owner_export_installed = True
