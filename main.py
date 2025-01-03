import bisect
import logging
import os
from pathlib import Path
import time

import discord
from discord import option
from discord.ext import commands
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from classes import clash_stats, wiki_operations
import config

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

class FileModifiedEventHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_modified = time.time()
        
    def on_modified(self, event):
        if event.is_directory or time.time() - self.last_modified < 1:
            return
        else:
            self.last_modified = time.time()
        
        start_time = time.time()
        previous_size = -1
        stable_count = 0
        max_stable_checks = 3  # Number of checks to ensure the file size is stable

        while True:
            try:
                current_size = os.path.getsize(event.src_path)
            except FileNotFoundError:
                # If the file is temporarily unavailable, wait and retry
                current_size = -1

            if current_size == previous_size and current_size > 0:
                stable_count += 1
                if stable_count >= max_stable_checks:
                    break
            else:
                stable_count = 0  # Reset the counter if the size changes
            
            if time.time() - start_time > 2:
                logging.warning(f"Timeout waiting for size of '{event.src_path}' to become non-zero. No changes were made.")
                return
            
            previous_size = current_size
            time.sleep(0.1)

        try:
            clash_stats.PAGES_WITH_MANUAL_ENTRIES = set([line.rstrip() for line in open("./updatemanually.txt") if line != '\n'])
        except OSError as e:
            logging.error(f"Error reading file '{event.src_path}': {e}")
        

observer = Observer()
observer.schedule(FileModifiedEventHandler(), "./updatemanually.txt", recursive = False)
observer.start()

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
        await file.save(Path(clash_stats.FILE_PATH))
    except:
        await ctx.respond(embed=create_embed(description="Something went wrong upon uploading your file. "
                                                         "Please try again.", color=0xFF0000), ephemeral=True)
        return
    
    data = clash_stats.update_wiki_stats(module, wiki)
    response = list(data.keys())[0]
    if response == 'edit' and data['edit']['result'] == 'Success':
        await ctx.respond(embed=create_embed(description="Added the data successfully!", color=0x00FF00))
    elif response == 'error':
        await ctx.respond(embed=create_embed(description="Something went wrong!",
                                             footer=data['error']['code'], color=0xFF0000), ephemeral=True)
    else:
        await ctx.respond(embed=create_embed(description="Something went wrong!", color=0xFF0000), ephemeral=True)


@bot.slash_command(
    name="addmodule",
    description="Adds a new module name to the module selection list (duplicates are ignored)"
)
@commands.is_owner()
async def addmodule(ctx: discord.ApplicationContext, name: str):
    bisect.insort(clash_stats.DATA_MODULE_NAMES, name, key=str.lower)
    clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(clash_stats.DATA_MODULE_NAMES))
    
    try:
        with open("csvmodules.txt", 'a') as file:
            file.write(name + '\n')
    except OSError as e:
        logging.error(f"Failed to store module name to file: {e}")
        await ctx.respond(embed=create_embed(description="Added the new name until next restart, but couldn't store it.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(
    name="removemodule",
    description="Removes a module name from the module selection list"
)
@option(
    "name",
    description="The module name you want to remove",
    autocomplete=discord.utils.basic_autocomplete(clash_stats.autocomplete_module_names)
)
@commands.is_owner()
async def removemodule(ctx: discord.ApplicationContext, name: str):
    try:
        clash_stats.DATA_MODULE_NAMES.remove(name)
    except ValueError:
        await ctx.respond(embed=create_embed(description="This module does not exist!", color=0xFF0000))
        return
    
    try:
        with open("csvmodules.txt", 'w') as file:
            for module in clash_stats.DATA_MODULE_NAMES:
                file.write(f"{module}\n")
    except OSError as e:
        logging.error(f"Failed to remove module name from file, file might be empty now: {e}")
        await ctx.respond(embed=create_embed(description="Removed the new name until next restart, but saving failed.", color=0xFF0000))
        return
    
    await ctx.respond(embed=create_embed(description="Removed the module name!", color=0x00FF00))

##################################################################
############################ RUN BOT #############################
##################################################################

@bot.listen(once=True)
async def on_ready():
    # initialize txt files
    try:
        clash_stats.DATA_MODULE_NAMES = list(dict.fromkeys(sorted([line.rstrip() for line in open("csvmodules.txt")], key=str.lower)))
        clash_stats.PAGES_WITH_MANUAL_ENTRIES = set([line.rstrip() for line in open("updatemanually.txt") if line != '\n'])
    except OSError as e:
        logging.error(f"Error when reading initializing txt files: {e}")
        pass

    logging.info(f'Logged in as {bot.user}')


bot.run(config.DISCORD_TOKEN)

observer.stop()
observer.join()

#TODO: hash configs, create command for updating manual pages, instead of editing the txt itself, implement scheduling stuff