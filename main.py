import json
import logging
import os
import sys
import threading
import time as _time
from datetime import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
import discord
import psutil
from discord.ext import commands, tasks

from cogs.clash_stats import MODULE_LIST_FILE_PATH, OBSERVABLE_PAGES_LIST_FILE_PATH, ClashStats
from cogs.scheduling import SCHEDULED_POSTS_FILE_PATH, Scheduling
from utils.bot_utils import LOCAL_TZ, create_embed
from utils.botstate import bot_state

if TYPE_CHECKING:
    from cogs.page_error_reminders import PageErrorReminders

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s]: %(message)s",
    handlers=[logging.FileHandler("barbaricutils.log"), logging.StreamHandler()],
)

logger = logging.getLogger(__name__)

bot = commands.Bot(owner_id=191530044491956224)


BOT_REPORTS_CHANNEL_ID = 1403711339355963443
MEMORY_INTERVAL_HOURS = 12  # must be 0 < h <= 24
RESTART_ARGS_MIN = 3  # require at least [script, channel_id, message_id]


@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException) -> None:
    """Handle application command errors with user-friendly responses."""
    if isinstance(error, commands.NotOwner):
        await ctx.respond(
            embed=create_embed(description="Sorry, only the bot owner can use this command!", color=0xFF0000),
            ephemeral=True,
        )
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.respond(embed=create_embed(description="Sorry, this command can't be used in a DM!", color=0xFF0000))
    else:
        logger.error(error)
        raise error


@tasks.loop(
    time=tuple(time(hour=(i * MEMORY_INTERVAL_HOURS) % 24, tzinfo=LOCAL_TZ) for i in range(24 // MEMORY_INTERVAL_HOURS))
)
async def memory_reporter(channel: discord.TextChannel, process: psutil.Process) -> None:
    """Periodically report memory and CPU usage to the given channel."""
    mem_mb = process.memory_info().rss / 1024 / 1024
    total_mb = psutil.virtual_memory().total / 1024 / 1024
    cpu_percent = process.cpu_percent(interval=None)
    if channel:
        await channel.send(f"🖥 Memory: {mem_mb:.2f} MB / {total_mb:.0f} MB | CPU: {cpu_percent:.1f}%")


@tasks.loop(seconds=5)
async def watchdog_ticker() -> None:
    """Update the watchdog timestamp every few seconds."""
    bot_state.watchdog_last_tick = _time.time()


def watchdog(interval: int = 5, timeout: int = 15) -> None:
    """Kill the process if the event loop appears frozen for too long."""
    while True:
        _time.sleep(interval)
        if _time.time() - bot_state.watchdog_last_tick > timeout:
            logger.error("Bot appears frozen, killing the process...")
            os._exit(1)


@bot.slash_command(name="ping", description="Check the bot's latency")
async def ping(ctx: discord.ApplicationContext) -> None:
    """Respond with the current bot latency in milliseconds."""
    await ctx.respond(embed=create_embed("Latency", f"{round(bot.latency * 1000)} ms", color=0x000000))


@bot.slash_command(name="restart", description="Restart the bot (owner only)")
@commands.is_owner()
async def restart(ctx: discord.ApplicationContext) -> None:
    """Restart the bot process and notify the invoking context."""
    interaction = await ctx.respond("Restarting...")
    response = await cast("discord.Interaction", interaction).original_response()
    os.execv(sys.executable, ["python", *sys.argv, str(response.channel.id), str(response.id)])  # noqa: S606


@bot.listen(once=True)
async def on_ready() -> None:
    """Initialize persisted data, start background tasks, and announce readiness."""
    page_error_reminders = cast("PageErrorReminders", bot.get_cog("PageErrorReminders"))
    scheduling = cast("Scheduling", bot.get_cog("Scheduling"))
    clash_stats = cast("ClashStats", bot.get_cog("ClashStats"))

    # initialize json files
    try:
        if Path(MODULE_LIST_FILE_PATH).exists():
            async with await anyio.open_file(MODULE_LIST_FILE_PATH, "r") as file:
                clash_stats.data_module_names = list(
                    dict.fromkeys(sorted(json.loads(await file.read()), key=str.lower))
                )

        if Path(OBSERVABLE_PAGES_LIST_FILE_PATH).exists():
            async with await anyio.open_file(OBSERVABLE_PAGES_LIST_FILE_PATH, "r") as file:
                data = json.loads(await file.read())
                sorted_data = {key: sorted(value, key=str.lower) for key, value in data.items()}
                sorted_data = {key: sorted_data[key] for key in sorted(sorted_data.keys(), key=str.lower)}
                clash_stats.pages_with_manual_entries = sorted_data

        if Path(SCHEDULED_POSTS_FILE_PATH).exists():
            async with await anyio.open_file(SCHEDULED_POSTS_FILE_PATH, "r") as file:
                await scheduling.load_scheduled_posts(json.loads(await file.read()))
    except (OSError, json.JSONDecodeError):
        logger.exception("Error when reading and initializing json files")

    logger.info("Logged in as %s", bot.user)
    page_error_reminders.check_wiki_page_errors.start(bot.get_channel(page_error_reminders.channel_id))
    memory_reporter.start(bot.get_channel(BOT_REPORTS_CHANNEL_ID), psutil.Process(os.getpid()))

    watchdog_ticker.start()
    threading.Thread(target=watchdog, daemon=True).start()

    await bot.wait_until_ready()
    await cast("discord.TextChannel", bot.get_channel(BOT_REPORTS_CHANNEL_ID)).send(
        ":arrows_counterclockwise: Finished restarting!"
    )

    # Called after bot was restarted via command
    if len(sys.argv) >= RESTART_ARGS_MIN:
        channel = bot.get_channel(int(sys.argv[1]))
        msg = await cast("discord.TextChannel", channel).fetch_message(int(sys.argv[2]))
        await msg.edit(content="Restart has finished, I'm back!")


cogs_list = [
    "clash_stats",
    "page_error_reminders",
    "scheduling",
]

for cog in cogs_list:
    bot.load_extension(f"cogs.{cog}")

if __name__ == "__main__":
    try:
        token = os.environ.get("DISCORD_TOKEN")
        if not token:
            logger.error("DISCORD_TOKEN environment variable is not set. Exiting.")
            sys.exit(1)
        bot.run(token)
    except Exception:
        logger.exception("Fatal error in outer run loop!")
        sys.exit(1)

# TODO: implement scheduling stuff (wiki), add command: list observed/manual pages,
# schedule_post deferren, wenn mehrere Bilder angehängt werden
# delete reminder confirmation ephemaeral machen
# checken ob die persistent files vorher Path(/foo/bar.txt).parent.mkdir(exist_ok=True, parents=True) brauchen
