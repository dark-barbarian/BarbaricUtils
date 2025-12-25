import bisect
import io
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
from discord import HTTPException, option
from discord.ext import commands, tasks

from cogs import clash_stats

if TYPE_CHECKING:
    from cogs.page_error_reminders import PageErrorReminders
    from cogs.scheduling import Scheduling
from utils import wiki_operations
from utils.bot_utils import LOCAL_TZ, create_embed
from utils.botstate import bot_state

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


# TODO: convert to reaction emojis
async def cancel(ctx: discord.ApplicationContext, content: str) -> bool:
    """Return True and send a confirmation if content equals 'cancel'."""
    if content.lower() == "cancel":
        await ctx.send(embed=create_embed(description="Cancelled current action.", color=0x00FF00))
        return True
    return False


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


####################################################################


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


@bot.slash_command(name="wikiupdate", description="Update the wiki with CSV data")
@option(
    "module",
    description="The module you want to update",
    input_type=str,
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_module_names),
)
@option(
    "wiki",
    description=f'The wiki in which you want to update the values, default is "{wiki_operations.DEFAULT_WIKI}"',
    input_type=str,
    required=False,
    default=wiki_operations.DEFAULT_WIKI,
)
@commands.is_owner()
async def wikiupdate(ctx: discord.ApplicationContext, file: discord.Attachment, module: str, wiki: str) -> None:
    """Update wiki pages from a CSV attachment for the given module and wiki."""
    if file.content_type is None or not file.content_type.startswith("text/csv;"):
        await ctx.respond(
            embed=create_embed(description="The file you uploaded doesn't seem to be a CSV file.", color=0xFF0000),
            ephemeral=True,
        )
        return

    if module not in clash_stats.DATA_MODULE_NAMES:
        await ctx.respond(
            embed=create_embed(description="There is no module with this name!", color=0xFF0000), ephemeral=True
        )
        return

    if not module.startswith(("Modul:", "Module:")):
        module = "Modul:" + module

    await ctx.defer()

    try:
        await file.save(Path(clash_stats.CSV_FILE_PATH))
    except HTTPException:
        await ctx.respond(
            embed=create_embed(
                description="Something went wrong upon uploading your file. Please try again.", color=0xFF0000
            ),
            ephemeral=True,
        )
        logger.exception("Saving the attachment failed")
        return

    data, update_manually = clash_stats.update_wiki_stats(module, wiki)
    if not data or not isinstance(data, dict):
        await ctx.respond(
            embed=create_embed(description="Something went wrong. Please try again.", color=0xFF0000), ephemeral=True
        )
        return

    response = next(iter(data.keys()))
    if response == "edit" and data["edit"]["result"] == "Success":
        if len(update_manually):
            output_file_data = io.BytesIO("\n".join(sorted(update_manually, key=str.lower)).encode("utf-8"))
            output_file = discord.File(fp=output_file_data, filename="pages.txt")
            await ctx.respond(
                embed=create_embed(
                    description="Added the data successfully, but some pages need to be updated manually!",
                    color=0x00FF00,
                ),
                file=output_file,
            )
            return
        await ctx.respond(embed=create_embed(description="Added the data successfully!", color=0x00FF00))
    elif response == "error":
        await ctx.respond(
            embed=create_embed(description="Something went wrong!", footer=data["error"]["code"], color=0xFF0000),
            ephemeral=True,
        )
    else:
        await ctx.respond(embed=create_embed(description="Something went wrong!", color=0xFF0000), ephemeral=True)


@bot.slash_command(
    name="add_module", description="Adds a new module name to the module selection list (duplicates are ignored)"
)
@option("name", description="The name of the module you want to add", input_type=str)
@commands.is_owner()
async def add_module(ctx: discord.ApplicationContext, name: str) -> None:
    """Add a module name to the selection list (ignores duplicates)."""
    if name.startswith(("Modul:", "Module:")):
        name = name.split(":", 1)[1]

    bisect.insort(clash_stats.DATA_MODULE_NAMES, name, key=str.lower)
    clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(clash_stats.DATA_MODULE_NAMES))

    try:
        async with await anyio.open_file(clash_stats.MODULE_LIST_FILE_PATH, "w") as file:
            await file.write(json.dumps(clash_stats.DATA_MODULE_NAMES, ensure_ascii=False, indent=4))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to store module name to file")
        await ctx.respond(
            embed=create_embed(
                description="Added the new name until next restart, but couldn't store it.", color=0xFF0000
            )
        )
        return

    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(name="remove_module", description="Removes a module name from the module selection list")
@option(
    "name",
    description="The name of the module you want to remove",
    input_type=str,
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_module_names),
)
@commands.is_owner()
async def remove_module(ctx: discord.ApplicationContext, name: str) -> None:
    """Remove a module name from the selection list if present."""
    try:
        clash_stats.DATA_MODULE_NAMES.remove(name)
    except ValueError:
        await ctx.respond(embed=create_embed(description="This module does not exist!", color=0xFF0000))
        return

    try:
        async with await anyio.open_file(clash_stats.MODULE_LIST_FILE_PATH, "w") as file:
            await file.write(json.dumps(clash_stats.DATA_MODULE_NAMES, ensure_ascii=False, indent=4))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to remove module name from file, file might be empty now")
        await ctx.respond(
            embed=create_embed(
                description="Removed the new name until next restart, but saving failed.", color=0xFF0000
            )
        )
        return

    await ctx.respond(embed=create_embed(description="Removed the module name!", color=0x00FF00))


@bot.slash_command(
    name="add_observable_page", description="Adds a new page to be warned about when updating the wiki data"
)
@option(
    "category",
    description="The category this page belongs to (is created if not listed)",
    input_type=str,
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_page_categories),
)
@option("name", description="The name of the page you want to be observed", input_type=str)
@commands.is_owner()
async def add_observable_page(ctx: discord.ApplicationContext, category: str, name: str) -> None:
    """Add a page under a category to observe for manual updates."""
    observable_pages = clash_stats.PAGES_WITH_MANUAL_ENTRIES

    bisect.insort(observable_pages.setdefault(category, []), name)
    clash_stats.PAGES_WITH_MANUAL_ENTRIES = {
        key: observable_pages[key] for key in sorted(observable_pages.keys(), key=str.lower)
    }

    try:
        async with await anyio.open_file(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, "w") as file:
            await file.write(json.dumps(clash_stats.PAGES_WITH_MANUAL_ENTRIES, ensure_ascii=False, indent=4))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to store page name to file")
        await ctx.respond(
            embed=create_embed(
                description="Added the new name until next restart, but couldn't store it.", color=0xFF0000
            )
        )
        return

    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(
    name="remove_observable_page",
    description="Removes a page or category from the observer list (does nothing if name doesn't exist)",
)
@option(
    "name",
    description="The page name you want to remove (its category is also removed if empty)",
    input_type=str,
    autocomplete=clash_stats.autocomplete_page_observer_names,
)
@commands.is_owner()
async def remove_observable_page(ctx: discord.ApplicationContext, name: str) -> None:
    """Remove a page (and empty categories) from the observer list."""
    observable_pages = clash_stats.PAGES_WITH_MANUAL_ENTRIES
    observable_pages_keys = list(observable_pages.keys())

    error_counter = 0
    for key in observable_pages_keys:
        try:
            observable_pages[key].remove(name)
        except ValueError:
            error_counter += 1

        if len(observable_pages[key]) == 0:
            del observable_pages[key]

    if error_counter == len(observable_pages_keys):
        await ctx.respond(embed=create_embed(description="Name doesn't exist!", color=0xFF0000))
        return

    try:
        async with await anyio.open_file(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, "w") as file:
            await file.write(json.dumps(observable_pages, ensure_ascii=False, indent=4))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to remove page name from file, file might be empty now")
        await ctx.respond(
            embed=create_embed(
                description="Removed the new name until next restart, but saving failed.", color=0xFF0000
            )
        )
        return

    await ctx.respond(embed=create_embed(description="Removed the page name!", color=0x00FF00))


##################################################################


@bot.listen(once=True)
async def on_ready() -> None:
    """Initialize persisted data, start background tasks, and announce readiness."""
    page_error_reminders = cast("PageErrorReminders", bot.get_cog("PageErrorReminders"))
    scheduling = cast("Scheduling", bot.get_cog("Scheduling"))

    # initialize json files
    try:
        if Path(clash_stats.MODULE_LIST_FILE_PATH).exists():
            async with await anyio.open_file(clash_stats.MODULE_LIST_FILE_PATH, "r") as file:
                clash_stats.DATA_MODULE_NAMES = list(
                    dict.fromkeys(sorted(json.loads(await file.read()), key=str.lower))
                )

        if Path(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH).exists():
            async with await anyio.open_file(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, "r") as file:
                data = json.loads(await file.read())
                sorted_data = {key: sorted(value, key=str.lower) for key, value in data.items()}
                sorted_data = {key: sorted_data[key] for key in sorted(sorted_data.keys(), key=str.lower)}
                clash_stats.PAGES_WITH_MANUAL_ENTRIES = sorted_data

        if Path(page_error_reminders.categories_json_file_path).exists():
            async with await anyio.open_file(page_error_reminders.categories_json_file_path, "r") as file:
                page_error_reminders.wiki_categories = json.loads(await file.read())

        if Path(scheduling.scheduled_posts_file_path).exists():
            async with await anyio.open_file(scheduling.scheduled_posts_file_path, "r") as file:
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
    # TODO: clash_stats in Klasse umwandeln und setup funktion geben
    #'clash_stats',
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

# TODO: implement scheduling stuff (wiki), add command: list observed/manual pages, make deletions use autocomplete
# when removing reminders or posts, pull up an autocomplete menu to delete it without having to enter the id
