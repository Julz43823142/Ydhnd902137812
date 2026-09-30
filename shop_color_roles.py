"""Shared Discord role placement, used by both Puzzle and Guess shops."""
import asyncio
import os
import discord
from shop_catalog import NAME_COLORS, SHOP_COLOR_ROLE_PREFIX, is_subscriber_name_color, subscriber_entitlement_role

_guild_locks = {}


def highest_nonshop_color(member):
    return max((role for role in member.roles
                if not getattr(role, "is_default", lambda: False)()
                and not role.name.startswith(SHOP_COLOR_ROLE_PREFIX)
                and getattr(getattr(role, "colour", None), "value", 0)), default=None)


async def position_color_role(guild, bot_member, role, member):
    base = highest_nonshop_color(member)
    protected_id = os.getenv("SHARKMEISTER_USER_ID", "362606514764251137")
    protected = str(member.id) == protected_id or str(member.id) == str(getattr(guild, "owner_id", "")) or getattr(member, "bot", False)
    if base is not None and protected:
        raise RuntimeError("Your owner/bot color is protected; shop colors cannot override it.")
    if role >= bot_member.top_role or (base is not None and base >= bot_member.top_role):
        raise RuntimeError("Move SharkBot's highest role above Subscriber and all Shop Color roles in Server Settings > Roles.")
    if base is not None and role <= base:
        # Moving a lower role to the base's current position shifts that base
        # down. No empty numeric slot between Subscriber and SharkBot is needed.
        roles = await guild.edit_role_positions(positions={role: base.position}, reason="Shop color above subscriber color")
        role = next((item for item in roles if item.id == role.id), role)
        base = next((item for item in roles if item.id == base.id), base)
        top = next((item for item in roles if item.id == bot_member.top_role.id), bot_member.top_role)
        if not base < role < top:
            raise RuntimeError("Discord did not place the shop color above Subscriber and below SharkBot. Please check the server role order.")
    return role


async def apply_color_role(member, color_name):
    guild = getattr(member, "guild", None)
    if guild is None:
        raise RuntimeError("Name colors can only be equipped inside the Discord server.")
    # Role positions are shared server-wide. Serialize color switches across
    # both commands and buttons within this process.
    lock = _guild_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            raise RuntimeError("SharkBot needs Manage Roles to equip colors.")
        color_name = str(color_name or "").casefold().strip()
        if color_name and color_name not in NAME_COLORS:
            raise ValueError("Unknown name color.")
        subscriber = subscriber_entitlement_role(member) if is_subscriber_name_color(color_name) else None
        if is_subscriber_name_color(color_name) and subscriber is None:
            raise ValueError("Pink requires an active Discord subscription.")
        old = [role for role in member.roles if role.name.startswith(SHOP_COLOR_ROLE_PREFIX)]
        if any(role >= me.top_role for role in old):
            raise RuntimeError("Move SharkBot's highest role above all Shop Color roles first.")
        if not color_name or subscriber is not None:
            if old:
                await member.remove_roles(*old, reason="Reveal default/subscriber color")
            return subscriber
        config = NAME_COLORS[color_name]
        role = discord.utils.get(guild.roles, name=SHOP_COLOR_ROLE_PREFIX + config["label"])
        if role is None:
            role = await guild.create_role(name=SHOP_COLOR_ROLE_PREFIX + config["label"], color=discord.Color(config["discord_color"]), reason="Shop cosmetic color")
        role = await position_color_role(guild, me, role, member)
        await member.add_roles(role, reason="Shop color equipped")
        removable = [item for item in old if item.id != role.id]
        try:
            if removable:
                await member.remove_roles(*removable, reason="Shop color changed")
        except Exception:
            if role.id not in {item.id for item in old}:
                await member.remove_roles(role, reason="Rollback incomplete color switch")
            raise
        return role
