"""Staff-only slash command to edit Sharkbot's own messages in the current channel."""

import discord
from discord import app_commands


def register_editmessage(tree: app_commands.CommandTree) -> None:
    @tree.command(name="editmessage", description="Edit a Sharkbot message in this channel (staff only).")
    @app_commands.describe(
        message_id="ID of Sharkbot's message in this channel",
        text="Replacement message text",
    )
    @app_commands.guild_only()
    async def editmessage(interaction: discord.Interaction, message_id: str, text: str) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "You need Manage Messages permission to use this command.", ephemeral=True
            )
            return

        if not text.strip() or len(text) > 2000:
            await interaction.response.send_message(
                "Text must contain 1–2000 characters.", ephemeral=True
            )
            return

        try:
            target_id = int(message_id)
            if target_id <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            return

        channel = interaction.channel
        if channel is None or not hasattr(channel, "fetch_message"):
            await interaction.response.send_message(
                "Use this command in the channel containing the message.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            message = await channel.fetch_message(target_id)
            if interaction.client.user is None or message.author.id != interaction.client.user.id:
                await interaction.followup.send(
                    "I can only edit messages posted by this bot.", ephemeral=True
                )
                return
            await message.edit(
                content=text,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            await interaction.followup.send(
                "Message not found in this channel.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to access or edit that message.", ephemeral=True
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord could not edit the message. Please try again.", ephemeral=True
            )
        else:
            await interaction.followup.send("Message updated.", ephemeral=True)
