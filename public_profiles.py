"""Resolve public showcase targets without creating wallets or changing gameplay."""
import asyncio
import re

from shared_leaderboard import resolve_cosmetic_profile


async def resolve_target(message, query=''):
    query = str(query or '').strip()
    if message.mentions:
        target = message.mentions[0]
        return str(target.id), target.display_name
    if not query:
        return str(message.author.id), message.author.display_name
    guild = getattr(message, 'guild', None)
    match = re.fullmatch(r'(?:<@!?)?(\d{1,20})>?', query)
    if match:
        uid = int(match.group(1))
        target = guild.get_member(uid) if guild else None
        if target is None and guild:
            try:
                target = await guild.fetch_member(uid)
            except Exception:
                pass
        if target:
            return str(target.id), target.display_name
        profile = await asyncio.to_thread(resolve_cosmetic_profile, '', target_user_id=uid)
        if profile.get('name') == 'Unknown':
            raise ValueError('Player not found. Mention a Discord user or use their exact name.')
        return str(uid), profile['name']
    matches = [member for member in getattr(guild, 'members', ())
               if query.casefold() in {str(getattr(member, key, '') or '').casefold()
                                       for key in ('display_name', 'name', 'global_name')}]
    if len(matches) > 1:
        raise ValueError('Several players have that name. Mention the Discord user instead.')
    if matches:
        return str(matches[0].id), matches[0].display_name
    try:
        profile = await asyncio.to_thread(resolve_cosmetic_profile, query)
    except ValueError as error:
        raise ValueError('Player not found or name is ambiguous. Mention the Discord user or use their exact name.') from error
    return str(profile['user_id']), profile.get('name', query)
