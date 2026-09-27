    if info is not None:
        display_name, description = info
        await message.channel.send(
            f"👤 **{display_name}**\n{description}"
        )
        return


@client.event
async def on_message(message):
    if not message.author.bot and message.channel.id == CHANNEL_ID:
        note_guess_human_activity()
    if (not message.author.bot and message.channel.id == CHANNEL_ID
            and await shark_admin.handle_message(message)):
        return

    try:
        await command_handler(message)
    except Exception as error:
        print(
            f"Guess controller command error: {error}",
            flush=True,
        )
        try:
            await message.channel.send(
                "❌ **Guess bot error:** "
                f"`{str(error)[:1000]}`"
            )
        except Exception:
            pass


@client.event
async def on_ready():
    global SCHEDULER_TASK

    if getattr(client, "_guess_controller_started", False):
        return

    client._guess_controller_started = True

    print(
        f"Guess Games controller ready as {client.user}",
        flush=True,
    )
    print(f"Controller build: {GUESS_CONTROLLER_BUILD}", flush=True)
    print(f"Chatter build: {GUESS_CHATTER_BUILD}", flush=True)
    print(f"Chess build: {GUESS_CHESS_BUILD}", flush=True)

    # Persistent button views must be registered before restoring messages so
    # buttons from a previous process keep working after a workflow rotation.
    client.add_view(GuessNewHereView())
    client.add_view(GuessOpenTradeView())

    channel = await client.fetch_channel(CHANNEL_ID)
    await restore_guess_idle_state_from_history(channel)
    asyncio.create_task(guess_idle_tip_loop(channel))

    # A fresh Actions checkout should never contain the marker, but remove a
    # stale local marker defensively before arming the planned handoff timer.
    try:
        GUESS_ROTATION_MARKER.unlink(missing_ok=True)
    except Exception as error:
        print(f"Guess rotation marker cleanup warning: {error}", flush=True)

    if GUESS_WORKFLOW_ROTATION_SECONDS > 0:
        asyncio.create_task(guess_workflow_rotation_loop())

    try:
        await load_guess_open_trades()
        await restore_guess_open_trades()
    except Exception as error:
        # Trading persistence must never prevent Guess rounds from coming online.
        print(f"Guess open-trade restore warning: {error}", flush=True)

    try:
        migration = await asyncio.to_thread(
            backfill_existing_guess_points_to_shared_coins
        )
        print(
            "Guess shared-coin backfill: "
            f"{migration.get('users', 0)} users, "
            f"{migration.get('credited', 0)} coins credited.",
            flush=True,
        )
    except Exception as error:
        # Never block the Guess scheduler if the one-time wallet backfill has
        # a temporary repository/network problem. Future point awards safely
        # retry the per-user watermark sync.
        print(f"Guess shared-coin backfill warning: {error}", flush=True)

    # Rounds are manual-only. `n` / `!n` / `!next` starts the first game,
    # ends/reveals an active game, and then alternates to the other Guess mode.
    # No clock-based scheduler is started here.
    SCHEDULER_TASK = None

    print(
        "Guess Games controller is online in manual-only mode; use !next to start a round.",
        flush=True,
    )


@client.event
async def on_disconnect():
    print(
        "Guess Games Discord connection lost; reconnecting automatically.",
        flush=True,
    )


@client.event
async def on_resumed():
    print(
        "Guess Games Discord connection resumed.",
        flush=True,
    )


print("Starting persistent Guess Games controller...", flush=True)
client.run(TOKEN, reconnect=True)
