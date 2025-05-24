import asyncio
from collections.abc import Callable
from datetime import datetime
from discord.ext import commands

from utils.bot_utils import local_tz


class Scheduling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    async def schedule(self, what: Callable, when: datetime):
        wait_seconds = (when - datetime.now(local_tz)).total_seconds()
        if wait_seconds <= 0:
            return False
        
        await asyncio.sleep(wait_seconds)
        
        what()
        
        return True
    
    
    
def setup(bot: commands.Bot):
    bot.add_cog(Scheduling(bot))