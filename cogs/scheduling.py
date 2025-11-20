import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import inspect
import json
import logging
import os
import re
from typing import Any, Coroutine, cast
from discord import option
import discord
from discord.ext import commands

from utils.bot_utils import create_embed, create_task_with_logging, local_tz

_bot: commands.Bot = commands.Bot()

class RemindMeSelect(discord.ui.Select):
    def __init__(self, message: discord.Message):
        self.message = message
        options = [
            discord.SelectOption(label="in 1h"),
            discord.SelectOption(label="in 12h"),
            discord.SelectOption(label="in 24h"),
            discord.SelectOption(label="at 8:00"),
            discord.SelectOption(label="at 18:00"),
            discord.SelectOption(label="at 20:00"),
            discord.SelectOption(label="Other")
        ]
        super().__init__(placeholder="Choose a time...", options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = cast(str, self.values[0])
        
        if chosen == "Other":
            await interaction.response.send_modal(CustomRemindModal(self.message))
            return
        
        now = datetime.now(local_tz)
        if chosen.startswith("in "):
            remind_at = now + timedelta(hours=int(cast(re.Match[str], re.search(r"\d+", chosen)).group()))
        elif chosen.startswith("at "):
            hour = int(chosen.split(" ")[1].split(":")[0])
            remind_at = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if remind_at < now:
                remind_at = remind_at + timedelta(days=1)
        else:
            remind_at = now + timedelta(minutes=1)
        
        scheduling = cast(Scheduling, _bot.get_cog("Scheduling"))
        task_id = '-' + f"{now.timestamp():.6f}".split(".")[1]
        while (task_id in scheduling.scheduled_tasks) or (task_id[1:] in scheduling.scheduled_tasks):
            task_id = str(int(task_id) - 1).zfill(7)
        
        content = f"Here's your reminder about https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{self.message.id}"
        scheduling.scheduled_posts.append({
            "id": task_id,
            "author_id": interaction.user.id, # type: ignore
            "guild_id": interaction.guild_id,
            "channel_id": interaction.user.id, # type: ignore
            "message_id": self.message.id,
            "channel_id_source": interaction.channel_id,
            "content": content,
            "post_time": remind_at.isoformat(),
            "attachments": [],
            "publish": False
        })
        
        scheduling.scheduled_tasks[task_id] = scheduling.create_schedule_task(
            wait_seconds=scheduling.get_wait_seconds(remind_at),
            task_id=task_id,
            channel=interaction.user,
            content=content,
            attachments=[],
            publish=False
            )
        
        try:
            with open(scheduling.scheduled_posts_file_path, 'w') as file:
                json.dump(scheduling.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store reminder to file: {e}")
        
        logging.info(f"Reminder {task_id} set for {remind_at.isoformat()}")

        await interaction.response.send_message(embed=create_embed(
            description=f"Alright, I'll remind you about https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{self.message.id} <t:{int(remind_at.timestamp())}:R>!",
            color=0x00FF00
        ), ephemeral=True)


class RemindSelectView(discord.ui.View):
    def __init__(self, message: discord.Message):
        super().__init__()
        self.add_item(RemindMeSelect(message))


class CustomRemindModal(discord.ui.Modal):
    def __init__(self, message: discord.Message):
        super().__init__(title="Reminding you...")
        self.message = message

        example_date = (datetime.now(local_tz) + timedelta(minutes=5)).strftime("%d.%m. %H:%M")
        self.add_item(discord.ui.InputText(
            label="When do you want me to remind you?",
            placeholder=example_date,
            value=example_date,
            style=discord.InputTextStyle.short
        ))

    async def callback(self, interaction: discord.Interaction):
        date = self.children[0].value or ""
        
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
            await interaction.response.send_message(
                embed=create_embed(description="Please use the correct format for the date: `15.1. 14:20`", color=0xFF0000),
                ephemeral=True
            )
            return
        
        remind_at = dt.replace(second=0, microsecond=0)
        
        scheduling = cast(Scheduling, _bot.get_cog("Scheduling"))
        task_id = '-' + f"{now.timestamp():.6f}".split(".")[1]
        while (task_id in scheduling.scheduled_tasks) or (task_id[1:] in scheduling.scheduled_tasks):
            task_id = str(int(task_id) - 1).zfill(7)
        
        content = f"Here's your reminder about https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{self.message.id}"
        scheduling.scheduled_posts.append({
            "id": task_id,
            "author_id": interaction.user.id, # type: ignore
            "guild_id": interaction.guild_id,
            "channel_id": interaction.user.id, # type: ignore
            "message_id": self.message.id,
            "channel_id_source": interaction.channel_id,
            "content": content,
            "post_time": remind_at.isoformat(),
            "attachments": [],
            "publish": False
        })
        
        scheduling.scheduled_tasks[task_id] = scheduling.create_schedule_task(
            wait_seconds=scheduling.get_wait_seconds(remind_at),
            task_id=task_id,
            channel=interaction.user,
            content=content,
            attachments=[],
            publish=False
            )
        
        try:
            with open(scheduling.scheduled_posts_file_path, 'w') as file:
                json.dump(scheduling.scheduled_posts, file, indent=4)
        except (OSError, json.JSONDecodeError) as e:
            logging.error(f"Failed to store reminder to file: {e}")
        
        logging.info(f"Reminder {task_id} set for {remind_at.isoformat()}")

        await interaction.response.send_message(embed=create_embed(
            description=f"Alright, I'll remind you about https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{self.message.id} <t:{int(remind_at.timestamp())}:R>!",
            color=0x00FF00
        ), ephemeral=True)


class Scheduling(commands.Cog):
    def __init__(self, bot):
        global _bot
        _bot = bot
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
        result = what(id, *args)
        if inspect.isawaitable(result):
            await result
    
    async def send_scheduled_message(self, id: str, channel: discord.TextChannel | discord.User, content: str, file_paths: list[str], publish: bool):
        files = [discord.File(path) for path in file_paths if os.path.exists(path)]
        try:
            message = await channel.send(content, files=files)
            if publish:
                await message.publish()
        finally:
            self.cleanup_schedule_remains(id, file_paths)
    
    async def load_scheduled_posts(self, posts: list):
        for post in posts:
            wait_seconds = self.get_wait_seconds(datetime.fromisoformat(post["post_time"]))
            if wait_seconds <= 0:
                logging.info(f"Trying to schedule {post['id']} failed: Due date is in the past.")
                continue
            
            if post["id"].startswith("-"):
                user = await self.bot.get_or_fetch_user(post["author_id"])
                channel = user
            else:
                channel = self.bot.get_channel(post["channel_id"])
            
            self.scheduled_tasks[post["id"]] = self.create_schedule_task(
                wait_seconds=wait_seconds,
                task_id=post["id"],
                channel=channel,
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
    
    def validate_post(self, ctx: discord.ApplicationContext, post: dict | None):
        if (post is None or
            ctx.guild_id != post["guild_id"] or
            ctx.author.id != post["author_id"] or
            post["id"].startswith("-")):
            return False
        return True
    
    def validate_reminder(self, ctx: discord.ApplicationContext, post: dict | None):
        if (post is None or
            ctx.author.id != post["author_id"] or
            not post["id"].startswith("-")):
            return False
        return True
    
    def create_schedule_task(self, **kwargs):
        task = create_task_with_logging(self.bot.loop, self.schedule(
            self.send_scheduled_message,
            kwargs["wait_seconds"],
            kwargs["task_id"],
            kwargs["channel"],
            kwargs["content"],
            kwargs["attachments"],
            kwargs["publish"]))
        
        return task
        
    def delete_finished_tasks(self):
        self.scheduled_tasks = {
            k: t for k, t in self.scheduled_tasks.items() if not t.done()
        }
    
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
        while (task_id in self.scheduled_tasks) or (('-' + task_id) in self.scheduled_tasks):
            task_id = str(int(task_id) + 1).zfill(6)
        
        content: str = to_schedule.content
        
        attachment_paths = []
        for i, attachment in enumerate(cast(discord.Message, to_schedule).attachments):
            filename = f"attachments/{task_id}_{i}_{attachment.filename}"
            await attachment.save(filename) # type: ignore
            attachment_paths.append(filename)
        
        self.scheduled_posts.append({
            "id": task_id,
            "author_id": ctx.author.id,
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
            if not self.validate_post(ctx, post):
                continue
            response += f"- `{post["id"]}`: https://discord.com/channels/{post["guild_id"]}/{post["channel_id_source"]}/{post["message_id"]} <t:{int(datetime.fromisoformat(post["post_time"]).timestamp())}:R> in <#{post["channel_id"]}>{' (\u2060:mega:\u2060)' if post["publish"] else ''}\n"

        if len(response) == 0:
            await ctx.respond(embed=create_embed(description="No posts have been scheduled."))
        else:
            await ctx.respond(embed=create_embed(description=response))
    
    
    #TODO: handle too many scheduled reminders
    @commands.slash_command(
        name="list_reminders",
        description="Lists all your reminders"
    )
    async def list_reminders(self, ctx: discord.ApplicationContext):
        response = ""
        for reminder in self.scheduled_posts:
            if not self.validate_reminder(ctx, reminder):
                continue
            response += f"- `{reminder["id"][1:]}`: https://discord.com/channels/{reminder["guild_id"]}/{reminder["channel_id_source"]}/{reminder["message_id"]} <t:{int(datetime.fromisoformat(reminder["post_time"]).timestamp())}:R>\n"
            
        if len(response) == 0:
            await ctx.respond(embed=create_embed(description="You have no reminders."), ephemeral=True)
        else:
            await ctx.respond(embed=create_embed(description=response), ephemeral=True)
    
    
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
        
        if (not self.validate_post(ctx, post)):
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return
        assert post != None
        
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
        
        if (not self.validate_post(ctx, post)):
            await ctx.respond(embed=create_embed(description="Couldn't find post with this ID.", color=0xFF0000))
            return
        assert post != None
        
        self.scheduled_tasks[id].cancel()
        self.cleanup_schedule_remains(id, post["attachments"])

        await ctx.respond(embed=create_embed(description="Deleted scheduled post successfully.", color=0x00FF00))
    
    
    @commands.slash_command(
        name="delete_reminder",
        description="Deletes a reminder"
    )
    @option(
        "id",
        description="The reminder's id",
        input_type=str,
        required=True
    )
    async def delete_reminder(self, ctx: discord.ApplicationContext, id: str):
        id = '-' + id
        reminder = self.get_post_by_id(id)
        
        if (not self.validate_reminder(ctx, reminder)):
            await ctx.respond(embed=create_embed(description="Couldn't find reminder with this ID.", color=0xFF0000))
            return
        assert reminder != None
        
        self.scheduled_tasks[id].cancel()
        self.cleanup_schedule_remains(id, reminder["attachments"])

        await ctx.respond(embed=create_embed(description="Deleted reminder successfully.", color=0x00FF00))
    
    
    @commands.message_command(name="Remind Me")
    async def remind_me(self, ctx: discord.ApplicationContext, message: discord.Message):
        await ctx.respond(
            "Remind me about this message:",
            view=RemindSelectView(message),
            ephemeral=True,
            delete_after=10
        )

def setup(bot: commands.Bot):
    bot.add_cog(Scheduling(bot))

#TODO: reduce code cloning, also date validate logik und schedule post logik auslagern
