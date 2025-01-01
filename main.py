import logging
from pathlib import Path

import discord
from discord import option
from discord.ext import commands

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
    autocomplete=discord.utils.basic_autocomplete(clash_stats.DATA_MODULE_NAMES)
)
@option(
    "wiki",
    description=f"The wiki in which you want to update the values, default is \"{wiki_operations.DEFAULT_WIKI}\"",
    required=False,
    default=wiki_operations.DEFAULT_WIKI
)
@commands.is_owner()
async def wikiupdate(ctx: discord.ApplicationContext, file: discord.Attachment, module: str, wiki: str):
    if not module.startswith('Modul:'):
        module = "Modul:" + module

    if not file.content_type.startswith("text/csv;"):
        await ctx.respond(embed=create_embed(description="The file you uploaded doesn't seem to be a CSV file.",
                                             color=0xFF0000), ephemeral=True)
        return
    
    await ctx.defer()

    try:
        await file.save(Path(clash_stats.FILE_PATH))
    except (Exception,):
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
    description="Adds a new module name to the module selection list"
)
@commands.is_owner()
async def addmodule(ctx: discord.ApplicationContext, name: str):
    clash_stats.DATA_MODULE_NAMES.append(name)
    await ctx.respond(embed=create_embed(description="Added the new name!", color=0x00FF00))


@bot.slash_command(
    name="removemodule",
    description="Removes a module name from the module selection list"
)
@option(
    "name",
    description="The module name want to remove",
    autocomplete=discord.utils.basic_autocomplete(clash_stats.DATA_MODULE_NAMES)
)
@commands.is_owner()
async def removemodule(ctx: discord.ApplicationContext, name: str):
    clash_stats.DATA_MODULE_NAMES.remove(name)
    await ctx.respond(embed=create_embed(description="Removed the module name!", color=0x00FF00))

##################################################################
############################ RUN BOT #############################
##################################################################

@bot.listen(once=True)
async def on_ready():
    logging.info(f'Logged in as {bot.user}')

bot.run(config.DISCORD_TOKEN)