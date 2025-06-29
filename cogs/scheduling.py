import asyncio
from collections.abc import Callable
from datetime import datetime
import inspect
import logging
from typing import cast
from discord import option
import discord
from discord.ext import commands

from utils.bot_utils import create_embed, local_tz


class Scheduling(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
    
    async def schedule(self, what: Callable, when: datetime):
        wait_seconds = (when - datetime.now(local_tz)).total_seconds()
        if wait_seconds <= 0:
            return False
        
        await asyncio.sleep(wait_seconds)
        
        result = what()
        if inspect.isawaitable(result):
            await result
        
        return True
    
    
    @commands.slash_command(
        name="schedule_post",
        description="Schedules a Discord post"
    )
    @option(
        "channel",
        description="Which channel to post the message in",
        input_type=discord.abc.GuildChannel,
        required=True
    )
    @option(
        "date",
        description="The date and time when to post the message (format: 15.1. 14:20)",
        input_type=str,
        required=True
    )
    @option(
        "id",
        description="The id of the message you want to schedule (only necessary if automatic fetch failed)",
        input_type=str,
        required=False
    )
    async def schedule_post(self, ctx: discord.ApplicationContext, channel: discord.abc.GuildChannel, date: str, id: str):
        to_schedule = None
        
        try:
            date_parts = date.split(" ")
            if date_parts and date_parts[0] and not date_parts[0].endswith("."):
                date = date.replace(" ", ". ", 1)
            dt = datetime.strptime(date, "%d.%m. %H:%M")
            now = datetime.now(local_tz)
            dt = dt.replace(year=now.year, tzinfo=local_tz)
            
            if dt < now:
                dt = dt.replace(year=now.year + 1, tzinfo=local_tz)
        except ValueError:
            await ctx.respond(embed=create_embed(description="Please use the correct format for the date: `15.1. 14:20`", color=0xFF0000))
            return
        
        if id:
            try:
                to_schedule = await ctx.channel.fetch_message(int(id))
            except discord.NotFound:
                await ctx.respond(embed=create_embed(description="Couldn't find the message, please check the id you provided.", color=0xFF0000))
                return
            except Exception as e:
                await ctx.respond(embed=create_embed(description="Something went wrong fetching your message, please try again.", color=0xFF0000))
                logging.error(f"Error fetching the message to schedule via id: {e}")
                return
        else:
            async for message in ctx.channel.history(limit=10):
                if message.author == ctx.author:
                    to_schedule = message
                    break
        
            if not to_schedule:
                await ctx.respond(embed=create_embed(description="Failed to fetch your message automatically, please provide the message id.", color=0xFF0000))
                return
        
        content: str = to_schedule.content
        files: list[discord.File] = await asyncio.gather(*(attachment.to_file() for attachment in to_schedule.attachments))
        
        print(f"Scheduled https://discord.com/channels/{ctx.guild_id}/{ctx.channel_id}/{to_schedule.id} for <t:{dt.timestamp()}:F>")
        
        async def send_scheduled_message():
            await cast(discord.TextChannel, channel).send(content, files=files)
        
        task: asyncio.Task = self.bot.loop.create_task(self.schedule(send_scheduled_message, dt))
        print(task)
        #TODO: liste erstellen, die die tasks speichert. mit .done() (ist ein boolean) und cancel() tasks verwalten
        #     self.scheduled_tasks = [t for t in self.scheduled_tasks if not t.done()]
        #for task in self.scheduled_tasks:
        #    if not task.done():
        #        task.cancel()

        #self.scheduled_tasks.clear()
        await ctx.respond(embed=create_embed(description=f"Scheduled https://discord.com/channels/{ctx.guild_id}/{ctx.channel_id}/{to_schedule.id} for <t:{int(dt.timestamp())}:F>", color=0x00FF00))
        
        #TODO: liste von planungen erstellen um sie bearbeiten oder canceln zu können
    
def setup(bot: commands.Bot):
    bot.add_cog(Scheduling(bot))