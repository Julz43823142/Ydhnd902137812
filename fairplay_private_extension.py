"""Add private report access to the existing Discord Fair Play view."""
import io
import asyncio
from fairplay_export_persistence import OwnerEvidenceStore
import discord
from fairplay_evidence_payload import evidence
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
        card.description = card.description.replace('up to 200', 'up to 500').replace('latest 100', 'latest 500').replace('then deeply reviews selected games', 'then reviews all selected games to depth 18')
        return card
    ui.panel_embed = new_panel

    def capture(result, limit=4):
        # Executed in the Fair Play scan thread before the full move trees are
        # deleted. Optional sidecars cannot influence the screening priority.
        from fairplay_multimodel import run_external_models
        try:
            result.diagnostics["independent_model_observations"] = run_external_models(result)
        except Exception:
            result.diagnostics["independent_model_observations"] = {
                "role": "Optional research-only comparisons; scoring unchanged",
                "status": "unavailable"}
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
                for name, payload in files:
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
