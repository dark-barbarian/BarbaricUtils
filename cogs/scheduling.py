import asyncio
from collections.abc import Callable
from datetime import datetime
import inspect
import json
import logging
import os
from typing import Any, Coroutine, cast
from discord import option
import discord
from discord.ext import commands

from utils.bot_utils import create_embed, create_task_with_logging, local_tz


class Scheduling(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.scheduled_tasks: dict[str, asyncio.Task] = {}
        self.scheduled_posts: list[dict] = []
        
        self.scheduled_posts_file_path = "./scheduled_posts.json"
        os.makedirs("attachments", exist_ok=True)
    
    def get_wait_seconds(self, dt: datetime):
        return (dt - datetime.now(local_tz)).total_seconds()
    
    async def schedule(self, what: Callable[..., Coroutine[Any, Any, None] | None], wait_seconds: float, id: str, *args):
        await asyncio.sleep(wait_seconds)
        
        logging.info(f"Finished waiting, posting {id} now.")
        result = what(*args)
        if inspect.isawaitable(result):
            await result
    
    async def send_scheduled_message(self, channel: discord.TextChannel, content: str, file_paths: list[str], publish: bool):
        files = [discord.File(path) for path in file_paths if os.path.exists(path)]
        message = await channel.send(content, files=files)
        if publish:
            await message.publish()
    
    def load_scheduled_posts(self, posts: list):
        for post in posts:
            wait_seconds = self.get_wait_seconds(datetime.fromisoformat(post["post_time"]))
            if wait_seconds <= 0:
                logging.info(f"Trying to schedule {post['id']} failed: Due date is in the past.")
                continue
            
            self.scheduled_tasks[post["id"]] = self.create_schedule_task(
                wait_seconds=wait_seconds,
                task_id=post["id"],
                channel=self.bot.get_channel(post["channel_id"]),
                content=post["content"],
                attachments=post["attachments"],
                publish=post["publish"]
                )
            
            self.scheduled_posts.append(post)

            logging.info(f"Scheduled {post['id']} for {post['post_time']}")

        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(self.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store scheduled post to file: {e}")
    
    def get_post_by_id(self, id: str):
        return next((d for d in self.scheduled_posts if d["id"] == id), None)
    
    def create_schedule_task(self, **kwargs):
        task = create_task_with_logging(self.bot.loop, self.schedule(
            self.send_scheduled_message,
            kwargs["wait_seconds"],
            kwargs["task_id"],
            kwargs["channel"],
            kwargs["content"],
            kwargs["attachments"],
            kwargs["publish"]))
        
        task.add_done_callback(lambda _: self.cleanup_schedule_remains(kwargs["task_id"], kwargs["attachments"]))
        
        return task
        
    def delete_finished_tasks(self):
        self.scheduled_tasks = {
            k: t for k, t in self.scheduled_tasks.items() if not t.done()
        }
    
    def cancel_all_tasks(self):
        for _, task in self.scheduled_tasks.items():
            task.cancel()
    
    def cleanup_schedule_remains(self, task_id: str, file_paths: list[str]):
        self.scheduled_posts = [post for post in self.scheduled_posts if post["id"] != task_id]
        self.delete_finished_tasks()

        for path in file_paths:
            try:
                os.remove(path)
            except Exception as e:
                logging.error(f"Failed to delete file: {e}")
        
        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(self.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to delete post from file: {e}")
    
    
    @commands.slash_command(
        name="schedule_post",
        description="Schedules a Discord post (your last sent message in this channel)"
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
        required=False,
        default=False
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
        
        task_id = f"{now.timestamp():.6f}".split(".")[1]
        while task_id in self.scheduled_tasks:
            task_id = str(int(task_id) + 1)
        
        content: str = to_schedule.content
        
        attachment_paths = []
        for i, attachment in enumerate(cast(discord.Message, to_schedule).attachments):
            filename = f"attachments/{task_id}_{i}_{attachment.filename}"
            await attachment.save(filename) # type: ignore
            attachment_paths.append(filename)
        
        self.scheduled_posts.append({
            "id": task_id,
            "guild_id": ctx.guild_id,
            "channel_id": channel.id,
            "message_id": to_schedule.id,
            "channel_id_source": ctx.channel_id,
            "content": content,
            "post_time": dt.isoformat(),
            "attachments": attachment_paths,
            "publish": publish
        })

        self.scheduled_tasks[task_id] = self.create_schedule_task(
            wait_seconds=wait_seconds,
            task_id=task_id,
            channel=channel,
            content=content,
            attachments=attachment_paths,
            publish=publish
            )
        
        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(self.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store scheduled post to file: {e}")
        
        logging.info(f"Scheduled {task_id} for {dt.isoformat()}")
        await ctx.respond(embed=create_embed(description=f"`{task_id}`: Scheduled https://discord.com/channels/{ctx.guild_id}/{ctx.channel_id}/{to_schedule.id} for <t:{int(dt.timestamp())}:F> in <#{channel.id}>{' (\u2060:mega:\u2060)' if publish else ''}", color=0x00FF00))
    
    
    #TODO: handle too many scheduled posts
    @commands.slash_command(
        name="list_scheduled_posts",
        description="Lists all scheduled posts"
    )
    async def list_scheduled_posts(self, ctx: discord.ApplicationContext):
        response = ""
        for post in self.scheduled_posts:
            if ctx.guild_id != post["guild_id"]:
                continue
            response += f"- `{post["id"]}`: https://discord.com/channels/{post["guild_id"]}/{post["channel_id_source"]}/{post["message_id"]} on <t:{int(datetime.fromisoformat(post["post_time"]).timestamp())}:F> in <#{post["channel_id"]}>{' (\u2060:mega:\u2060)' if post["publish"] else ''}\n"

        local_posts = [post for post in self.scheduled_posts if post["guild_id"] == ctx.guild_id]
        if len(local_posts) == 0:
            await ctx.respond(embed=create_embed(description="No posts have been scheduled."))
        else:
            await ctx.respond(embed=create_embed(description=response))
    
    
    @commands.slash_command(
        name="modify_scheduled_post",
        description="Modifies a scheduled post"
    )
    @option(
        "id",
        description="The id used to reference the scheduled message",
        input_type=str,
        required=True
    )
    @option(
        "channel",
        description="Which channel to post the message in",
        input_type=discord.abc.GuildChannel,
        required=False
    )
    @option(
        "date",
        description="The date and time when to post the message (format: 15.1. 14:20)",
        input_type=str,
        required=False
    )
    @option(
        "message_id",
        description="The id of the message you want to schedule (to update the content and/or attachments)",
        input_type=str,
        required=False
    )
    @option(
        "publish",
        description="Whether or not to publish the message after it was posted",
        input_type=bool,
        required=False
    )
    async def modify_scheduled_post(self, ctx: discord.ApplicationContext, id: str, channel: discord.abc.GuildChannel, date: str, message_id: str, publish: bool):
        post = self.get_post_by_id(id)
        
        if post is None or ctx.guild_id != post["guild_id"]:
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return
        
        if not channel and not date and not message_id and not publish:
            await ctx.respond(embed=create_embed(description="Please specify at least one value to update.", color=0xFF0000))
            return
        
        if date:
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
            post["post_time"] = dt.isoformat()
            
        if channel:
            post["channel_id"] = channel.id
        
        if message_id:     
            message = await ctx.channel.fetch_message(int(message_id))       
            attachment_paths = []
            for i, attachment in enumerate(message.attachments):
                filename = f"attachments/{id}_{i}_{attachment.filename}"
                await attachment.save(filename) # type: ignore
                attachment_paths.append(filename)

            post["message_id"] = int(message_id)
            post["channel_id_source"] = ctx.channel_id
            post["content"] = message.content
            post["attachments"] = attachment_paths
        
        if publish is not None:
            post["publish"] = publish
        
        
        self.scheduled_tasks[id].cancel()
            
        self.scheduled_tasks[id] = self.create_schedule_task(
            wait_seconds=self.get_wait_seconds(datetime.fromisoformat(post["post_time"])),
            task_id=id,
            channel=channel or self.bot.get_channel(post["channel_id"]),
            content=post["content"],
            attachments=post["attachments"],
            publish=post["publish"]
            )
        
        try:
            with open(self.scheduled_posts_file_path, 'w') as file:
                json.dump(self.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store scheduled post to file: {e}")

        await ctx.respond(embed=create_embed(description=f"`{post["id"]}`: Scheduled https://discord.com/channels/{ctx.guild_id}/{post["channel_id_source"]}/{post["message_id"]} for <t:{int(datetime.fromisoformat(post["post_time"]).timestamp())}:F> in <#{post["channel_id"]}>{' (\u2060:mega:\u2060)' if post["publish"] else ''}", color=0x00FF00))
        
    
    @commands.slash_command(
        name="delete_scheduled_post",
        description="Deletes a scheduled post"
    )
    @option(
        "id",
        description="The id used to reference the scheduled message",
        input_type=str,
        required=True
    )
    async def delete_scheduled_post(self, ctx: discord.ApplicationContext, id: str):
        post = self.get_post_by_id(id)
        
        if post is None or ctx.guild_id != post["guild_id"]:
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return
        
        self.scheduled_tasks[id].cancel()
        self.cleanup_schedule_remains(id, post["attachments"])

        await ctx.respond(embed=create_embed(description="Deleted scheduled post successfully.", color=0x00FF00))

def setup(bot: commands.Bot):
    bot.add_cog(Scheduling(bot))
