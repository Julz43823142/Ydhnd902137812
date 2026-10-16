"""Authenticated, restricted dispatch across all existing SharkBot message listeners."""
from copy import copy
from shark_commands import _is_chat_channel, _parse_command

# Internal/administrative commands are never callable through AI suggestions.
PRIVILEGED = frozenset({
    "!edit", "!editcoins", "!editcolor", "!delete", "!adminfields",
    "!addheart", "!heart", "!kill", "!bf2",
})

async def dispatch_existing_command(message, client, command):
    """Schedule a normal Discord message event for a checked command.

    This reaches the same listeners as a user typing a command manually.
    No synthetic identity, elevated permissions, coins or admin tool access.
    """
    if not message.guild or not client.user or not _is_chat_channel(message.channel.id):
        return False
    validated = _parse_command(command, command)
    if validated != command or command.partition(" ")[0].lower() in PRIVILEGED:
        return False
    routed = copy(message)
    routed.content = command
    routed.mentions = [m for m in message.mentions if m.id != client.user.id]
    client.dispatch("message", routed)
    return True
