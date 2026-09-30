"""One box picker/confirmation flow for every SharkBot shop."""
import asyncio
import discord
from holiday_events import HOLIDAYS, HOLIDAY_BOX_COST, active_holidays
from shop_catalog import BADGE_BOX_COST
from shared_leaderboard import buy_badge_box, format_points


class BadgeBoxPicker(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = int(user_id)
        self.busy = False
        self.build_picker()

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("Open your own shop first.", ephemeral=True)
            return False
        return True

    def build_picker(self):
        self.clear_items()
        for holiday in [None, *active_holidays()]:
            label = HOLIDAYS[holiday]["label"] if holiday else "Random Badge"
            cost = HOLIDAY_BOX_COST if holiday else BADGE_BOX_COST
            button = discord.ui.Button(label=f"{label} Box • {format_points(cost)} coins", style=discord.ButtonStyle.primary)

            async def select(interaction, holiday=holiday, label=label, cost=cost):
                if self.busy:
                    await interaction.response.send_message("Your box is already opening.", ephemeral=True)
                    return
                self.clear_items()
                confirm = discord.ui.Button(label=f"Open {label} Box", style=discord.ButtonStyle.success, emoji="🎁")

                async def purchase(interaction):
                    if self.busy:
                        await interaction.response.send_message("Your box is already opening.", ephemeral=True)
                        return
                    self.busy = True
                    await interaction.response.defer()
                    try:
                        result = await asyncio.to_thread(
                            buy_badge_box, interaction.user.id, interaction.user.display_name,
                            f"badge-box:{interaction.id}:{interaction.user.id}", holiday=holiday,
                        )
                    except Exception as error:
                        self.busy = False
                        await interaction.followup.send(f"❌ Could not open box: {str(error)[:700]}", ephemeral=True)
                        return
                    self.stop()
                    await interaction.edit_original_response(
                        content=f"🎁 **{label} Box opened!** You got {result['badge']} — **{result['rarity_label']}**.\n🪙 Coins left: **{format_points(result['coins'])}**",
                        embed=None, view=None,
                    )

                confirm.callback = purchase
                self.add_item(confirm)
                cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel_purchase(interaction):
                    if self.busy:
                        await interaction.response.send_message("Your box is already opening and cannot be cancelled now.", ephemeral=True)
                        return
                    self.stop()
                    await interaction.response.edit_message(content="Cancelled. No coins spent.", embed=None, view=None)
                cancel.callback = cancel_purchase
                self.add_item(cancel)
                extra = " Guaranteed one badge from this holiday; each has a 10% chance. Duplicates are possible." if holiday else " One random badge; duplicates are possible."
                await interaction.response.edit_message(content=f"Open one **{label} Box** for **{format_points(cost)} coins**?{extra}", embed=None, view=self)

            button.callback = select
            self.add_item(button)
