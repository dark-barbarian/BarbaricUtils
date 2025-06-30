import asyncio
from collections.abc import Callable
from datetime import datetime
import hashlib
import inspect
import json
import logging
import os
from typing import cast
from discord import option
import discord
from discord.ext import commands

from utils.bot_utils import create_embed, local_tz


class Scheduling(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.scheduled_tasks: dict[str, asyncio.Task] = {}
        
        self.scheduled_posts_file_path = "./scheduled_posts.json"
        os.makedirs("attachments", exist_ok=True)
    
    def get_wait_seconds(self, dt: datetime):
        return (dt - datetime.now(local_tz)).total_seconds()
    
    async def schedule(self, what: Callable, wait_seconds: float, *args):       
        await asyncio.sleep(wait_seconds)
        
        result = what(*args)
        if inspect.isawaitable(result):
            await result
    
    async def send_scheduled_message(self, scheduled_posts, task_id, channel, content, files, publish):
        message = await cast(discord.TextChannel, channel).send(content, files=files)
        if publish:
            await message.publish()
        
        scheduled_posts = [post for post in scheduled_posts if post["task_id"] != task_id]
        
        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store scheduled post to file: {e}")
    
    def load_scheduled_posts(self, posts: list):
        for post in posts:
            wait_seconds = self.get_wait_seconds(datetime.fromisoformat(post["post_time"]))
            if wait_seconds <= 0:
                continue
            
            self.scheduled_tasks[post["id"]] = self.bot.loop.create_task(self.schedule(
                self.send_scheduled_message,
                wait_seconds,
                posts,
                post["id"],
                self.bot.get_channel(post["channel_id"]),
                post["content"],
                [discord.File(path) for path in post["attachments"] if os.path.exists(path)],
                post["publish"]))
    
    def delete_finished_tasks(self):
        self.scheduled_tasks = {
            k: t for k, t in self.scheduled_tasks.items() if not t.done()
        }
    
    def cancel_all_tasks(self):
        for _, task in self.scheduled_tasks.items():
            task.cancel()
    
    
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
    @option(
        "publish",
        description="Whether or not to publish the message after it was posted",
        input_type=bool,
        required=False
    )
    async def schedule_post(self, ctx: discord.ApplicationContext, channel: discord.abc.GuildChannel, date: str, id: str, publish: bool):
        self.delete_finished_tasks()
        
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
        
        wait_seconds = self.get_wait_seconds(dt)
        if wait_seconds <= 0:
            await ctx.respond(embed=create_embed(description="The date must be in the future.", color=0xFF0000))
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
        
        scheduled_posts = []
        try:
            if os.path.exists(self.scheduled_posts_file_path):
                with open(self.scheduled_posts_file_path, 'r') as file:
                    scheduled_posts = json.load(file)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Error when reading {self.scheduled_posts_file_path}: {e}")
        
        task_id = str(int(now.timestamp()))[-6:]
        while task_id in self.scheduled_tasks:
            task_id = str(int(task_id) + 1)
        
        content: str = to_schedule.content
        files: list[discord.File] = await asyncio.gather(*(attachment.to_file() for attachment in to_schedule.attachments))
            
        self.scheduled_tasks[task_id] = self.bot.loop.create_task(self.schedule(
            self.send_scheduled_message, wait_seconds, scheduled_posts, task_id, channel, content, files, publish))
        
        attachment_paths = []
        for i, attachment in enumerate(cast(discord.Message, to_schedule).attachments):
            filename = f"attachments/{task_id}_{i}_{attachment.filename}"
            await attachment.save(filename) # type: ignore
            attachment_paths.append(filename)
        
        scheduled_posts.append({
            "id": task_id,
            "channel_id": channel.id,
            "content": content,
            "post_time": dt.isoformat(),
            "attachments": attachment_paths,
            "publish": publish
        })
        
        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store scheduled post to file: {e}")
        
        await ctx.respond(embed=create_embed(description=f"`{task_id}`: Scheduled https://discord.com/channels/{ctx.guild_id}/{ctx.channel_id}/{to_schedule.id} for <t:{int(dt.timestamp())}:F>", color=0x00FF00))
    
    #TODO
    @commands.slash_command(
        name="list_scheduled_posts",
        description=""
    )
    
    @commands.slash_command(
        name="modify_scheduled_post",
        description=""
    )
    
    @commands.slash_command(
        name="delete_scheduled_post",
        description=""
    )
    
def setup(bot: commands.Bot):
    bot.add_cog(Scheduling(bot))
