"""Authenticated, restricted dispatch across all existing SharkBot message listeners."""
from copy import copy
from shark_commands import _is_chat_channel, _parse_command

# Internal/administrative commands are never callable through AI suggestions.
PRIVILEGED = frozenset({
    "!edit", "!editcoins", "!editcolor", "!delete", "!adminfields",
    "!addheart", "!heart", "!kill", "!bf2",
})

async def dispatch_existing_command(message, client, command, *, confirmed=False):
    """Schedule a normal Discord message event for a checked command.

    This reaches the same listeners as a user typing a command manually.
    No synthetic identity, elevated permissions, coins or admin tool access.
    """
    if (not message.guild or not client.user or message.author.bot
            or not _is_chat_channel(message.channel.id)):
        return False
    member = message.guild.me
    if member is None:
        return False
    permissions = message.channel.permissions_for(member)
    if not (permissions.view_channel and permissions.send_messages):
        return False
    # Confirmations carry a command validated against the original request.
    # The follow-up message may contain only 'yes', not the earlier arguments.
    validated = _parse_command(command, command if confirmed else message.content)
    is_original_pgn = (
        command.startswith("!review ")
        and validated == "!review"
        and len(command) <= 1500
        and not confirmed
        and command[len("!review "):] in message.content
    )
    if (validated != command and not is_original_pgn) or command.partition(" ")[0].lower() in PRIVILEGED:
        return False
    routed = copy(message)
    routed.content = command
    routed.mentions = [m for m in message.mentions if m.id != client.user.id]
    client.dispatch("message", routed)
    return True


async def dispatch_puzzle_request(message, client, kind):
    """Only dispatch fixed, audited puzzle commands from explicit AI intent."""
    if kind not in {"random", "enpassant"}:
        return False
    if (not message.guild or not client.user or message.author.bot
            or not _is_chat_channel(message.channel.id)):
        return False
    member = message.guild.me
    if member is None:
        return False
    permissions = message.channel.permissions_for(member)
    if not (permissions.view_channel and permissions.send_messages):
        return False
    routed = copy(message)
    routed.content = "!random" if kind == "random" else "!themepuzzle enpassant"
    routed.mentions = [m for m in message.mentions if m.id != client.user.id]
    client.dispatch("message", routed)
    return True
