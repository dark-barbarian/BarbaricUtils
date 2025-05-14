import asyncio
import bisect
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

import discord
from discord import HTTPException, option
from discord.ext import commands

from cogs import clash_stats, page_error_reminders
import config
from utils import wiki_operations

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s]: %(message)s', handlers=[
    logging.FileHandler('barbaricutils.log'),
    logging.StreamHandler()
])

bot = commands.Bot(owner_id=191530044491956224)

####################################################################
######################### GENERAL METHODS ##########################
####################################################################

def create_embed(title=None, description=None, color=None, footer=None):
    embed_var = discord.Embed(title=title, description=description, color=color)
    embed_var.set_footer(text=footer)
    return embed_var

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

async def check_wiki_page_errors():
    await bot.wait_until_ready()
    channel = bot.get_channel(page_error_reminders.CHANNEL_ID)
    
    data = page_error_reminders.fetch_categories("darkbarbarian.fandom.com", "Benutzer:DarkBarbarian/WikiCategories.json")
    if data:
        page_error_reminders.WIKI_CATEGORIES.update(data)
        try:
            with open(page_error_reminders.CATEGORIES_JSON_FILE_PATH, 'w') as file:
                json.dump(page_error_reminders.WIKI_CATEGORIES, file, ensure_ascii=False, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store wiki categories to file: {e}")
            return
    
    while True:
        error_counter = 0
        for wiki, categories in page_error_reminders.WIKI_CATEGORIES.items():
            if wiki == "allowed_errors":
                continue
            
            message = f"# Category report for {wiki}\n"
            for category in categories:
                logging.info(f"Checking category '{category}' on wiki '{wiki}'")
                pages = page_error_reminders.fetch_category_members(wiki, category)
                
                message = f"{message}- [{category}](<https://{wiki}/wiki/Kategorie:{category.replace(' ', '_')}>) - "
                if pages is not None:
                    error_count = len(pages)
                    message += f"{error_count} page(s){" :warning:\n" if error_count > 0 else "\n"}"
                    error_counter += error_count
                else:
                    message += "error\n"
                
                await asyncio.sleep(1)
            
            if error_counter > page_error_reminders.WIKI_CATEGORIES.get("allowed_errors", 0):
                message += f"<@{bot.owner_id}>"
            
            if channel:
                await channel.send(message)

        # TODO: auf local timezone umschreiben (server hat UTC)
        now = datetime.now(timezone.utc).astimezone()
        days_until = (page_error_reminders.CATEGORY_CHECK_DAY_HOUR[0] - now.weekday()) % 7
        target_time = (now + timedelta(days=days_until)).replace(hour=page_error_reminders.CATEGORY_CHECK_DAY_HOUR[1], minute=0, second=0, microsecond=0)
        if now > target_time:
            target_time += timedelta(weeks=1)
        
        await asyncio.sleep((target_time - now).total_seconds())
        

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
    if not file.content_type.startswith("text/csv;"):
        await ctx.respond(embed=create_embed(description="The file you uploaded doesn't seem to be a CSV file.",
                                             color=0xFF0000), ephemeral=True)
        return
    
    if not module in clash_stats.DATA_MODULE_NAMES:
        await ctx.respond(embed=create_embed(description="There is no module with this name!",
                                             color=0xFF0000), ephemeral=True)
        return
    
    if not module.startswith('Modul:'):
        module = "Modul:" + module
    
    await ctx.defer()

    try:
        await file.save(Path(clash_stats.CSV_FILE_PATH))
    except HTTPException as e:
        await ctx.respond(embed=create_embed(description="Something went wrong upon uploading your file. "
                                                         "Please try again.", color=0xFF0000), ephemeral=True)
        logging.error(f"Saving the attachment failed: {e}")
        return

    # Todo: ins embed schreiben
    data, update_manually = clash_stats.update_wiki_stats(module, wiki)
    response = list(data.keys())[0]
    if response == 'edit' and data['edit']['result'] == 'Success':
        await ctx.respond(embed=create_embed(description="Added the data successfully!", color=0x00FF00))
    elif response == 'error':
        await ctx.respond(embed=create_embed(description="Something went wrong!",
                                             footer=data['error']['code'], color=0xFF0000), ephemeral=True)
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


@bot.slash_command(
    name="update_allowed_errors",
    description="Updates the amount of errors the category report may yield without notifying a certain someone"
)
@option(
    "value",
    description="Up to this many errors are allowed and will not result in a ping",
    input_type=int,
    min_value=0
)
@commands.is_owner()
async def update_allowed_errors(ctx: discord.ApplicationContext, value: int):
    page_error_reminders.WIKI_CATEGORIES["allowed_errors"] = value
    try:
        with open(page_error_reminders.CATEGORIES_JSON_FILE_PATH, 'w') as file:
            json.dump(page_error_reminders.WIKI_CATEGORIES, file, ensure_ascii=False, indent=4)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Failed to store allowed errors to file: {e}")
        await ctx.respond(embed=create_embed(description="Updated allowed errors until next restart, but saving failed.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Updated allowed errors in the category report!", color=0x00FF00))

##################################################################
############################ RUN BOT #############################
##################################################################

@bot.listen(once=True)
async def on_ready():
    # initialize json files
    try:
        with open(clash_stats.MODULE_LIST_FILE_PATH, 'r') as file:
            clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(sorted(json.load(file), key=str.lower)))
        
        with open(clash_stats.OBSERVABLE_PAGES_LIST_FILE_PATH, 'r') as file:
            data = json.load(file)
            sorted_data = {key: sorted(value, key=str.lower) for key, value in data.items()}
            sorted_data = {key: sorted_data[key] for key in sorted(sorted_data.keys(), key=str.lower)}
            clash_stats.PAGES_WITH_MANUAL_ENTRIES = sorted_data
        
        with open(page_error_reminders.CATEGORIES_JSON_FILE_PATH, 'r') as file:
            page_error_reminders.WIKI_CATEGORIES = json.load(file)
    except (OSError, json.JSONDecodeError) as e:
        logging.error(f"Error when reading and initializing json files: {e}")
        pass

    logging.info(f'Logged in as {bot.user}')
    bot.loop.create_task(check_wiki_page_errors())


bot.run(config.DISCORD_TOKEN)

#TODO: hash configs, create command for updating manual pages, instead of editing the txt itself, implement scheduling stuff,
# output über manual pages als bot nachricht, nicht als log, list observed pages
