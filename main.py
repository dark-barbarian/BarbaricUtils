import bisect
from datetime import time
import io
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time as t
from typing import cast

import discord
from discord import HTTPException, option
from discord.ext import commands, tasks
import psutil

from cogs import clash_stats
from cogs.page_error_reminders import PageErrorReminders
from cogs.scheduling import Scheduling
import config
from utils import wiki_operations
from utils.bot_utils import create_embed, local_tz

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s]: %(message)s', handlers=[
    logging.FileHandler('barbaricutils.log'),
    logging.StreamHandler()
])

bot = commands.Bot(owner_id=191530044491956224)
watchdog_last_tick = t.time()

####################################################################
######################### GENERAL METHODS ##########################
####################################################################

BOT_REPORTS_CHANNEL_ID = 1403711339355963443
MEMORY_INTERVAL_HOURS = 12  # must be 0 < h <= 24

# TODO: convert to reaction emojis
async def cancel(ctx: discord.ApplicationContext, content: str):
    if content.lower() == "cancel":
        await ctx.send(embed=create_embed(description="Cancelled current action.", color=0x00FF00))
        return True
    return False

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.NotOwner):
        await ctx.respond(embed=create_embed('Error', "Sorry, only the bot owner can use this command!", color=0xFF0000))
    else:
        logging.error(error)
        raise error

@tasks.loop(
    time=tuple(
        time(hour=(i * MEMORY_INTERVAL_HOURS) % 24, tzinfo=local_tz)
        for i in range(24 // MEMORY_INTERVAL_HOURS)
    )
)
async def memory_reporter(channel: discord.TextChannel, process: psutil.Process):
    mem_mb = process.memory_info().rss / 1024 / 1024
    total_mb = psutil.virtual_memory().total / 1024 / 1024
    cpu_percent = process.cpu_percent(interval=None)
    if channel:
        await channel.send(f"🖥 Memory: {mem_mb:.2f} MB / {total_mb:.0f} MB | CPU: {cpu_percent:.1f}%")

@tasks.loop(seconds=5)
async def watchdog_ticker():
    global watchdog_last_tick
    watchdog_last_tick = t.time()

def watchdog(interval=5, timeout=15):
    while True:
        t.sleep(interval)
        if t.time() - watchdog_last_tick > timeout:
            logging.error("Bot appears frozen, killing the process...")
            os._exit(1)

####################################################################
############################ COMMANDS ##############################
####################################################################

@bot.slash_command(
        name="ping",
        description="Check the bot's latency"
)
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond(embed=create_embed('Latency', f'{round(bot.latency * 1000)} ms', color=0x000000))
    return


@bot.slash_command(
    name="restart",
    description="Restart the bot (owner only)"
)
@commands.is_owner()
async def restart (ctx: discord.ApplicationContext):
    interaction = await ctx.respond("Restarting...")
    response = await cast(discord.Interaction, interaction).original_response()
    os.execv(sys.executable, ['python'] + sys.argv + [str(response.channel.id), str(response.id)])


@bot.slash_command(
    name="wikiupdate",
    description="Update the wiki with CSV data"
)
@option(
    "module",
    description="The module you want to update",
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_module_names)
)
@option(
    "wiki",
    description=f"The wiki in which you want to update the values, default is \"{wiki_operations.DEFAULT_WIKI}\"",
    required=False,
    default=wiki_operations.DEFAULT_WIKI
)
@commands.is_owner()
async def wikiupdate(ctx: discord.ApplicationContext, file: discord.Attachment, module: str, wiki: str):
    if file.content_type is None or not file.content_type.startswith("text/csv;"):
        await ctx.respond(embed=create_embed(description="The file you uploaded doesn't seem to be a CSV file.",
                                             color=0xFF0000), ephemeral=True)
        return
    
    if not module in clash_stats.DATA_MODULE_NAMES:
        await ctx.respond(embed=create_embed(description="There is no module with this name!",
                                             color=0xFF0000), ephemeral=True)
        return
    
    if not module.startswith(('Modul:', 'Module:')):
        module = "Modul:" + module
    
    await ctx.defer()

    try:
        await file.save(Path(clash_stats.CSV_FILE_PATH))
    except HTTPException as e:
        await ctx.respond(embed=create_embed(description="Something went wrong upon uploading your file. "
                                                         "Please try again.", color=0xFF0000), ephemeral=True)
        logging.error(f"Saving the attachment failed: {e}")
        return

    data, update_manually = clash_stats.update_wiki_stats(module, wiki)
    if not data:
        await ctx.respond(embed=create_embed(description="Something went wrong. Please try again.", color=0xFF0000), ephemaral=True)
        return
    
    response = list(data.keys())[0] # type: ignore
    if response == 'edit' and data['edit']['result'] == 'Success': # type: ignore
        if len(update_manually):
            output_file_data = io.BytesIO("\n".join(sorted(update_manually, key=str.lower)).encode("utf-8"))
            output_file = discord.File(fp=output_file_data, filename="pages.txt")
            await ctx.respond(embed=create_embed(description="Added the data successfully, but some pages need to be updated manually!", color=0x00FF00), file=output_file)
            return
        await ctx.respond(embed=create_embed(description="Added the data successfully!", color=0x00FF00))
    elif response == 'error':
        await ctx.respond(embed=create_embed(description="Something went wrong!",
                                             footer=data['error']['code'], color=0xFF0000), ephemeral=True) # type: ignore
    else:
        await ctx.respond(embed=create_embed(description="Something went wrong!", color=0xFF0000), ephemeral=True)


@bot.slash_command(
    name="add_module",
    description="Adds a new module name to the module selection list (duplicates are ignored)"
)
@option(
    "name",
    description="The name of the module you want to add"
)
@commands.is_owner()
async def add_module(ctx: discord.ApplicationContext, name: str):
    if name.startswith(('Modul:', 'Module:')):
        name = name.split(':', 1)[1]
    
    bisect.insort(clash_stats.DATA_MODULE_NAMES, name, key=str.lower)
    clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(clash_stats.DATA_MODULE_NAMES))
    
    try:
        with open(clash_stats.MODULE_LIST_FILE_PATH, 'w') as file:
            json.dump(clash_stats.DATA_MODULE_NAMES, file, ensure_ascii=False, indent=4)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Failed to store module name to file: {e}")
        await ctx.respond(embed=create_embed(description="Added the new name until next restart, but couldn't store it.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(
    name="remove_module",
    description="Removes a module name from the module selection list"
)
@option(
    "name",
    description="The name of the module you want to remove",
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_module_names)
)
@commands.is_owner()
async def remove_module(ctx: discord.ApplicationContext, name: str):
    try:
        clash_stats.DATA_MODULE_NAMES.remove(name)
    except ValueError:
        await ctx.respond(embed=create_embed(description="This module does not exist!", color=0xFF0000))
        return
    
    try:
        with open(clash_stats.MODULE_LIST_FILE_PATH, 'w') as file:
            json.dump(clash_stats.DATA_MODULE_NAMES, file, ensure_ascii=False, indent=4)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Failed to remove module name from file, file might be empty now: {e}")
        await ctx.respond(embed=create_embed(description="Removed the new name until next restart, but saving failed.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Removed the module name!", color=0x00FF00))


@bot.slash_command(
    name="add_observable_page",
    description="Adds a new page to be warned about when updating the wiki data"
)
@option(
    "category",
    description="The category this page belongs to (is created if not listed)",
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_page_categories)
)
@option(
    "name",
    description="The name of the page you want to be observed"
)
@commands.is_owner()
async def add_observable_page(ctx: discord.ApplicationContext, category: str, name: str):
    observable_pages = clash_stats.PAGES_WITH_MANUAL_ENTRIES
    
    bisect.insort(observable_pages.setdefault(category, []), name)
    clash_stats.PAGES_WITH_MANUAL_ENTRIES = {key: observable_pages[key] for key in sorted(observable_pages.keys(), key=str.lower)}

    try:
        with open(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, 'w') as file:
            json.dump(clash_stats.PAGES_WITH_MANUAL_ENTRIES, file, ensure_ascii=False, indent=4)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Failed to store page name to file: {e}")
        await ctx.respond(embed=create_embed(description="Added the new name until next restart, but couldn't store it.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(
    name="remove_observable_page",
    description="Removes a page or category from the observer list (does nothing if name doesn't exist)"
)
@option(
    "name",
    description="The page name you want to remove (its category is also removed if empty)",
    autocomplete=clash_stats.autocomplete_page_observer_names
)
@commands.is_owner()
async def remove_observable_page(ctx: discord.ApplicationContext, name: str):
    observable_pages = clash_stats.PAGES_WITH_MANUAL_ENTRIES
    observable_pages_keys = list(observable_pages.keys())
    
    error_counter = 0
    for key in observable_pages_keys:
        try:
            observable_pages[key].remove(name)
        except ValueError:
            error_counter += 1
            pass
        
        if len(observable_pages[key]) == 0:
            del observable_pages[key]
    
    if error_counter == len(observable_pages_keys):
        await ctx.respond(embed=create_embed(description="Name doesn't exist!", color=0xFF0000))
        return
    
    try:
        with open(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, 'w') as file:
            json.dump(observable_pages, file, ensure_ascii=False, indent=4)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Failed to remove page name from file, file might be empty now: {e}")
        await ctx.respond(embed=create_embed(description="Removed the new name until next restart, but saving failed.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Removed the page name!", color=0x00FF00))

##################################################################
############################ RUN BOT #############################
##################################################################

@bot.listen(once=True)
async def on_ready():
    page_error_reminders = cast(PageErrorReminders, bot.get_cog("PageErrorReminders"))
    scheduling = cast(Scheduling, bot.get_cog("Scheduling"))
        
    # initialize json files
    try:
        if os.path.exists(clash_stats.MODULE_LIST_FILE_PATH):
            with open(clash_stats.MODULE_LIST_FILE_PATH, 'r') as file:
                clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(sorted(json.load(file), key=str.lower)))
        
        if os.path.exists(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH):
            with open(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, 'r') as file:
                data = json.load(file)
                sorted_data = {key: sorted(value, key=str.lower) for key, value in data.items()}
                sorted_data = {key: sorted_data[key] for key in sorted(sorted_data.keys(), key=str.lower)}
                clash_stats.PAGES_WITH_MANUAL_ENTRIES = sorted_data
        
        if os.path.exists(page_error_reminders.categories_json_file_path):
            with open(page_error_reminders.categories_json_file_path, 'r') as file:
                page_error_reminders.wiki_categories = json.load(file)
        
        if os.path.exists(scheduling.scheduled_posts_file_path):
            with open(scheduling.scheduled_posts_file_path, 'r') as file:
                await scheduling.load_scheduled_posts(json.load(file))
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Error when reading and initializing json files: {e}")
    
    logging.info(f'Logged in as {bot.user}')
    page_error_reminders.check_wiki_page_errors.start(bot.get_channel(page_error_reminders.channel_id))
    memory_reporter.start(bot.get_channel(BOT_REPORTS_CHANNEL_ID), psutil.Process(os.getpid()))
    
    watchdog_ticker.start()
    threading.Thread(target=watchdog, daemon=True).start()
    
    await bot.wait_until_ready()
    await cast(discord.TextChannel, bot.get_channel(BOT_REPORTS_CHANNEL_ID)).send(":arrows_counterclockwise: Finished restarting!")
    
    # Called after bot was restarted via command
    if (len(sys.argv) > 2):
        channel = bot.get_channel(int(sys.argv[1]))
        msg = await cast(discord.TextChannel, channel).fetch_message(int(sys.argv[2]))
        await msg.edit(content="Restart has finished, I'm back!")


cogs_list = [
    #TODO: clash_stats in Klasse umwandeln und setup funktion geben
    #'clash_stats',
    'page_error_reminders',
    'scheduling'
]

for cog in cogs_list:
    bot.load_extension(f"cogs.{cog}")

try:
    bot.run(config.DISCORD_TOKEN)
except Exception:
    logging.exception('Fatal error in outer run loop!')
    sys.exit(1)

#TODO: implement scheduling stuff (wiki), add command: list observed/manual pages
