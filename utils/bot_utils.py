from zoneinfo import ZoneInfo
import discord


local_tz = ZoneInfo("Europe/Berlin")
        
def create_embed(title=None, description=None, color=None, footer=None):
    embed_var = discord.Embed(title=title, description=description, color=color)
    embed_var.set_footer(text=footer)
    return embed_var