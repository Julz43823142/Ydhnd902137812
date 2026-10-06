"""One box picker/confirmation flow for every SharkBot shop."""
import asyncio
import discord
from holiday_events import HOLIDAYS, HOLIDAY_BOX_COST, active_holidays, next_holiday, holiday_event_details
from shop_catalog import BADGE_BOX_COST
from shared_leaderboard import buy_badge_box, format_points, claim_daily_mystery_box, mystery_box_day


async def claim_mystery_box(interaction):
    """No wallet minimum; backend daily audit prevents multi-view double claims."""
    await interaction.response.defer(thinking=False)
    try:
        result = await asyncio.to_thread(claim_daily_mystery_box, interaction.user.id, interaction.user.display_name)
        from pets import next_daily
        import time
        status = "You already claimed today's free Mystery Box" if result['already_claimed'] else 'Your free daily Mystery Box is open'
        if not result['already_claimed']:
            status = f"{discord.utils.escape_markdown(interaction.user.display_name)} opened a free daily Mystery Box"
        now = time.time()
        reset = f'Your next free box is available <t:{next_daily(now)}:R> (midnight Amsterdam).'
        if result.get('claim_day', mystery_box_day(now)) != mystery_box_day(now):
            status = 'Daily Mystery Box receipt'
            reset = 'A new day has begun. You can already claim your next free box in `!shop`.'
        embed = discord.Embed(
            title=f"🎁 {status}!",
            description=f"Reward: {result['badge']} — **{result['rarity_label']}**.\nNo coins spent. {reset}",
            colour=discord.Colour.gold(),
        )
        await interaction.followup.send(
            embed=embed, ephemeral=result['already_claimed'], allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        await interaction.followup.send('Your free box could not be confirmed. Retry in `!shop`; the daily claim is safe to repeat.', ephemeral=True)


def badge_box_picker_message(moment=None):
    holiday, _, days = next_holiday(moment)
    unit = "day" if days == 1 else "days"
    active = active_holidays(moment)
    availability = "Holiday boxes are available only during their events."
    if active:
        availability += " Available now: **" + ", ".join(HOLIDAYS[key]["label"] for key in active) + "**."
        availability += "\n" + "\n".join(holiday_event_details(key, moment) for key in active)
    return (
        "🎁 **Choose a Badge Box.**\n"
        "🎁 Everyone can claim **1 free Mystery Box every day** in `!shop` — resets at midnight Amsterdam.\n"
        f"{availability}\n"
        f"📅 The next Holiday Box opens in **{days} {unit}**: **{HOLIDAYS[holiday]['label']}**.\n"
        "Select a box, then confirm before spending coins."
    )


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
        daily = discord.ui.Button(label='Free Daily Mystery Box', emoji='🎁', style=discord.ButtonStyle.success, row=2)
        daily.callback = claim_mystery_box
        active = active_holidays()
        for holiday, quantity in [(None,1), *[(key,1) for key in active], (None,5)]:
            label = HOLIDAYS[holiday]["label"] if holiday else "Random Badge"
            cost = HOLIDAY_BOX_COST if holiday else BADGE_BOX_COST
            cost *= quantity
            emoji = "🎊" if holiday else "🎁"
            box_label=f'{label} Box' if quantity==1 else '5 Mystery Boxes'
            button = discord.ui.Button(label=f"{box_label} • {format_points(cost)} coins", style=discord.ButtonStyle.primary, emoji=emoji, row=1 if holiday else 0)

            async def select(interaction, holiday=holiday, label=label, cost=cost, emoji=emoji, quantity=quantity, box_label=box_label):
                if self.busy:
                    await interaction.response.send_message("Your box is already opening.", ephemeral=True)
                    return
                self.clear_items()
                confirm = discord.ui.Button(label=f"Open {box_label}", style=discord.ButtonStyle.success, emoji=emoji)

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
                            **({'quantity':quantity} if quantity!=1 else {}),
                        )
                    except Exception as error:
                        self.busy = False
                        await interaction.followup.send(f"❌ Could not open box: {str(error)[:700]}", ephemeral=True)
                        return
                    self.stop()
                    if result.get('quantity',1)>1:
                        rewards='\n'.join(f"{i}. {reward['badge']} — **{reward['rarity_label']}**" for i,reward in enumerate(result['rewards'],1))
                        content=f"{emoji} **{result['quantity']} Mystery Boxes opened!**\n{rewards}\n🪙 Coins left: **{format_points(result['coins'])}**"
                    else:
                        content=f"{emoji} **{label} Box opened!** You got {result['badge']} — **{result['rarity_label']}**.\n🪙 Coins left: **{format_points(result['coins'])}**"
                    await interaction.edit_original_response(
                        content=content,
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
                chance = 100 / len(HOLIDAYS[holiday]["badges"]) if holiday else None
                extra = f" Guaranteed one badge from this holiday; each has a {chance:.2f}% chance. Duplicates are possible." if holiday else f" {quantity} random badge{'s' if quantity>1 else ''}; duplicates are possible."
                await interaction.response.edit_message(content=f"Open **{box_label}** for **{format_points(cost)} coins**?{extra}", embed=None, view=self)

            button.callback = select
            self.add_item(button)
        if not active:
            self.add_item(discord.ui.Button(
                label=f"Holiday Box • {format_points(HOLIDAY_BOX_COST)} coins",
                emoji="🎊", style=discord.ButtonStyle.secondary, disabled=True, row=1,
            ))
        self.add_item(daily)
