"""Quarantine before command/view/modal dispatch; on_interaction is too late.

The small gateway-parser adapter is tested against pinned discord.py 2.7.1.
Other channels pass through unchanged; no financial code is called here.
"""
import asyncio
import discord
from fairplay_config import CHANNEL_ID, NAMESPACE


def permitted(payload):
    try:channel = int(payload.get('channel_id',0))
    except (ValueError,TypeError):channel = 0
    if channel!=CHANNEL_ID:return True
    data = payload.get('data',{})
    if payload.get('type') in (2,4):return data.get('name') in ('fairplay','stopfairplay','fairplaydiagnostic')
    if payload.get('type') in (3,5):return str(data.get('custom_id','')).startswith(NAMESPACE)
    return False


async def reject(interaction):
    try:
        if interaction.type==discord.InteractionType.autocomplete:await interaction.response.autocomplete([])
        else:await interaction.response.send_message('🛡️ This channel is reserved for Fair Play reviews.',ephemeral=True)
    except discord.HTTPException:pass


def install_gate(connection):
    original = connection.parsers['INTERACTION_CREATE']
    def dispatch(payload):
        if permitted(payload):original(payload)
        else:
            ctx = discord.Interaction(data=payload,state=connection)
            connection._view_store.add_task(asyncio.create_task(reject(ctx),name='fairplay-channel-guard'))
    connection.parsers['INTERACTION_CREATE'] = dispatch


class FairPlayClient(discord.Client):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        install_gate(self._connection)
        # Install the owner-only evidence button before persistent views are registered.
        from fairplay_private_extension import install as install_owner_evidence
        install_owner_evidence()

    async def close(self):
        import fairplay_ui
        service = fairplay_ui._service
        if service is not None and service.client is self:await service.close()
        await super().close()
