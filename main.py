import logging
import config
import discord
from discord.ext import commands

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

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    if isinstance(error, commands.NotOwner):
        await ctx.respond("Sorry, only the bot owner can use this command!")
    else:
        logging.error(error)
        raise error

####################################################################
############################ COMMANDS ##############################
####################################################################

@bot.slash_command(
        name="ping",
        description="Check the bot's latency (test)",
        guild_ids=[248493533537763328]
)
@commands.is_owner()
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond(embed=create_embed('Latency', f'{round(bot.latency * 1000)} ms', color=0x000000))

##################################################################
############################ RUN BOT #############################
##################################################################

@bot.listen(once=True)
async def on_ready():
    logging.info(f'Logged in as {bot.user}')

bot.run(config.DISCORD_TOKEN)